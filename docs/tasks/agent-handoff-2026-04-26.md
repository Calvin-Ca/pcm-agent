# Agent 派单 — 2026-04-26

> **目标**：在 2026-05-10 离职前完成简历定稿 + e2e 测试覆盖。
> **执行方式**：本文档分阶段，每阶段一个 agent 独立执行；agent 执行前**必读**本文档对应阶段 + 引用文档。

---

## 总览

| 阶段 | 时间窗口 | Agent | 类型 | 阻塞 |
|------|---------|-------|------|------|
| 阶段 1 | 4/26（周六） | Agent A | 简历指标定稿（只读 + 数据采集） | 无 |
| 阶段 2 | 4/26 晚（可与 1 并行） | Agent B | docs 整理（git mv + 索引） | 无 |
| 阶段 3 | 4/27 ~ 4/29 | Agent C | e2e 测试执行（M1~M5） | 阶段 1 完成 |
| 阶段 4 | 4/29 ~ 5/9 | Agent D | 简历最终修订 | 阶段 1+3 完成 |

---

## Agent 通用约束（所有阶段）

1. **声明优先于执行**：每个任务开始前用一句话声明你要做什么，结束后说明产物路径。
2. **写文件前先 Read**：任何 Edit / Write 已存在文件，必须先 Read，否则会失败。
3. **删除文件后必须 `git ls-tree -r HEAD --name-only | grep <file>` 验证**，不能信 commit message。
4. **不动基准测试数据**：`docs/benchmarks/` 目录冻结，阶段 3 发现的 bug 修复**不重跑基准**。
5. **遵守简历叙事约束**：任何涉及简历指标的修改，必须读 `.claude/memory/feedback_benchmark_narrative.md`，**不写**「FC 延迟下降 X%」「拦截率 100%」之类注水句。
6. **遵守生产环境速查**：见 `CLAUDE.md` §"生产环境速查"，特别是 116 跳板 + WAF 规避。
7. **不擅自部署**：阶段 3 发现 bug 后只**修代码 + 写 changelog**，不重启容器、不部署。
8. **任务粒度**：完成一个子任务就汇报一次，不批量打包。

---

## 阶段 1：简历指标定稿（Agent A，4/26）

### 输入
- `docs/benchmarks/2026-04-25-final.md` — 当前最终基准报告
- `.claude/memory/feedback_benchmark_narrative.md` — 简历叙事约束

### 测试账号 / Token 获取

employee 测试账号的加密密码已存在 `.env.local`（gitignored）：

```bash
# 在本仓库根目录加载
set -a; source .env.local; set +a
echo "$E2E_TEST_LOGIN"  # 159****0206

# 获取 token（注意：响应字段是 data.token，不是 id_token）
TOKEN=$(curl -s -X POST https://gst.thsware.com/api/authenticate \
  -H "Content-Type: application/json" \
  -d "{\"username\":\"$E2E_TEST_LOGIN\",\"password\":\"$E2E_TEST_PASSWORD_ENCRYPTED\",\"rememberMe\":false}" \
  | python -c "import sys,json; print(json.load(sys.stdin)['data']['token'])")
echo "TOKEN: ${TOKEN:0:50}..."
```

> **同样适用于阶段 3** 的所有 curl 测试。脚本里 `${TOKEN:?}` 引用即可。

### 子任务

#### [1.1] Grafana 真实运行数据采集（30 min）

**目标**：从生产 Grafana 取最近 7 天的 5 项指标，填入基准报告"运行时数据"章节。

**步骤**：
1. Grafana 直连 172：浏览器打开 `http://172.19.3.136:3000`（默认 admin/admin，按 docker-compose 配置可能已改）
2. 取以下 5 项指标，时间范围"最近 7 天"：
   - `ai_requests_total` 累计 → 总请求数
   - `ai_request_duration_seconds` P50 / P95 → 端到端延迟
   - `tool_call_total` by `tool_name` → 各工具调用占比
   - `rag_retrieval_duration_seconds` P50 → RAG 检索延迟
   - `sql_query_blocked_total` → SQL 拦截次数（如有）
