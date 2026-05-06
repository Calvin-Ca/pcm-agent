# Knowledge Base MCP Server

## 1. What

这个目录把 ai-service 内部已有的 4 个知识库检索工具（`kb_outline` / `kb_keyword_search` / `kb_semantic_search` / `kb_read_section`）通过 **MCP（Model Context Protocol）** 协议开放给外部 LLM 客户端，**完全不动 ai-service 主进程的任何业务代码**。

MCP server 是独立进程，直接 `import app.services.kb_navigator` 的 4 个纯函数做薄包装，stdio transport，可被 Claude Desktop、Cursor、Windsurf 或任何支持 MCP 的客户端调用。

---

## 2. Why MCP

[MCP](https://modelcontextprotocol.io/) 是 Anthropic 于 2024 年底推出的开放协议，2025 年已被 OpenAI、Google、Microsoft 等主要 AI 平台接入。它对**企业知识库**的核心价值在于：

- **标准化**：一次封装，到处复用。同一组工具既服务 ai-service 内部的 LangGraph Agent，也服务 Claude Desktop / Cursor 等外部客户端。
- **可移植**：工具实现与客户端解耦。换 LLM 客户端不需要重写检索逻辑。
- **跨客户端复用**：Claude Desktop 看到 `kb_semantic_search`，Cursor 也能看到同名同签名的工具，简历可以讲"同一组工具协议化输出"。

---

## 3. How to use

### Claude Desktop（推荐）

配置文件路径（Windows）：`%APPDATA%\Claude\claude_desktop_config.json`

如果不存在则创建，内容如下（**路径需替换为你本地的实际绝对路径**）：

```json
{
  "mcpServers": {
    "workhour-knowledge-base": {
      "command": "E:\\huan\\工时管理系统\\trunk\\1 源代码\\1.0 系统代码\\ai-service\\fastapi-service\\.venv\\Scripts\\python.exe",
      "args": [
        "-m",
        "mcp_servers.kb_mcp_server"
      ],
      "cwd": "E:\\huan\\工时管理系统\\trunk\\1 源代码\\1.0 系统代码\\ai-service\\fastapi-service",
      "env": {
        "KB_PATH": "E:\\huan\\工时管理系统\\trunk\\1 源代码\\1.0 系统代码\\ai-service\\knowledge-base",
        "MILVUS_HOST": "172.19.3.136",
        "MILVUS_PORT": "19530",
        "CHAT_LLM_API_KEY": "EMPTY",
        "CHAT_LLM_API_BASE": "http://172.19.3.136:8099/v1",
        "PYTHONIOENCODING": "utf-8"
      }
    }
  }
}
```

配置要点：
- `command` 必须指向 `.venv/Scripts/python.exe`（禁止用全局 Python）
- `cwd` 设为 `fastapi-service/` 让 `import app.services.kb_navigator` 正确解析
- `env.KB_PATH` 覆盖为项目根目录的 `knowledge-base/`
- `env.MILVUS_HOST` 用 IP 地址（默认的容器名 `milvus` 在 Claude Desktop 进程里不可达）

保存配置后**完全退出并重启 Claude Desktop**（系统托盘右键退出，不是只关窗口）。

### Cursor

Cursor 同样支持 MCP，配置方式参考官方文档：
https://docs.cursor.com/context/model-context-protocol

在 Cursor Settings > Features > MCP > Add new MCP server 中，类型选 `command`，填入与上方 Claude Desktop 相同的 `command` + `args` + `env`。

### 调试

```powershell
$env:PYTHONIOENCODING="utf-8"
$env:KB_PATH="E:\huan\工时管理系统\trunk\1 源代码\1.0 系统代码\ai-service\knowledge-base"
$env:MILVUS_HOST="172.19.3.136"
fastapi-service/.venv/Scripts/mcp.exe dev fastapi-service/mcp_servers/kb_mcp_server.py
```

打开浏览器访问 http://localhost:5173（或终端提示的地址），手动调用每个工具测试。

---

## 4. Architecture

```
Claude Desktop / Cursor / Windsurf
            │
            │ stdio (MCP protocol)
            ▼
    ┌───────────────┐
    │ kb_mcp_server │   FastMCP + on-demand RAG init
    │   .py         │
    └───────┬───────┘
            │
    ┌───────┼───────┐
    │       │       │
    ▼       ▼       ▼       ▼
kb_navigator.get_outline      (filesystem 扫描)
kb_navigator.keyword_search   (BM25 + jieba 分词)
kb_navigator.semantic_search  (Milvus vector + bge-large 嵌入)
kb_navigator.read_section     (filesystem 精读 h2 章节)
```

- **紫线**：MCP stdio transport（JSON-RPC over stdin/stdout）
- **绿线**：直接 import `app.services.kb_navigator` 的纯函数，零业务逻辑重写
- `kb_keyword_search` / `kb_semantic_search` 首次调用时自动触发 RAG 初始化（加载 102 文档、切分 849 chunks、构建 BM25 + Milvus 索引，约 15~20 秒），之后所有搜索调用秒回
- `kb_outline` / `kb_read_section` 不依赖 RAG，启动即可用

---

## 5. Limitations

- **只读**：v1 仅暴露查询类工具，不提供写入/修改知识库的接口。
- **stdio only**：当前仅支持 stdio transport，未暴露 HTTP/SSE 端点。
- **无认证**：任何能拿到 `KB_PATH` 和 Milvus 连接信息的 MCP 客户端都能查全库。不要在生产环境将此 MCP server 暴露给非可信客户端——认证与权限控制是 v2 的规划事项。
- **首次搜索调用延迟 15~20 秒**：`kb_keyword_search` / `kb_semantic_search` 首次调用时会触发 RAG 初始化（BM25 + Milvus 索引，102 文件 / 849 chunks）。初始化完成后，后续搜索调用秒回。`kb_outline` / `kb_read_section` 不依赖 RAG，始终立即可用。
  - **v2 路线**：把 RAG 索引预序列化到磁盘（BM25 pickle + Milvus collection 持久化），server 启动时直接加载，消除首次搜索延迟。
- **环境依赖**：需要能访问内网 `172.19.3.136:8097`（vLLM embedding）和 `:19530`（Milvus）。如果网络不通，semantic_search / keyword_search 会降级为空结果，但 kb_outline / kb_read_section 不受影响。
