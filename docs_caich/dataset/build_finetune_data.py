#!/usr/bin/env python3
"""
从 Langfuse trace 构建 SFT / DPO 数据集。

依据 langgraph_agent 埋的点位（见 app/services/langfuse_client.py）：
  trace "chat"                     metadata: entity_type(角色) / user_message
    ├─ GENERATION generate_with_tools   input=完整messages, output=模型带参数的 tool_call
    ├─ SPAN resolve_project_id          input={raw,is_name}, output={resolved_id,error}, level
    └─ SPAN tool:<name>                 input=params, output=result{success,error}, level

产出：
  - sft.jsonl ：干净成功轨迹 → prompt + 标准 tool_call（正样本）
  - dpo.jsonl ：规则检测出的“客观错” → prompt + chosen + rejected

用法：
  python build_finetune_data.py --session-prefix batch100-1784186728 --synth-chosen
环境变量（缺则从仓库根 .env.local 读）：
  LANGFUSE_PUBLIC_KEY / LANGFUSE_SECRET_KEY / LANGFUSE_BASE_URL

⚠️ 铁律（对齐 finetuning.md）：环境错（连接失败/超时）不是模型错，一律剔除，不进任何数据集。
"""
import argparse
import base64
import json
import os
import re
import sys
import urllib.request

# ── 拒绝/澄清类 chosen 文案（DPO 里“该拒绝”的正样本）────────────────────────
DURATION_MAX = 10  # save_workhour 校验上限（见 app/tools/save_workhour.py）
REFUSE_OVER_LIMIT = f"单次填报工时不能超过 {DURATION_MAX} 小时，请确认实际工时，或分多天填报。"
REFUSE_HALF_STEP = "工时时长必须为 0.5 的整数倍，请调整后再填报。"
REFUSE_OVER_PRIV = "抱歉，您当前的角色（employee）无权查询/填报他人的工时记录，请联系部门管理员。"
CLARIFY_PROJECT = "未能将「{name}」解析为项目，请提供项目 ID 或确认项目全称后再填报。"

ENV_ERROR_PAT = re.compile(
    r"connection|connect|timeout|timed out|All connection attempts|502|503|504|无法获取|网络",
    re.I,
)


# ── Langfuse 读取 ────────────────────────────────────────────────────────────
def load_env():
    pk = os.getenv("LANGFUSE_PUBLIC_KEY")
    sk = os.getenv("LANGFUSE_SECRET_KEY")
    host = os.getenv("LANGFUSE_BASE_URL") or os.getenv("LANGFUSE_HOST")
    if not (pk and sk and host):
        # 回落读仓库根 .env.local
        root = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
        envf = os.path.join(root, ".env.local")
        if os.path.exists(envf):
            for line in open(envf):
                line = line.strip()
                if line.startswith("#") or "=" not in line:
                    continue
                k, v = line.split("=", 1)
                v = v.strip().strip('"').strip("'")
                if k == "LANGFUSE_PUBLIC_KEY" and not pk:
                    pk = v
                elif k == "LANGFUSE_SECRET_KEY" and not sk:
                    sk = v
                elif k in ("LANGFUSE_BASE_URL", "LANGFUSE_HOST") and not host:
                    host = v
    if not (pk and sk and host):
        sys.exit("缺 LANGFUSE_PUBLIC_KEY / SECRET_KEY / BASE_URL（环境或 .env.local）")
    return pk, sk, host.rstrip("/")


class LF:
    def __init__(self, pk, sk, host):
        self.host = host
        self.auth = base64.b64encode(f"{pk}:{sk}".encode()).decode()

    def _get(self, path):
        req = urllib.request.Request(self.host + path, headers={"Authorization": "Basic " + self.auth})
        with urllib.request.urlopen(req, timeout=20) as r:
            return json.load(r)

    def list_traces(self, tag, session_prefix, max_pages):
        out = []
        for page in range(1, max_pages + 1):
            q = f"/api/public/traces?limit=100&page={page}"
            if tag:
                q += f"&tags={tag}"
            data = self._get(q).get("data", [])
            if not data:
                break
            for t in data:
                if session_prefix and not (t.get("sessionId") or "").startswith(session_prefix):
                    continue
                out.append(t)
        return out

    def trace(self, tid):
        return self._get(f"/api/public/traces/{tid}")


