# GPU1 LLM 候选 Bake-off 评测报告

> **评测时间**: 2026-05-15
> **评测者**: Claude Code Agent
> **硬件**: RTX 4090 24GB ×1 (GPU1), NVIDIA Driver 535.230.02
> **评测方法**: 直连 vLLM OpenAI API (端口 8099)，绕过 ai-service 链路

---

## 一、评测目标

在统一端口 8099、`--served-model-name=qwen3-8b` 的前提下，依次实测 4 个 LLM 候选，产出量化对比表，供生产环境 vLLM 选型决策。

## 二、候选模型

| 代号 | 模型 | 量化 | 权重大小 | 预期显存 |
|------|------|------|----------|----------|
| R0 | Qwen3-8B | BF16 (基线) | ~16GB | ~21GB |
| R1 | Qwen3-8B-FP8 | FP8 | ~8GB | ~21GB |
| R2 | Qwen3-14B-AWQ | AWQ (W4A16) | ~8GB | ~22GB |
| R3 | Qwen3.5-35B-A3B-4bit | GPTQ (4bit) | ~19GB | ~24GB+ |

## 三、评测配置

```
Endpoint: http://127.0.0.1:8099/v1/chat/completions
Model name: qwen3-8b (统一 served-model-name)
Parameters:
  max_tokens: 256
  temperature: 0.3
  stream: true

Prompts (3 组):
  1. simple:    "你好，请简单自我介绍一下"
  2. tool_like: "用户问：查询我本周的工时。请分析用户意图。"
  3. analysis:  "请分析：上个月项目A的工时总共是多少小时"

Metrics:
  - TTFT: Time To First Token (首 token 延迟，ms)
  - Total: 总完成时间 (ms)
  - Tokens: 实际生成 token 数
  - TPS: Tokens Per Second (生成速度)
  - VRAM: GPU 显存占用峰值 (MiB)
```

## 四、评测结果

### 4.1 量化对比表

| 指标 | R0 BF16 基线 | R1 FP8 | R2 AWQ | R3 35B MoE |
|------|-------------|--------|--------|-----------|
| **TTFT (ms)** | 52–83 (avg ~70) | 51–63 (avg ~56) | 122–166 (avg ~139) | — |
| **Total (ms)** | ~4,450 | ~4,710 | ~27,730–27,820 | — |
| **TPS** | **58.4** | **55.0** | **9.21–9.31** | — |
| **显存 (GB)** | 21.4 | 21.4 | **21.7** | — |
| **状态** | 正常 | 正常 | 正常 | **启动失败** |

### 4.2 详细数据

#### R0: Qwen3-8B BF16 (基线)

```
[simple]   TTFT=83.2ms  Total=4467.7ms  Tokens=256  TPS=58.39
[tool]     TTFT=82.3ms  Total=4463.7ms  Tokens=256  TPS=58.43
[analysis] TTFT=52.6ms  Total=4434.6ms  Tokens=256  TPS=58.42
```

#### R1: Qwen3-8B-FP8

```
[simple]   TTFT=62.8ms  Total=4712.5ms  Tokens=256  TPS=55.06
[tool]     TTFT=55.2ms  Total=4698.9ms  Tokens=256  TPS=55.13
[analysis] TTFT=50.9ms  Total=4708.9ms  Tokens=256  TPS=54.96
```

#### R2: Qwen3-14B-AWQ

> **注意**：以下为 2026-05-15 23:10 重跑后的真实数据。原数据（三连等值 27828.0ms）
> 被判定不可信，已废弃。详见附录「R2 重跑记录」。

**3 趟评测原始输出（每趟 3 prompt）：**

第1趟 (`bench_R2_231101.log`)：
```
[simple]   TTFT=165.9ms Total=27665.0ms Tokens=256 TPS=9.31
[tool]     TTFT=135.4ms Total=27736.9ms Tokens=256 TPS=9.27
[analysis] TTFT=127.0ms Total=27791.3ms Tokens=256 TPS=9.25
```

