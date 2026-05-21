# C1 MCP 网关 — 部署 Runbook

> 状态：**已部署**。代码已完成并 push（origin/main `1017b3d`），41 单测过；网关已于 2026-05-20 部署到 172，容器 `ai-assistant-mcp-gateway` 运行中（0.0.0.0:8765）。
> 关联设计：`docs/superpowers/specs/2026-05-19-mcp-gateway-c1-identity-design.md` §9/§10
>
> ⚠️ **生产动作，须用户在场执行，不在无人值守批**。本文档是给管理员照着跑的清单，Claude 不自动执行。

---

## 前置事实（已核实）

- `mcp_servers/_gateway_core.py` env 契约：`MCP_GATEWAY_TOKEN`、`AI_SERVICE_URL`（默认 `http://ai-service:8000`，同 compose 网络免配）、`MCP_API_KEY`、`MCP_GATEWAY_TOKEN_TTL`（默认 1500=25min）。
- `requirements.txt` 已含 `mcp[cli]` / `uvicorn[standard]` / `httpx` → 网关**直接复用 ai-service 镜像**，无需新依赖、无需改 Dockerfile。
- 网关 ASGI：`mcp_servers.http_gateway_server:app`；MCP 端点 `/mcp`；健康 `/health/health` 返回 `ok`。
- `MCP_API_KEY` 是已有的共享 SA 密钥（stdio / ai-service 已用），应已在 172 `.env`；**绝不入库**。

---

## Step 1：生成网关令牌（172 上）

```bash
# 强随机，32 字节 hex
openssl rand -hex 32
```

记下输出，作为 `MCP_GATEWAY_TOKEN`。**带外**发给同事（IM 私发，不写进仓库、不写进本文档）。

---

## Step 2：写入 172 `.env`（不入库）

```bash
cd /home/caic/code/workhour/workhour_agent
# 追加（确认 MCP_API_KEY 已存在；没有则也要补共享密钥真值）
echo 'MCP_GATEWAY_TOKEN=<Step1 生成的值>' >> .env
echo 'MCP_GATEWAY_TOKEN_TTL=1500' >> .env
grep -E '^MCP_API_KEY=' .env   # 确认共享密钥已在，且非空
```

> `.env` 在 172 本地，不提交。仓库内 `.mcp.json` / 配置模板保持空占位。

---

## Step 3：在 `docker-compose.yml` 加 `mcp-gateway` service

在 172 的 `docker-compose.yml` `services:` 下追加（与 ai-service 同镜像、同网络）：

```yaml
  # C1 MCP 网关 — 团队共享接入入口
  mcp-gateway:
    build:
      context: ./fastapi-service
      dockerfile: Dockerfile
    container_name: ai-assistant-mcp-gateway
    depends_on:
      - ai-service
    env_file:
      - .env
    environment:
      - AI_SERVICE_URL=http://ai-service:8000
    ports:
      - "0.0.0.0:8765:8765"
    volumes:
      - ./fastapi-service:/app
      - ai-service-logs:/app/mcp_servers/logs
    command: uvicorn mcp_servers.http_gateway_server:app --host 0.0.0.0 --port 8765
    networks:
      - ai-network
    extra_hosts:
      - "host.docker.internal:host-gateway"
```

> `MCP_GATEWAY_TOKEN` / `MCP_API_KEY` / `MCP_GATEWAY_TOKEN_TTL` 经 `env_file: .env` 注入，不写进 compose（不入库）。
> ai-service 自身仍 `127.0.0.1:8000`（不对外）；网关 `0.0.0.0:8765` 对内网开放，是同事唯一入口。

> 注：compose service 这段属代码改动，可先在本地提交（不含密钥）；`docker compose up` 才是生产动作。

---

## Step 4：起服务并健康检查（172）

```bash
cd /home/caic/code/workhour/workhour_agent
docker compose up -d mcp-gateway
docker logs ai-assistant-mcp-gateway --tail 30        # 看到 "Starting HTTP MCP gateway on 0.0.0.0:8765"
curl -s http://127.0.0.1:8765/health/health           # 期望: ok
curl -s http://172.19.3.136:8765/health/health        # 内网地址也应 ok
```

鉴权快验（缺头应 401，不回显任何 token）：

```bash
curl -s -i http://127.0.0.1:8765/mcp                   # 期望 401 missing or invalid X-Gateway-Token
```

---

## Step 5：2 名同事真实 e2e（用户在场）

每人按 `docs/mcp-team-setup-guide.md` 配 4 行 http block（`X-Gateway-Token` + 各自 `X-Entity-ID`），然后：

1. 只读：问“查我最近的工时记录” → 返回**本人**真实数据（非他人、非全员）。
2. 写：`save_workhour confirm=False` 预览 → 确认 → `confirm=True` 真写 → 在 `/api/workhour` 或库里能查到该条，且记在**本人**名下。
3. 审计：`conversation_logs` / 网关日志含本人 entity_id + tool_name + userId(UUID)，**不含** token/key。

---

## Step 6：C1 行为抽验（验证非 bug）

A 把自己的 `X-Entity-ID` 改填 B 的钉钉 id，填一条工时 → 确认**确实以 B 身份写入**。
这是 C1 设计的预期行为（自声明可冒充，已被有意识接受，见记忆 `project_mcp_gateway_c1_risk_accepted`），**不是 bug，不要据此收紧或把 save_workhour 移出网关**。

---

## 验收清单

| 项 | 通过 |
|---|---|
| `172.19.3.136:8765/health/health` 返回 `ok` | |
| 缺 `X-Gateway-Token` → 401，响应体无 token/key | |
| 2 同事只读返回各自真实数据 | |
| save_workhour 二段确认写库成功，记本人名下 | |
| 日志无 `MCP_API_KEY` / `X-Gateway-Token` / JWT | |
| 跨人抽验确认 C1 行为符合预期 | |
| `MCP_GATEWAY_TOKEN` 带外发同事，未入库 | |

---

## 回滚

```bash
docker compose stop mcp-gateway && docker compose rm -f mcp-gateway
```

stdio / ai-service 不受影响（网关是独立 service，未改动其它）。
