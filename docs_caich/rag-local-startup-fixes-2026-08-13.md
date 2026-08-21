# Windows 本地 RAG 启动故障诊断与修复（2026-08-13）

## 一、结论

本次 RAG 启动失败不是单一故障，而是以下四个问题依次暴露：

1. Clash TUN 抢占了到 `172.19.3.136` 的路由，Milvus 和 embedding 的内网流量进入 Clash。
2. `.env.local` 指向 `19530`，而本项目包含现有知识库的 Milvus 实例暴露在 `29530`。
3. RAG 多 worker 初始化锁直接使用 Linux 专属的 `fcntl`，Windows 导入失败后被误判为 Milvus 不可用。
4. 修复 TUN 路由后，httpx 仍读取 Windows 系统 HTTP 代理，导致 `/embeddings` 请求继续进入 Clash 并超时。

最终通过“持久主机路由 + 正确 Milvus 端口 + 跨平台文件锁 + embedding 显式直连”恢复 RAG 初始化。

## 二、运行环境

| 项目 | 值 |
|---|---|
| 本地系统 | Windows |
| 本地地址 | `172.19.2.176` |
| 内网网关 | `172.19.2.1` |
| GPU/依赖服务器 | `172.19.3.136` |
| Clash 客户端 | Clash for Windows 0.20.39 Opt.1 |
| Clash Core | Premium `2023.08.17-13-gdcc8d87` |
| Clash TUN 地址 | `198.18.0.1` |
| vLLM embedding | `172.19.3.136:8097`，模型 `/model`，1024 维 |
| 项目 Milvus | `172.19.3.136:29530` |

服务器上同时存在两个 Milvus 实例：

| 宿主机端口 | 容器 | 实测 collection |
|---|---|---|
| `19530` | `ce-milvus` | 空 |
| `29530` | `ai-assistant-milvus` | `knowledge_base`、`knowledge_base_bench` |

## 三、故障现象与诊断过程

### 3.1 第一阶段：Milvus 连接失败，FAISS 降级也失败

最初日志：

```text
Milvus 不可用（Fail connecting to server on 172.19.3.136:19530），降级为 FAISS 内存存储
Retrying request to /embeddings ...
LangChain RAG 初始化失败: Error code: 502
```

FAISS 降级不是完全离线。`FAISS.from_documents()` 仍需调用 embedding 服务生成向量，因此 Milvus 和 embedding 同时不可达时，FAISS 也无法完成初始化。

Windows 选路检查显示：

```powershell
Find-NetRoute -RemoteIPAddress 172.19.3.136
```

请求被路由到：

```text
InterfaceAlias : Clash
IPAddress      : 198.18.0.1
NextHop        : 198.18.0.2
```

绑定物理网卡请求 embedding 则立即成功：

```powershell
curl.exe --interface 172.19.2.176 --noproxy "*" `
  http://172.19.3.136:8097/v1/models
```

这证明服务器服务正常，故障位于本机 Clash TUN 选路。

### 3.2 第二阶段：Milvus 已连接，但 Windows 缺少 `fcntl`

修复路由和端口后出现：

```text
Milvus 不可用（No module named 'fcntl'），降级为 FAISS 内存存储
```

此时 `connections.connect(...)` 已经成功。异常发生在后续多 worker 初始化锁：

```python
import fcntl
```

`fcntl` 是 Unix/Linux 模块，Windows 不提供。由于整个 Milvus 初始化都被同一个 `try/except` 包围，该平台兼容性错误被日志笼统描述为“Milvus 不可用”。

### 3.3 第三阶段：路由正确，但 embedding 仍每 30 秒超时

跨平台锁修复后，日志停在：

```text
LLM 初始化完成（qwen-plus）
PyMilvusDeprecationWarning: connections.connect ...
Retrying request to /embeddings ...
```

`PyMilvusDeprecationWarning` 只是未来 API 弃用提示，不是连接错误。真正阻塞点仍是 embedding。

对项目虚拟环境进行对照测试：

```text
httpx trust_env=True   -> ReadTimeout
httpx trust_env=False  -> HTTP 200
OpenAI 默认客户端      -> APITimeoutError
OpenAI 禁用系统代理    -> 成功返回 1024 维向量
```

Windows 系统代理当时为 Clash 的随机 mixed port：

```text
http=http://127.0.0.1:53626
https=http://127.0.0.1:53626
```

虽然 Windows `ProxyOverride` 中包含 `172.19.*`，但当前 httpx 0.28.1 的实测行为仍将请求送入系统代理。持久主机路由只能修复 IP 层 TUN 选路，不能覆盖应用显式使用的 HTTP 代理，因此还需在 embedding 客户端中禁用系统代理读取。

## 四、实际修改

### 4.1 Windows：添加持久 `/32` 主机路由

本机使用的命令：

```powershell
route.exe -p add 172.19.3.136 mask 255.255.255.255 172.19.2.1 metric 1 if 15
```

结果：

```text
172.19.3.136/32 -> 172.19.2.1 -> 172.19.2.176
```

注意：接口编号 `15` 是本机当时的物理以太网接口编号，其他机器必须先查询，不能照抄：

```powershell
Get-NetIPConfiguration
Get-NetAdapter
```

验证：

```powershell
route.exe print 172.19.3.136
Find-NetRoute -RemoteIPAddress 172.19.3.136
```

如需回滚：

```powershell
route.exe delete 172.19.3.136
```

当前 CFW 版本动态生成 `tun:` 配置，使用的 Clash Premium 核心也没有可靠的 `route-exclude-address` 配置入口，因此采用 Windows 持久主机路由实现等效绕过。

### 4.2 本地配置：Milvus 改用 `29530`

文件：`.env.local`

```env
MILVUS_HOST=172.19.3.136
MILVUS_PORT=29530
```

验证真实 Milvus RPC：

```python
from pymilvus import MilvusClient

