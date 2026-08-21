# R2 (Qwen3-14B-AWQ) 慢根因诊断对照报告

> **诊断时间**: 2026-05-16
> **诊断者**: Claude Code Agent
> **硬件**: RTX 4090 24GB ×1 (GPU1), NVIDIA Driver 535.230.02
> **目标**: 用变量隔离的对照确证"R2 慢 6.3× 是否 = 未走 Marlin kernel"

---

## 一、参照基线（已确认，本次不重跑）

| 代号 | 模型 | 量化 | TPS | VRAM | 来源 |
|------|------|------|-----|------|------|
| R0 | Qwen3-8B | BF16 | **58.4** | 21.4 GB | bake-off 首测 |
| R2 | Qwen3-14B-AWQ | awq (gemm) | **9.26** | 21.7 GB | bake-off 重跑 (2026-05-15) |

---

## 二、对照候选

### C1: 同一 14B-AWQ 权重，仅改 `--quantization awq_marlin`

**目的**: 纯 kernel 对照——与 R2 唯一差异就是 kernel 后端，用于隔离确证根因。

**启动差异**（与 R2 相比仅改一行）:
```diff
- --quantization awq
+ --quantization awq_marlin
```

### C2: Qwen3-14B-FP8

**实证**: ModelScope API 返回 `/msg success`，模型存在。已下载到 `/home/caic/.cache/modelscope/hub/Qwen/Qwen3-14B-FP8`（16GB，4 个 safetensors 分片）。

**量化配置**:
```json
{"quantization_config": {"quant_method": "fp8", "activation_scheme": "dynamic", "fmt": "e4m3", "weight_block_size": [128, 128]}}
```

---

## 三、评测结果

### 3.1 量化对照表

| 指标 | R0 BF16 基线 | R2 AWQ(gemm) | **C1 AWQ(Marlin)** | C2 FP8 |
|------|-------------|-------------|-------------------|--------|
| **TTFT (ms)** | ~70 | ~139 | **~24–68** | ~37–81 |
| **Total (ms)** | ~4,450 | ~27,760 | **~2,724** | ~7,292 |
| **TPS** | **58.4** | 9.26 | **94.7** | 35.3 |
| **显存 (GB)** | 21.4 | 21.7 | **21.2** | 21.7 |
| **vs R0** | — | 慢 6.3× | **快 1.6×** | 慢 1.65× |
| **vs R2** | — | — | **快 10.2×** | 快 3.8× |

### 3.2 C1 详细数据（3 趟）

**kernel 日志关键行**（来源: `vllm_start_C1_003425.log`）:
```
awq_marlin.py:162] The model is convertible to awq_marlin during runtime. Using awq_marlin kernel.
```

第1趟 (`bench_C1_003437.log`):
```
[simple]   TTFT=67.7ms Total=2767.8ms Tokens=256 TPS=94.81
[tool]     TTFT=40.1ms Total=2747.5ms Tokens=256 TPS=94.55
[analysis] TTFT=38.7ms Total=2746.6ms Tokens=256 TPS=94.54
```

第2趟 (`bench_C1_003457.log`):
```
[simple]   TTFT=38.2ms Total=2737.9ms Tokens=256 TPS=94.83
[tool]     TTFT=29.4ms Total=2728.1ms Tokens=256 TPS=94.86
[analysis] TTFT=24.8ms Total=2724.2ms Tokens=256 TPS=94.84
```

第3趟 (`bench_C1_003512.log`):
```
[simple]   TTFT=35.8ms Total=2738.6ms Tokens=255 TPS=94.35
[tool]     TTFT=24.8ms Total=2724.4ms Tokens=256 TPS=94.83
[analysis] TTFT=24.0ms Total=2723.6ms Tokens=256 TPS=94.83
```

**VRAM**: GPU1 = 21212 MiB = **21.2 GB**（来源: `bench_C1_vram_003457.log`）

### 3.3 C2 详细数据（3 趟）

**kernel 日志关键行**（来源: `vllm_start_C2_004614.log`）:
```
quantization=fp8
WARNING fp8_utils.py:1175] Using default W8A8 Block FP8 kernel config. Performance might be sub-optimal!
```

