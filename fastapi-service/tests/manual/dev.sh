# 本地联调一键环境（source 我，别执行我）
#
#   source fastapi-service/tests/manual/dev.sh
#   ask "我这周填了多少工时？"
#
# 幂等：隧道通就不重起，token 没过期就不重换。重复 source 无副作用。
#
# 提供：
#   ask "问题" [session]    非流式提问，打印 [route=… tool=…] + 回复
#   asks "问题" [session]   流式提问，看原始 SSE（含 event: chart）
#   as_emp / as_adm / as_sup  切角色（employee / deptAdmin / superAdmin）
#   probe [CATEGORY=xx]     跑 run_probe.py 只读集
#   repl                    多轮对话调试
#   dev_status              看隧道/服务/token 状态
#   token_refresh           强制重换 token
#   有记忆，而且默认所有 ask 共用一个会话
# 密钥不落本地：MCP_API_KEY 只在 172 上展开；token 缓存在仓库外
# ~/.cache/workhour-agent/tokens.json（0600）。

_WH_DIR="$(cd "$(dirname "${BASH_SOURCE[0]:-${(%):-%x}}")" && pwd)"
_WH_CACHE="$HOME/.cache/workhour-agent/tokens.json"
_WH_TUNNEL_LOG="$HOME/.cache/workhour-agent/tunnel.log"
_WH_GPU="caic@172.19.3.136"
_WH_REMOTE_DIR="/home/caic/code/workhour/workhour_agent"
_WH_PROJECT_ROOT="$(cd "$_WH_DIR/../../.." && pwd)"

# 始终优先使用项目内 uv 环境；兼容 Windows Git Bash 和 Linux/macOS。
if [ -x "$_WH_PROJECT_ROOT/.venv/Scripts/python.exe" ]; then
  _WH_PYTHON="$_WH_PROJECT_ROOT/.venv/Scripts/python.exe"
elif [ -x "$_WH_PROJECT_ROOT/.venv/bin/python" ]; then
  _WH_PYTHON="$_WH_PROJECT_ROOT/.venv/bin/python"
else
  echo "  ✗ 未找到项目 uv 环境，请先在项目根目录运行 uv venv .venv"
  return 1 2>/dev/null || exit 1
fi

export BASE="${BASE:-http://localhost:8000}"

# Git for Windows 默认不附带 nc，使用 Bash 内置 /dev/tcp 检测端口。
_wh_port_open() {
  (exec 3<>"/dev/tcp/$1/$2") 2>/dev/null
}

# 三个测试身份：entity_id 用于换 token，user_id 是 sys_user.id（= 工时表 memberId）
_WH_EMP_ENTITY='0103163734221037995'; _WH_EMP_UID='d1e88d66-cc87-40c7-bbe3-2dff2d093b41'
_WH_ADM_ENTITY='020832615020860355';  _WH_ADM_UID='4cbabf4b-6ba2-4b12-aacc-15077187f47a'
_WH_SUP_ENTITY='123';                 _WH_SUP_UID='5565b9e2-1348-4c4b-b7f2-386e67a3c02b'


# ── 隧道 ──────────────────────────────────────────────────────────────────────
# 本地 9900 → 172 → 116:9900（生产 SpringBoot）。外层 ssh 不能加 -N，
# 否则内层远程命令不执行，172:9900 空着 → connection refused。
tunnel_up() {
  if _wh_port_open 127.0.0.1 9900; then
    echo "  隧道 9900 已通"
    return 0
  fi
  echo "  隧道未通，正在建立…"
  mkdir -p "$(dirname "$_WH_TUNNEL_LOG")"
  : > "$_WH_TUNNEL_LOG"
  # 清理 172 上的孤儿内层 ssh（占着 172:9900 会导致 Address already in use）
  # [s]sh 括号技巧避免 pgrep/pkill 匹配到自身命令行
  ssh -o ConnectTimeout=8 "$_WH_GPU" \
    "pkill -f '[s]sh -N.*9900:127.0.0.1:9900 useryzk'" 2>/dev/null
  # 用 ssh -f 在前台完成密码认证，再由 ssh 自行转入后台。
  # 不能用 shell 的 `&`：后台进程读取密码会被 Git Bash 挂起为 Stopped。
  ssh -f -o ExitOnForwardFailure=yes -o ServerAliveInterval=30 \
      -L 127.0.0.1:9900:127.0.0.1:9900 "$_WH_GPU" \
      "ssh -N -o ExitOnForwardFailure=yes -o ServerAliveInterval=30 -L 127.0.0.1:9900:127.0.0.1:9900 useryzk@116.205.174.57" \
      >>"$_WH_TUNNEL_LOG" 2>&1
  for _ in 1 2 3 4 5 6 7 8 9 10; do
    sleep 1
    _wh_port_open 127.0.0.1 9900 && { echo "  隧道 9900 已建立"; return 0; }
  done
  echo "  ✗ 隧道建立失败，SSH 日志："
  if [ -s "$_WH_TUNNEL_LOG" ]; then
    sed 's/^/    /' "$_WH_TUNNEL_LOG"
  else
    echo "    （日志为空；检查 172→116 的 SSH 认证与 116:9900 服务）"
  fi
  echo "  手动排查：ssh $_WH_GPU 'pgrep -af \"[s]sh -N.*9900\"'"
  return 1
}


