#!/bin/bash
# E2E regression — 从 116 本机发,--resolve 绕 CDN 打 nginx
# 用法: TOKEN=<jwt> bash e2e-regression-0423.sh
set -u

TOKEN="${TOKEN:?need TOKEN env}"
BASE="https://gst.thsware.com"
OUT=/tmp/e2e-0423
mkdir -p "$OUT"

run() {
    local name=$1 msg=$2 sid=$3 timeout=${4:-60}
    local log="$OUT/$name.log"
    echo "=== $name ($sid) ==="
    local start=$(date +%s)
    curl -Nsk --max-time "$timeout" \
        --resolve gst.thsware.com:443:127.0.0.1 \
        -w "\n--HTTP:%{http_code}--\n" \
        -H "Authorization: Bearer $TOKEN" \
        -H "Content-Type: application/json" \
        -d "{\"message\":\"$msg\",\"session_id\":\"$sid\",\"stream\":true}" \
        "$BASE/api/ai/chat" > "$log" 2>&1
    local rc=$?
    local end=$(date +%s)
    local http=$(grep -oE 'HTTP:[0-9]+' "$log" | tail -1 | tr -d 'HTTP:')
    local bytes=$(wc -c < "$log")
    echo "  rc=$rc http=$http bytes=$bytes elapsed=$((end-start))s"
    echo ""
}

run T1-query_timesheet    "查我本周工时"                            e2e-t1-0423
run T2-query_project      "查一下我参与的项目"                      e2e-t2-0423
run T3-compute_statistics "统计我上周的工时汇总"                    e2e-t3-0423
run T4-weekly_report      "帮我生成本周周报"                        e2e-t4-0423 90
run T5-save_workhour      "帮我填报今天在测试项目工作了2小时"       e2e-t5-0423
run T6-rag                "工时填报的截止时间是什么时候"            e2e-t6-0423
run T7-sql_query          "统计所有员工上周的总工时"                e2e-t7-0423
run T8-chat               "你好，请介绍一下你自己"                  e2e-t8-0423 40
run T9-zh_stable          "查我本周工时"                            e2e-t9-0423

echo "=== done, logs in $OUT ==="
ls -la "$OUT"
