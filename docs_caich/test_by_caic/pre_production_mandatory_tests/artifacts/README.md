# 测试证据目录

本目录存放本次候选发布版本生成的脱敏证据，例如：

- 任务完成率逐条结果和汇总。
- Function Calling 原始评测结果。
- RAG 检索与回答评分结果。
- 安全攻击、权限和故障注入结果。
- Locust HTML/CSV 报告。
- Prometheus/Grafana 截图或导出数据。
- 最终 `GO/NO-GO` 报告。

不得提交 API Key、JWT、Authorization Header、生产用户隐私或未脱敏业务数据。

建议文件名统一包含版本、模型和日期：

```text
<test-name>_<release-version>_<model>_<YYYY-MM-DD>.<ext>
```
