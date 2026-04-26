# 生产上线修复清单（2026-04-22）

> 作者：架构评估轮，交给实现 agent 执行。
> 范围：ai-service 当前已部署在 172.19.3.136（Docker Compose），SpringBoot 在 116，通过反向 SSH 隧道连通。本文档只覆盖让 E2E 跑通所需的最小改动，roadmap 其他事项不含。

---

## 一、实测拓扑

```
浏览器
  │ https
  ▼
116.205.174.57
  ├── nginx (80/443, /usr/local/nginx/conf/nginx.conf)
  │     ├── /api/ai/  →  127.0.0.1:9900 (SpringBoot AIController)
  │     └── /api/ /thsuaa/ ...  →  127.0.0.1:9900 (SpringBoot)
  │
  ├── SpringBoot (127.0.0.1:9900, 绑本地)
  │     └── AIController WebClient → ${AI_SERVICE_URL}
  │
  └── [被动监听] 127.0.0.1:9901 (反向 SSH 隧道终点)
              ▲
              │ ssh -R 9901:127.0.0.1:8000  useryzk@116
              │ (autossh 在 172 上常驻)
              │
172.19.3.136
  ├── Docker Compose（ai-assistant-*）
  │     ├── ai-assistant-service   127.0.0.1:8000  (只绑本地)
  │     ├── ai-assistant-redis     16379:6379
  │     ├── ai-assistant-milvus    29530:19530
  │     ├── ai-assistant-minio     29000/29001
  │     └── ai-assistant-etcd
  ├── vllm-qwen3-8b   :8099
  ├── vllm-bge-large  :8097
  └── ollama          :11434

192.168.0.94:3306  MySQL workhour
```

**关键结论**：ai-service 容器只绑 `127.0.0.1:8000`，外部（包括 116）不能直连，唯一入口是 116 上的 `127.0.0.1:9901`（反向隧道）。SpringBoot 要把 `AI_SERVICE_URL` 指到隧道端口。

---

## 二、问题清单（按优先级）

| # | 问题 | 位置 | 严重度 | 影响 |
|---|------|------|--------|------|
| P1-1 | `docker-compose.yml` 把 `REDIS_PORT` 硬编码成 16379 | `docker-compose.yml:125` | 🔴 阻断 | 容器间互联内部还是 6379，Redis 连不上 → 短期/长期记忆全失效（被 try/except 吞） |
| P1-2 | `docker-compose.yml` 把 `SPRINGBOOT_BASE_URL` 覆盖成 `host.docker.internal:9900` | `docker-compose.yml:129` | 🔴 阻断 | 172 宿主机没有 SpringBoot → 所有调 SpringBoot 的 Tool 失败 |
| P1-3 | `AIController` 请求路径缺 `/api` 前缀 | `AIController.java:109,203,245,293` | 🔴 阻断 | SpringBoot 调 ai-service 全部 404 |
| P1-4 | SpringBoot `AI_SERVICE_URL` 未指向隧道端口 | 116 SpringBoot 启动参数/环境变量 | 🔴 阻断 | 默认 `http://localhost:8001`，连不上 ai-service |
| P1-5 | ai-service `.env` 里没配 `DASHSCOPE_API_KEY` | `172:/home/caic/code/workhour/workhour_agent/.env` | 🔴 阻断 RAG | RAG 查询时 embedding 调用失败（启动加载走的是已缓存 Milvus collection，所以看起来正常） |
| P2-1 | `AIController` 未透传 `Authorization` 给 ai-service | `AIController.java:108-114` | 🟠 功能降级 | ai-service 调 SpringBoot 业务接口时缺 JWT → 401 |
| P2-2 | `AIController` 和 `AIPermissionInterceptor` 里 `entity_type`/`department_id` 硬编码 | `AIController.java:171-173`, `AIPermissionInterceptor.java:58-59` | 🟠 功能降级 | 权限校验全部按普通用户处理，管理员能力失效 |
| P2-3 | 生产 nginx `/api/ai/` 未加 SSE 专用 location | 116 `/usr/local/nginx/conf/nginx.conf` | 🟠 体验 | 默认 `proxy_buffering on` → SSE 被缓冲，流式效果丢失（现在走公网 /api/ai/ 返回 401 也意味着它走 SpringBoot 默认 /api/ location，没 SSE 友好参数） |
| P3-1 | `.env` 文件每行有 2 空格缩进，不规范 | `172:/home/caic/code/workhour/workhour_agent/.env` | 🟡 细节 | docker-compose env_file 目前能解析，但建议清理 |
| P3-2 | `docs/deploy/deploy-guide.md` 多处自相矛盾 | `docs/deploy/deploy-guide.md` | 🟡 文档 | §4.1/§4.2 Docker 部署 vs §8.1/§8.3 建议 conda+116，两段没清理 |
| P3-3 | `docs/deploy/deploy-guide.md` nginx 示例写 `proxy_pass http://172.19.3.136:8000` | `docs/deploy/deploy-guide.md §4.5` | 🟡 文档 | 与实际的反向隧道方案不符，应为 `http://127.0.0.1:9901` |