# ── 解析一条 trace → 归一化轨迹 ──────────────────────────────────────────────
def as_dict(x):
    if isinstance(x, dict):
        return x
    if isinstance(x, str):
        try:
            return json.loads(x)
        except Exception:
            return {}
    return {}


def is_numeric(v):
    return isinstance(v, (int, float)) or (isinstance(v, str) and v.strip().isdigit())


def parse_trace(t):
    md = t.get("metadata") or {}
    obs = t.get("observations", [])
    # 一次请求可能有多个 generate_with_tools（Agent loop）：按时间排序，
    # 取“第一个真正产生 tool_call 的”作为模型决策；文本回复取最后一个。
    gens, resolves, tools = [], [], []
    for o in obs:
        name = o.get("name") or ""
        typ = o.get("type")
        if typ == "GENERATION" and name == "generate_with_tools":
            gens.append(o)
        elif name == "resolve_project_id":
            resolves.append(o)
        elif name.startswith("tool:"):
            tools.append(o)
    if not gens:
        return None
    gens.sort(key=lambda x: x.get("startTime") or "")
    tools.sort(key=lambda x: x.get("startTime") or "")

    call, prompt_msgs, content = None, None, None
    for g in gens:
        out = as_dict(g.get("output"))
        tcs = out.get("tool_calls") or []
        if tcs and call is None:
            call = tcs[0]                 # {name, arguments}
            prompt_msgs = g.get("input")  # 该次调用的完整 messages
        if out.get("content"):
            content = out.get("content")
    if prompt_msgs is None:               # 全程无工具调用（知识问答/闲聊）
        prompt_msgs = gens[0].get("input")

    return {
        "role": md.get("entity_type"),
        "user_message": md.get("user_message"),
        "messages": prompt_msgs,
        "call": call,
        "content": content,
        "resolves": [as_dict(r.get("output")) | {"level": r.get("level")} for r in resolves],
        "tools": [{"result": as_dict(x.get("output")), "level": x.get("level")} for x in tools],
    }


# ── 归一化 assistant tool_call（trl/openai 风格）───────────────────────────────
def assistant_toolcall(call):
    return {
        "role": "assistant", "content": "",
        "tool_calls": [{
            "id": "call_0", "type": "function",
            "function": {"name": call["name"], "arguments": json.dumps(call.get("arguments", {}), ensure_ascii=False)},
        }],
    }


def assistant_text(text):
    return {"role": "assistant", "content": text}


def norm_result(tool_output):
    """工具 span 的 output 里 success/error 嵌在 .result 下（也可能在顶层）→ 归一。"""
    o = as_dict(tool_output)
    inner = o.get("result")
    base = inner if isinstance(inner, dict) else o
    success = base.get("success", o.get("success", True))
    error = base.get("error") or o.get("error") or ""
    return bool(success), str(error)


def env_failed(traj):
    """任一工具结果是环境错（连接/超时）→ 该轨迹剔除，不作为模型信号。"""
    for tv in traj["tools"]:
        _, err = norm_result(tv["result"])
        if ENV_ERROR_PAT.search(err):
            return True
    return False


def tool_ok(traj):
    """最后一个工具是否真正成功。无工具→None。"""
    if not traj["tools"]:
        return None
    last = traj["tools"][-1]
    if last["level"] == "ERROR":
        return False
    succ, _ = norm_result(last["result"])
    return succ


# 工具选择 golden（对齐 eval 指标：教"选对工具"）
KNOWN_TOOLS = {"query_timesheet", "compute_statistics", "sql_query", "query_project",
               "generate_weekly_report", "save_workhour", "batch_save_workhour", "export_report"}


def golden_call(tool):
    """为"选对工具"合成一个最简合理 tool_call（参数非重点，重点是工具名对）。"""
    defaults = {
        "query_timesheet": {"start_date": "2026-07-13", "end_date": "2026-07-19"},
        "compute_statistics": {"start_date": "2026-07-01", "end_date": "2026-07-31", "statistics_type": "project"},
        "sql_query": {"query": "统计各项目工时并排序"},
        "query_project": {},
        "generate_weekly_report": {},
        "save_workhour": {"date": "2026-07-16", "duration": 8},
        "batch_save_workhour": {},
        "export_report": {"start_date": "2026-07-01", "end_date": "2026-07-31"},
    }
    return {"name": tool, "arguments": defaults.get(tool, {})}