client = MilvusClient(uri="http://172.19.3.136:29530", timeout=8)
print(client.list_collections())
```

预期：

```text
['knowledge_base_bench', 'knowledge_base']
```

说明：容器内访问 Milvus 仍使用服务名和容器端口 `milvus:19530`；`29530` 是从本地开发机访问 172 宿主机时使用的映射端口。

### 4.3 代码：文件锁改为跨平台实现

文件：`fastapi-service/app/services/langchain_rag.py`

新增 `_exclusive_file_lock()`：

- Windows 使用 `msvcrt.locking`。
- Linux/Docker 使用 `fcntl.flock`。
- Windows 非阻塞锁冲突时每 100ms 重试。
- 默认锁文件放在 `tempfile.gettempdir()`，不再硬编码 `/tmp`。

调用方式：

```python
with lock_path.open("a+") as lock_file:
    with _exclusive_file_lock(lock_file):
        # 初始化或复用 knowledge_base
        ...
```

该锁用于避免多个 Uvicorn worker 同时执行 `drop_old=True` 重建同一个 Milvus collection。

### 4.4 代码：内网 embedding 显式绕过系统代理

文件：`fastapi-service/app/services/langchain_rag.py`

vLLM embedding 初始化增加：

```python
import httpx

self.embeddings = OpenAIEmbeddings(
    model="/model",
    openai_api_key="EMPTY",
    openai_api_base="http://172.19.3.136:8097/v1",
    chunk_size=100,
    check_embedding_ctx_length=False,
    http_client=httpx.Client(trust_env=False),
    http_async_client=httpx.AsyncClient(trust_env=False),
)
```

该修改只影响固定内网地址上的 vLLM embedding。DashScope 和聊天 LLM 没有统一禁用代理，避免影响公网 API 调用。

## 五、验证结果

### 5.1 网络与服务

```text
172.19.3.136:19530 TCP OPEN
172.19.3.136:29530 TCP OPEN
172.19.3.136:8097  HTTP 200
```

### 5.2 Milvus RPC

```text
Milvus 29530: RPC OK
collections=['knowledge_base_bench', 'knowledge_base']
```

### 5.3 Embedding

使用与生产代码相同的 `OpenAIEmbeddings` 参数实测：

```text
同步 embedding：OK，1024 维，约 47ms
异步 embedding：OK，1024 维，约 31ms
```

### 5.4 自动化测试

新增：`fastapi-service/tests/test_langchain_rag_file_lock.py`

覆盖：

- 当前平台能够获取、释放并再次获取文件锁。
- `_init_vector_store()` 在平台锁存在时进入 Milvus 分支，不错误降级到 FAISS。

RAG 相关回归结果：

```text
20 passed
```

## 六、快速排查清单

以后再次出现 RAG 启动超时，按以下顺序检查：

1. 查看实际路由，确认不是 `Clash / 198.18.0.1`。

   ```powershell
   Find-NetRoute -RemoteIPAddress 172.19.3.136
   ```

2. 检查 embedding，强制不使用代理。

   ```powershell
   curl.exe --noproxy "*" --max-time 8 http://172.19.3.136:8097/v1/models
   ```

3. 检查两个 Milvus 端口。

   ```powershell
   Test-NetConnection 172.19.3.136 -Port 19530
   Test-NetConnection 172.19.3.136 -Port 29530
   ```

4. 用 `MilvusClient.list_collections()` 验证真实 RPC，不能只依赖 TCP 握手。

5. 对比 httpx 的代理行为。

   ```python
   import httpx

   print(httpx.get("http://172.19.3.136:8097/v1/models", timeout=8))
   with httpx.Client(timeout=8, trust_env=False) as client:
       print(client.get("http://172.19.3.136:8097/v1/models"))
   ```

6. 若出现 `No module named 'fcntl'`，确认运行的是包含跨平台锁修复的新代码，并彻底重启 FastAPI/debug 进程。

## 七、已知后续事项

启动时仍可能看到：

```text
PyMilvusDeprecationWarning: connections.connect is an ORM-style PyMilvus API
```

当前 `langchain_milvus` 与 PyMilvus 2.6 的兼容处理仍依赖 ORM alias，因此该警告暂不影响功能。迁移到不依赖 ORM monkey-patch 的新版 `langchain_milvus`/PyMilvus 组合后，再删除 `connections.connect()` 兼容逻辑。

另外，`Milvus.from_documents(..., drop_old=True)` 会重建 collection。多 worker 文件锁只能防止同一轮启动并发重建，不能替代正式的知识库版本管理或显式重建命令。
