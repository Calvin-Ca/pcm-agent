#!/usr/bin/env python3
"""逐条把「只读」探针请求打给本地 agent，打印命中工具 + 回复，用于快速摸清全链路。

纯标准库，无需 jq。写操作(safety=write)默认跳过，避免误写生产库。

用法：
    TOKEN=<jwt> USER_ID=<userId> python3 run_probe.py
    TOKEN=... USER_ID=... ENTITY=deptAdmin python3 run_probe.py      # 换角色
    TOKEN=... USER_ID=... CATEGORY=knowledge_qa python3 run_probe.py # 只跑某类
    TOKEN=... USER_ID=... ONLY=TS-01,KB-03 python3 run_probe.py      # 只跑指定 id
    INCLUDE_WRITE=1 ...                                              # 危险：连写操作一起跑（仅本地SpringBoot路B）

拿 TOKEN（经隧道，绕开华为云 WAF）：
    curl -s -X POST http://127.0.0.1:9900/api/authenticate \\
      -H 'Content-Type: application/json' \\
      -d '{"username":"...","password":"<密文>","rememberMe":false}'
  取返回 JSON 的 data.token。
"""
import json
import os
import sys
import time
import urllib.request
from pathlib import Path

BASE = os.environ.get("BASE", "http://localhost:8000")
ENTITY = os.environ.get("ENTITY", "employee")
CATEGORY = os.environ.get("CATEGORY")
ONLY = set(filter(None, os.environ.get("ONLY", "").split(",")))
INCLUDE_WRITE = os.environ.get("INCLUDE_WRITE") == "1"
DATA = Path(__file__).parent / "agent_probe_requests.jsonl"

USER_ID = os.environ.get("USER_ID")
TOKEN = os.environ.get("TOKEN")
if not USER_ID or not TOKEN:
    sys.exit("需要环境变量 USER_ID 和 TOKEN")

C = {"dim": "\033[2m", "b": "\033[1m", "cy": "\033[36m", "yl": "\033[33m", "0": "\033[0m"}


def post(query: str) -> dict:
    body = json.dumps({
        "message": query, "stream": False,
        "user_context": {"user_id": USER_ID, "entity_type": ENTITY, "auth_token": TOKEN},
    }).encode()
    req = urllib.request.Request(BASE + "/api/ai/chat", data=body,
                                 headers={"Content-Type": "application/json"}, method="POST")
    with urllib.request.urlopen(req, timeout=60) as r:
        return json.loads(r.read())


def main():
    for raw in DATA.read_text().splitlines():
        raw = raw.strip()
        if not raw:
            continue
        d = json.loads(raw)
        if CATEGORY and d["category"] != CATEGORY:
            continue
        if ONLY and d["id"] not in ONLY:
            continue
        if d["safety"] == "write" and not INCLUDE_WRITE:
            print(f'{C["dim"]}[SKIP-WRITE] {d["id"]:<9} {d["query"]}{C["0"]}')
            continue

        print(f'\n{C["b"]}▶ {d["id"]:<9}[{d["category"]}]{C["0"]} {d["query"]}')
        print(f'  {C["dim"]}期望: {d["expect_route"]}/{d["expect_tool"]} | {d["note"]}{C["0"]}')
        try:
            resp = post(d["query"])
        except Exception as e:  # noqa: BLE001
            print(f'  {C["yl"]}请求失败: {e}{C["0"]}')
            continue
        result = resp.get("result") or {}
        tool = result.get("tool_name", "-")
        text = resp.get("message") or ""
        print(f'  {C["cy"]}命中工具: {tool}{C["0"]}')
        print("  " + text[:1200].replace("\n", "\n  "))
        time.sleep(2)


if __name__ == "__main__":
    main()
