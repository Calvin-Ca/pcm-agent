#!/bin/bash

# AI智能助手开发环境启动脚本

echo "🚀 启动AI智能助手开发环境..."

# 检查Docker是否运行
if ! docker info > /dev/null 2>&1; then
    echo "❌ Docker未运行，请先启动Docker"
    exit 1
fi

# 检查.env文件
if [ ! -f .env ]; then
    echo "⚠️  未找到.env文件，从.env.example创建..."
    if [ -f .env.example ]; then
        cp .env.example .env
        echo "✅ 已创建.env文件，请配置LLM API密钥"
    else
        echo "❌ 未找到.env.example文件"
        exit 1
    fi
fi

# 加载环境变量
export $(cat .env | grep -v '^#' | xargs)

# 启动服务
echo "📦 启动Docker容器..."
# docker-compose up -d
docker compose -f docker-compose.yml -f docker-compose.prod.yml up -d

# 等待服务就绪
echo "⏳ 等待服务启动..."
sleep 10

# 检查服务状态
echo ""
echo "📊 服务状态："
docker-compose ps

echo ""
echo "✅ AI智能助手开发环境已启动！"
echo ""
echo "🔗 服务访问地址："
echo "  - AI Service (FastAPI):  http://localhost:8000"
echo "  - API文档 (Swagger):     http://localhost:8000/docs"
echo "  - Redis:                 localhost:6379"
echo "  - Milvus:                localhost:19530"
echo "  - Prometheus:            http://localhost:9090"
echo "  - Grafana:               http://localhost:3000 (admin/admin)"
echo ""
echo "📝 查看日志: docker-compose logs -f [service-name]"
echo "🛑 停止服务: ./stop.sh"
