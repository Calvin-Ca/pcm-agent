# E2E 测试总策略

> **目的**：补齐"指标基准测试"覆盖不到的真实用户场景。
> **背景**：2026-04-26 一次浏览器实测发现 7 个 bug（详见 `docs/changelog/2026-04-26.md`），全部落在"跨集成边界"的链路上 — 而这些链路在之前的 FC/RAG/SQL 基准测试中被 mock 或绕过。

---

## 1. 为什么需要 e2e 测试

| 已有测试类型 | 覆盖了什么 | **没覆盖**什么 |
|------------|-----------|--------------|
| 单元测试（pytest） | 单个函数行为 | 跨模块协作 |
| 基准测试（benchmarks/） | FC 延迟、RAG 召回、SQL 拦截率 | 真实用户视角的"业务正确性" |
| 接口探活（health） | 服务起没起来 | 数据流是否正确 |

**真正的端到端**必须穿过这 5 个集成边界，缺一不可：

```
浏览器气泡 ─┐
            │ (前端 SSE 渲染：B4)
            ▼
        SpringBoot DTO ─┐
                        │ (字段名/格式：B1)
                        ▼
                    FastAPI Tool ─┐
                                  │ (工具描述/路由：B3)
                                  ▼
                              vLLM 推理 ─┐
                                        │ (think 块/tool_call 文本：B5/B7)
                                        ▼
                                    真实数据库
                                    (字段值约定：B2)
```

任何 mock 都会掩盖这条链路上的 bug。

---

## 2. 测试前置检查清单

### 2.1 服务器健康（每次开始测前）

```bash
# 172 ai-service 容器在跑
ssh caic@172.19.3.136 "docker ps --filter name=ai-assistant-service --format '{{.Status}}'"
# 期望：Up X hours/days

# 116 SpringBoot 在跑
ssh caic@172.19.3.136 "ssh useryzk@116.205.174.57 'curl -sf http://localhost:9900/api/ai/health -o /dev/null && echo UP || echo DOWN'"
# 期望：UP

# 反向 SSH 隧道在通
ssh caic@172.19.3.136 "pgrep -fa autossh | head -1"
# 期望：能看到 autossh -M 0 -N -R 9901 ... useryzk@116.205.174.57

# vLLM 在跑（172）
ssh caic@172.19.3.136 "curl -sf http://localhost:8099/v1/models | python -m json.tool | head -10"
# 期望：返回 qwen3-8b 模型信息
```

任何一个 DOWN，**先排障再测**，不要测完才发现底层断了。

### 2.2 测试账号准备

| 类型 | 登录名 | 用途 |
|------|--------|------|
| employee | `159****0206` | 工时填报、查询、漏填、加班 |
| deptAdmin | `thsware` | 跨用户查询、统计 |
| deptSubAdmin | （按需补） | 子部门权限测试 |

**账号锁定恢复**（失败 5 次会锁）：
```sql
-- 在 192.168.0.94 workhour 库执行
UPDATE jhi_user SET failed_attempts = 0, locked_date = NULL
WHERE login IN ('159****0206', 'thsware');
```

### 2.3 Token 获取

**优先**：从浏览器 DevTools → Network → `authenticate` → Response → `data.token` 复制。

**备选**（密码已加密，需提前从 reference 拿到密文）：
```bash
TOKEN=$(curl -s -X POST https://gst.thsware.com/api/authenticate \
  -H "Content-Type: application/json" \
  -d '{"username":"159****0206","password":"<加密后密文>","rememberMe":false}' \
  | python -c "import sys,json; print(json.load(sys.stdin)['data']['token'])")
```

### 2.4 WAF 规避（**重要，不读这条会浪费 1 小时**）

从**本地开发机**直接 curl `https://gst.thsware.com` 多次会被华为云 WAF CC 限流（403 + `Set-Cookie: HWWAFSESID`）。

**正确做法**：
1. 测试脚本推到 116 服务器跑：
   ```bash
   cat scripts/your-test.sh | ssh caic@172.19.3.136 \
     "ssh useryzk@116.205.174.57 'cat > /tmp/test.sh && chmod +x /tmp/test.sh'"
   ssh caic@172.19.3.136 "ssh useryzk@116.205.174.57 'TOKEN=<jwt> bash /tmp/test.sh'"
   ```
2. 每条 curl 之间 `sleep 3`
3. 加浏览器 UA + Origin + Referer
4. 已被限流 → 等 15-30 分钟，或换 4G 热点

