# 03 RAG 质量

## 1. 验证范围

生产配置下验证完整管道：

```text
知识文档加载与切分
  → Embedding
  → Milvus/FAISS 向量检索 + BM25
  → 可选 A-RAG 多步导航
  → 上下文构建
  → LLM 回答与来源
```

消融实验用于解释方案收益；上线门禁只评估即将上线的真实配置。

## 2. 数据集要求

- 使用生产规模或生产等价知识库，不得只用 4 篇小库。
- 最终盲测问题不少于 200 条。
- 问题不得直接复制文档原句。
- 必须包含直接事实、同义改写、专有名词、口语、错别字、多约束、多跳和无答案问题。
- 每条标注相关文档、相关 chunk、必须回答的事实点和是否应拒答。
- 加入主题相似但答案不同的困难负样本。

## 3. 检索指标

| 指标 | 门槛 |
|---|---:|
| Recall@5 | `>= 90%` |
| Recall@10 | `>= 95%` |
| 无答案错误召回率 | `< 5%` |
| 文档加载成功率 | `100%`，不支持格式必须显式报告 |
| 多跳问题所需文档覆盖率 | `>= 90%` |

同时记录 Precision@5、MRR@10、nDCG@10、检索延迟 P50/P95 和各检索后端贡献。

## 4. 回答指标

| 指标 | 门槛 |
|---|---:|
| 事实正确率 | `>= 95%` |
| 答案要点覆盖率 | `>= 90%` |
| Groundedness/忠实度 | `>= 95%` |
| 引用来源正确率 | `>= 95%` |
| 无答案正确拒答率 | `>= 95%` |
| 关键制度类幻觉 | `0` |

LLM Judge 只能辅助评分。最终盲测必须人工复核全部失败案例，并随机抽检至少 20% 的通过案例。

## 5. A-RAG 与循环安全

如果生产启用渐进式知识导航，额外统计：

- 简单问题进入多步路径的误触发率。
- 多跳问题的 A-RAG 触发率和完成率。
- 平均、P95 工具调用轮数。
- 重复相同工具和参数的比例。
- 最大迭代撞顶率。
- 撞顶后正常汇总或明确失败的比例。
- 相比 one-shot 的答案完整度、token 和延迟变化。

要求死循环为 0，重复调用检测和 `max_iterations` 兜底必须有效。模型不同会显著改变工具选择，因此必须使用生产模型重新测试。

## 6. 故障降级

- Embedding 服务不可用：初始化或查询必须明确失败，不能静默给出伪知识答案。
- Milvus 不可用：验证是否按设计降级到 FAISS，并记录降级事件。
- BM25 初始化失败：向量检索仍可工作且有告警。
- 生成 LLM 超时：返回明确错误，不丢失 SSE `done/error`。
- 无检索结果：明确说明知识库无答案，不使用模型常识补造制度。

## 7. 必跑现有测试

```powershell
cd fastapi-service
..\.venv\Scripts\python.exe -m pytest `
  tests/test_langchain_document_loader.py `
  tests/test_langchain_rag_retrieval.py `
  tests/test_knowledge_qa_rag_strategy.py `
  tests/integration/test_progressive_rag.py `
  tests/unit/test_kb_navigator.py `
  tests/unit/test_kb_tools.py -v
```

专项评测方案沿用：`../../rag_hybrid_retrieval_experiment_validation/`。
