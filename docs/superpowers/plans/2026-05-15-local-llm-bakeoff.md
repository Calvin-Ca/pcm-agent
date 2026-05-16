# 本地 LLM 候选 Bake-off 执行计划（172 GPU1）

> **For agentic workers:** 这是一份**运维 + 评测 runbook**（非 TDD 代码计划）。逐 Phase / 逐 Step 执行，每步给出确切命令 + 预期输出 + 失败处理。Steps 用 `- [ ]` 跟踪。
> ⚠️ 本计划在**多团队共享的生产 GPU 服务器**上操作，且 Phase 2 会**停掉生产 8099**。务必先读《硬约束》整节。

**Goal:** 在统一端口 8099、`--served-model-name` 恒为 `qwen3-8b` 的前提下，于腾空的 GPU1 上依次实测 4 个模型（BF16 基线 / Qwen3-8B-FP8 / Qwen3-14B-AWQ / Qwen3.5-35B-A3B-4bit），用项目既有评测集产出量化对比表，供选型决策。

**Architecture:** 候选轮流占用同一容器名+端口 8099，调用方（ai-service `.env` CHAT_LLM_MODEL=qwen3-8b）零改动。每轮只换 `--model` 路径 / `--quantization` / `--max-model-len`。生产原 BF16 容器规格在 Phase 0 快照为一键回滚脚本，全程可秒回滚。

**Tech Stack:** vLLM (OpenAI server) / Docker / modelscope / RTX 4090 ×4（无 NVLink、无 P2P）/ 项目 bench 脚本（pytest 无关，纯脚本）

---

## 硬约束（违反任一条 = 任务失败，必须逐条遵守）

1. **这是多团队共享生产机**。仅允许操作：容器 `vllm-qwen3-8b`、端口 8099、`/mnt/nvme/stone/modelscope_cache/` 下的下载。
   **绝对不许动**的（其它团队/项目，跑了 5 天~8 个月）：
   - 任何非 `vllm-qwen3-8b` 的容器：`water-cost-ai` / `checklist-convert-v5` / `dify-*` / `anju-*` / `vllm-bge-large` / `ai-assistant-*` / `ollama` / `portainer` / `hbbr/hbbs` 等全部
   - GPU0 / GPU2 / GPU3 上的任何进程（root `app.py`、caic `vlm_service.py` 等）
   - **禁止用 kill / nvidia-smi --gpu-reset / docker stop 去“腾显存”碰任何非 vllm-qwen3-8b 的东西**
2. **Phase 2 停生产 = 破坏性高影响操作**。停 `vllm-qwen3-8b` 前必须：① 确认用户已在派单中明确授权“可以停 8099 + 给定时间窗”；② 回滚脚本（Phase 0 产出）已就绪并自测过语法。**未拿到明确窗口授权，不许进入 Phase 2，停在 Phase 1 末尾报告等指令。**
3. **不许编造评测数字**。所有质量/延迟数据必须是脚本真实输出，原样贴；脚本跑不起来就如实报“未跑通+原因”，不许估算或填占位数。
4. **回滚优先**。任一轮异常（起不来 / OOM / 报错）→ 立即执行回滚脚本恢复原 BF16，再排查，不许把 8099 长时间晾着。
5. **破坏性 git/docker 操作先报告**：`docker rm` 生产容器、任何 `reset --hard` 等，执行前报“我要做 X，原因 Y，影响 Z”，本计划已含的 `docker stop/rm vllm-qwen3-8b` 属预期内但仍须在报告中逐次记录实际命令与时间。
6. **不下结论替用户选型**。Phase 3 只产出对比表 + 客观观察，最终选哪个由用户定。
7. 仓库代码层面若需新增评测脚本，commit message 用 conventional commits 名实相符（`test(bench):` 之类），且只动评测脚本，不碰 ai-service 业务代码。
8. 完成报告必须含：每个破坏性命令的实际执行记录 + 时间、是否动过约束 1 清单外的任何东西（没有则明确写“无”）、每轮原始评测输出、回滚脚本内容与自测结果。