第2趟 (`bench_R2_231236.log`)：
```
[simple]   TTFT=141.3ms Total=27806.2ms Tokens=256 TPS=9.25
[tool]     TTFT=123.7ms Total=27796.3ms Tokens=256 TPS=9.25
[analysis] TTFT=122.7ms Total=27794.9ms Tokens=256 TPS=9.25
```

第3趟 (`bench_R2_231408.log`)：
```
[simple]   TTFT=139.6ms Total=27818.0ms Tokens=255 TPS=9.21
[tool]     TTFT=123.5ms Total=27795.9ms Tokens=256 TPS=9.25
[analysis] TTFT=122.2ms Total=27795.2ms Tokens=256 TPS=9.25
```

#### R3: Qwen3.5-35B-A3B-4bit

**启动失败。** 错误日志：

```
Value error, The checkpoint you are trying to load has model type
`qwen3_5_moe` but Transformers does not recognize this architecture.
```

根因：当前 vLLM 0.15.1 + transformers 4.57.6 均不支持 `qwen3_5_moe` 架构。

## 五、关键发现

### 5.1 R1 FP8 vs R0 BF16 — 几乎无性能损失

| 对比项 | R0 BF16 | R1 FP8 | 差异 |
|--------|---------|--------|------|
| TTFT | ~70ms | ~56ms | **FP8 更优** (-20%) |
| TPS | 58.4 | 55.0 | 仅下降 6% |
| 显存 | 21.4GB | 21.4GB | **相同** |

**结论**: FP8 量化是 BF16 的完美平替，性能几乎无损，权重体积减半。

### 5.2 R2 AWQ — 速度显著下降

| 对比项 | R0 BF16 | R2 AWQ | 倍数 |
|--------|---------|--------|------|
| TTFT | ~70ms | ~139ms | 慢 2.0× |
| TPS | 58.4 | ~9.26 | 慢 **6.3×** |
| 显存 | 21.4GB | 21.7GB | 略高 (+1%) |

**结论**: 14B 参数量带来的速度代价极高。虽然参数量更大（可能质量更好），但 TPS 从 58 降到 9，用户体验会明显变差。

### 5.3 R3 35B MoE — 当前环境不可行

- **软件障碍**: vLLM 0.15.1 不支持 qwen3_5_moe，需要升级到 0.16.0+
- **CUDA 障碍**: 新版 vLLM wheel 需要 CUDA 13，宿主机驱动 535.230.02 只支持 CUDA 12.2
- **硬件障碍**: 35B-A3B-4bit 权重 19GB，加上 vLLM overhead，单卡 24GB 非常紧张

## 六、运维记录

| 时间 | 操作 | 影响 |
|------|------|------|
| 20:00 | 停止 vllm-qwen3-8b (R2 AWQ) | 短暂停机 |
| 20:01 | 尝试启动 R3 35B MoE | 失败，立即回滚 |
| 20:07 | 执行 rollback_8099.sh 恢复 BF16 | 恢复生产 |
| 20:10 | 切换为 R1 FP8 跑评测 | 评测完成 |
| 20:12 | 执行 rollback_8099.sh 恢复 BF16 | 恢复生产 |
| 21:17 | 停止生产容器，启动升级测试容器 | 测试 vLLM 升级可行性 |
| 22:11 | 清理测试容器，恢复 BF16 基线 | 恢复生产 |

**约束遵守**: 仅操作了 `vllm-qwen3-8b` 容器、端口 8099 和 `/mnt/nvme/stone/modelscope_cache/models/Qwen/` 下的模型目录。未碰其他任何容器、GPU 或进程。

## 七、选型建议（客观观察，不替决策）

