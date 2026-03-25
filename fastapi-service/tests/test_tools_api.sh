# 测试 Phase 8 工具的 API 调用示例

## 1. 测试周报生成

curl -X POST http://localhost:8000/api/ai/chat \
  -H "Content-Type: application/json" \
  -H "Authorization: Bearer YOUR_TOKEN" \
  -d '{
    "message": "生成本周周报",
    "session_id": "test-session-001"
  }'

## 2. 测试工时填报（通过自然语言）

curl -X POST http://localhost:8000/api/ai/chat \
  -H "Content-Type: application/json" \
  -H "Authorization: Bearer YOUR_TOKEN" \
  -d '{
    "message": "填报工时，项目ID是123，日期今天，8小时，完成了登录功能开发",
    "session_id": "test-session-002"
  }'

## 3. 直接调用工具（需要 tool_registry）

# 在 Python 中:
# from app.tools.generate_weekly_report import generate_weekly_report_handler
# result = await generate_weekly_report_handler(user_id="1", week="thisWeek")
