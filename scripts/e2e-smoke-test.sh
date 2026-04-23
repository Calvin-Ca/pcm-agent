#!/bin/bash
set -e

TOKEN="${TOKEN:-eyJhbGciOiJIUzUxMiJ9.eyJzdWIiOiIxNTk4ODU4OTAyMDYiLCJhdXRoIjoiUk9MRV9BRE1JTixST0xFX1VTRVIiLCJleHAiOjE3NDU0NjU5MTJ9.3yN0NMWQn2NkvKAOxycU8aWKXCbPQ8XcK-KX8L8r3RDK1M4vI1QJdNMB8dO8r}"
BASE="http://127.0.0.1:9901"

run_test() {
    local name="$1"
    local msg="$2"
    local sid="$3"
    local timeout="${4:-60}"

    echo "=== $name ==="
    curl -Ns --max-time "$timeout" \
        -H "Authorization: Bearer $TOKEN" \
        -H "Content-Type: application/json" \
        -d "{\"message\":\"$msg\",\"session_id\":\"$sid\",\"stream\":true}" \
        "$BASE/api/ai/chat" > "/tmp/e2e-$sid.log" 2>&1

    # 检查结果
    if grep -q "event: tool_call" "/tmp/e2e-$sid.log" 2>/dev/null; then
        echo "  -> tool_call 触发"
        grep "tool_name" "/tmp/e2e-$sid.log" | head -1 || true
    elif grep -q "event: response" "/tmp/e2e-$sid.log" 2>/dev/null; then
        echo "  -> response 返回"
    elif grep -q "event: error" "/tmp/e2e-$sid.log" 2>/dev/null; then
        echo "  -> error 事件"
    else
        echo "  -> 未知结果，查看日志："
        tail -5 "/tmp/e2e-$sid.log"
    fi
    echo ""
}

echo "T1: 工时查询"
run_test "T1-工时查询" "查我本周工时" "t1"

echo "T2: 项目查询"
run_test "T2-项目查询" "查一下我参与的项目" "t2"

echo "T3: 统计分析"
run_test "T3-统计分析" "统计我上周的工时汇总" "t3"

echo "T4: 周报生成"
run_test "T4-周报生成" "帮我生成本周周报" "t4" 90

echo "T7: SQL查询"
run_test "T7-SQL查询" "统计所有员工上周的总工时" "t7"

echo "T8: 通用对话"
run_test "T8-通用对话" "你好，请介绍一下你自己" "t8"

echo "=== 全部测试完成 ==="