3. 各指标截图保存到 `docs/benchmarks/screenshots/2026-04-26-grafana-*.png`
4. 在 `docs/benchmarks/2026-04-25-final.md` 末尾追加章节「## 9. 生产 7 天运行数据（2026-04-26 采集）」，列出 5 项数字 + 截图引用

**验收**：
- 5 项指标都有数字（不是 N/A）
- 截图都能打开
- 报告末尾章节存在

**风险**：Grafana 可能未暴露端口；如不可达，先 `docker ps | grep grafana` 确认服务状态。

---

#### [1.2] e3db51a 生产链路验证（1 hour）

**目标**：确认 e3db51a 工具误分类修复在生产链路（116 入口 → 172 ai-service）生效。

**前置阅读**：
- `CLAUDE.md` §"生产环境速查"§"获取 JWT Token"
- `docs/testing/e2e-strategy.md` §"WAF 规避"

**步骤**：
1. 登录浏览器拿 employee token（`159****0206`）
2. 在本地写脚本 `scripts/e3db51a-prod-verify-2026-04-26.sh`：
   ```bash
   #!/bin/bash
   TOKEN="${TOKEN:?}"
   UA="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
   BASE="https://gst.thsware.com/api/ai/chat"
   for q in \
     "统计部门上月加班时长" \
     "查一下李四的工时" \
     "我本周工时" \
     "工时 Top 5 排名" \
     "各部门工时对比"; do
     echo "=== $q ==="
     curl -Ns --max-time 60 \
       -H "Authorization: Bearer $TOKEN" \
       -H "Content-Type: application/json" \
       -H "User-Agent: $UA" \
       -H "Origin: https://gst.thsware.com" \
       -H "Referer: https://gst.thsware.com/" \
       -d "{\"message\":\"$q\",\"session_id\":\"e3db51a-$(date +%s)\",\"stream\":true}" \
       "$BASE"
     echo; sleep 3
   done
   ```
3. 推到 116 跑：
   ```bash
   cat scripts/e3db51a-prod-verify-2026-04-26.sh | \
     ssh caic@172.19.3.136 "ssh useryzk@116.205.174.57 'cat > /tmp/verify.sh && chmod +x /tmp/verify.sh'"
   ssh caic@172.19.3.136 "ssh useryzk@116.205.174.57 'TOKEN=<jwt> bash /tmp/verify.sh'" 2>&1 | tee verify-output.log
   ```
4. 拉 ai-service 日志：
   ```bash
   ssh caic@172.19.3.136 "docker logs ai-assistant-service --since 30m | grep '执行工具:'" > verify-tools.log
   ```
5. 验证工具命中（原表为用户预设，未核对 `compute_statistics` 实际能力，以下已修正）：
   | query | 期望工具 |
   |-------|---------|
   | 统计部门上月加班时长 | `sql_query`（加班数据在 `workhour_attendance.overtime_hours`，`compute_statistics` 走 API 拿不到此字段） |
   | 查一下李四的工时 | `query_timesheet` |
   | 我本周工时 | `query_timesheet` |
   | 工时 Top 5 排名 | `sql_query`（`compute_statistics` 无 ranking/top_n 类型，无法 LIMIT 5） |
   | 各部门工时对比 | `sql_query`（`department_hours` 仅列数字，无跨部门对比语义） |
6. 在 `docs/benchmarks/report-2026-04-25-final.md` 末尾追加「## 10. e3db51a 生产链路验证（2026-04-26）」，记录 5/5 命中情况 + 日志摘录

**验收**：
- 5 条 query 全部命中能力匹配的工具
- id=2/3 走 `query_timesheet`，id=1/4/5 走 `sql_query`，路由各自正确
- 报告章节有日志证据

**风险**：
- 触发 WAF 限流：等 30 分钟或换 4G 热点
- token 过期：从浏览器重新拿

**异常处理**：
- 如发现误路由到 `sql_query`：**立即上报**，不修复，让用户决定是否回滚 e3db51a

---

### 阶段 1 产物清单

