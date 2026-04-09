"""
LLM 性能基准测试：多配置对比
────────────────────────────────────────────────────────────────
测试层次：
  Layer 1 - 直连 Ollama（纯 LLM 速度）
  Layer 2 - ai-service 完整链路（端到端）
  Layer 3 - 并发压力（多用户吞吐量）
  Layer 4 - Context 长度敏感性（输入长度对延迟的影响）

核心指标：
  TTFT    Time To First Token     首 token 延迟（用户感知最重要）
  TPOT    Time Per Output Token   每 token 生成耗时（流畅度）
  ITL     Inter-Token Latency     token 间隔均值（streaming 卡顿感）
  Jitter  ITL 标准差              streaming 抖动（数值大 = 时快时慢）
  TPS     Tokens Per Second       生成速度
  RPS     Requests Per Second     吞吐量（并发测试）
  P50/P95/P99                     延迟分位数
  Error Rate                      错误率及错误类型分布

用法：
  python tests/performance/benchmark_llm.py                          # 全跑
  python tests/performance/benchmark_llm.py --layer 1               # 只跑 Layer 1
  python tests/performance/benchmark_llm.py --layer 1 --repeats 5   # 每组跑 5 次
  python tests/performance/benchmark_llm.py --configs baseline_8b,optimized_8b
"""

import asyncio
import aiohttp
import time
import json
import statistics
import argparse
import os
import sys
from dataclasses import dataclass, field
from typing import Optional

# ──────────────────────────────────────────────
# 地址配置
# ──────────────────────────────────────────────
OLLAMA_BASE = os.getenv("OLLAMA_BASE",  "http://172.19.3.136:11434/v1")
AI_SERVICE  = os.getenv("AI_SERVICE",  "http://localhost:8000")

# ──────────────────────────────────────────────
# 对比配置（新增配置追加在此，不修改已有配置）
# ──────────────────────────────────────────────
CONFIGS = {
    "baseline_8b": {
        "desc": "qwen3:8b 默认（thinking ON，全量 context）",
        "model": "qwen3:8b",
        "extra": {},
    },
    "optimized_8b": {
        "desc": "qwen3:8b（think:false，num_ctx:4096）",
        "model": "qwen3:8b",
        "extra": {"think": False, "num_ctx": 4096},
    },
    "baseline_4b": {
        "desc": "qwen3:4b 默认",
        "model": "qwen3:4b",
        "extra": {},
    },
    "optimized_4b": {
        "desc": "qwen3:4b（think:false，num_ctx:4096）",
        "model": "qwen3:4b",
        "extra": {"think": False, "num_ctx": 4096},
    },
    "flash_8b": {
        "desc": "qwen3:8b + FLASH_ATTENTION（think:false, num_ctx:8192）",
        "model": "qwen3:8b",
        "extra": {"think": False, "num_ctx": 8192},
    },
}

LAYER1_PROMPTS = [
    ("simple",    "你好"),
    ("tool_call", "查一下我本周的工时"),
    ("analysis",  "帮我分析一下上个月的工时情况"),
]

LAYER2_CASES = [
    ("chat",    {"message": "你好",                       "session_id": "bench-chat"}),
    ("tool",    {"message": "查一下我本周的工时",            "session_id": "bench-tool"}),
    ("rag",     {"message": "工时填报的截止时间是几号",       "session_id": "bench-rag"}),
    ("complex", {"message": "帮我统计上个月项目A的总工时",    "session_id": "bench-complex"}),
]

LAYER2_HEADERS = {
    "Content-Type": "application/json",
    "X-User-ID": "1",
    "X-Entity-Type": "employee",
    "X-Department-ID": "1",
}

# Context 敏感性测试的输入长度梯度（字符数）
CONTEXT_LENGTHS = [100, 500, 1000, 2000, 4000]


# ──────────────────────────────────────────────
# 数据结构
# ──────────────────────────────────────────────
@dataclass
class Result:
    ttft:    float        # Time To First Token (s)
    total:   float        # 总耗时 (s)
    tpot:    float        # Time Per Output Token = (total - ttft) / tokens (s)
    tokens:  int          # 输出 token 数
    tps:     float        # Tokens Per Second
    itl_mean: float       # Inter-Token Latency 均值 (s)
    jitter:  float        # ITL 标准差（streaming 抖动）
    success: bool = True
    error:   str = ""
    error_type: str = ""  # timeout / connection / server / decode


def _percentile(data: list, p: float) -> float:
    if not data:
        return 0.0
    s = sorted(data)
    idx = int(len(s) * p / 100)
    return s[min(idx, len(s) - 1)]