---

## Phase 0 — 准备与快照（只读 + 生成回滚，零停机）

- [ ] **S0.1 快照当前生产容器完整规格 → 回滚脚本**

```bash
ssh caic@172.19.3.136 'docker inspect vllm-qwen3-8b > /tmp/vllm-qwen3-8b.snapshot.json && \
  echo "--- Args ---" && docker inspect vllm-qwen3-8b --format "{{json .Args}}" && \
  echo "--- Mounts ---" && docker inspect vllm-qwen3-8b --format "{{json .Mounts}}" && \
  echo "--- HostConfig.DeviceRequests ---" && docker inspect vllm-qwen3-8b --format "{{json .HostConfig.DeviceRequests}}" && \
  echo "--- restart/ipc/ports ---" && docker inspect vllm-qwen3-8b --format "{{.HostConfig.RestartPolicy.Name}} ipc={{.HostConfig.IpcMode}} {{json .NetworkSettings.Ports}}" && \
  echo "--- Image ---" && docker inspect vllm-qwen3-8b --format "{{.Config.Image}}"'
```
预期：拿到真实镜像名（应为 `vllm-qwen3:latest-cu122`）、挂载源（原 BF16 模型目录 `/mnt/nvme/stone/modelscope_cache/models/Qwen/Qwen3-8B`）、`--gpus device` 映射、完整 Args。

- [ ] **S0.2 用快照生成一键回滚脚本 `/tmp/rollback_8099.sh`**

依据 S0.1 的真实字段拼出**与现状完全一致**的 `docker run`（不要照搬本仓库 doc，doc 已过期；以 inspect 为准）。模板：

```bash
cat > /tmp/rollback_8099.sh <<'EOF'
#!/bin/bash
set -e
docker stop vllm-qwen3-8b 2>/dev/null || true
docker rm   vllm-qwen3-8b 2>/dev/null || true
docker run -d --name vllm-qwen3-8b --restart always \
  --gpus '"device=<S0.1 实际 device>"' --ipc=host -p 8099:8099 \
  -v /mnt/nvme/stone/modelscope_cache/models/Qwen/Qwen3-8B:/model:ro \
  -e VLLM_LOGGING_LEVEL=INFO <S0.1 实际镜像> \
  python -m vllm.entrypoints.openai.api_server \
  --host 0.0.0.0 --port 8099 --model /model --served-model-name qwen3-8b \
  --tensor-parallel-size 1 --trust-remote-code \
  --gpu-memory-utilization 0.90 --max-model-len 32768 \
  --max-num-seqs 8 --max-num-batched-tokens 4096 \
  --enable-prefix-caching --enable-auto-tool-choice --tool-call-parser hermes
EOF
chmod +x /tmp/rollback_8099.sh
bash -n /tmp/rollback_8099.sh && echo "rollback 语法 OK"
```
预期：`rollback 语法 OK`。**此脚本必须在 S0.1 字段核对无误后定稿，Phase 2 前不得变更。**

- [ ] **S0.3 确认 3 个候选的仓库 ID 与量化方式（不许猜，查实）**

```bash
ssh caic@172.19.3.136 '
echo "--- 35B 已缓存，看它的量化方式 ---"
cat /mnt/nvme/stone/modelscope_cache/models/Qwen/Qwen3.5-35B-A3B-4bit/config.json | python3 -c "import sys,json;c=json.load(sys.stdin);print(\"quant=\",c.get(\"quantization_config\"))"
echo "--- 看 modelscope CLI 可用性 ---"
which modelscope || pip show modelscope 2>/dev/null | head -2
'
```
然后用 `modelscope` 或浏览器核实官方仓库 ID（**以实际存在为准，下面是待核实候选，不得想当然**）：
- Qwen3-8B-FP8 → 预期 `Qwen/Qwen3-8B-FP8`（FP8）
- Qwen3-14B INT4 → 预期 `Qwen/Qwen3-14B-AWQ`（AWQ / W4A16）
- Qwen3.5-35B-A3B-4bit → 已缓存，量化方式以 S0.3 的 `quantization_config` 为准（决定 vLLM `--quantization` 取值）

