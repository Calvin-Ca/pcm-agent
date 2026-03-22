"""
性能测试 - 34.1 响应时间测试

测试目标：
- 健康检查响应时间 < 1秒
- AI聊天首次响应时间（TTFB）< 3秒
- 端到端响应时间 < 60秒

运行方式：
    cd ai-service/fastapi-service
    ../.venv/Scripts/pytest tests/performance/test_response_time.py -v -s
"""

import asyncio
import time
import json
import httpx
import pytest

BASE_URL = "http://localhost:8000"

TEST_HEADERS = {
    "X-User-ID": "test-user-001",
    "X-Entity-Type": "employee",
    "X-Department-ID": "dept-001",
    "Content-Type": "application/json",
}

CHAT_ENDPOINT = f"{BASE_URL}/api/ai/chat/stream"


def make_payload(message: str, session_id: str) -> dict:
    return {"message": message, "session_id": session_id, "stream": True}


class TestResponseTime:
    """响应时间测试"""

    def test_health_check_response_time(self):
        """健康检查响应时间应 < 1秒"""
        with httpx.Client() as client:
            start = time.perf_counter()
            response = client.get(f"{BASE_URL}/health/health")
            elapsed = time.perf_counter() - start

        print(f"\n[健康检查] 响应时间: {elapsed:.3f}s | 状态码: {response.status_code}")
        assert response.status_code == 200
        assert elapsed < 1.0, f"健康检查响应时间 {elapsed:.3f}s 超过 1秒"

    def test_ping_response_time(self):
        """Ping 接口响应时间应 < 0.5秒"""
        with httpx.Client() as client:
            start = time.perf_counter()
            response = client.get(f"{BASE_URL}/health/ping")
            elapsed = time.perf_counter() - start

        print(f"\n[Ping] 响应时间: {elapsed:.3f}s | 状态码: {response.status_code}")
        assert response.status_code == 200
        assert elapsed < 0.5, f"Ping 响应时间 {elapsed:.3f}s 超过 0.5秒"

    @pytest.mark.asyncio
    async def test_chat_time_to_first_byte(self):
        """AI聊天首次响应时间（TTFB）应 < 3秒"""
        payload = make_payload("hello", "perf-test-ttfb")

        async with httpx.AsyncClient(timeout=30.0) as client:
            start = time.perf_counter()
            first_byte_time = None

            async with client.stream(
                "POST", CHAT_ENDPOINT, headers=TEST_HEADERS, json=payload
            ) as response:
                assert response.status_code == 200, f"状态码: {response.status_code}"
                async for chunk in response.aiter_bytes():
                    if chunk and first_byte_time is None:
                        first_byte_time = time.perf_counter() - start
                        break

        print(f"\n[聊天TTFB] 首次响应时间: {first_byte_time:.3f}s")
        assert first_byte_time is not None, "未收到任何响应数据"
        assert first_byte_time < 3.0, f"首次响应时间 {first_byte_time:.3f}s 超过 3秒"

    @pytest.mark.asyncio
    async def test_chat_end_to_end_response_time(self):
        """AI聊天端到端响应时间应 < 60秒"""
        payload = make_payload("how are you", "perf-test-e2e")

        async with httpx.AsyncClient(timeout=120.0) as client:
            start = time.perf_counter()
            events = []

            async with client.stream(
                "POST", CHAT_ENDPOINT, headers=TEST_HEADERS, json=payload
            ) as response:
                assert response.status_code == 200, f"状态码: {response.status_code}"
                async for line in response.aiter_lines():
                    if line.startswith("data: "):
                        try:
                            data = json.loads(line[6:])
                            events.append(data)
                            elapsed = time.perf_counter() - start
                            event_type = data.get("chunk") or data.get("message", "")
                            safe = ascii(str(event_type)[:60])
                            print(f"  [{elapsed:.2f}s] {safe}")
                        except json.JSONDecodeError:
                            pass

            total_time = time.perf_counter() - start

        print(f"\n[端到端] 总时间: {total_time:.3f}s | 事件数: {len(events)}")
        assert len(events) > 0, "未收到任何SSE事件"
        assert total_time < 60.0, f"端到端响应时间 {total_time:.3f}s 超过 60秒"

    @pytest.mark.asyncio
    async def test_multiple_requests_average_time(self):
        """连续5次请求，平均首次响应时间应 < 3秒"""
        ttfb_list = []
        n = 5

        async with httpx.AsyncClient(timeout=30.0) as client:
            for i in range(n):
                payload = make_payload("hello", f"perf-avg-{i}")
                start = time.perf_counter()
                first_byte_time = None

                async with client.stream(
                    "POST", CHAT_ENDPOINT, headers=TEST_HEADERS, json=payload
                ) as response:
                    if response.status_code == 200:
                        async for chunk in response.aiter_bytes():
                            if chunk and first_byte_time is None:
                                first_byte_time = time.perf_counter() - start
                                break

                if first_byte_time:
                    ttfb_list.append(first_byte_time)
                    print(f"  第{i+1}次请求 TTFB: {first_byte_time:.3f}s")

                await asyncio.sleep(0.5)

        avg_ttfb = sum(ttfb_list) / len(ttfb_list)
        max_ttfb = max(ttfb_list)
        min_ttfb = min(ttfb_list)

        print(f"\n[平均响应] 平均: {avg_ttfb:.3f}s | 最大: {max_ttfb:.3f}s | 最小: {min_ttfb:.3f}s")
        assert avg_ttfb < 3.0, f"平均首次响应时间 {avg_ttfb:.3f}s 超过 3秒"


if __name__ == "__main__":
    pytest.main([__file__, "-v", "-s"])