def _stats(vals: list) -> dict:
    if not vals:
        return {k: 0.0 for k in ("mean", "p50", "p95", "p99", "min", "max", "std")}
    return {
        "mean": statistics.mean(vals),
        "p50":  _percentile(vals, 50),
        "p95":  _percentile(vals, 95),
        "p99":  _percentile(vals, 99),
        "min":  min(vals),
        "max":  max(vals),
        "std":  statistics.stdev(vals) if len(vals) > 1 else 0.0,
    }


# ──────────────────────────────────────────────
# 通用 SSE 流式请求（记录每个 chunk 的时间戳）
# ──────────────────────────────────────────────
async def _stream_ollama(session, cfg: dict, messages: list) -> Result:
    payload = {
        "model": cfg["model"],
        "messages": messages,
        "stream": True,
        **cfg.get("extra", {}),
    }
    t0 = time.perf_counter()
    ttft = None
    tokens = 0
    chunk_times = []  # 每个有内容 chunk 的时间戳

    try:
        async with session.post(
            f"{OLLAMA_BASE}/chat/completions",
            json=payload,
            timeout=aiohttp.ClientTimeout(total=120),
        ) as resp:
            if resp.status != 200:
                body = await resp.text()
                return Result(0, 0, 0, 0, 0, 0, 0, False,
                              f"HTTP {resp.status}", "server")
            async for raw in resp.content:
                raw = raw.strip()
                if not raw or raw == b"data: [DONE]":
                    continue
                if raw.startswith(b"data: "):
                    try:
                        chunk = json.loads(raw[6:])
                    except json.JSONDecodeError:
                        continue
                    delta = chunk["choices"][0].get("delta", {}).get("content", "")
                    if delta:
                        now = time.perf_counter()
                        if ttft is None:
                            ttft = now - t0
                        chunk_times.append(now)
                        tokens += 1

        total = time.perf_counter() - t0
        if ttft is None:
            ttft = total

        # ITL：相邻 chunk 时间差
        itl_vals = [chunk_times[i] - chunk_times[i-1]
                    for i in range(1, len(chunk_times))]
        itl_mean = statistics.mean(itl_vals) if itl_vals else 0.0
        jitter   = statistics.stdev(itl_vals) if len(itl_vals) > 1 else 0.0

        decode_time = total - ttft
        tpot = decode_time / tokens if tokens > 1 else 0.0
        tps  = tokens / total if total > 0 else 0.0

        return Result(ttft=ttft, total=total, tpot=tpot, tokens=tokens,
                      tps=tps, itl_mean=itl_mean, jitter=jitter)

    except asyncio.TimeoutError:
        return Result(0, 0, 0, 0, 0, 0, 0, False, "超时", "timeout")
    except aiohttp.ClientConnectorError as e:
        return Result(0, 0, 0, 0, 0, 0, 0, False, str(e)[:60], "connection")
    except Exception as e:
        return Result(0, 0, 0, 0, 0, 0, 0, False, str(e)[:60], "unknown")


async def _stream_ai_service(session, body: dict) -> Result:
    t0 = time.perf_counter()
    ttft = None
    tokens = 0
    chunk_times = []

    try:
        async with session.post(
            f"{AI_SERVICE}/api/ai/chat/stream",
            json=body,
            headers=LAYER2_HEADERS,
            timeout=aiohttp.ClientTimeout(total=120),
        ) as resp:
            if resp.status != 200:
                body_text = await resp.text()
                return Result(0, 0, 0, 0, 0, 0, 0, False,
                              f"HTTP {resp.status}: {body_text[:60]}", "server")
            async for raw in resp.content:
                raw = raw.strip()
                if not raw or raw.startswith(b"event:"):
                    continue
                if raw.startswith(b"data: "):
                    data_str = raw[6:].decode("utf-8", errors="ignore")
                    if data_str == "[DONE]":
                        continue
                    try:
                        chunk = json.loads(data_str)
                        content = (chunk.get("content")
                                   or chunk.get("delta", {}).get("content", ""))
                        if content:
                            now = time.perf_counter()
                            if ttft is None:
                                ttft = now - t0
                            chunk_times.append(now)
                            tokens += len(content)
                    except json.JSONDecodeError:
                        pass

        total = time.perf_counter() - t0
        if ttft is None:
            ttft = total

        itl_vals = [chunk_times[i] - chunk_times[i-1]
                    for i in range(1, len(chunk_times))]
        itl_mean = statistics.mean(itl_vals) if itl_vals else 0.0
        jitter   = statistics.stdev(itl_vals) if len(itl_vals) > 1 else 0.0
        decode_time = total - ttft
        tpot = decode_time / tokens if tokens > 1 else 0.0
        tps  = tokens / total if total > 0 else 0.0

        return Result(ttft=ttft, total=total, tpot=tpot, tokens=tokens,
                      tps=tps, itl_mean=itl_mean, jitter=jitter)

    except asyncio.TimeoutError:
        return Result(0, 0, 0, 0, 0, 0, 0, False, "超时", "timeout")
    except aiohttp.ClientConnectorError as e:
        return Result(0, 0, 0, 0, 0, 0, 0, False, str(e)[:60], "connection")
    except Exception as e:
        return Result(0, 0, 0, 0, 0, 0, 0, False, str(e)[:60], "unknown")