---

## 三、架构决策与评估

### 3.1 跨机部署方案：保留反向 SSH 隧道 ✅

**上下文**：ai-service 在 172（GPU 机），SpringBoot 在 116（公网）。跨机互通三种方案：

| 方案 | 说明 | 评估 |
|------|------|------|
| A. ai-service 搬到 116 | 同机部署 | 最简单，但 116 资源相对 GPU 机紧张；vLLM 嵌入/知识重建时跨机延迟累计（RAG 每次 query 要调 172:8097 embedding） |
| B. ai-service 绑 `0.0.0.0:8000` + 内网访问 | 172 对内网暴露 8000 | 安全面扩大，需要加 token/IP 白名单 |
| C. **反向 SSH 隧道**（现方案） | 172 主动 push 端口到 116 | ✅ 安全面最小（只 116 本机访问），故障时只影响 AI 模块 |

**决策**：保留方案 C。此方案已经有 autossh 常驻，无需再改动。

### 3.2 AI 网关：SpringBoot AIController 继续做网关 ✅

**备选**：让 nginx 直接把 `/api/ai/*` 代理到 `127.0.0.1:9901`，绕过 SpringBoot。

**评估**：
- 直连的优点：少一跳、全头部透传、代码最少
- 直连的缺点：ai-service 要自己校验 JWT + 拉取权限（现在完全不做），改动反而大

**决策**：保留 AIController 网关模式。但需要修 P1-3 / P2-1 / P2-2 三点，让它真正能工作。

### 3.3 docker-compose 拆分？不拆 ✅

**问题**：生产（172）用 16379/29530/29000 等高位端口是为避开 172 上其它 Redis/Milvus/MinIO 冲突，开发机可能没这需求，是否要拆 `docker-compose.dev.yml`？

**评估**：
- 端口映射只影响"宿主机"可见端口，容器内都是 6379/19530 标准端口
- 拆两份文件的心智负担 > 高位端口带来的阅读负担
- 开发机如果真有冲突，改根目录 `.env.local` 里的 `REDIS_HOST/PORT` 即可（但要同时去掉 `docker-compose.yml` 的 environment override，否则 .env 被覆盖，这就是 P1-1/P1-2 的锅）

**决策**：保持一份 `docker-compose.yml`，修好 P1-1/P1-2 后它就是通用的。

---

## 四、待执行任务（给实现 agent）

### 任务 1【P1-1 & P1-2】修 docker-compose.yml 的 environment override

**文件**：`docker-compose.yml`

**改动**：删除 ai-service 服务下面这段 override 里的 3 行（REDIS_PORT 改成 6379，SPRINGBOOT_BASE_URL 整行删除或注释）。

```yaml
# docker-compose.yml:122-129 现在
    environment:
      # 容器内网通信（覆盖 .env 中的 127.0.0.1，使用 Docker 内部服务名）
      - REDIS_HOST=redis
      - REDIS_PORT=16379                                  # ❌ 删掉或改成 6379
      - MILVUS_HOST=milvus
      - MILVUS_PORT=19530
      # Spring Boot 在宿主机运行，通过 host.docker.internal 访问
      - SPRINGBOOT_BASE_URL=http://host.docker.internal:9900  # ❌ 删掉，让 .env 生效
```

**改后**：

