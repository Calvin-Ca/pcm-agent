"""一次性 spike：实打 /api/internal/auth/mcp-token，原样落盘完整响应 JSON。
确认 SpringBoot 经 ai-service 返回的角色字段真实键名。失败即停，不掩盖。"""
import json
import os
import sys
from pathlib import Path

import httpx

AI_SERVICE_URL = os.getenv("AI_SERVICE_URL", "http://localhost:8000")
ENTITY_ID = os.getenv("MCP_ENTITY_ID", "")
API_KEY = os.getenv("MCP_API_KEY", "")

if not ENTITY_ID or not API_KEY:
    print("STOP: 缺 MCP_ENTITY_ID / MCP_API_KEY env，无法 spike", file=sys.stderr)
    sys.exit(2)

out_path = Path(__file__).parent.parent / "mcp_servers" / "logs" / "spike_mcp_token_response.json"
out_path.parent.mkdir(parents=True, exist_ok=True)

try:
    resp = httpx.post(
        f"{AI_SERVICE_URL}/api/internal/auth/mcp-token",
        json={"entity_id": ENTITY_ID, "api_key": API_KEY},
        timeout=10.0,
    )
except Exception as e:
    print(f"STOP: 请求失败 {e!r}", file=sys.stderr)
    sys.exit(3)

print(f"HTTP {resp.status_code}")
raw = resp.text
out_path.write_text(raw, encoding="utf-8")
print(f"原始响应已落盘: {out_path}")

if resp.status_code != 200:
    print(f"STOP: 非 200，body={raw[:500]}", file=sys.stderr)
    sys.exit(4)

data = resp.json()
keys = sorted(data.keys())
print(f"响应顶层键: {keys}")
role_candidates = [k for k in keys if k.lower() in
                   ("entitytype", "role", "usertype", "userrole", "entity_type")]
print(f"疑似角色字段: {role_candidates}")
print(f"token 是否非空: {bool(data.get('token'))}")
print(f"userId 是否非空: {bool(data.get('userId'))}")
