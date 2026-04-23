---
name: 生产环境基础设施（2026-04-22 实测更新）
description: 实际部署拓扑、端口、反向 SSH 隧道、已知 bug 清单
type: project
---

## 实测拓扑（2026-04-22）

ai-service **部署在 172.19.3.136**（Docker Compose，不是 deploy-guide.md 早期版本写的 116），通过反向 SSH 隧道暴露到 116 的 127.0.0.1:9901，SpringBoot 网关模式（走 AIController）。

```
浏览器 → nginx (116:80/443) → SpringBoot (116:9900) → WebClient → 127.0.0.1:9901 → [SSH 隧道] → ai-service (172:8000)
```

反向隧道进程（常驻在 172）：
```
autossh -M 0 -N -R 9901:127.0.0.1:8000 useryzk@116.205.174.57
```

## 服务器分工

| 服务器 | 角色 |
|--------|------|
| 116.205.174.57 | 公网应用服务器：nginx、SpringBoot 9900（绑 127.0.0.1）、前端静态文件、反向隧道终点 127.0.0.1:9901 |
| 172.19.3.136 | GPU + 服务服务器：ai-service Docker Compose、Redis、Milvus、MinIO、vLLM、Ollama |
| 192.168.0.94 | 数据库服务器：MySQL 3306（库名 workhour） |

## 端口

**172.19.3.136（Docker，ai-assistant-* 容器）**
| 宿主机 | 容器内 | 服务 |
|--------|--------|------|
| 127.0.0.1:8000 | 8000 | ai-service（只绑本地，只能走隧道访问） |
| 16379 | 6379 | Redis |
| 29530 | 19530 | Milvus |
| 29000/29001 | 9000/9001 | MinIO API/Console |
| 19091 | 9091 | Milvus metrics |

**172.19.3.136（LLM 推理，独立 Docker）**
| 端口 | 服务 |
|------|------|
| 8099 | vLLM qwen3-8b（主对话 + Function Calling） |
| 8097 | vLLM bge-large-zh-v1.5（Embedding） |
| 8098 | vLLM bge-base-zh-v1.5（备） |
| 11434 | Ollama（备） |

**116.205.174.57**
| 端口 | 服务 |
|------|------|
| 80/443 | nginx（公网入口） |
| 127.0.0.1:9900 | SpringBoot（WAR + nohup） |
| 127.0.0.1:9901 | 反向 SSH 隧道入口（实际打到 172:8000） |

**192.168.0.94**：MySQL 3306，库 workhour，业务账号 yunzuku / 只读账号 read_only_ai

## 关键配置文件位置

- 172 上 ai-service 目录：`/home/caic/code/workhour/workhour_agent/`
- 172 启动：`docker compose up -d`（不是 nohup 裸跑）
- 116 SpringBoot 启动脚本：`/home/gongshi/gongshi-ht.sh`
- 116 nginx 配置：`/usr/local/nginx/conf/nginx.conf`

## ⚠️ 2026-04-22 发现的阻断 bug（见 docs/deploy-fixes-2026-04-22.md）

| # | 位置 | 问题 |
|---|------|------|
| P1-1 | `docker-compose.yml:125` | `REDIS_PORT=16379` 错误 override（应为 6379，容器内互联） |
| P1-2 | `docker-compose.yml:129` | `SPRINGBOOT_BASE_URL=host.docker.internal:9900` 错误 override，应让 .env 的 `https://gst.thsware.com` 生效 |
| P1-3 | `springboot3/.../AIController.java:109,203,245,293` | 调用 ai-service 缺 `/api` 前缀（`/ai/chat/stream` 应为 `/api/ai/chat/stream`） |
| P1-4 | 116 SpringBoot 环境变量 | `AI_SERVICE_URL` 未设，默认 `http://localhost:8001`，应为 `http://127.0.0.1:9901` |
| P1-5 | 172 `.env` | `DASHSCOPE_API_KEY` 缺失（启动能用 Milvus 缓存，查询时 embedding 会挂） |
| P2-1 | `AIController.java:108-114` | 不透传 Authorization header 给 ai-service → 工具调 SpringBoot 时 401 |
| P2-2 | `AIController.java:171-173`, `AIPermissionInterceptor.java:58-59` | entity_type/department_id 硬编码 → 权限降级 |
| P2-3 | 116 nginx | `/api/ai/` 未加 SSE 专用 location（proxy_buffering on 会毁流式） |

完整修复清单见 `docs/deploy-fixes-2026-04-22.md`。

## 文档状态

- `deploy-guide.md` 内部多处自相矛盾（Docker vs conda、172 vs 116），需要清理（列入 P3-2）
- `deployment.md` 架构图已更新为 172 Docker 方案
- `improvement-plan-2026-04-10.md` 仍有参考价值