| 文件 | 状态 |
|------|------|
| `docs/benchmarks/screenshots/2026-04-26-grafana-*.png` | 5 张 |
| `docs/benchmarks/2026-04-25-final.md` | 追加 §9 + §10 |
| `scripts/e3db51a-prod-verify-2026-04-26.sh` | 新增 |
| `verify-output.log` / `verify-tools.log` | 临时日志（可不入库） |

---

## 阶段 2：docs 整理（Agent B，4/26 晚，可并行）

### 输入
- 当前 `docs/` 下 51 个 md（散在根目录）

### 目标
按主题归类到子目录，保留 git 历史，新增 `docs/README.md` 索引。

### 子任务

#### [2.1] 创建子目录 + git mv 文件（30 min）

**操作**（每条都用 `git mv` 保留历史，**不要** `mv` + `git add`）：

```bash
cd "E:/huan/工时管理系统/trunk/1 源代码/1.0 系统代码/ai-service"

# 创建目录
mkdir -p docs/benchmarks docs/deploy docs/testing/modules

# 1. benchmarks 归类（5 个）
git mv docs/benchmark-review-2026-04-24.md             docs/benchmarks/review-2026-04-24.md
git mv docs/benchmark-report-2026-04-24-v2.md          docs/benchmarks/report-2026-04-24-v2.md
git mv docs/benchmark-report-2026-04-24-v3.md          docs/benchmarks/report-2026-04-24-v3.md
git mv docs/benchmark-review-2026-04-25-corrected.md   docs/benchmarks/review-2026-04-25-corrected.md
git mv docs/benchmark-report-2026-04-25-final.md       docs/benchmarks/report-2026-04-25-final.md
git mv docs/benchmark-tasks-2026-04.md                 docs/benchmarks/tasks-2026-04.md

# 2. deploy 归类（5 个，waf-403 留根，避免改 CLAUDE.md 路径）
git mv docs/deploy-guide.md                            docs/deploy/deploy-guide.md
git mv docs/deploy-fixes-2026-04-22.md                 docs/deploy/deploy-fixes-2026-04-22.md
git mv docs/deploy-model-server.md                     docs/deploy/deploy-model-server.md
git mv docs/deployment.md                              docs/deploy/deployment.md
git mv docs/grafana-validation-2026-04-23.md           docs/deploy/grafana-validation-2026-04-23.md
# docs/waf-403-diagnosis-2026-04-23.md 保留在根目录（CLAUDE.md L63 引用此路径）

# 3. tasks 老文件归档（3 个）
mkdir -p docs/archive/tasks-2026-04
git mv docs/tasks/e2e-test-plan.md           docs/archive/tasks-2026-04/e2e-test-plan.md
git mv docs/tasks/p0-cdn-401-diagnosis.md    docs/archive/tasks-2026-04/p0-cdn-401-diagnosis.md
git mv docs/tasks/p1-user-id-anonymous.md    docs/archive/tasks-2026-04/p1-user-id-anonymous.md

# 4. e2e-regression 也归档
git mv docs/e2e-regression-2026-04-23.md     docs/archive/tasks-2026-04/e2e-regression-2026-04-23.md
git mv docs/improvement-plan-2026-04-10.md   docs/archive/improvement-plan-2026-04-10.md

# 验证
git status
git ls-tree HEAD docs/ | head
```

**验收**：
- `git status` 显示全部为 renamed（不是 deleted+added）
- `docs/` 根目录只剩：`README.md`（待新建）+ `api.md` + `user-guide.md` + `springboot-api-reference.md` + `roadmap.md`
- `docs/benchmarks/` 6 个文件
- `docs/deploy/` 6 个文件
- `docs/testing/` 已有内容（阶段 0 产出）

#### [2.2] 修复跨文件引用（30 min）

文件移动后，部分 md 内引用路径会失效。`grep` 找出并修：

```bash
cd "E:/huan/工时管理系统/trunk/1 源代码/1.0 系统代码/ai-service"
```