第1趟 (`bench_C2_004624.log`):
```
[simple]   TTFT=80.9ms Total=7311.8ms Tokens=256 TPS=35.4
[tool]     TTFT=58.9ms Total=7308.9ms Tokens=256 TPS=35.31
[analysis] TTFT=54.8ms Total=7307.0ms Tokens=256 TPS=35.3
```

第2趟 (`bench_C2_004658.log`):
```
[simple]   TTFT=49.0ms Total=7311.4ms Tokens=256 TPS=35.25
[tool]     TTFT=38.6ms Total=7292.8ms Tokens=256 TPS=35.29
[analysis] TTFT=37.2ms Total=7291.8ms Tokens=256 TPS=35.29
```

第3趟 (`bench_C2_004727.log`):
```
[simple]   TTFT=49.4ms Total=7313.4ms Tokens=256 TPS=35.24
[tool]     TTFT=37.7ms Total=7309.8ms Tokens=256 TPS=35.2
[analysis] TTFT=37.9ms Total=7313.7ms Tokens=256 TPS=35.19
```

**VRAM**: GPU1 = 22250 MiB = **21.7 GB**（来源: `bench_C2_vram_004658.log`）

---

## 四、诊断结论

### 4.1 根因确证：R2 慢 6.3× = 未走 Marlin kernel

**证据链**:

1. **vLLM 明确提示**（R2 启动日志）:
   ```
   awq_marlin.py:166] Detected that the model can run with awq_marlin,
   however you specified quantization=awq explicitly, so forcing awq.
   Use quantization=awq_marlin for faster inference
   ```

2. **C1 启动日志确认**:
   ```
   awq_marlin.py:162] The model is convertible to awq_marlin during runtime.
   Using awq_marlin kernel.
   ```

3. **TPS 差异**（唯一变量 = kernel 后端）:
   - R2 (awq gemm): 9.26 TPS
   - C1 (awq_marlin): 94.7 TPS
   - **提升倍数: 10.2×**

4. **变量隔离**: C1 与 R2 使用**完全相同的权重目录**（`/mnt/nvme/stone/modelscope_cache/models/Qwen/Qwen3-14B-AWQ`），启动参数仅 `--quantization` 不同。这是纯 kernel 对照，排除了模型差异、权重差异、环境差异等干扰因素。

**结论**: R2 (Qwen3-14B-AWQ) 在 bake-off 中 TPS 仅 9.26 的根本原因是 `--quantization awq` 强制使用了未优化的 AWQ GEMM kernel，而非自动转换到 awq_marlin。改用 `--quantization awq_marlin` 后，同一权重 TPS 提升 10.2×，达到 94.7，甚至超过 8B BF16 基线（58.4）1.6 倍。

### 4.2 14B 优化部署路径盘点

| 路径 | TPS | vs 8B-BF16 | 显存 | 可行性 |
|------|-----|-----------|------|--------|
| 14B-AWQ (gemm) | 9.26 | 慢 6.3× | 21.7 GB | 不可用 |
| **14B-AWQ (Marlin)** | **94.7** | **快 1.6×** | **21.2 GB** | **推荐** |
| 14B-FP8 | 35.3 | 慢 1.65× | 21.7 GB | 可用，但有 sub-optimal warning |

**C2 FP8 的 sub-optimal warning**:
vLLM 启动时多次提示 `Using default W8A8 Block FP8 kernel config. Performance might be sub-optimal!`，说明当前使用的是默认 FP8 kernel 配置，未针对 RTX 4090 进行调优。这意味着 C2 的 35.3 TPS 可能不是 FP8 在该硬件上的上限，但当前实测数据已有效。

---

## 五、原始日志文件（服务器可核验）

### C1 日志

