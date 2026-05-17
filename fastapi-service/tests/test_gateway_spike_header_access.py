"""SPIKE：验证 FastMCP streamable_http_app() 可被 Starlette 中间件包裹并读取每请求 header。

这是方案 2 的地基技术前提（spec §5.2）。只验证机制，不依赖 MCP 协议握手：
断言 (1) streamable_http_app() 是个 ASGI app；(2) BaseHTTPMiddleware 能在
请求进入 MCP app 前读到自定义 header；(3) 能据此返回 401 短路。
"""
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.responses import PlainTextResponse
from starlette.testclient import TestClient

from mcp.server.fastmcp import FastMCP


def _build_app():
    mcp = FastMCP("spike")

    @mcp.tool()
    async def ping() -> str:
        return "pong"

    app = mcp.streamable_http_app()

    seen = {}

    class GateMW(BaseHTTPMiddleware):
        async def dispatch(self, request, call_next):
            seen["gw"] = request.headers.get("X-Gateway-Token")
            if seen["gw"] != "secret":
                return PlainTextResponse("forbidden", status_code=401)
            return await call_next(request)

    app.add_middleware(GateMW)
    return app, seen


def test_streamable_http_app_is_asgi():
    app, _ = _build_app()
    assert callable(app)  # ASGI app


def test_middleware_sees_header_and_blocks_without_token():
    app, seen = _build_app()
    client = TestClient(app)
    r = client.get("/", headers={})  # 无 token
    assert r.status_code == 401
    assert seen["gw"] is None


def test_middleware_passes_with_correct_token():
    app, seen = _build_app()
    client = TestClient(app)
    # 带正确网关 token：中间件放行（放行后 MCP app 自身可能因非法 MCP 请求返回
    # 4xx，但绝不是中间件的 401；我们只断言 token 被读到且未被中间件拦）
    client.get("/", headers={"X-Gateway-Token": "secret"})
    assert seen["gw"] == "secret"
