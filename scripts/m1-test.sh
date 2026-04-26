#!/bin/bash
# M1 — 工时填报全链路 e2e 测试脚本
# 执行方式：TOKEN=<jwt> bash /tmp/m1-test.sh

TOKEN="${TOKEN:?需要 TOKEN}"
UA="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
BASE="https://gst.thsware.com/api/ai/chat"

run() {
  local msg="$1" sid="$2"
  echo "=== $sid: $msg ==="
  curl -Ns --max-time 60 \
    -H "Authorization: Bearer $TOKEN" \
    -H "Content-Type: application/json" \
    -H "User-Agent: $UA" \
    -H "Origin: https://gst.thsware.com" \
    -H "Referer: https://gst.thsware.com/" \
    -d "{\"message\":\"$msg\",\"session_id\":\"$sid\",\"stream\":true}" \
    "$BASE"
  echo
  sleep 3
}

run "帮我填今天的工时，项目预管理系统，3 小时，[E2E TEST] 写了单元测试" "m1-tc01-$(date +%s)"
run "今天 8h，预管理系统，[E2E TEST] 完成回弹检测的后端开发（含单元测试&集成测试）" "m1-tc02-$(date +%s)"
run "帮我补一条昨天的工时，预管理系统，4h，[E2E TEST] 需求评审" "m1-tc03-$(date +%s)"
run "今天 5h，不存在的项目xyz，[E2E TEST] 做了点东西" "m1-tc04-$(date +%s)"
run "今天填 30 小时，预管理系统，[E2E TEST] 加班" "m1-tc05-$(date +%s)"