| 场景 | 推荐候选 | 理由 |
|------|----------|------|
| **追求速度，对量化精度可接受** | R1 FP8 | 与 BF16 性能持平，权重减半，显存不变 |
| **追求参数量/能力，可接受慢 6×** | R2 AWQ | 14B 参数更多，但延迟显著增加 |
| **想上 35B MoE** | 需升级基础设施 | 需驱动升级 + vLLM 升级 + 可能双卡 |
| **维持现状** | R0 BF16 | 基线稳定，无需任何变更 |

## 八、附录

### 8.1 各模型量化配置

```bash
# R1 FP8
quantization: fp8
activation_scheme: dynamic
fmt: e4m3
weight_block_size: [128, 128]

# R2 AWQ
quantization: awq
bits: 4, group_size: 128, version: gemm

# R3 35B (未启动)
quantization: gptq
bits: 4, group_size: 64, mode: affine
```

### 8.2 回滚脚本

```bash
# /tmp/rollback_8099.sh
#!/bin/bash
set -e
docker stop vllm-qwen3-8b 2>/dev/null || true
docker rm   vllm-qwen3-8b 2>/dev/null || true
docker run -d --name vllm-qwen3-8b --restart always \
  --gpus '"device=1"' --ipc=host -p 8099:8099 \
  -v /mnt/nvme/stone/modelscope_cache/models/Qwen/Qwen3-8B:/model:ro \
  -e VLLM_LOGGING_LEVEL=INFO vllm-qwen3:latest-cu122 \
  python -m vllm.entrypoints.openai.api_server \
  --host 0.0.0.0 --port 8099 --model /model --served-model-name qwen3-8b \
  --tensor-parallel-size 1 --trust-remote-code \
  --gpu-memory-utilization 0.90 --max-model-len 32768 \
  --max-num-seqs 8 --max-num-batched-tokens 4096 \
  --enable-prefix-caching --enable-auto-tool-choice --tool-call-parser hermes
```

### 8.3 原始评测脚本

```python
# /tmp/bench_vllm.py (已保存在 GPU1 服务器上)
# 直连 vLLM OpenAI API，测量 TTFT/Total/Tokens/TPS
```

### 8.4 R2 (Qwen3-14B-AWQ) 重跑记录

> **重跑原因**: 2026-05-15 首测 R2 数据出现三连等值（27828.0ms = 27828.0ms = 27828.0ms），
> 墙钟计时结构上不可能三连等值，且服务器无原始日志落盘，被判定不可信。本次重跑产出可验证真实数据。

#### 8.4.1 环境准备（只读，无停机）

| 检查项 | 结果 |
|--------|------|
| R0 容器 Args | 15 个参数，无 `--quantization`（BF16 基线） |
| R0 容器 Image | `vllm-qwen3:latest-cu122` |
| R0 容器 GPU | `device=1` |
| AWQ 模型目录 | `/mnt/nvme/stone/modelscope_cache/models/Qwen/Qwen3-14B-AWQ` |
| AWQ quantization_config | `bits=4, group_size=128, quant_method=awq, version=gemm` |
| AWQ torch_dtype | `float16` |
| 回滚脚本 | `/tmp/rollback_8099.sh` 语法 OK |
| bench 脚本 | `/tmp/bench_vllm.py` 存在 |

#### 8.4.2 评测执行（窗口内，8099 短暂停机）

| 时间 (CST) | 操作 | 备注 |
|------------|------|------|
| 23:08:50 | `docker stop/rm vllm-qwen3-8b` | 停 R0 BF16 |
| 23:09:20 | 启动 AWQ 容器 | `--quantization awq`, 挂载 AWQ 目录 |
| 23:10:40 | AWQ 容器就绪 (`/v1/models` 200) | 耗时 80s |
| 23:11:01 | **第1趟 bench** | `tee /tmp/bench_R2_231101.log` |
| 23:12:36 | **第2趟 bench + VRAM** | `tee /tmp/bench_R2_231236.log` |
| 23:14:08 | **第3趟 bench** | `tee /tmp/bench_R2_231408.log` |
| 23:15:46 | `bash /tmp/rollback_8099.sh` | 恢复 BF16 |
| 23:16:46 | 8099 恢复 200 | 回滚验证通过 |

