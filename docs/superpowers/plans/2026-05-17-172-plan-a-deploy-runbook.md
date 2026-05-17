# 172 生产部署 Runbook — origin/main(方案 A) 替换 2026-04-29 分叉文件

> **For agentic workers:** 这是生产部署操作手册，不是开发任务。**严格按步执行，任何门失败立即按预定义回滚或硬停上报，禁止即兴。** 共享生产 GPU 服务器，只许碰 `ai-assistant-service` 容器与 `/home/caic/code/workhour/workhour_agent/` 目录。

**Goal:** 把 172 生产 ai-service 从一个未提交的 2026-04-29 分叉 `langgraph_agent.py` 升级到 `origin/main`（含方案 A），使方案 A 生产侧首次真正生效。

**前置背景（2026-05-17 已只读侦察核验，执行 agent 无需重新推导）:**

- 172 容器 `ai-assistant-service` bind-mount `fastapi-service → /app`，跑磁盘活文件
- 172 `git HEAD = b76e2d9`，落后 `origin/main`（`5087db3`）**47 个 commit**
- 172 工作树 `fastapi-service/app/services/langgraph_agent.py` 处 `M`（未提交修改），sha256 `2e49102f0cc500244d3d521194822272d84ee58b8b9187e8781a319df0e7e23d`，1631 行，mtime `2026-04-29 19:38`
- 该 94 行未提交手改 = 纯 `suggest_workhour` 结果格式化 + `_generate_llm_summary` 兜底，**已被 main 字节等价吸收**（main 5087db3 行1687 含 `_generate_llm_summary`，suggest_workhour 格式化齐全）→ **部署不丢任何独有生产逻辑**，stash 仅作冗余保险
- ⚠️ `.env` 在 **repo 根**（`/home/caic/code/workhour/workhour_agent/.env`），**不是** `fastapi-service/.env`（首版 runbook 笔误，2026-05-17 部署实测更正）。已设 `PLANNER_LLM_MODEL=qwen3.5-plus` / `SQL_AGENT_LLM_MODEL=qwen3.5-plus`，另存根目录 `.env.bak.20260516`（**不要覆盖它**）
- ⚠️ 健康端点是 `/health/health`（main.py 把 health.router 挂在 prefix `/health`，路由本身又是 `/health`）；`/health` 返回 404 是端点名笔误非故障。功能门以 `/api/ai/chat` 冒烟为准
- ⚠️ 2026-05-17 首次部署发现 origin/main 当时 `55ce941` 含 chat 必崩 bug（`stream_agent_response` 函数内裸 `import os` 遮蔽 → line 1254 `os.getenv` UnboundLocalError），已回滚。**已修复并 push（`77ec242`：删 os/re 遮蔽 import + ast 回归守卫测试）**，本次重试以 `77ec242` 或更新为目标
- 172 工作树另有未跟踪：`fastapi-service/tests/benchmark/`、`tests/benchmark/results/latency_*_20260515.csv`（无害，勿动）

**Tech Stack:** SSH(`ssh caic@172.19.3.136`)、git、docker compose。172 上是 bash（`tee`/`sha256sum` 可正常用，无 PowerShell UTF-16 坑）。

---

## 硬停条件（命中则不回滚、不即兴，立即上报等人工裁决）

- `origin/main` 的 `langgraph_agent.py` **不含**方案 A 标记（`grep -c "rag_strategy\|_rag_fallback\|_probe_planner_availability"` 应 >0，约 19）或**不含本次 os 修复**（`grep -c "^            import os" ` 在 `stream_agent_response` 段应为 0）
- `b76e2d9..origin/main` commit 数显著 < 47（目标版本应 ≥ `77ec242`）
- `git status` 出现**除 `langgraph_agent.py` 以外**的已跟踪文件被修改/新增（可能有未知生产手术，必须先查清）
- `git pull` 非 fast-forward（HEAD 已分叉）
- `.env` 中 PLANNER/SQL model 不是 `qwen3.5-plus`
- 观察到其他团队容器/GPU 任务受影响

## 预定义回滚（仅在“部署后健康/冒烟门失败”时执行，执行后停下上报）

```bash
ssh caic@172.19.3.136 'cd /home/caic/code/workhour/workhour_agent && \
  cp -f fastapi-service/app/services/langgraph_agent.py.bak.20260517 fastapi-service/app/services/langgraph_agent.py && \
  git reset --hard b76e2d9 && \
  git stash list | head && \
  docker compose up -d --force-recreate ai-service && \
  sleep 8 && docker logs ai-assistant-service --tail 40'
```
> 回滚目标 = 已知良好的 b76e2d9 基线 + 备份文件。回滚后**不要**再自动尝试别的，停下上报。

---

## 执行步骤