把三者最终确定的「仓库ID / 本地路径 / `--quantization` 值 / 建议 `--max-model-len`」列成一张表写入报告，作为 Phase 2 的输入。**ID 核不准就停下问，不许下错。**

---

## Phase 1 — 下载候选模型（零停机，不碰 8099）

- [ ] **S1.1 下载 Qwen3-8B-FP8 与 Qwen3-14B-AWQ 到 modelscope 缓存**

```bash
ssh caic@172.19.3.136 'cd /mnt/nvme/stone/modelscope_cache/models/Qwen && \
  modelscope download --model <S0.3 确认的 8B-FP8 ID>  --local_dir ./Qwen3-8B-FP8 && \
  modelscope download --model <S0.3 确认的 14B-AWQ ID> --local_dir ./Qwen3-14B-AWQ'
```
（35B 已缓存，跳过。下载期间生产 8099 不受影响。盘剩 5.5T 充足。）

- [ ] **S1.2 校验下载完整性**

```bash
ssh caic@172.19.3.136 'for d in Qwen3-8B-FP8 Qwen3-14B-AWQ; do echo "== $d =="; ls -lah /mnt/nvme/stone/modelscope_cache/models/Qwen/$d | grep -E "safetensors|config.json"; done'
```
预期：两目录均含 `config.json` + 完整 `*.safetensors` 分片，无 0 字节文件。

- [ ] **S1.3 阶段性报告，停下等窗口授权**

输出：S0 表格 + S1 下载结果。**然后停止，向用户报告“Phase 1 完成，下载就绪，请求 Phase 2 停机窗口授权（确认可停 8099 + 时间段）”。未获明确授权不得进入 Phase 2。**

---

## Phase 2 — GPU1 轮测（**需用户明确窗口授权后**才执行）

> 进入前再次确认：约束 2 的窗口授权已拿到。每轮容器名恒 `vllm-qwen3-8b`、端口恒 8099、`--served-model-name` 恒 `qwen3-8b`（调用方零改动）。GPU1 = 启动参数里原本的 `--gpus device`（与生产同槽，停了生产即腾空，单卡独占，util 可 0.90）。

每轮通用步骤（R1~R3，R0 为不动的当前生产基线，先在停机前对 R0 跑评测）：

- [ ] **S2.0 （停机前）对 R0 基线跑评测**：8099 仍是生产 BF16，直接按 Phase 3 评测流程对它跑一遍，存为基线。
- [ ] **S2.x 每轮切换**（x=1 FP8 / 2 AWQ / 3 35B）：

```bash
# 1) 记录时间，停旧
date '+%F %T 切换 R<x> 开始'
docker stop vllm-qwen3-8b && docker rm vllm-qwen3-8b
# 2) 起新（仅 <模型路径>/<quant>/<max-len> 按 S0.3 表替换，其余恒定）
docker run -d --name vllm-qwen3-8b --restart always \
  --gpus '"device=<S0.1 device>"' --ipc=host -p 8099:8099 \
  -v /mnt/nvme/stone/modelscope_cache/models/Qwen/<候选目录>:/model:ro \
  -e VLLM_LOGGING_LEVEL=INFO <S0.1 镜像> \
  python -m vllm.entrypoints.openai.api_server \
  --host 0.0.0.0 --port 8099 --model /model --served-model-name qwen3-8b \
  --tensor-parallel-size 1 --trust-remote-code \
  --quantization <S0.3 quant 值> \
  --gpu-memory-utilization 0.90 --max-model-len <S0.3 max-len> \
  --max-num-seqs 8 --max-num-batched-tokens 4096 \
  --enable-prefix-caching --enable-auto-tool-choice --tool-call-parser hermes
# 3) 等就绪（最多 5 分钟，每 10s 探一次）
for i in $(seq 1 30); do curl -sf -m 5 http://127.0.0.1:8099/v1/models >/dev/null && echo READY && break; sleep 10; done
docker logs vllm-qwen3-8b --tail 20
```
失败处理：若 5 分钟未 READY 或日志有 OOM/Error → **立即 `bash /tmp/rollback_8099.sh`** 恢复 BF16，记录该轮失败原因，跳过该轮继续下一轮（或按用户指示中止）。