# ──────────────────────────────────────────────
# 打印工具
# ──────────────────────────────────────────────
def _sep(title=""):
    print("\n" + "═" * 72)
    if title:
        print(f"  {title}")
        print("═" * 72)


def _print_results_table(rows: list, columns: list):
    """通用表格打印"""
    widths = [max(len(str(r[i])) for r in [columns] + rows) + 2
              for i in range(len(columns))]
    fmt = "".join(f"{{:<{w}}}" for w in widths)
    print(fmt.format(*columns))
    print("─" * sum(widths))
    for row in rows:
        print(fmt.format(*[str(x) for x in row]))


def _f(v, digits=2):
    """格式化浮点数"""
    return f"{v:.{digits}f}"


# ──────────────────────────────────────────────
# Layer 1：直连 Ollama
# ──────────────────────────────────────────────
async def run_layer1(configs: list, repeats: int):
    _sep("Layer 1：直连 Ollama（纯 LLM 速度，排除 ai-service 影响）")

    rows = []
    connector = aiohttp.TCPConnector(limit=32)
    async with aiohttp.ClientSession(connector=connector) as session:
        for cfg_name in configs:
            cfg = CONFIGS[cfg_name]
            for label, prompt in LAYER1_PROMPTS:
                results = []
                for i in range(repeats):
                    r = await _stream_ollama(
                        session, cfg, [{"role": "user", "content": prompt}]
                    )
                    results.append(r)
                    icon = "✓" if r.success else "✗"
                    print(f"  {icon} [{cfg_name}][{label}] #{i+1}  "
                          f"TTFT={r.ttft:.2f}s  total={r.total:.2f}s  "
                          f"TPS={r.tps:.0f}  jitter={r.jitter*1000:.0f}ms"
                          + (f"  ERR={r.error}" if not r.success else ""))

                ok = [r for r in results if r.success]
                errors = {}
                for r in results:
                    if not r.success:
                        errors[r.error_type] = errors.get(r.error_type, 0) + 1

                if ok:
                    ttft_s  = _stats([r.ttft   for r in ok])
                    tps_s   = _stats([r.tps    for r in ok])
                    tpot_s  = _stats([r.tpot   for r in ok])
                    jitter_s= _stats([r.jitter for r in ok])
                    rows.append([
                        cfg_name, label,
                        _f(ttft_s["mean"]),  _f(ttft_s["p95"]),
                        _f(tps_s["mean"]),   _f(tpot_s["mean"] * 1000, 1),
                        _f(jitter_s["mean"] * 1000, 1),
                        f"{len(ok)}/{repeats}",
                        str(errors) if errors else "—",
                    ])

    print()
    _print_results_table(rows, [
        "配置", "场景",
        "TTFT mean", "TTFT p95",
        "TPS mean", "TPOT(ms)", "Jitter(ms)",
        "成功率", "错误分布"
    ])

    print("""
指标说明：
  TTFT      首 token 延迟（用户等待感知，越低越好）
  TPS       每秒生成 token 数（生成速度）
  TPOT      每个 token 平均耗时（= 1/TPS，越低越好）
  Jitter    token 间隔标准差（数值大 = 流式输出忽快忽慢，体验差）
  TTFT p95  95% 请求的首 token 延迟上限（稳定性指标）
""")