所有命令输出**同时 tee 到** `/tmp/deploy_172_20260517.log`，结束 `sha256sum` 该日志，报告里附 sha。禁止编造（规则 8）。

### Task 1: 预检快照（只读，不改任何东西）

- [ ] **Step 1: 采集现状**

```bash
ssh caic@172.19.3.136 'cd /home/caic/code/workhour/workhour_agent && { \
  echo "== date =="; date; \
  echo "== HEAD =="; git rev-parse HEAD; \
  echo "== status =="; git status --porcelain; \
  echo "== langgraph sha256 =="; sha256sum fastapi-service/app/services/langgraph_agent.py; \
  echo "== container =="; docker ps --filter name=ai-assistant-service --format "{{.Names}} {{.Status}}"; \
  echo "== env model =="; grep -E "^(PLANNER_LLM_MODEL|SQL_AGENT_LLM_MODEL)=" .env; \
  } 2>&1 | tee /tmp/deploy_172_20260517.log'
```
Expected: HEAD=`b76e2d9...`；status 仅 ` M fastapi-service/app/services/langgraph_agent.py`（+ 已知未跟踪 benchmark）；sha256=`2e49102f…`；容器 Up；model 均 `qwen3.5-plus`。
**任一不符 → 命中硬停条件，上报，不继续。**

### Task 2: 备份（可逆保险）

- [ ] **Step 1: 备份文件 + .env**

```bash
ssh caic@172.19.3.136 'cd /home/caic/code/workhour/workhour_agent && \
  cp -n fastapi-service/app/services/langgraph_agent.py fastapi-service/app/services/langgraph_agent.py.bak.20260517 && \
  cp -n .env .env.bak.20260517-deploy && \
  ls -l fastapi-service/app/services/langgraph_agent.py.bak.20260517 .env.bak.20260517-deploy 2>&1 | tee -a /tmp/deploy_172_20260517.log'
```
> `cp -n` 不覆盖已存在文件，保护 `.env.bak.20260516`。

- [ ] **Step 2: stash 那 94 行 scratch（冗余保险，已证明被 main 取代）**

```bash
ssh caic@172.19.3.136 'cd /home/caic/code/workhour/workhour_agent && \
  git stash push -m "172-suggest_workhour-scratch-pre-deploy-20260517 (superseded by main 5087db3, backup only)" -- fastapi-service/app/services/langgraph_agent.py && \
  git stash list | head -3 && git status --porcelain 2>&1 | tee -a /tmp/deploy_172_20260517.log'
```
Expected: stash 创建成功；status 不再有 langgraph_agent.py 的 `M`。
**若 status 仍有其他已跟踪文件 `M`/`A` → 硬停上报。**

### Task 3: 拉取并校验目标版本

- [ ] **Step 1: fetch + 前提校验**

```bash
ssh caic@172.19.3.136 'cd /home/caic/code/workhour/workhour_agent && \
  git fetch origin 2>&1 && \
  echo "origin/main=$(git rev-parse --short origin/main)" && \
  echo "ahead=$(git rev-list --count b76e2d9..origin/main)" 2>&1 | tee -a /tmp/deploy_172_20260517.log'
```
Expected: `origin/main=5087db3`，`ahead=47`。**不符 → 硬停上报。**

- [ ] **Step 2: fast-forward pull**

```bash
ssh caic@172.19.3.136 'cd /home/caic/code/workhour/workhour_agent && \
  git pull --ff-only origin main 2>&1 && \
  echo "NEW HEAD=$(git rev-parse --short HEAD)" && \
  sha256sum fastapi-service/app/services/langgraph_agent.py && \
  grep -c "rag_strategy\|_rag_fallback\|_probe_planner_availability" fastapi-service/app/services/langgraph_agent.py 2>&1 | tee -a /tmp/deploy_172_20260517.log'
```
Expected: pull 成功且为 fast-forward；NEW HEAD=`5087db3`；方案 A 标记计数 > 0（约 19）。
**pull 报非 ff / 冲突 → 硬停上报（不要 `reset --hard`/`-f`）。**

### Task 4: 重建容器（只重建 ai-service）

- [ ] **Step 1: force-recreate**

```bash
ssh caic@172.19.3.136 'cd /home/caic/code/workhour/workhour_agent && \
  docker compose up -d --force-recreate ai-service 2>&1 | tee -a /tmp/deploy_172_20260517.log'
```
> 只 `ai-service` 一个 service，禁止 `up -d`（全量）或碰其他容器/其他团队 GPU 任务。

### Task 5: 健康 + 方案 A 生效验证

- [ ] **Step 1: 容器与日志**

```bash
ssh caic@172.19.3.136 'sleep 10 && docker ps --filter name=ai-assistant-service --format "{{.Status}}" && docker logs ai-assistant-service --tail 80 2>&1 | tee -a /tmp/deploy_172_20260517.log'
```
Expected: 容器 Up（健康）；日志无致命 traceback / 启动失败。