```yaml
    environment:
      # 容器内网通信（覆盖 .env 中的 127.0.0.1，使用 Docker 内部服务名）
      - REDIS_HOST=redis
      - REDIS_PORT=6379
      - MILVUS_HOST=milvus
      - MILVUS_PORT=19530
      # SPRINGBOOT_BASE_URL 由 .env 控制（跨机部署走公网域名）
```

**注意**：`host.docker.internal` 的 extra_hosts 映射可以保留（本地开发时还能用）。

**验证**（在 172 上）：
```bash
cd /home/caic/code/workhour/workhour_agent
docker compose up -d --force-recreate ai-service
docker exec ai-assistant-service sh -c 'echo $REDIS_PORT $SPRINGBOOT_BASE_URL'
# 期望：6379 https://gst.thsware.com
docker exec ai-assistant-service python -c "import redis; r=redis.Redis(host='redis',port=6379); print(r.ping())"
# 期望：True
```

---

### 任务 2【P1-3】修 AIController 的四处 URL 路径

**文件**：`../springboot3/src/main/java/com/thsware/framework/web/rest/AIController.java`

**改动**：四个调用点都把 `/ai/...` 改成 `/api/ai/...`，让全 URL 是 `<aiServiceUrl>/api/ai/chat/stream` 等。

| 行号 | 当前 | 改为 |
|------|------|------|
| 109 | `.uri(aiServiceUrl + "/ai/chat/stream")` | `.uri(aiServiceUrl + "/api/ai/chat/stream")` |
| 203 | `.uri(aiServiceUrl + "/health")` | `.uri(aiServiceUrl + "/api/ai/health")` |
| 245 | `.uri(aiServiceUrl + "/tools")` | `.uri(aiServiceUrl + "/api/ai/tools")` ⚠️ |
| 293 | `.uri(aiServiceUrl + "/tools/register")` | `.uri(aiServiceUrl + "/api/ai/tools/register")` ⚠️ |

**⚠️ 先确认**：245/293 的 `/tools` 和 `/tools/register` 在 ai-service 端根本不存在（实测 `/openapi.json` 里只有 `/api/ai/chat`, `/api/ai/chat/stream`, `/api/ai/health`, `/api/ai/memory`, `/api/ai/status`, `/api/ai/audit`）。实现前先验证这两个端点 ai-service 有没有实现；没有就把 `getTools()` 和 `registerTool()` 改为调 `/api/ai/status`（返回已注册工具）或临时返回静态信息，不然前端调用会 500。建议：`getTools()` 暂改调 `/api/ai/status`，`registerTool()` 直接返回 501 Not Implemented（工具注册当前只在 ai-service 代码侧静态完成）。

**验证**：构建后从 116 直接测
```bash
# 116 上（用 useryzk 或有权限的账号）
curl -s http://127.0.0.1:9901/api/ai/health
# 期望 200
```

---

### 任务 3【P1-4】配置 SpringBoot 的 AI_SERVICE_URL

**位置**：116 服务器 SpringBoot 启动脚本 `/home/gongshi/gongshi-ht.sh` 或同级 `.env` / `application-prod.yml`

**改动**：把 `AI_SERVICE_URL` 指向反向隧道端口。

推荐方式：改启动脚本里的环境变量注入（避免修改 application.yml 默认值）。

```bash
# /home/gongshi/gongshi-ht.sh 启动命令前加：
export AI_SERVICE_URL=http://127.0.0.1:9901
```

如果偏好改 profile：

```yaml
# application-prod.yml
app:
  ai-service:
    url: http://127.0.0.1:9901
```

**验证**：SpringBoot 重启后，从浏览器带 JWT 访问 `https://gst.thsware.com/api/ai/health`，期望 200 + ai-service 的 components JSON。

---

### 任务 4【P1-5】补 DASHSCOPE_API_KEY

**文件**：`172:/home/caic/code/workhour/workhour_agent/.env`

**改动**：追加一行（值从团队 Key 存档获取，之前 commit `0ee69ea` 把含 Key 的本地配置从 git 移除了，Key 本身应在团队保密存档里）：

```bash
DASHSCOPE_API_KEY=sk-xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx
```

**验证**：
```bash
docker compose restart ai-service
curl -X POST -H 'Content-Type: application/json' -H 'X-User-ID: test' \
  -d '{"message":"工时填报的截止时间","session_id":"smoke-rag"}' \
  http://127.0.0.1:8000/api/ai/chat
# 期望：返回 RAG 答案，日志里没有 "embedding" 相关 401/403
```

