#!/bin/bash
set -e

TOKEN="eyJhbGciOiJIUzUxMiJ9.eyJzcGVjaWFsaXN0X3R5cGUiOiIiLCJzdWIiOiIxNTkqKioqMDIwNiIsImF1dGgiOiJST0xFX0FETUlOIiwiZW50aXR5X25hbWUiOiLnvZfmrKIiLCJlbnRpdHlfaWQiOiIwMTAzMTYzNzM0MjIxMDM3OTk1IiwiYWNjb3VudF9pZCI6ImQxZTg4ZDY2LWNjODctNDBjNy1iYmUzLTJkZmYyZDA5M2I0MSIsImVudGl0eV90eXBlIjoiZW1wbG95ZWUiLCJvcmdfaWQiOiJmYjE3ZTc2Yy1kYzkwLTRjMzgtOGZmZC01MWZkZmU1MjhjM2IiLCJhY2NvdW50X25hbWUiOiIxNTkqKioqMDIwNiIsInVzZXJfcGhvbmUiOiIxNTkqKioqMDIwNiIsImV4cCI6MTc3NzA3NTMwNywiZGVwdF9pZCI6ImZiMTdlNzZjLWRjOTAtNGMzOC04ZmZkLTUxZmRmZTUyOGMzYiIsImlhdCI6MTc3Njk4ODkwN30.IXyW_0tQZCxnitLP3AvS1uWXhX6pnGfHPmdyM0xKHECrxbRMETf_bBurTH82SMY3R7CbDSckgT8y_N3NPXfh0w"
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
        -d "{\"message\":\"$msg\",\"session_id\":\"$sid\",\"stream\":true,\"user_context\":{\"user_id\":\"159****0206\",\"entity_type\":\"employee\",\"department_id\":\"fb17e76c-dc90-4c38-8ffd-51fdfe528c3b\",\"auth_token\":\"Bearer $TOKEN\"}}" \
        "$BASE/api/ai/chat" > "/tmp/e2e-$sid.log" 2>&1

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

echo "T5: 工时填报"
run_test "T5-工时填报" "帮我填报今天在测试项目工作了2小时" "t5"

echo "T6: RAG问答"
run_test "T6-RAG问答" "工时填报的截止时间是什么时候" "t6"

echo "T7: SQL查询"
run_test "T7-SQL查询" "统计所有员工上周的总工时" "t7" 90

echo "T8: 通用对话"
run_test "T8-通用对话" "你好，请介绍一下你自己" "t8"

echo "=== 全部测试完成 ==="