- [ ] **S2.x-eval**：READY 后按 Phase 3 跑评测，存结果，再进下一轮。
- [ ] **S2.end 收尾**：全部轮次结束后，**默认执行 `bash /tmp/rollback_8099.sh` 恢复原 BF16**（除非用户在窗口中另行指示直接换某模型常驻）。确认 8099 恢复 200。

---

## Phase 3 — 评测与对比报告

- [ ] **S3.1 定位并运行既有评测集**（对每轮 READY 的 8099 各跑一遍）

```bash
ssh caic@172.19.3.136 'cd <ai-service 仓库 fastapi-service 目录> && ls tests/benchmark/ tests/benchmark/data/'
# 用项目自带脚本（不新增逻辑，仅指向 8099）：
# tests/benchmark/bench_fc_vs_two_calls.py / bench_rag_recall.py / bench_sql_agent.py
# 数据集 tests/benchmark/data/{latency,rag,sql}_eval_50.jsonl
```
若脚本硬编码了 endpoint，仅允许改成指向 `http://127.0.0.1:8099/v1`（这是评测脚本不是业务代码），改动以 `test(bench):` 单独 commit。脚本跑不通如实报，不许造数。

- [ ] **S3.2 记录每轮指标**：质量（各 bench 自带打分）、延迟（TTFT、tokens/s，从 vLLM 日志的 throughput 行 + 脚本计时取）、稳定性（KV cache 占用峰值、有无 preemption，看 `docker logs` 的 `loggers.py` 行）、显存（`nvidia-smi` 该卡占用）。

- [ ] **S3.3 产出对比表**（Markdown，写入报告）：

| 模型 | quant | 质量(FC/RAG/SQL) | TTFT | tokens/s | 显存 | KV峰值/preempt | 备注 |
|------|-------|------------------|------|----------|------|----------------|------|
| R0 Qwen3-8B BF16 | - | | | | | | 基线 |
| R1 Qwen3-8B-FP8 | fp8 | | | | | | |
| R2 Qwen3-14B-AWQ | awq | | | | | | |
| R3 Qwen3.5-35B-A3B-4bit | (S0.3) | | | | | | MoE |

- [ ] **S3.4 完成报告**（按约束 8）：对比表 + 每轮原始输出摘录 + 所有破坏性命令执行记录与时间 + “是否动过约束1清单外的东西（无则明确写无）” + 回滚脚本内容及自测结果 + 客观观察（不替用户选型）。

---

## 回滚（任何阶段可用）

`bash /tmp/rollback_8099.sh` → 等 `curl http://127.0.0.1:8099/v1/models` 200 → 确认 ai-service 正常。原 BF16 模型目录 `Qwen3-8B` 始终在缓存，回滚不依赖网络。

## Self-Review

- 覆盖：4 个模型（含 BF16 基线）/ 统一 8099+模型名 / GPU1 单卡 / 既有评测集 / 一键回滚 / 共享机隔离 —— 用户需求全覆盖 ✓
- 无占位真值：模型 ID/quant 用 Phase 0 实查代替硬编码（避免我臆测出错），回滚以 live inspect 为准非过期 doc ✓
- 高危隔离：停生产为显式 gated step（约束 2 + S1.3 停点），他人 GPU/容器列入禁止清单 ✓