---

### 任务 5【P2-1 & P2-2】修 AIController / AIPermissionInterceptor 的权限上下文

**目标**：让 ai-service 收到真实的 Authorization / entity_type / department_id，而不是硬编码。

**文件 A**：`AIController.java`

- `chat()` 里把原始 `Authorization` header 透传到 WebClient（`.header(HttpHeaders.AUTHORIZATION, ...)`）。取值可以从 `ServerHttpRequest`（增加参数）或从 `SecurityContextHolder` 里 JWT 拼一次。
- `buildPermissionContext()` 里的 `entity_type`/`department_id` 改成真实查询（见文件 B）。

**文件 B**：`AIPermissionInterceptor.java`

- `preHandle()` 里的 TODO 要落地：通过 `userId` 查 `UserRepository` / `EmployeeRepository` 取 `entityType` 和 `departmentId`，放进 `request.setAttribute`，再让 AIController 读出来。
- 如果 Spring Boot 侧已有现成的 `SecurityUtils.getCurrentDepartmentId()` 之类的工具方法，优先复用；搜一下 `OrgDeptQueryService` 或 `UserService`。

**验证**：ai-service 日志里看到工具调用时携带真实 token（可以暂时在 ai-service 日志里打一行 `auth_token[:20]` 来确认）；权限拒绝场景（普通员工查他人工时）应返回 rejected。

---

### 任务 6【P2-3】生产 nginx 加 /api/ai/ SSE 专用 location

**位置**：116 `/usr/local/nginx/conf/nginx.conf`，`gst.thsware.com` server 块内，**在 `location /api/ ` 之前**插入：

```nginx
location /api/ai/ {
    proxy_pass http://127.0.0.1:9900;   # 仍走 SpringBoot AIController
    proxy_set_header Host $host;
    proxy_set_header X-Real-IP $remote_addr;
    proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
    proxy_set_header Authorization $http_authorization;
    proxy_http_version 1.1;
    proxy_set_header Connection '';
    proxy_buffering off;
    proxy_cache off;
    proxy_read_timeout 300s;
}
```

**注意**：`proxy_pass` 指 SpringBoot（不是 9901），因为我们决定保留 SpringBoot 做网关（见 §3.2）。9901 只是 SpringBoot 内部调 ai-service 用的。

**验证**：
```bash
/usr/local/nginx/sbin/nginx -t
/usr/local/nginx/sbin/nginx -s reload
# 浏览器里发一条消息，看 Network 面板的 /api/ai/chat 是否是长连接、分片到达（不是一次性全部返回）
```

---

### 任务 7【P3-2 & P3-3】清理 docs/deploy/deploy-guide.md

**文件**：`docs/deploy/deploy-guide.md`

**改动**：
1. 删掉 §8.1 "推荐部署方式：conda 裸跑 vs Docker"（与 §4.2 矛盾，现在实际就是 Docker Compose）
2. 删掉 §8.3 "部署位置建议：推荐 116"（与 §一 和 §4.1 矛盾，实际在 172）
3. 删掉 §七 里重复的旧章节（"Milvus（生产）" 还在写 `milvus-standalone`，"ai-service（开发，Docker）"、"Spring Boot（参考）" 这几段 §七 的内容与 §4.2 和前面的 Docker 运维命令重复）
4. §4.5 的 nginx 示例 `proxy_pass http://172.19.3.136:8000/api/ai/` 改成 `http://127.0.0.1:9900`（走 SpringBoot 网关），并加一段说明反向 SSH 隧道（`127.0.0.1:9901` 是 SpringBoot 内部用的，不是 nginx 用的）
5. §4.4 示例 `.env` 里 `REDIS_HOST=127.0.0.1, REDIS_PORT=16379`、`MILVUS_HOST=127.0.0.1, MILVUS_PORT=29530` 是 ai-service 裸跑到宿主机 Docker 的用法，Docker Compose 部署应是 `REDIS_HOST=redis, REDIS_PORT=6379`。需要分两个子节明确区分：
   - §4.4a "Docker Compose 部署时 .env（推荐）"
   - §4.4b "ai-service 裸跑 + 依赖 Docker 时 .env"

