#!/usr/bin/env python3
"""交互式终端聊天客户端 —— 配合 VSCode F5 断点调试用。

跟 send.sh 的区别：这个脚本在同一个 session_id 下连续对话（可测多轮指代消解），
send.sh 每次都是独立单发请求。两者都用非流式 /api/ai/chat，断点暂停期间请求
会一直挂着不报超时，继续执行后才会返回。

用法：
    python3 chat_repl.py                              # 闲聊/知识问答，无需 token
    TOKEN=<jwt> python3 chat_repl.py                   # 工具调用（需 9900 隧道通）
    TOKEN=<jwt> USER_ID=<userId> ENTITY=deptAdmin python3 chat_repl.py

交互命令：
    /new       开始新会话（换一个 session_id，清空上下文）
    /session   打印当前 session_id
    exit / quit / :q   退出

拿 TOKEN（经隧道绕开华为云 WAF）：
    curl -s -X POST http://127.0.0.1:9900/api/authenticate \\
      -H 'Content-Type: application/json' \\
      -d '{"username":"...","password":"<密文>","rememberMe":false}'
  取返回 JSON 的 data.token。
"""
import json
import os
import subprocess
import sys
import urllib.request
import urllib.error
import uuid


def _default_base() -> str:
    """默认打局域网 IP 而非 localhost。

    VS Code 的端口自动转发有时会在 127.0.0.1:8000 上起一个自己的监听进程，
    抢在 uvicorn（监听 0.0.0.0:8000）前面接管 localhost 的连接，导致请求
    卡住没反应。改用本机局域网 IP 可以绕开这个 127.0.0.1 专属监听者。

    不写死具体 IP（换网络会变），改为每次启动时用 `ipconfig getifaddr` 探测
    真实网卡地址（en0/en1）。注意：不能用"连 UDP 到 8.8.8.8 看本地地址"这种
    通用技巧——如果本机开着 VPN/代理（Surge/ClashX 等），默认路由会被接管到
    一个 198.18.0.0/15 之类的虚拟网卡，那个地址连不回本机服务。
    """
    for iface in ("en0", "en1"):
        try:
            ip = subprocess.run(
                ["ipconfig", "getifaddr", iface],
                capture_output=True, text=True, timeout=2,
            ).stdout.strip()
            if ip:
                return f"http://{ip}:8000"
        except (OSError, subprocess.SubprocessError):
            pass
    return "http://localhost:8000"


BASE = os.environ.get("BASE") or _default_base()
USER_ID = os.environ.get("USER_ID", "debug-user")
ENTITY = os.environ.get("ENTITY", "employee")
TOKEN = os.environ.get("TOKEN", "")

C = {"dim": "\033[2m", "b": "\033[1m", "cy": "\033[36m", "yl": "\033[33m", "rd": "\033[31m", "0": "\033[0m"}


def new_session_id() -> str:
    return str(uuid.uuid4())


def post(message: str, session_id: str) -> dict:
    user_context = {"user_id": USER_ID, "entity_type": ENTITY}
    if TOKEN:
        user_context["auth_token"] = TOKEN
    body = json.dumps({
        "message": message,
        "stream": False,
        "session_id": session_id,
        "user_context": user_context,
    }, ensure_ascii=False).encode()
    req = urllib.request.Request(
        BASE + "/api/ai/chat", data=body,
        headers={"Content-Type": "application/json"}, method="POST",
    )
    # 不设超时：断点暂停期间请求应该一直挂着等，而不是被客户端超时打断
    with urllib.request.urlopen(req, timeout=None) as r:
        return json.loads(r.read())


def main():
    session_id = new_session_id()
    print(f"{C['b']}交互式聊天调试客户端{C['0']}")
    print(f"  {C['dim']}BASE={BASE}  USER_ID={USER_ID}  ENTITY={ENTITY}  TOKEN={'已设置' if TOKEN else '未设置'}{C['0']}")
    print(f"  {C['dim']}session_id={session_id}{C['0']}")
    print(f"  {C['dim']}/new 开新会话，/session 查看当前会话，exit 退出{C['0']}")
    print(f"  {C['dim']}断点停住时这里会一直卡着，正常现象，VSCode 里继续执行后才会打印回复{C['0']}\n")

    while True:
        try:
            message = input(f"{C['cy']}你>{C['0']} ").strip()
        except (EOFError, KeyboardInterrupt):
            print("\n退出。")
            break

        if not message:
            continue
        if message in ("exit", "quit", ":q"):
            print("退出。")
            break
        if message == "/new":
            session_id = new_session_id()
            print(f"  {C['dim']}已切换新会话 session_id={session_id}{C['0']}")
            continue
        if message == "/session":
            print(f"  {C['dim']}当前 session_id={session_id}{C['0']}")
            continue

        try:
            resp = post(message, session_id)
        except KeyboardInterrupt:
            print(f"\n  {C['yl']}已中断当前请求（可能正停在断点上），继续下一轮{C['0']}")
            continue
        except urllib.error.URLError as e:
            print(f"  {C['rd']}请求失败: {e}（服务是否在跑？F5 是否已启动？）{C['0']}")
            continue
        except Exception as e:  # noqa: BLE001
            print(f"  {C['rd']}请求异常: {e}{C['0']}")
            continue

        result = resp.get("result") or {}
        tool = result.get("tool_name")
        text = resp.get("message") or ""
        if tool:
            print(f"  {C['dim']}命中工具: {tool}{C['0']}")
        print(f"{C['b']}助手>{C['0']} {text}\n")


if __name__ == "__main__":
    main()