# ── Token ────────────────────────────────────────────────────────────────────
# 解析 JWT 的 exp 声明判断是否临期，只在需要时才走 ssh 换新的。
_wh_token_valid() {
  "$_WH_PYTHON" - "$1" <<'PY'
import base64, json, sys, time
tok = sys.argv[1]
if not tok or tok.count(".") != 2:
    sys.exit(1)
try:
    p = tok.split(".")[1]
    p += "=" * (-len(p) % 4)                      # base64url 补齐 padding
    exp = json.loads(base64.urlsafe_b64decode(p))["exp"]
except Exception:
    sys.exit(1)
sys.exit(0 if exp - time.time() > 300 else 1)     # 留 5 分钟余量
PY
}

_wh_fetch_tokens() {
  echo "  正在经 172 换取 token（MCP_API_KEY 不落本地）…"
  mkdir -p "$(dirname "$_WH_CACHE")"
  local out
  out=$(ssh -o ConnectTimeout=10 "$_WH_GPU" "cd $_WH_REMOTE_DIR && set -a && . ./.env && set +a && for e in $_WH_EMP_ENTITY $_WH_ADM_ENTITY $_WH_SUP_ENTITY; do curl -s -m 20 -X POST https://gst.thsware.com/api/auth/mcp-token -H 'Content-Type: application/json' -H 'User-Agent: Mozilla/5.0' -d \"{\\\"entity_id\\\":\\\"\$e\\\",\\\"api_key\\\":\\\"\$MCP_API_KEY\\\"}\"; echo; done" 2>/dev/null | grep '^{')
  [ -z "$out" ] && { echo "  ✗ 换取失败（172 不可达 / MCP_API_KEY 缺失）"; return 1; }
  printf '%s' "$out" | "$_WH_PYTHON" -c '
import json, sys
by = {}
for line in sys.stdin:
    line = line.strip()
    if not line:
        continue
    d = json.loads(line)
    by[d["entityType"]] = {"token": d["token"], "user_id": d["userId"]}
json.dump(by, open(sys.argv[1], "w"))
' "$_WH_CACHE" || { echo "  ✗ 解析响应失败"; return 1; }
  chmod 600 "$_WH_CACHE"
  echo "  token 已缓存（${_WH_CACHE}，24h 有效）"
}