---

### 任务 8【P3-1】清 .env 里的前置空格

172 上 `/home/caic/code/workhour/workhour_agent/.env` 里每行有 2 空格缩进，删掉即可。操作前先 `cp .env .env.bak`。

---

## 五、E2E 验证顺序（所有任务完成后）

按顺序执行，上一步通过才进下一步：

1. **容器层**（172 上）
   ```bash
   docker compose up -d --force-recreate ai-service
   docker exec ai-assistant-service curl -sf http://127.0.0.1:8000/api/ai/health | jq .
   # 期望 status=healthy
   docker exec ai-assistant-service python -c "import redis; print(redis.Redis(host='redis',port=6379).ping())"
   # 期望 True
   docker exec ai-assistant-service curl -sf https://gst.thsware.com/api/authenticate -o /dev/null -w '%{http_code}\n'
   # 期望 401（能连通，没 token 是对的）
   ```

2. **隧道层**（116 上）
   ```bash
   curl -sf http://127.0.0.1:9901/api/ai/health | jq .
   # 期望 200
   ```

3. **SpringBoot 网关层**（116 上或公网）
   ```bash
   # 先取一个有效 JWT（从浏览器 F12 复制或调 /authenticate）
   TOKEN="Bearer ey..."
   curl -sk -H "Authorization: $TOKEN" https://gst.thsware.com/api/ai/health | jq .
   # 期望 200，返回 ai-service 的 components
   ```

4. **E2E 工具调用**
   ```bash
   curl -Nsk -H "Authorization: $TOKEN" -H 'Content-Type: application/json' \
     -d '{"message":"查我本周工时","session_id":"smoke-e2e-01","stream":true}' \
     https://gst.thsware.com/api/ai/chat
   # 期望：SSE 流式输出，包含 tool_call / tool_result / response / done 事件
   # ai-service 日志里看到 query_timesheet 被调用，SpringBoot 日志里看到 /api/workhour/list 被 ai-service 反向调用
   ```

5. **E2E RAG 问答**
   ```bash
   curl -Nsk -H "Authorization: $TOKEN" -H 'Content-Type: application/json' \
     -d '{"message":"工时填报的截止时间","session_id":"smoke-e2e-02","stream":true}' \
     https://gst.thsware.com/api/ai/chat
   # 期望：返回答案 + 📚 来源标注
   ```

6. **浏览器回归**：登录 https://gst.thsware.com，在 AI 对话页问上面两个问题，对比效果。

---

## 六、风险与回滚

| 改动 | 风险 | 回滚 |
|------|------|------|
| 任务 1 改 docker-compose.yml | 无数据风险 | `git revert` + `docker compose up -d --force-recreate ai-service` |
| 任务 2 改 AIController | 老客户端可能在调 `/api/ai/tools`，注意前端是否有用 | 保留 `.java` 旧版本，`git revert` |
| 任务 5 改 Interceptor | 如果查库逻辑错误会导致所有 AI 请求 500 | 加开关 `app.ai-service.strict-permission=false` 时退回硬编码；上线前灰度验证一个用户 |
| 任务 6 改 nginx | 语法错会让 nginx reload 失败 | `nginx -t` 预检 + `nginx -s reload`（不会中断现有连接） |

---

## 七、不在本次范围

下列 roadmap 项目**不在本次修复范围**，等 E2E 通了再启动：

- ec 类别精度提升（roadmap §十 🔵）
- Self-Reflection（0.5 天）
- SQL Agent 精度测试集
- 查询结果可视化（ECharts）
- 工具注册校验 / 工具调用重试 / Tool 基类提取
- MCP Server 接入
- logrotate 配置（日志目前进 docker volume，docker 本身有 rotation，暂缓）

---

## 八、执行顺序建议

推荐顺序：**1 → 4 → 2 → 3 → 6 → 5 → 7 → 8**

理由：
- 1、4 改完 Redis 和 DashScope 就先通了内部功能，方便后续排查
- 2、3 改完 SpringBoot 就能通到 ai-service
- 6 改完 nginx SSE 体验到位
- 5 最复杂（涉及查库），放后面单独打磨
- 7、8 收尾文档 + 环境

每完成一个任务执行对应的"验证"步骤，通过再进下一个。