# ──────────────────────────────────────────────
# Layer 2：ai-service 完整链路
# ──────────────────────────────────────────────
async def run_layer2(repeats: int):
    _sep("Layer 2：ai-service 完整链路（TTFT 含 RAG 检索 + LLM 首 token）")

    rows = []
    connector = aiohttp.TCPConnector(limit=32)
    async with aiohttp.ClientSession(connector=connector) as session:
        for label, body in LAYER2_CASES:
            results = []
            for i in range(repeats):
                r = await _stream_ai_service(session, body)
                results.append(r)
                icon = "✓" if r.success else "✗"
                print(f"  {icon} [{label}] #{i+1}  "
                      f"TTFT={r.ttft:.2f}s  total={r.total:.2f}s  "
                      f"jitter={r.jitter*1000:.0f}ms"
                      + (f"  ERR={r.error}" if not r.success else ""))

            ok = [r for r in results if r.success]
            errors = {}
            for r in results:
                if not r.success:
                    errors[r.error_type] = errors.get(r.error_type, 0) + 1

            if ok:
                ttft_s  = _stats([r.ttft  for r in ok])
                total_s = _stats([r.total for r in ok])
                jit_s   = _stats([r.jitter for r in ok])
                rows.append([
                    label,
                    _f(ttft_s["mean"]),  _f(ttft_s["p95"]),  _f(ttft_s["p99"]),
                    _f(total_s["mean"]), _f(total_s["p95"]),
                    _f(jit_s["mean"] * 1000, 1),
                    f"{len(ok)}/{repeats}",
                    str(errors) if errors else "—",
                ])

    print()
    _print_results_table(rows, [
        "场景",
        "TTFT mean", "TTFT p95", "TTFT p99",
        "总耗时 mean", "总耗时 p95",
        "Jitter(ms)", "成功率", "错误分布"
    ])

    print("""
说明：
  TTFT p99  99% 请求的首 token 延迟（极端慢请求指标）
  总耗时    完整响应结束时间（含流式输出全部内容）
  tool 场景的 TTFT 高于 chat，是因为多了工具调用 HTTP 往返
  rag  场景的 TTFT 高于 chat，是因为多了 Milvus 向量检索
""")


# ──────────────────────────────────────────────
# Layer 3：并发压力
# ──────────────────────────────────────────────
async def run_layer3(concurrency_levels: list):
    _sep("Layer 3：并发压力（模拟多用户同时请求，测吞吐量与错误率）")

    prompt_body = {"message": "查一下我本周的工时", "session_id": "bench-concurrent"}
    rows = []

    for n in concurrency_levels:
        print(f"\n  并发数 = {n}，发送 {n} 个请求...")
        connector = aiohttp.TCPConnector(limit=n + 10)
        async with aiohttp.ClientSession(connector=connector) as session:
            tasks = [
                _stream_ai_service(
                    session,
                    {**prompt_body, "session_id": f"bench-concurrent-{i}"}
                )
                for i in range(n)
            ]
            wall_t0 = time.perf_counter()
            results = await asyncio.gather(*tasks)
            wall = time.perf_counter() - wall_t0

        ok = [r for r in results if r.success]
        fail = [r for r in results if not r.success]
        error_types = {}
        for r in fail:
            error_types[r.error_type] = error_types.get(r.error_type, 0) + 1

        rps = len(ok) / wall if wall > 0 else 0
        total_s = _stats([r.total for r in ok])
        ttft_s  = _stats([r.ttft  for r in ok])

        rows.append([
            n,
            f"{len(ok)}/{n}",
            f"{len(fail)/n*100:.0f}%",
            _f(rps, 2),
            _f(wall, 1),
            _f(ttft_s["mean"]),  _f(ttft_s["p95"]),
            _f(total_s["mean"]), _f(total_s["p95"]),
            str(error_types) if error_types else "—",
        ])
        print(f"    → RPS={rps:.2f}  错误率={len(fail)/n*100:.0f}%  "
              f"TTFT_mean={ttft_s['mean']:.1f}s  wall={wall:.1f}s")

    print()
    _print_results_table(rows, [
        "并发数", "成功率", "错误率", "RPS",
        "wall(s)",
        "TTFT mean", "TTFT p95",
        "总耗时 mean", "总耗时 p95",
        "错误分布"
    ])

    print("""
说明：
  RPS       每秒完成请求数（吞吐量核心指标）
  错误率    连接/超时/服务端错误百分比
  wall      所有请求从发出到全部完成的挂钟时间
  随并发增加 TTFT 上涨 = 请求在 Ollama 队列排队
  随并发增加 错误率上涨 = 服务已过载
""")


