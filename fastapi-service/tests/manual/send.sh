#!/usr/bin/env bash
# 给本地调试中的 agent 发单条请求，方便对着断点看链路。
# 非流式 /api/ai/chat（stream:false）——单个响应，断点停住不会断流。
#
# 用法：
#   bash send.sh 1                 # 闲聊（无需隧道/token）
#   bash send.sh 2                 # 知识问答（走 RAG / Milvus）
#   TOKEN=<jwt> bash send.sh 3     # 工具调用：查工时（需先起 9900 隧道 + token）
#   bash send.sh "自定义问题"       # 直接发任意一句（当闲聊/自动路由）
#
# 取 TOKEN（经隧道绕 WAF，需 9900 通）：
#   TOKEN=$(curl -s -X POST http://127.0.0.1:9900/api/authenticate \
#     -H 'Content-Type: application/json' \
#     -d '{"username":"159****0206","password":"<密文>","rememberMe":false}' \
#     | python3 -c "import sys,json;print(json.load(sys.stdin)['data']['token'])")
set -euo pipefail

BASE="${BASE:-http://localhost:8000}"
USER_ID="${USER_ID:-debug-user}"
ENTITY="${ENTITY:-employee}"
TOKEN="${TOKEN:-}"

case "${1:-1}" in
  1) MSG="你好，你能帮我做什么？" ;;
  2) MSG="年假有几天？怎么请年假？" ;;
  3)
    MSG="我这周填了多少工时？"
    USER_ID="${USER_ID_3:-$USER_ID}"   # 可用 USER_ID_3 覆盖
    if [ -z "$TOKEN" ]; then
      echo "⚠️  请求 3 需要 TOKEN，且 9900 隧道要通。示例：TOKEN=<jwt> bash send.sh 3" >&2
      exit 1
    fi
    ;;
  *) MSG="$1" ;;   # 传的不是 1/2/3 就当自定义问题
esac

# 用 python 拼 JSON，避免中文/引号转义问题
BODY=$(MSG="$MSG" USER_ID="$USER_ID" ENTITY="$ENTITY" TOKEN="$TOKEN" python3 -c '
import json, os
uc = {"user_id": os.environ["USER_ID"], "entity_type": os.environ["ENTITY"]}
if os.environ.get("TOKEN"):
    uc["auth_token"] = os.environ["TOKEN"]
print(json.dumps({"message": os.environ["MSG"], "stream": False, "user_context": uc}, ensure_ascii=False))
')

echo "▶ POST $BASE/api/ai/chat"
echo "  message: $MSG"
echo "  （断点停住时会一直等，放行后返回）"
echo "---"
RESP=$(curl -s -N -X POST "$BASE/api/ai/chat" -H "Content-Type: application/json" -d "$BODY" || true)
if [ -z "$RESP" ]; then
  echo "(无响应 / 服务未运行 / 仍停在断点)"
else
  echo "$RESP" | RESP="$RESP" python3 -c 'import sys,json,os
raw=os.environ["RESP"]
try:
    d=json.loads(raw)
    print("命中工具:", (d.get("result") or {}).get("tool_name","-"))
    print("回复:", d.get("message",""))
except Exception:
    print(raw)
'
fi
echo