| 文件 | 大小 | sha256 |
|------|------|--------|
| `/tmp/vllm_start_C1_003425.log` | 14776 B | `71672750f38c4bb9550649d32ab1e581483bf1ac4080fecf8a4a7f7f92d524e4` |
| `/tmp/bench_C1_003437.log` | 204 B | `e33d30e9179f6486353e76f1192102682b1ae21466ba5663b68602684f7a1a71` |
| `/tmp/bench_C1_003457.log` | 204 B | `f24028946686f9a5f6a619e5c29e4df2f2aaf5338fff650ca519f5027576217c` |
| `/tmp/bench_C1_003512.log` | 204 B | `7b9e21ae7df018931760845422c8faa64bc843f9ad13f5b78688e3c0e33beb2b` |
| `/tmp/bench_C1_vram_003457.log` | 76 B | `ebdd6085d3994194ae912f1852631e96f751c94141d5c1a52cd95f416c5bae7a` |

### C2 日志

| 文件 | 大小 | sha256 |
|------|------|--------|
| `/tmp/vllm_start_C2_004614.log` | 35467 B | `016bc79d47411ae800aab771067a190aa16872ca905127f48345fe32bb5608ed` |
| `/tmp/bench_C2_004624.log` | 202 B | `5e2f776fd8b70ce82e1b09b1369598466191fa71301a03c7d434c28aa2dc79a4` |
| `/tmp/bench_C2_004658.log` | 204 B | `cec2f934fe859a4566f8dd3126cde5b38be3b4c24c59d77ce259d27485c23fcb` |
| `/tmp/bench_C2_004727.log` | 203 B | `11c5cb058d072254230c420a23b6b792eda6f6646b75a2659cd4e76c27253d9f` |
| `/tmp/bench_C2_vram_004658.log` | 76 B | `37948eb2c9f08b36e1ceada4751734ef07779b887a7aa5d89e75039e603bb892` |

---

## 六、运维记录

| 时间 (CST) | 操作 | 备注 |
|------------|------|------|
| 00:32:44 | docker stop/rm vllm-qwen3-8b (R0 BF16) | 停生产 |
| 00:33:05 | 启动 C1 awq_marlin | 挂载 14B-AWQ 目录，quant=awq_marlin |
| 00:34:25 | C1 容器就绪 | 耗时 80s |
| 00:34:37 | C1 第1趟 bench | `bench_C1_003437.log` |
| 00:34:57 | C1 第2趟 bench + VRAM | `bench_C1_003457.log` |
| 00:35:12 | C1 第3趟 bench | `bench_C1_003512.log` |
| 00:35:36 | bash /tmp/rollback_8099.sh | 恢复 BF16 |
| 00:36:46 | 8099 恢复 200，dtype=bfloat16 | 回滚验证通过 |
| 00:37:xx | 开始下载 C2 Qwen3-14B-FP8 | modelscope 下载到默认缓存 |
| 00:43:xx | C2 下载完成 (16GB) | `~/.cache/modelscope/hub/Qwen/Qwen3-14B-FP8` |
| 00:43:44 | docker stop/rm vllm-qwen3-8b (R0 BF16) | 停生产 |
| 00:44:03 | 启动 C2 FP8 | 挂载 C2 目录，quant=fp8 |
| 00:46:14 | C2 容器就绪 | 耗时 130s |
| 00:46:24 | C2 第1趟 bench | `bench_C2_004624.log` |
| 00:46:58 | C2 第2趟 bench + VRAM | `bench_C2_004658.log` |
| 00:47:27 | C2 第3趟 bench | `bench_C2_004727.log` |
| 00:47:57 | bash /tmp/rollback_8099.sh | 恢复 BF16 |
| 00:48:xx | 8099 恢复 200，dtype=bfloat16 | 回滚验证通过 |

---

## 七、约束遵守

**是否动过约束1清单外任何东西**: **无**。
仅操作了:
- `vllm-qwen3-8b` 容器 / 端口 8099
- `/mnt/nvme/stone/modelscope_cache/models/Qwen/Qwen3-14B-AWQ`（C1 挂载）
- `/home/caic/.cache/modelscope/hub/Qwen/Qwen3-14B-FP8`（C2 下载，默认缓存目录）

未碰任何其他容器、进程、GPU。

---

*报告生成时间: 2026-05-16 00:50*
*评测环境: 172.19.3.136 GPU1, RTX 4090*
