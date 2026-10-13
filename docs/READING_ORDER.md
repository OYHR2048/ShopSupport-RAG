# 核心代码阅读顺序

先运行浏览器，问“你好”“七天无理由退货需要满足什么条件？”和复合定制退货问题，展开过程，再按顺序阅读：

1. app/schemas/chat.py、agents/state.py、core/enums.py：理解一次请求携带的状态、路由枚举和响应。
2. app/services/chat_service.py：从 chat 到初始会话、图执行、确认事务、响应和持久化。这是全项目入口。
3. app/agents/graph.py：沿条件边追踪 DIRECT、SINGLE、MULTI、AGENT、REFUSE。
4. app/retrieval/ingest.py → chunking.py → bm25.py / vector.py → fusion.py → reranker.py：对应 all-in-rag 的索引构建与检索进阶，特别看标题是否参与检索、ID 为什么使用相对路径。
5. app/retrieval/self_rag.py、agents/nodes/retrieval.py：在普通 RAG 上增加证据充分性、改写、证据积累和有限停止。用轨迹核对实际轮数。
6. app/agents/nodes/generate.py、faithfulness.py、guardrails/hallucination.py：区分回答是否被证据支持、是否已经覆盖问题。无答案和裁判故障都不能假装成功。
7. app/agents/nodes/planner.py、executor.py、repositories/actions.py、idempotency.py：理解为什么“需要确认”不是一个布尔输入，而是对服务端保存的具体动作授权。
8. app/memory/compression.py、agents/nodes/memory.py、repositories/session_repository.py：联系 all-in-rag 的上下文组织；看摘要如何加载和预算如何包含工具结果。
9. app/observability/trace.py、eval/compare.py、tests/e2e/test_demo_project.py：学会用实际调用、耗时、引用和失败路径支撑设计说明。
10. app/static/chat.js：界面如何显示后端状态，以及确认按钮如何发送 action_id。

面试讲解可选一条真实路径：问题为何路由到 MULTI → 检索了哪些查询 → 每轮证据是什么 → 为什么停止 → 最终回答引用哪些片段。最后展示一个失败例与未观察到增益的评估结果，比背模块名称更能说明你理解实现。