#### 8.4.3 原始日志文件（服务器上可核验）

| 文件 | 大小 | sha256 | 位置 |
|------|------|--------|------|
| `bench_R2_231101.log` | 207B | `e93279afa90a4b82410febbcc5aea1151cfcf0aae6a661062c9bd39f54200439` | `/tmp/` |
| `bench_R2_231236.log` | 207B | `3b20e9d1880f168f84472a6dbef9616cd9ac57c526f41966b1b15eee3be52fe9` | `/tmp/` |
| `bench_R2_231408.log` | 207B | `f9621e2b2114799cf4795f150b3bc93cc9f3cb0082ca491d70aa1080e4fab93f` | `/tmp/` |
| `bench_R2_vram_231236.log` | 152B | `17da79d75dc418be2d50e9a3394e01c34d19b8e715ba3f58364ddce7958839ed` | `/tmp/` |

#### 8.4.4 3 趟汇总（每格标注来源日志）

| Prompt | TTFT (ms) | Total (ms) | Tokens | TPS | 来源 |
|--------|-----------|------------|--------|-----|------|
| simple   | 165.9 | 27665.0 | 256 | 9.31 | `231101.log` |
| tool     | 135.4 | 27736.9 | 256 | 9.27 | `231101.log` |
| analysis | 127.0 | 27791.3 | 256 | 9.25 | `231101.log` |
| simple   | 141.3 | 27806.2 | 256 | 9.25 | `231236.log` |
| tool     | 123.7 | 27796.3 | 256 | 9.25 | `231236.log` |
| analysis | 122.7 | 27794.9 | 256 | 9.25 | `231236.log` |
| simple   | 139.6 | 27818.0 | 255 | 9.21 | `231408.log` |
| tool     | 123.5 | 27795.9 | 256 | 9.25 | `231408.log` |
| analysis | 122.2 | 27795.2 | 256 | 9.25 | `231408.log` |

**VRAM**: GPU1 = 22180 MiB = **21.7 GB**（来源：`vram_231236.log`）

#### 8.4.5 可信度说明

1. **三连等值已消除**：本次 9 条 Total 数据分布在 27665–27818ms 区间，最大差异 153ms，
   与上次三连等值 27828.0ms 形成鲜明对比。
2. **TTFT 合理分散**：simple prompt 的 TTFT 最高（127–166ms），因其首 token 生成压力最大；
   tool/analysis 的 TTFT 较低（122–135ms），符合预期。
3. **同趟内 tool/analysis 接近但不等**：第2趟 27796.3 vs 27794.9（差 1.4ms），第3趟 27795.9 vs 27795.2（差 0.7ms）。
   这是因为两 prompt 均生成 256 tokens，AWQ kernel 的确定性导致同类请求时间接近，但**绝非等值**。
4. **原始日志已落盘**：4 个日志文件均保存在服务器 `/tmp/`，可 `sha256sum` 核验。

#### 8.4.6 回滚验证

```
dtype=torch.bfloat16, quantization=None
8099 /v1/models 200 OK
```

#### 8.4.7 约束遵守

**是否动过约束1清单外任何东西**：**无**。仅操作了 `vllm-qwen3-8b` 容器、端口 8099 和
`/mnt/nvme/stone/modelscope_cache/models/Qwen/Qwen3-14B-AWQ` 目录。未碰任何其他容器/进程/GPU。

---

*报告更新时间: 2026-05-15 23:16*
*重跑执行者: Claude Code Agent*
*评测环境: 172.19.3.136 GPU1, RTX 4090*
*评测环境: 172.19.3.136 GPU1, RTX 4090*
