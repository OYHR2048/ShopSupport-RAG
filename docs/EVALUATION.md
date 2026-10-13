# 实际评估结果

本轮原文集共 26 题，8 dev、18 test。下表检索指标只计 16 道有答案测试题；证据决策计全部 18 题。小集与自身政策匹配度高，不能外推生产效果。

## 真实本地模型

backend：{"embedding": "bge-small-zh-v1.5", "embedding_model": "data/models/bge-small-zh-v1.5", "reranker": "cross-encoder", "reranker_model": "data/models/bge-reranker-base", "llm": "DemoLLMProvider", "generation_evaluated": false, "grader": "demo_lexical"}

| 路径 | P@1 | R@3 | 原文完整覆盖 | 充分性决策准确率 | 平均耗时 ms |
|---|---:|---:|---:|---:|---:|
| vector | 100.0% | 100.0% | 100.0% | - | 21.6 |
| hybrid | 100.0% | 100.0% | 100.0% | - | 20.4 |
| hybrid_rerank | 100.0% | 100.0% | 100.0% | - | 402.4 |
| fixed_single | 100.0% | 100.0% | 100.0% | 66.7% | 398.2 |
| adaptive | 100.0% | 100.0% | 100.0% | 72.2% | 1015.3 |

逐题完整证据、查询、检查、停止原因、调用、HTTP 尝试与 usage 见 [eval_test_local_models.json](../reports/eval_test_local_models.json)。

## 最终离线占位

backend：{"embedding": "hash", "embedding_model": "BAAI/bge-small-zh-v1.5", "reranker": "lexical", "reranker_model": "BAAI/bge-reranker-base", "llm": "DemoLLMProvider", "generation_evaluated": false, "grader": "demo_lexical"}

| 路径 | P@1 | R@3 | 原文完整覆盖 | 充分性决策准确率 | 平均耗时 ms |
|---|---:|---:|---:|---:|---:|
| vector | 93.8% | 96.9% | 93.8% | - | 3.3 |
| hybrid | 93.8% | 100.0% | 100.0% | - | 3.9 |
| hybrid_rerank | 100.0% | 100.0% | 100.0% | - | 4.1 |
| fixed_single | 100.0% | 100.0% | 100.0% | 66.7% | 4.2 |
| adaptive | 100.0% | 100.0% | 100.0% | 72.2% | 10.7 |

逐题完整证据、查询、检查、停止原因、调用、HTTP 尝试与 usage 见 [eval_test_demo_final.json](../reports/eval_test_demo_final.json)。

## 结论与边界

真实 BGE 与 Cross-Encoder 的本地推理已验证。三种检索路径在本小集均为 P@1/R@3 100%，没有观察到融合或重排的文档命中增益；Cross-Encoder 明显增加延迟。

固定单次与自适应的充分性判断仍使用显式词面裁判：66.7% → 72.2%，只改善 1 题且耗时增加。不能说真实语义 Self-RAG、生成或忠实度达到了这些指标。

未使用付费生成 API。真实生成、真实语义裁判、独立人工答案质量与更大/更难测试集仍待验证。无答案题、误拒答和裁判失败不能从检索 P@1 推断。

评估原文与清洗后的文本采用同一规范化，且核对指定文档来源，避免中文标点变换造成虚假的 coverage 失败。Recall 使用集合交集避免重复文档膨胀。

eval_dev_demo.json、eval_test_demo.json 是修复中文 Hash/标题索引前的开发记录；eval_test_demo_final.json 与本表对应。没有根据最终测试题逐条添加特殊规则。

reports/eval_dev_local_models.json 为调参集首次真实模型运行，旧 coverage 采用原文逐字匹配；最终测试报告已修正规范化口径并包含完整证据。

所有模型 API network_attempts 均为 0；模型下载是单独授权的下载步骤。时间为本机实测，不包括模型首次加载和索引构建。标注可以按 evidence_spans 人工复核，但没有声称完成独立人工标注审核。