用 Grep 工具搜索（**不要用 bash grep**）：
- pattern：`docs/benchmark-`，files：`*.md` → 改为 `docs/benchmarks/...`
- pattern：`docs/deploy-`，files：`*.md` → 改为 `docs/deploy/...`
- pattern：`docs/grafana-validation`，files：`*.md` → 改为 `docs/deploy/grafana-...`
- pattern：`docs/e2e-regression-2026-04-23`，files：`*.md` → 改为 `docs/archive/tasks-2026-04/e2e-regression-2026-04-23.md`
- pattern：`docs/tasks/`（除新建的 `agent-handoff` 外）→ 视情况改

> **不改** `docs/waf-403-diagnosis-2026-04-23.md`：该文件保留在根目录，CLAUDE.md L63 路径不变，避免触动核心配置。

#### [2.3] 新建 docs/README.md（30 min）

```markdown
# docs 索引

## 主目录
- [user-guide.md](user-guide.md) — 用户使用指南
- [api.md](api.md) — REST/SSE API 参考
- [springboot-api-reference.md](springboot-api-reference.md) — SpringBoot 后端接口
- [roadmap.md](roadmap.md) — 升级路线图

## 子目录
- [benchmarks/](benchmarks/) — 基准测试报告（FC 延迟 / RAG 召回 / SQL 拦截）
- [changelog/](changelog/) — 各版本变更记录（按日期）
- [deploy/](deploy/) — 部署运维（Docker、CDN、WAF、Grafana）
- [design/](design/) — 设计文档（SQL Agent 等）
- [testing/](testing/) — E2E 测试体系
  - [e2e-strategy.md](testing/e2e-strategy.md)
  - [matrix.md](testing/matrix.md)
  - [modules/](testing/modules/) — M1~M5 模块测试用例
- [tasks/](tasks/) — 当前任务派单
- [test-reports/](test-reports/) — 历史准确率测试报告
- [archive/](archive/) — 已归档（过期任务/分析）
```

#### [2.4] 提交（10 min）