完整诊断：`docs/waf-403-diagnosis-2026-04-23.md`。

### 2.5 测试数据准备

**只读模块**（M2 漏填、M3 加班、M4 权限、M8 工时查询）必须先确认数据库里**有相应数据**：

```sql
-- M2 漏填：确认本月有工作日且测试用户没填
SELECT DATE(wc.date_value) AS d, wc.work_hour AS expect, wh.id AS filled
FROM work_calendar wc
LEFT JOIN workhour wh
  ON DATE(wc.date_value) = DATE(wh.workhour_date)
 AND wh.member_id = '<测试 user_id>'
WHERE wc.is_work_day = '1'
  AND DATE(wc.date_value) BETWEEN DATE_FORMAT(CURDATE(), '%Y-%m-01') AND CURDATE()
ORDER BY wc.date_value;
-- 预期：filled IS NULL 至少 1 行

-- M3 加班：确认 workhour_attendance 有当前用户加班记录
SELECT work_date, overtime_hours, overtime_type
FROM workhour_attendance
WHERE member_id = '<测试 user_id>'
  AND work_date BETWEEN DATE_SUB(CURDATE(), INTERVAL 7 DAY) AND CURDATE()
  AND overtime_hours > 0;
-- 预期：≥ 1 行
```

**写操作模块**（M1 填报、M7 多轮填报）测试前后必须清理：

```sql
-- 测试前记录 baseline
SELECT COUNT(*) FROM workhour WHERE member_id = '<测试 user_id>' AND workhour_date = CURDATE();
-- 测试后比对，避免重复填报污染
DELETE FROM workhour WHERE member_id = '<测试 user_id>' AND workhour_date = CURDATE() AND description LIKE '%[E2E TEST]%';
```

> **测试数据约定**：所有 e2e 测试写的工时记录，`description` 必须以 `[E2E TEST]` 开头，便于事后清理。

---

## 3. 测试通道选择

| 通道 | 何时用 | 命令模式 |
|------|-------|---------|
| **浏览器手测**（生产入口） | M4/M6 渲染相关、最终验收 | DevTools 看 SSE 事件 + 气泡内容 |
| **116 跳板 curl**（生产入口绕 WAF） | M1/M2/M3/M5 工具链路、批量回归 | 见 2.4 |
| **172 直连 ai-service**（绕过 SpringBoot） | 本地排障、对比基线 | `curl http://localhost:8000/api/ai/chat ...`（在 172 上） |
| **本地 pytest** | 数据准备、清理脚本 | `pytest tests/e2e/<module>.py -v` |

**默认通道**：116 跳板 + 浏览器交叉验收。

---

## 4. 验收三层

每个模块必须同时通过这三层，少一层不算通过：

1. **接口层**：SSE 流里看到正确的 `tool_call` / `response` / `error` 事件
2. **数据层**：数据库里有/没有预期记录（写操作） 或 返回的 row 字段名/值正确（读操作）
3. **渲染层**：浏览器气泡内容是自然语言、无 JSON 字符、错误用红色

---

## 5. 失败上报模板

发现新 bug 时，在 `docs/changelog/2026-04-XX.md` 追加一条：

```markdown
## B<N> — <一句话问题描述>

### 问题
<用户输入 + 实际表现>

### 根因
<代码层根因，必须指到文件 + 函数名>

### 修复
<diff 形式，前后对比>

### E2E 验证路径
<浏览器 / curl 步骤 + 预期>
```

---

## 6. 模块清单与执行顺序

详见 [`matrix.md`](matrix.md)。

**P0 必跑**（M1~M5）：本次简历定稿前必须全绿。
**P1 选跑**（M6~M8）：简历定稿后补。
**P2 后置**（M9~M10）：5/10 离职前如有时间。

---

## 7. 与基准测试的边界

| 维度 | 基准测试（benchmarks/） | E2E 测试（testing/） |
|------|------------------------|---------------------|
| 目的 | 量化指标 → 简历 | 用户场景闭环 → 上线信心 |
| 数据 | 固定测试集（CSV） | 真实生产库 |
| LLM | 可 mock / 可强制 fallback | 必须真实 vLLM |
| 验收 | P50/P95/Recall/拦截率 | 接口 + 数据 + 渲染三层全绿 |
| 频率 | 季度一次 | 每次大变更前 |

**关键原则**：e2e 阶段发现的 bug 修复**不重跑基准测试**。基准数字以 `docs/benchmarks/2026-04-25-final.md` 为准，简历定稿后冻结至 v1.3。
