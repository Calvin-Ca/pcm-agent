#!/bin/bash
set -e

TOKEN="${TOKEN:?TOKEN environment variable is required}"
UA="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
BASE="https://gst.thsware.com/api/ai/chat"

for q in \
  "统计部门上月加班时长" \
  "查一下李四的工时" \
  "我本周工时" \
  "工时 Top 5 排名" \
  "各部门工时对比"; do
  echo "=== $q ==="
  curl -Ns --max-time 60 \
    -H "Authorization: Bearer $TOKEN" \
    -H "Content-Type: application/json" \
    -H "User-Agent: $UA" \
    -H "Origin: https://gst.thsware.com" \
    -H "Referer: https://gst.thsware.com/" \
    -d "{\"message\":\"$q\",\"session_id\":\"e3db51a-$(date +%s)\",\"stream\":true}" \
    "$BASE"
  echo; sleep 3
done
