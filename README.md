# ShopSupport-RAG｜电商客服知识检索与问答

基于本地 Adaptive-rag 源码开发的电商客服项目，保留五路自适应工作流。新增浏览器界面连接实际 FastAPI 后端，可查看引用、检索过程和模拟操作确认。

**当前交付可运行。170 项测试通过，真实中文 BGE 与 Cross-Encoder 已完成本地检索评估。生成模型、语义充分性与忠实度裁判本轮未调用付费 API，相关流程通过模拟/故障注入验证。**

## 能做什么

- Markdown 加载、清洗、分块、相对路径文档 ID、标题/小节索引、政策版本与来源管理；PDF/Office 解析为可选入口。
- 中文向量 + jieba/BM25、RRF 融合、真实 Cross-Encoder；后备模式明确显示 Hash 与词面重排。
- DIRECT / SINGLE / MULTI / AGENT / REFUSE 路由；查询改写、原问题证据检查、有限检索重试与停止原因。
- 回答引用、忠实度检查、一次修正与拒答；离线演示采用原文摘录，不伪装成生成式模型。
- SQLite 模拟订单、物流、取消和退款/退货申请；写操作保存具体动作后要求确认，重复确认幂等。
- 连续对话、摘要加载、上下文预算与分级压缩；请求独立日志、计数与耗时。
- **本次新增浏览器聊天界面**：输入/追问、来源、展开轨迹、确认按钮和实际运行模式。

能力来源、修复与验证清单见 [功能检查](docs/FEATURE_AUDIT.md)。

## 快速启动

在项目根目录打开 PowerShell，使用 Python 3.11+。首次使用时创建环境并安装依赖：

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -e ".[dev]"
Copy-Item .env.example .env   # 仅首次使用；已有 .env 时不要覆盖
.\.venv\Scripts\python.exe -m uvicorn app.asgi:app --host 127.0.0.1 --port 8000
```

打开 [http://127.0.0.1:8000](http://127.0.0.1:8000)，API 文档在 [/docs](http://127.0.0.1:8000/docs)。

默认只需要 SQLite 和本地文件，无需 PostgreSQL、Redis、Qdrant 或 Docker。启动会创建演示订单和向量快照。数据库在 data/local/app.db；向量每次启动按当前知识库与 backend 全量重建，快照在 data/local/vectors.json。

如果模拟订单已经取消，希望重做演示，可运行 `scripts/start.ps1 -FreshDemo`，它选用新的数据库文件并保留旧数据；也可自行把 DATABASE_URL 设置为新文件。基础版请只启动一个 worker。

## 演示问题

1. 你好
2. 七天无理由退货需要满足什么条件？
3. 定制马克杯过了7天但是有质量问题，还能退吗，运费谁承担？
4. 支持火星配送吗？
5. 查一下订单 ORD10001 → 它的物流到哪了？
6. 帮我取消订单 ORD10003 → 点击确认模拟操作
7. 给 ORD10002 申请退款，原因是质量问题

演示用户为 u_demo：ORD10001 是已发货耳机，ORD10002 是已签收定制杯，ORD10003 是未发货 T 恤。所有政策均标注为虚构演示，所有业务操作仅写本地模拟数据库，退款申请成功不代表付款或到账。

## 模型配置与状态

RUNTIME_MODE=demo 默认关闭真实生成和模型下载，使用规则、中文特征哈希、BM25、词面重排与原文摘录。Hash、FakeLLM、词面重排仅用于演示/测试，不能称为真实语义 RAG。

本轮授权下载的两个模型位于 data/models/，revision 记录在 reports/model_manifest.json。要使用真实本地检索，将 configs/local-model.env.example 的配置合并到 .env。下载权重与 SDK 不会随 Git 上传；在新机器上需主动安装并下载：

```powershell
.\.venv\Scripts\python.exe -m pip install -e ".[rerank]"
.\.venv\Scripts\python.exe scripts/download_models.py
```

下载脚本只下载 BAAI/bge-small-zh-v1.5、BAAI/bge-reranker-base 的 safetensors 与配置，不执行远程自定义代码；服务默认 MODEL_DOWNLOAD_ALLOWED=false。

生成模型通过 DEEPSEEK_API_KEY、DEEPSEEK_BASE_URL、DEEPSEEK_MODEL 提供，必须显式 RUNTIME_MODE=model。支持兼容的 embedding API，字段见 .env.example。密钥只放环境变量/.env，禁止加入源码、文档或日志。

界面中的模式来自实际 backend：演示模式是规则/摘录；真实模型模式要求生成、向量和 Cross-Encoder 均实际配置/加载；任一后备或请求失败显示降级原因。本地真实检索搭配演示生成时显示降级模式。readiness 只报告配置与本地依赖，不通过付费请求证明 API 可用。

## 测试与评估

```powershell
.\.venv\Scripts\python.exe -m pytest -q
.\.venv\Scripts\python.exe -m ruff check app eval tests scripts alembic
.\.venv\Scripts\python.exe -m ruff format --check app eval tests scripts alembic
.\.venv\Scripts\python.exe -m mypy app eval
.\.venv\Scripts\python.exe -m eval.compare --mode demo --split dev --output reports/eval_dev_demo.json
.\.venv\Scripts\python.exe -m eval.compare --mode demo --split test --output reports/eval_test_demo.json
.\.venv\Scripts\python.exe -m eval.compare --mode model --local-models --require-real --split test --output reports/eval_test_local_models.json
```

reviewed.jsonl 共 26 题，8 dev 调参、18 test 最终测试，每题给出可核对的原文证据。报告保存参数、实际 backend、完整检索片段、耗时、逻辑模型调用、HTTP 尝试与 token usage。相关指标按去重后的文档计算，无答案题单独检验充分性决策。

默认评估不调用生成 API；即使使用真实 BGE/重排，自适应裁判仍明确标为 demo_lexical。若自行启用 `--allow-paid`，会使用已配置模型做改写与裁判，费用由 API 提供方计费；先在 dev 用 `--limit 2` 检查配置和范围。此脚本不评估生成正确率。旧 eval.runner 和历史集保留作来源参考，不作为本轮结论。

当前真实本地检索在 16 道有答案测试题上的 P@1 / R@3 为 100%；纯向量、混合、Cross-Encoder 在该小集上相同，不能证明重排增益。固定/自适应词面证据决策分别为 66.7% / 72.2%，不能当作真实语义裁判成绩。详见 [实际结果与限制](docs/EVALUATION.md)，不要继承原仓库历史 README 的指标。

## 代码与设计阅读

- [架构和请求数据流](docs/ARCHITECTURE.md)
- [核心代码阅读顺序](docs/READING_ORDER.md)
- [改进记录](docs/CHANGES.md)
- [评估口径](eval/ANNOTATION.md)
- [来源与许可](docs/SOURCE.md)

## 已知限制

- user_id 隔离不是登录认证
- 同会话锁只保证单进程，不能直接多 worker 部署
- 向量快照每次启动重建，无增量同步
- 上下文预算为启发式估计，历史消息数据库不做自动删除
- 演示词面检查会误拒答，语义充分性、生成与忠实度仍需真实 API + 更大独立人工集验证。