_wh_load_role() {   # $1 = employee | deptAdmin | superAdmin
  local role="$1" line
  line=$("$_WH_PYTHON" -c '
import json, sys
try:
    d = json.load(open(sys.argv[1])).get(sys.argv[2]) or {}
except Exception:
    d = {}
print(d.get("token", ""), d.get("user_id", ""))
' "$_WH_CACHE" "$role" 2>/dev/null)
  export TOKEN="${line%% *}"
  export USER_ID="${line##* }"
  export ENTITY="$role"
}

token_refresh() { _wh_fetch_tokens && _wh_load_role "${ENTITY:-employee}" && echo "  当前身份: $ENTITY ($USER_ID)"; }

as_emp() { _wh_load_role employee;   echo "→ employee  罗欢    $USER_ID"; }
as_adm() { _wh_load_role deptAdmin;  echo "→ deptAdmin 刘会超  $USER_ID"; }
as_sup() { _wh_load_role superAdmin; echo "→ superAdmin 管理员 $USER_ID"; }


# ── 发请求 ────────────────────────────────────────────────────────────────────
ask() {
  [ -z "$1" ] && { echo "用法: ask \"问题\" [session_id]"; return 1; }
  MSG="$1" SID="${2:-t1}" "$_WH_PYTHON" -c '
import json, os, urllib.request, urllib.error
body = json.dumps({
    "message": os.environ["MSG"], "session_id": os.environ["SID"],
    "user_context": {"user_id": os.environ["USER_ID"], "entity_type": os.environ["ENTITY"],
                     "auth_token": os.environ["TOKEN"]},
}).encode()
req = urllib.request.Request(os.environ["BASE"] + "/api/ai/chat", data=body,
                             headers={"Content-Type": "application/json"})
try:
    d = json.load(urllib.request.urlopen(req, timeout=180))
except urllib.error.URLError as e:
    raise SystemExit(f"✗ 请求失败: {e.reason}（服务起了吗？{os.environ['BASE']}）")
r = d.get("result") or {}
ri = r.get("route_info") or {}
# 先取值再拼——f-string 表达式内不能出现反斜杠（Py<3.12），转义引号会 SyntaxError
intent, target, tool = ri.get("intent_type"), ri.get("target"), r.get("tool_name")
print(f"[route={intent} target={target} tool={tool}]")
print(d.get("message"))'
}

asks() {
  [ -z "$1" ] && { echo "用法: asks \"问题\" [session_id]"; return 1; }
  MSG="$1" SID="${2:-t1}" "$_WH_PYTHON" -c '
import json, os
print(json.dumps({"message": os.environ["MSG"], "session_id": os.environ["SID"],
  "user_context": {"user_id": os.environ["USER_ID"], "entity_type": os.environ["ENTITY"],
                   "auth_token": os.environ["TOKEN"]}}))' \
  | curl -N -s -X POST "$BASE/api/ai/chat/stream" -H 'Content-Type: application/json' -d @-
}

probe() { (cd "$_WH_DIR" && "$_WH_PYTHON" run_probe.py "$@"); }
repl()  { (cd "$_WH_DIR" && "$_WH_PYTHON" chat_repl.py "$@"); }


# ── 状态 ──────────────────────────────────────────────────────────────────────
dev_status() {
  _wh_port_open 127.0.0.1 9900 && echo "  隧道 9900   ✅" || echo "  隧道 9900   ❌ 运行 tunnel_up"
  # 路径是 /health/ping：main.py 以 prefix="/health" 挂载，router 内又是 @get("/ping")
  # 必须看状态码——curl 拿到 404 也是退出码 0，会把"服务在但路径错"误判为健康
  local _code
  _code=$(curl -s -m 3 -o /dev/null -w '%{http_code}' "$BASE/health/ping" 2>/dev/null)
  [ "$_code" = "200" ] \
    && echo "  服务 $BASE ✅" \
    || echo "  服务 $BASE ❌ (HTTP ${_code:-无响应}) VSCode F5 或 cd fastapi-service && python main.py"
  _wh_token_valid "$TOKEN" && echo "  token       ✅ ($ENTITY $USER_ID)" \
                           || echo "  token       ❌ 运行 token_refresh"
  local _envlocal="$_WH_DIR/../../../.env.local"
  grep -q '^WRITE_DRY_RUN_DEFAULT=true' "$_envlocal" 2>/dev/null \
    && echo "  写安全阀    ✅ 开启（写工具只预览，不写生产库）" \
    || echo "  写安全阀    ⚠️  关闭 — 填报类请求会真写生产库！"
  grep -q '^CONVERSATION_LOG_ENABLED=false' "$_envlocal" 2>/dev/null \
    && echo "  审计日志    ✅ 已关（避免内网 MySQL 超时 + 污染微调数据集）" \
    || echo "  审计日志    ⚠️  开启 — 每请求会白等数秒连 192.168.0.94"
}


# ── 初始化 ────────────────────────────────────────────────────────────────────
echo "workhour 联调环境"
tunnel_up
_wh_load_role employee
if ! _wh_token_valid "$TOKEN"; then
  _wh_fetch_tokens && _wh_load_role employee
else
  echo "  token 未过期，复用缓存"
fi
echo
dev_status
echo
echo "  ask \"问题\" | asks（流式）| as_emp/as_adm/as_sup | probe | repl | dev_status"
