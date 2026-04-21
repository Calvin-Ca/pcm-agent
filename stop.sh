#!/bin/bash

# AI智能助手开发环境停止脚本

echo "🛑 停止AI智能助手开发环境..."

# docker-compose down
docker compose -f docker-compose.yml -f docker-compose.prod.yml down

echo "✅ 服务已停止"
echo ""
echo "💡 提示："
echo "  - 重新启动: ./start.sh"
echo "  - 完全清理（包括数据卷）: docker-compose down -v"