# ── 构建 ─────────────────────────────────────────────────────────────────────
def build(trajs, synth_chosen):
    sft, dpo, stats = [], [], {"sft": 0, "dpo": 0, "skip_env": 0, "skip_nomodel": 0}
    for tr in trajs:
        if tr is None:
            stats["skip_nomodel"] += 1
            continue
        if env_failed(tr):
            stats["skip_env"] += 1
            continue
        call = tr["call"]
        msgs = tr["messages"] if isinstance(tr["messages"], list) else None
        if msgs is None:
            continue

        # 该 trace 里项目名是否解析成功（仅用于判 SFT vs DPO，不把 ID 写进样本）。
        # 注意：ID 若来自 mock 种子是"假号"，绝不写入训练目标；模型本就该输出项目名，
        # 由 ParamResolver 在推理时解析——所以 SFT 保留模型的原始 tool_call（含项目名）。
        resolved_pid = next(
            (str(r.get("resolved_id")) for r in tr["resolves"]
             if r.get("resolved_id") and is_numeric(r.get("resolved_id"))),
            None,
        )

        expected = tr.get("expected_tool")   # prompt 的期望工具（golden）
        dim = tr.get("dim")
        is_toolsel = dim in ("tool_select", "date_reason")  # 这两类才用"选对工具"判据

        # ---------- SFT：只收"选对工具"的正样本（execution 成功 ≠ 选对）----------
        if call and tool_ok(tr) is True:
            correct = (not is_toolsel) or (call["name"] == expected)
            if correct:
                sft.append({"messages": msgs + [assistant_toolcall(call)]})
                stats["sft"] += 1
            # 选错但执行成功 → 不进 SFT（否则教错），下面进 wrong_tool DPO
        elif not call and tr["content"]:
            if is_toolsel and expected in KNOWN_TOOLS:
                # 该调工具却没调（如"本季度汇总"→不作为）→ DPO：chosen=对工具, rejected=文本
                dpo.append({"prompt": msgs, "chosen": assistant_toolcall(golden_call(expected)),
                            "rejected": assistant_text(tr["content"]),
                            "meta": {"trap": "should_call_tool", "expected": expected}})
                stats["dpo"] += 1
            else:  # 知识问答/闲聊：文本正样本
                sft.append({"messages": msgs + [assistant_text(tr["content"])]})
                stats["sft"] += 1

        # ---------- DPO：规则检测“客观错” ----------
        if not call:
            continue
        name, args = call["name"], (call.get("arguments") or {})
        _traps = set()  # 每条 trace 内每类陷阱只收一次
        # 最后一个工具的报错文本（用于按错误信息兜底检测）
        _last_err = norm_result(tr["tools"][-1]["result"])[1] if tr["tools"] else ""

        def _add(trap, chosen, meta):
            if trap in _traps:
                return
            _traps.add(trap)
            dpo.append({"prompt": msgs, "chosen": chosen, "rejected": assistant_toolcall(call),
                        "meta": {"trap": trap, **meta}})
            stats["dpo"] += 1

        # 0) 工具边界纠错（对齐 eval 指标）：工具选择/日期类，模型选了别的工具
        if is_toolsel and expected in KNOWN_TOOLS and name != expected:
            _add("wrong_tool", assistant_toolcall(golden_call(expected)),
                 {"expected": expected, "got": name})

        # 1) 项目名当 ID —— 只有"解析失败"才算 DPO 坏样本；解析成功的已作为
        #    SFT 正样本（canonical 用真实 ID），不再进 DPO（路B 实锤：能解析≠模型错）。
        if name == "save_workhour" and args.get("project_id") and not is_numeric(args["project_id"]):
            raw = str(args["project_id"])
            if not resolved_pid:  # 解析失败（生僻/占位/查无此项目）
                _add("project_name_as_id",
                     assistant_text(CLARIFY_PROJECT.format(name=raw)),
                     {"raw": raw, "resolved": None})

        # 2) 单次工时超上限（schema 上限 10h）
        dur = args.get("duration")
        if name == "save_workhour" and isinstance(dur, (int, float)) and dur > DURATION_MAX:
            _add("duration_over_limit", assistant_text(REFUSE_OVER_LIMIT), {"duration": dur})

        # 3) 工时非 0.5 整数倍
        if name == "save_workhour" and isinstance(dur, (int, float)) and dur > 0 and abs(round(dur * 2) - dur * 2) > 1e-9:
            _add("duration_not_half_step", assistant_text(REFUSE_HALF_STEP), {"duration": dur})

        # 4) 越权：employee 指向他人（优先按参数，其次按工具报错兜底）
        member = args.get("member_name")
        if tr["role"] == "employee" and member:
            _add("over_privilege", assistant_text(REFUSE_OVER_PRIV), {"member": member})
        elif tr["role"] == "employee" and re.search(r"没有权限|无权", _last_err):
            _add("over_privilege", assistant_text(REFUSE_OVER_PRIV), {"from": "error"})

    return sft, dpo, stats


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--session-prefix", default="", help="只取 sessionId 以此开头的 trace")
    ap.add_argument("--tag", default="workhour-agent", help="按 trace tag 过滤（默认 workhour-agent）")
    ap.add_argument("--max-pages", type=int, default=5, help="最多翻几页（每页100）")
    ap.add_argument("--synth-chosen", action="store_true",
                    help="项目名解析失败时，用澄清文案合成 chosen（否则跳过该对）")
    _here = os.path.dirname(os.path.abspath(__file__))
    ap.add_argument("--prompts", default=",".join(
        os.path.join(_here, f) for f in ("prompts_v2.jsonl", "prompts.jsonl", "prompts2.jsonl")),
        help="prompt 池（含期望工具 tool/dim），用于对齐工具选择 golden")
    ap.add_argument("--out-dir", default=_here)
    args = ap.parse_args()

    # 加载 prompt → 期望工具/dim（golden）
    pmap = {}
    for pf in args.prompts.split(","):
        pf = pf.strip()
        if not pf or not os.path.exists(pf):
            continue
        for l in open(pf):
            l = l.strip()
            if not l:
                continue
            d = json.loads(l)
            if d.get("_comment") or "id" not in d:
                continue
            pmap[d["id"]] = {"tool": d.get("tool"), "dim": d.get("dim")}

    lf = LF(*load_env())
    print(f"拉取 trace（tag={args.tag} prefix={args.session_prefix or '*'}）...", file=sys.stderr)
    heads = lf.list_traces(args.tag, args.session_prefix, args.max_pages)
    print(f"命中 {len(heads)} 条，逐条取 observations...", file=sys.stderr)
    trajs = []
    for h in heads:
        t = lf.trace(h["id"])
        traj = parse_trace(t)
        if traj is not None:
            sid = t.get("sessionId") or h.get("sessionId") or ""
            pid = sid[len(args.session_prefix) + 1:] if args.session_prefix and sid.startswith(args.session_prefix) else None
            info = pmap.get(pid, {})
            traj["expected_tool"] = info.get("tool")
            traj["dim"] = info.get("dim")
        trajs.append(traj)

    sft, dpo, stats = build(trajs, args.synth_chosen)

    sft_path = os.path.join(args.out_dir, "sft.jsonl")
    dpo_path = os.path.join(args.out_dir, "dpo.jsonl")
    with open(sft_path, "w") as f:
        for r in sft:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")
    with open(dpo_path, "w") as f:
        for r in dpo:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")

    print(f"\n=== 完成 ===")
    print(f"  SFT 正样本 : {stats['sft']}  → {sft_path}")
    print(f"  DPO 偏好对 : {stats['dpo']}  → {dpo_path}")
    print(f"  剔除(环境错): {stats['skip_env']}   剔除(无模型输出): {stats['skip_nomodel']}")
    import collections
    tc = collections.Counter(d["meta"]["trap"] for d in dpo)
    if tc:
        print("  DPO 陷阱分布:", dict(tc))


if __name__ == "__main__":
    main()
