#!/usr/bin/env bash
set -e

TOKEN="$1"
if [ -z "$TOKEN" ]; then
    echo "Usage: $0 <JWT_TOKEN>"
    exit 1
fi

# 容器只绑定了 127.0.0.1，必须用 localhost
BASE="http://localhost:8000"

# 从 token 解析出的用户信息（159****0206 / employee）
USER_CONTEXT='{"user_id":"159****0206","entity_type":"employee","department_id":"fb17e76c-dc90-4c38-8ffd-51fdfe528c3b","auth_token":"'$TOKEN'"}'

QUERIES=(
    "统计部门上月加班时长"
    "查一下李四的工时"
    "我本周工时"
    "工时 Top 5 排名"
    "各部门工时对比"
)

for q in "${QUERIES[@]}"; do
    echo "=== $q ==="
    RESP=$(curl -s -X POST "$BASE/api/ai/chat" \
      -H "Authorization: Bearer $TOKEN" \
      -H "Content-Type: application/json" \
      -d "{\"message\":\"$q\",\"stream\":false,\"user_context\":$USER_CONTEXT}")
    echo "$RESP" | python3 -m json.tool 2>/dev/null || echo "$RESP"
    echo ""
    echo "---"
    sleep 3
done
