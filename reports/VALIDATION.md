# 验证范围与交付记录

- 全量 pytest：170 passed，原有 154 项测试保留且文件哈希未改；[JUnit](pytest.xml)。
- Ruff lint/format、mypy（131 个源码文件）、pip check、浏览器 JavaScript 语法检查通过。
- 默认离线聊天：五路工作流、政策原文引用、复杂检索检查、无答案拒答、订单物流追问、取消/退款确认、重复与并发确认、跨会话隔离及故障状态通过。
- 真实本地模型：BGE 中文向量与 Cross-Encoder 实际推理、独立 dev/test 检索评估通过；[模型 manifest](model_manifest.json)。
- 真实本地检索聊天链路：简单政策、复合政策、无答案三场景通过；[完整响应与轨迹](local_model_smoke.json)。实际生成为 DemoLLMProvider，裁判为词面检查，状态为 degraded。
- 浏览器实际点击：政策回答、来源入口、待确认取消动作、确认后返回 cancelled；[验收截图](browser_confirmation.png)。浏览器验收使用独立 browser_qa.db，默认 app.db 的订单数据未用于这些写操作。
- 未验证：付费生成模型、真实语义充分性/忠实度裁判、外部 PostgreSQL/Redis/Qdrant/HTTP MCP、真实支付退款物流、独立人工答案质量审核。
- 已授权安装基础与模型依赖；模型下载连接中断后续传完成。未发布、推送或部署，未改参考仓库。
- Windows 沙箱内 asyncio 本机 socketpair 阻塞，已批准在沙箱外执行离线测试和本地服务；未通过改弱断言掩盖环境问题。
- 只有一条 LangGraph 依赖弃用提醒；未宣称零警告。

首次原测试：153 passed / 1 failed，修复了演示模式挡住注入模型的工厂判断。后续强化政策条件断言暴露了标题缺失问题，已修复索引/重排文本。最后添加测试时遗漏一个 Settings 导入，修复后全量 170 项通过。早期结果作为开发记录保留，最终比较以 docs/EVALUATION.md 指定的报告为准。