# ──────────────────────────────────────────────
# Layer 4：Context 长度敏感性
# ──────────────────────────────────────────────
async def run_layer4(configs: list, context_lengths: list):
    _sep("Layer 4：Context 长度敏感性（输入长度对 TTFT 的影响）")
    base_text = "这是用来填充上下文长度的测试文本，内容是关于工时管理系统的一些背景信息。" * 10

    rows = []
    connector = aiohttp.TCPConnector(limit=10)
    async with aiohttp.ClientSession(connector=connector) as session:
        for cfg_name in configs[:2]:  # Layer 4 只对比前两个配置，避免太慢
            cfg = CONFIGS[cfg_name]
            for length in context_lengths:
                ctx = base_text[:length]
                messages = [
                    {"role": "user", "content": ctx + "\n\n基于以上背景，查一下我本周的工时"},
                ]
                r = await _stream_ollama(session, cfg, messages)
                icon = "✓" if r.success else "✗"
                print(f"  {icon} [{cfg_name}] input={length}chars  "
                      f"TTFT={r.ttft:.2f}s  total={r.total:.2f}s  tokens={r.tokens}")
                rows.append([
                    cfg_name,
                    length,
                    _f(r.ttft) if r.success else "ERR",
                    _f(r.total) if r.success else "ERR",
                    r.tokens if r.success else 0,
                ])

    print()
    _print_results_table(rows, ["配置", "输入长度(chars)", "TTFT(s)", "总耗时(s)", "输出tokens"])

    print("""
说明：
  TTFT 随输入长度增加而增加（Prefill 阶段需要处理更多 token）
  num_ctx 设置 4096 时，超过 4096 token 的输入会被截断
  本测试帮助确定合理的 num_ctx 设置范围
""")


# ──────────────────────────────────────────────
# 冷启动测试
# ──────────────────────────────────────────────
async def run_cold_warm(configs: list):
    _sep("Cold Start vs Warm Start（模型未加载 vs 已加载的延迟差异）")
    print("  注意：此测试需要 Ollama 中途卸载模型，结果仅供参考")

    connector = aiohttp.TCPConnector(limit=5)
    async with aiohttp.ClientSession(connector=connector) as session:
        for cfg_name in configs[:1]:
            cfg = CONFIGS[cfg_name]
            print(f"\n  配置: {cfg_name}")
            warm_results = []
            for i in range(3):
                r = await _stream_ollama(
                    session, cfg, [{"role": "user", "content": "hello"}]
                )
                warm_results.append(r)
                print(f"    Warm #{i+1}: TTFT={r.ttft:.2f}s  total={r.total:.2f}s")

            ok = [r for r in warm_results if r.success]
            if ok:
                print(f"    Warm 均值: TTFT={statistics.mean(r.ttft for r in ok):.2f}s")


# ──────────────────────────────────────────────
# 入口
# ──────────────────────────────────────────────
async def main(layers: list, configs: list, repeats: int,
               concurrency: list, ctx_lengths: list):
    print(f"\n{'='*72}")
    print("  LLM 性能基准测试")
    print(f"  Ollama    : {OLLAMA_BASE}")
    print(f"  ai-service: {AI_SERVICE}")
    print(f"  测试配置  : {configs}")
    print(f"  每组重复  : {repeats} 次")
    print(f"{'='*72}")

    if 1 in layers:
        await run_layer1(configs, repeats)
    if 2 in layers:
        await run_layer2(repeats)
    if 3 in layers:
        await run_layer3(concurrency)
    if 4 in layers:
        await run_layer4(configs, ctx_lengths)

    print(f"\n{'='*72}")
    print("  ✅ 测试完成")
    print(f"{'='*72}\n")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="LLM 性能基准测试")
    parser.add_argument("--layer", type=int, choices=[1, 2, 3, 4],
                        help="只跑指定层（默认全跑）")
    parser.add_argument("--configs",
                        default="baseline_8b,optimized_8b,baseline_4b,optimized_4b",
                        help="对比配置，逗号分隔")
    parser.add_argument("--repeats",  type=int, default=3,
                        help="每场景重复次数（默认 3）")
    parser.add_argument("--concurrency", default="1,2,4,8",
                        help="Layer 3 并发数列表（默认 1,2,4,8）")
    parser.add_argument("--ctx-lengths", default="100,500,1000,2000,4000",
                        help="Layer 4 输入长度梯度（默认 100,500,1000,2000,4000）")
    args = parser.parse_args()

    layers     = [args.layer] if args.layer else [1, 2, 3, 4]
    configs    = [c.strip() for c in args.configs.split(",") if c.strip() in CONFIGS]
    concurrency = [int(x) for x in args.concurrency.split(",")]
    ctx_lengths = [int(x) for x in args.ctx_lengths.split(",")]

    if not configs:
        print(f"错误：无效配置名，可用配置：{list(CONFIGS.keys())}")
        sys.exit(1)

    asyncio.run(main(layers, configs, args.repeats, concurrency, ctx_lengths))