```bash
git add docs/README.md CLAUDE.md  # CLAUDE.md 如有引用更新
git commit -m "$(cat <<'EOF'
docs: 整理 docs 目录结构（benchmarks/deploy/testing 分目录）

- 6 个 benchmark-*.md → docs/benchmarks/
- 6 个 deploy-*.md / waf / grafana → docs/deploy/
- 4 个过期 task → docs/archive/tasks-2026-04/
- 新增 docs/README.md 索引
- 修复跨文件引用路径

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

### 阶段 2 验收
- `git ls-files docs/ | wc -l` 数量与 mv 前一致（仅位置变更）
- `docs/` 根目录只剩 5 个 md（README + 4 个保留）
- `git log -1 --stat` 显示全部为 R（rename）

---

## 阶段 3：e2e 测试执行（Agent C，4/27 ~ 4/29）

### 输入
- `docs/testing/e2e-strategy.md` — 总策略
- `docs/testing/matrix.md` — 模块矩阵
- `docs/testing/modules/M1~M5*.md` — 各模块测试用例

### 执行原则
1. **按 matrix.md §"执行顺序建议"**：
   - 4/27：M1 + M4
   - 4/28：M2 + M3
   - 4/29：M5（50 次压测，单独占一天）
2. **每个模块完成后填写"完成标记"章节**（见各 M 文档 §8）
3. **发现 bug**：按 `e2e-strategy.md` §5 模板写到 `docs/changelog/2026-04-XX.md`，**只记录不修复**（修复让用户决定优先级）
4. **数据清理**：每个模块测试结束后立即跑 §"测试数据准备 / 清理"的清理 SQL

### 阶段 3 产物
| 文件 | 内容 |
|------|------|
| `docs/testing/modules/M1~M5*.md` | 各文件追加"## 执行记录"章节 |
| `docs/changelog/2026-04-2X.md` | 新发现 bug 的报告（如有） |
| `scripts/m1-test.sh` ~ `scripts/m5-test.sh` | 各模块测试脚本（可选入库） |

### 阶段 3 出口
M1~M5 各模块"执行记录"章节都填了，且：
- 接口/数据/渲染三层全绿 → 阶段 3 完成
- 任一层失败 → 在 changelog 标记 P0/P1，等用户决定下一步

---

## 阶段 4：简历最终修订（Agent D，4/29 ~ 5/9）

### 输入
- `docs/benchmarks/2026-04-25-final.md`（已含阶段 1 §9 + §10）
- `docs/testing/modules/*` 执行记录
- `.claude/memory/feedback_benchmark_narrative.md`（**严格遵守**）

### 子任务

#### [4.1] 起草 5 条 bullet（在简历 / 作品集仓库，不在本仓库）

| Bullet | 主题 | 关键数字 | 必读约束 |
|--------|------|---------|---------|
| 1 | FC 架构 | save 持平（P50 +0.7%）+ query/kb 慢（37~44%/21%）+ 托管 API 预期 20-40% | feedback §"FC 延迟" |
| 2 | RAG | Recall@5 100% Milvus / 98% Hybrid + 解释（小库 BM25 噪音） | feedback §"RAG Recall" |
| 3 | SQL Agent 安全 | 硬规则 25%（5/20）+ LLM 改写 75%（15/20），**不写综合 100%** | feedback §"SQL Agent 安全拦截" |
| 4 | vLLM + Docker 工程化 | 内网部署 + 三层 fallback + e2e 7 个 bug 修复 | 引用 changelog/2026-04-26 |
| 5 | 监控可观测 | 阶段 1 §9 的 5 项 Grafana 数字 | 数字现采现填 |

#### [4.2] 在作品集详情页挂 CSV 链接

将 `tests/benchmark/results/*.csv` 的 3 份关键 CSV 上传到作品集仓库 `assets/`，详情页加链接：
- `latency_full_20260424_rerun.csv` — FC 延迟原始数据
- `rag_recall_*.csv` — RAG 召回原始数据
- `sql_security_*.csv` — SQL 拦截原始数据

#### [4.3] 生成 resume_v2.pdf

按作品集模板渲染，校对 5 条 bullet 没有违反 feedback 约束。

### 阶段 4 验收
- 简历 5 条 bullet 都有可验证数字
- 没有「降 90%」「拦截率 100%」之类注水
- 作品集详情页 CSV 链接可访问

---

## 风险与应对

| 风险 | 概率 | 应对 |
|------|-----|------|
| 阶段 1 Grafana 不可访问 | 中 | 尝试 116 暴露端口 / SSH 端口转发；若仍不可达，标记为 Blocker 上报 |
| 阶段 1 e3db51a 验证发现 sql_query 误路由 | 低 | **立即停止**阶段 4，回滚 e3db51a 或修工具描述 |
| 阶段 2 跨文件引用没全改 | 中 | 提交前 grep 一遍 `docs/benchmark-` `docs/deploy-` `docs/waf-403` 应为 0 命中 |
| 阶段 3 M5 50 次压测触发 OOM | 中 | 跑前 nvidia-smi 确认显存 ≥ 30%；中途 docker stats 监控；OOM 立即 sleep 60s |
| 阶段 3 发现 e2e bug 影响基准数字 | 低 | 按"通用约束 §4" — 不重跑基准，简历数字以 2026-04-25-final 冻结 |
| 阶段 4 简历写注水 | 中 | feedback_benchmark_narrative.md 是硬约束，对每条 bullet 自检一遍 |

---

## 检查表（用户可在阶段间快速核查）

阶段 1 完成后：
- [ ] `docs/benchmarks/report-2026-04-25-final.md`（迁移后路径）末尾有 §9 §10 章节
- [ ] §10 显示 5/5 命中预期工具
- [ ] Grafana 5 项数字有截图

阶段 2 完成后：
- [ ] `docs/` 根只剩 5 个 md
- [ ] `git log -1 --stat` 全为 rename
- [ ] CLAUDE.md 里 `docs/waf-403-...` 路径已更新

阶段 3 完成后：
- [ ] M1~M5 各 md 末尾"执行记录"已填
- [ ] 任何新 bug 在 changelog 有报告
- [ ] 数据库无 `[E2E TEST]` 残留

阶段 4 完成后：
- [ ] resume_v2.pdf 5 条 bullet 全部有可验证数字
- [ ] 作品集 CSV 链接可访问
- [ ] feedback_benchmark_narrative.md 自检通过