- [ ] **Step 2: 基础冒烟（健康端点 + 一条 knowledge 类对话）**

```bash
ssh caic@172.19.3.136 'curl -s -m 10 http://localhost:8000/health/health 2>&1; echo; curl -s -m 60 -X POST http://localhost:8000/api/ai/chat -H "Content-Type: application/json" -d "{\"message\":\"公司加班调休政策是怎么规定的\",\"user_context\":{\"user_id\":\"\",\"entity_type\":\"employee\"}}" 2>&1 | head -c 600 | tee -a /tmp/deploy_172_20260517.log'
```
Expected: health 200；chat 有知识库回答（非报错、非空）。

- [ ] **Step 3: 确认方案 A 真触发（关键验收）**

```bash
ssh caic@172.19.3.136 'cd /home/caic/code/workhour/workhour_agent && docker logs ai-assistant-service --since 5m 2>&1 | grep -iE "qwen3.5-plus|planner|rag_strategy|escalat" | tail -20 | tee -a /tmp/deploy_172_20260517.log'
```
Expected: 出现升级到 `qwen3.5-plus` / planner 升级 / rag_strategy 相关日志，证明 knowledge 类走了推理层（方案 A 生效）。
**若完全无升级痕迹 → 方案 A 仍未触发：执行预定义回滚，停下上报（不要在生产即兴调参）。**

### Task 6: 收尾与上报

- [ ] **Step 1: 日志指纹 + 结构化报告**

```bash
ssh caic@172.19.3.136 'sha256sum /tmp/deploy_172_20260517.log'
```

报告必须含：每步实际原始输出（不转述、不编造）、关键 sha256、最终 HEAD、容器状态、方案 A 是否实证触发、回滚句柄（备份文件路径 + `git reset --hard b76e2d9` + stash ref）、任何异常。**不 push、不动其他容器、不动 .env 内容。**

---

## 验收标准

1. 172 HEAD ≥ `77ec242`（含 os/re 遮蔽修复），langgraph_agent.py 含方案 A 标记
2. 容器健康、`/api/ai/chat` 冒烟返回 `success:true`（**关键：不再有 UnboundLocalError**）
3. 日志实证 knowledge 类请求走 `qwen3.5-plus`（方案 A 首次生产生效）
4. 全程原始日志落盘 + sha256，无编造
5. 备份与 stash 句柄齐全，回滚路径可用

---

## 重试增量（2026-05-17 第二次部署 — 取代上方 Task 1/2 的首版预期）

第一次部署后已 `git reset --hard b76e2d9` 回滚。当前 172 真实态（2026-05-17 已只读核验）：

- HEAD = `b76e2d9`，`langgraph_agent.py` = **b76e2d9 pristine**，sha256 `947c5c52540942a641d1f1e91b4abbf90cf95177ebaf909fdb1af0e480c907a8`（**不再是** 2e49102f；94 行 scratch 已不在运行文件中）
- `git status` 中该文件**不再是 `M`**（reset --hard 后干净）
- 备份**已存在**（勿重建，`cp -n` 会自动跳过）：`fastapi-service/app/services/langgraph_agent.py.bak.20260517`（74113 B）、根目录 `.env.bak.20260517-deploy`（2649 B）
- `stash@{0}` = 94 行 scratch（已在）；`stash@{1}` = 无关的他人 WIP，**勿动**
- 容器 Up，chat 200 可用（走 b76e2d9 rag_engine 路径）

**二次部署简化路径（跳过 Task 2 备份/stash，已完成）：**

1. **预检**（改判据）：确认 HEAD=`b76e2d9`、langgraph sha256=`947c5c5…`、该文件 git 干净（非 `M`）、`.env`（**repo 根**）model 仍 `qwen3.5-plus`、容器 Up。任一不符 → 硬停上报。
2. 直接 Task 3：`git fetch origin` → 校验 `origin/main` ≥ `77ec242` 且含方案 A 标记 + 含 os 修复（`grep -c "^            import os" fastapi-service/app/services/langgraph_agent.py` 应为 0）→ `git pull --ff-only origin main`。
3. Task 4：`docker compose up -d --force-recreate ai-service`（仅此 service）。
4. Task 5 验证，**重点确认 chat 不再抛 UnboundLocalError**（这是上次失败点），再查日志 knowledge 类是否走 `qwen3.5-plus`。
5. 失败回滚仍用「预定义回滚」段（cp .bak → reset --hard b76e2d9 → recreate）。
6. 过渡期注意：当前 prod 是 b76e2d9 pristine，比首次部署前少 94 行 suggest_workhour 格式化（仅存 .bak + stash@{0}）。部署成功即被 main 等价逻辑覆盖，无需单独恢复。
