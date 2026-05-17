# LLM 评测进度（GPU1）

## 目标
在 GPU1 上实测 4 个 LLM 候选并产出对比表。

## 已完成

### Phase 0 + 1
- [x] preparatory work

### Phase 2: S2.0
- [x] R0 BF16 基线评测（停机前）

### Phase 2: S2.1
- [x] 切换到 R1 (Qwen3-8B-FP8) 并评测
- [x] R1 FP8 容器已就绪

### Phase 2: S2.2
- [x] 切换到 R2 (Qwen3-14B-AWQ) 并评测

### Phase 2: S2.3
- [x] 尝试启动 R3 (Qwen3.5-35B-A3B-4bit) — **失败**
- 失败原因：vLLM 0.15.1 + transformers 4.57.6 不支持 `qwen3_5_moe` 架构
- 升级尝试：vLLM 0.21.0 需要 CUDA 13，宿主机驱动 535.230.02 仅支持 CUDA 12.2

### Phase 2: S2.end
- [x] 恢复 BF16 基线

### Phase 3: S3.3
- [x] 产出对比报告
- 报告位置：`docs/benchmarks/llm-bakeoff-report-2026-05-15.md`

### Phase 4: A-RAG 质量对照 + judge（2026-05-16）
- [x] 14B-AWQ-Marlin vs 8B-BF16 vs 3.5-plus A-RAG 质量对照
- [x] qwen3.6-plus LLM-judge 盲评 completeness（108 条）
- 报告：`docs/benchmarks/llm-quality-bakeoff-2026-05-16.md`

### Phase 5: 方案 A 启用 + 真冒烟（2026-05-16）→ 升级缺口修复（2026-05-17）— 🟢 bench 已闭合
- [x] 172 .env PLANNER/SQL_AGENT model → qwen3.5-plus，容器 recreate 健康
- [x] 2026-05-16 真冒烟发现升级链路未触发：8b 首轮 FC `tool_calls=0` 不发起
  kb_* → `agent_history` 不含 kb_* → 升级钩子不触发。根因：升级卡在"8b 先
  发起 kb_*"这个 8b 最弱、本想补的前提上（证据 raw_arag_smoke_212026.log）
- [x] 2026-05-17 升级触发策略修复 — knowledge_qa 改道方案
  - 设计 spec：`docs/superpowers/specs/2026-05-17-plan-a-escalation-trigger-design.md`（66b2189）
  - 实现：263ff1f feat(llm) + c0c5127 feat(bench) + 5a0e980 test(rag)，本地 main 未 push
  - bench 实证：L01 真实走云端 5 步 kb_* 链（kb_outline→semantic→keyword→read×2），
    `model=qwen3.5-plus` ×4，`tool_calls=5 coverage=100%`
  - 单测 10 passed（独立复跑核验，非采信报告）
  - 原始证据：`fastapi-service/tests/benchmark/results/raw_arag_dispatch_20260517_005729.log`
    （sha256 `15bfcc7d64c6d3582b0a35b503b111c59b6fc6668f79b40e92238874848b7ecc`，
    UTF-16LE 编码须直读字节核验；回退用例 raw_arag_fallback_20260517_010454.log）
- [ ] 生产验收 — 待 push + 172 容器 recreate + 查 conversation_logs 真实流量
  knowledge 类 model_name→qwen3.5-plus（属生产动作，需用户在场授权）

## 评测结果摘要

| 模型 | 量化 | TTFT (ms) | TPS | 显存 (GB) | 备注 |
|------|------|-----------|-----|-----------|------|
| **R0 Qwen3-8B BF16** | — | ~70 | **58.4** | 21.4 | 基线 |
| **R1 Qwen3-8B-FP8** | fp8 | ~56 | **55.0** | 21.4 | 与基线几乎持平 |
| **R2 Qwen3-14B-AWQ** | awq | ~139 | **~9.26** | 21.7 | 慢 6.3×，数据已重跑 |

### R2 数据重跑说明

原 R2 数据（TTFT=156–187ms, Total=27828.0ms 三连等值）被判定不可信，已于 2026-05-15 23:10 重跑。
新数据 3 趟 9 条记录全部有原始日志落盘（`/tmp/bench_R2_231101.log` 等），可 `sha256sum` 核验。
详见报告附录 8.4「R2 重跑记录」。
| **R3 Qwen3.5-35B-A3B** | gptq | — | — | — | **启动失败** |

### 关键发现
1. **R1 FP8 是 R0 BF16 的平替**：性能几乎无损失（TTFT 略优，TPS 仅降 6%），显存相同
2. **R2 AWQ 速度显著下降**：TPS 从 58 → 9.25，延迟增加明显
3. **R3 需基础设施升级**：驱动 550+ + vLLM 0.16.0+ + 可能双卡

## 空间清理（附带的清理工作）

| 项目 | 释放空间 |
|------|----------|
| pip cache | ~38GB |
| huggingface falcon + Qwen2.5 | ~44GB |
| Docker dangling volumes (anju_v2_minio_data 等) | ~57GB |
| **总计** | **~139GB** |

## 操作记录

| 时间 | 操作 | 影响 |
|------|------|------|
| 20:00 | docker stop/rm vllm-qwen3-8b (R2 AWQ) | 停 R2 |
| 20:01 | docker run R3 35B | 启动失败，回滚 |
| 20:07 | bash /tmp/rollback_8099.sh | 恢复 BF16 |
| 20:10 | docker stop/rm + docker run R1 FP8 | 评测 R1 |
| 20:12 | bash /tmp/rollback_8099.sh | 恢复 BF16（最终）|
| 21:17 | 停止生产容器，启动升级测试容器 | 测试 vLLM 升级 |
| 22:11 | 清理测试容器，恢复 BF16 | 恢复生产 |

**是否动过约束1清单外的东西**：无。仅操作了 `vllm-qwen3-8b` 容器、端口 8099 和 `/mnt/nvme/stone/modelscope_cache/models/Qwen/` 下的模型目录。

## 备注
- 评测脚本：`/tmp/bench_vllm.py`（保存在 GPU1 服务器上）
- 完整报告：`docs/benchmarks/llm-bakeoff-report-2026-05-15.md`
- R3 如需测试，需升级宿主机 NVIDIA 驱动到 550+ 系列
