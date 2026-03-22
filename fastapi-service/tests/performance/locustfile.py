"""
压测文件 - 34.2 并发性能测试

使用 Locust 进行并发压测，验证系统在高并发下的稳定性。

安装依赖：
    pip install locust

运行方式（Web UI，推荐）：
    cd ai-service/fastapi-service
    locust -f tests/performance/locustfile.py --host=http://localhost:8000
    然后打开 http://localhost:8089，设置并发用户数后点 Start

运行方式（命令行，无UI）：
    # 50个并发用户，每秒新增5个，运行60秒
    locust -f tests/performance/locustfile.py --host=http://localhost:8000 \
        --headless -u 50 -r 5 --run-time 60s --html=report.html

性能目标：
    - 健康检查：响应时间 < 500ms，错误率 < 1%
    - AI聊天首次响应：< 3秒
    - 工具调用：< 5秒
    - 50并发用户下系统稳定运行
"""

import json
import time
import random
from locust import HttpUser, task, between, events
from locust.runners import MasterRunner


# 测试消息集合
CHAT_MESSAGES = [
    "你好",
    "查询我本周工时",
    "工时填报规则是什么？",
    "帮我统计本月总工时",
    "最近项目的工时情况怎么样？",
    "我今天填了多少工时？",
]

# 模拟用户列表
TEST_USERS = [
    {"user_id": f"test-user-{i:03d}", "entity_type": "employee", "dept": f"dept-{i % 5}"}
    for i in range(1, 21)
]


class AIAssistantUser(HttpUser):
    """
    模拟 AI 助手用户行为
    每个用户随机间隔 1~3 秒发送请求
    """
    wait_time = between(1, 3)

    def on_start(self):
        """用户开始时随机分配身份"""
        self.user_info = random.choice(TEST_USERS)
        self.session_id = f"load-test-{self.user_info['user_id']}-{int(time.time())}"
        self.headers = {
            "X-User-ID": self.user_info["user_id"],
            "X-Entity-Type": self.user_info["entity_type"],
            "X-Department-ID": self.user_info["dept"],
            "Content-Type": "application/json",
        }

    @task(1)
    def health_check(self):
        """健康检查（低权重，仅用于基线监控）"""
        with self.client.get("/health/health", catch_response=True) as response:
            if response.status_code == 200:
                response.success()
            else:
                response.failure(f"健康检查失败: {response.status_code}")

    @task(5)
    def chat_simple(self):
        """简单对话（高权重，主要测试场景）"""
        message = random.choice(CHAT_MESSAGES)
        payload = {
            "message": message,
            "session_id": self.session_id,
            "stream": True,
        }

        start_time = time.perf_counter()
        first_byte_received = False

        with self.client.post(
            "/api/ai/chat/stream",
            headers=self.headers,
            json=payload,
            stream=True,
            catch_response=True,
            timeout=30,
            name="/api/ai/chat/stream [chat]",
        ) as response:
            if response.status_code != 200:
                response.failure(f"聊天请求失败: {response.status_code}")
                return

            # 读取流式响应，统计首字节时间
            for chunk in response.iter_content(chunk_size=None):
                if chunk and not first_byte_received:
                    ttfb = time.perf_counter() - start_time
                    first_byte_received = True
                    # 记录自定义指标
                    events.request.fire(
                        request_type="SSE_TTFB",
                        name="chat首次响应时间",
                        response_time=ttfb * 1000,  # 转为毫秒
                        response_length=len(chunk),
                        exception=None,
                        context={},
                    )

            if first_byte_received:
                response.success()
            else:
                response.failure("未收到任何响应数据")

    @task(2)
    def chat_workhour_query(self):
        """工时查询（中等权重）"""
        payload = {
            "message": "查询我本周工时",
            "session_id": f"{self.session_id}-wh",
            "stream": True,
        }

        with self.client.post(
            "/api/ai/chat/stream",
            headers=self.headers,
            json=payload,
            stream=True,
            catch_response=True,
            timeout=30,
            name="/api/ai/chat/stream [workhour]",
        ) as response:
            if response.status_code != 200:
                response.failure(f"工时查询失败: {response.status_code}")
                return

            received = False
            for chunk in response.iter_content(chunk_size=None):
                if chunk:
                    received = True

            if received:
                response.success()
            else:
                response.failure("未收到工时查询响应")

    @task(1)
    def chat_knowledge(self):
        """知识库问答（低权重）"""
        payload = {
            "message": "工时填报规则是什么？",
            "session_id": f"{self.session_id}-rag",
            "stream": True,
        }

        with self.client.post(
            "/api/ai/chat/stream",
            headers=self.headers,
            json=payload,
            stream=True,
            catch_response=True,
            timeout=30,
            name="/api/ai/chat/stream [knowledge]",
        ) as response:
            if response.status_code != 200:
                response.failure(f"知识库问答失败: {response.status_code}")
                return

            received = False
            for chunk in response.iter_content(chunk_size=None):
                if chunk:
                    received = True

            if received:
                response.success()
            else:
                response.failure("未收到知识库问答响应")


class LightUser(HttpUser):
    """
    轻量级用户，只做健康检查
    用于模拟监控系统的探活请求
    """
    wait_time = between(5, 10)
    weight = 1  # 低权重，少量实例

    @task
    def ping(self):
        with self.client.get("/health/ping", catch_response=True) as response:
            if response.status_code == 200:
                response.success()
            else:
                response.failure(f"Ping失败: {response.status_code}")


@events.quitting.add_listener
def on_quitting(environment, **kwargs):
    """压测结束时输出汇总报告"""
    stats = environment.runner.stats
    print("\n" + "=" * 60)
    print("压测结果汇总")
    print("=" * 60)
    for name, entry in stats.entries.items():
        print(f"\n接口: {name[1]}")
        print(f"  总请求数:    {entry.num_requests}")
        print(f"  失败请求数:  {entry.num_failures}")
        print(f"  错误率:      {entry.fail_ratio * 100:.1f}%")
        print(f"  平均响应时间: {entry.avg_response_time:.1f}ms")
        print(f"  最小响应时间: {entry.min_response_time:.1f}ms")
        print(f"  最大响应时间: {entry.max_response_time:.1f}ms")
        print(f"  P50:         {entry.get_response_time_percentile(0.5):.1f}ms")
        print(f"  P90:         {entry.get_response_time_percentile(0.9):.1f}ms")
        print(f"  P99:         {entry.get_response_time_percentile(0.99):.1f}ms")
    print("=" * 60)
