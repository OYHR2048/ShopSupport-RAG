# ShopSupport-RAG

> 面向电商客服场景的自适应 RAG 系统，支持政策问答、来源引用、多轮检索与业务工具调用。

ShopSupport-RAG 实现了一套完整的中文 RAG Pipeline，支持 **文档处理、Dense Retrieval、BM25、RRF 混合检索、Cross-Encoder Reranking 与离线评测**。

在此基础上，项目进一步通过 **LangGraph** 编排问题路由、查询改写、证据检查与回答校验，并接入订单、物流和售后工具。用户可以在浏览器中连续提问，查看回答来源与执行过程，对取消订单、申请退款等操作进行确认。

---

## ✨ 核心能力

- **知识库处理**：Markdown 加载、清洗、去重、按标题分块，保留来源与政策版本。
- **中文混合检索**：BGE 向量检索 + jieba/BM25，通过 RRF 融合候选结果。
- **二阶段重排**：使用 Cross-Encoder 对候选片段重新排序。
- **自适应路由**：根据问题选择直接回答、单轮检索、多轮检索、工具调用或拒答。
- **检索反馈循环**：查询改写、证据充分性检查、有限重试与明确的停止原因。
- **回答与引用**：回答关联实际检索片段，支持忠实度检查、一次修正与拒答。
- **业务工具**：订单查询、物流跟踪、取消订单、退款与退货申请。
- **操作确认**：写操作保存为待确认动作，校验用户与会话归属，重复确认返回已保存结果。
- **连续对话**：历史消息、会话摘要、上下文预算与分级压缩。
- **浏览器界面**：聊天、追问、来源查看、检索轨迹与操作确认。
- **运行观测**：记录实际模型后端、检索轮次、耗时、模型调用与 HTTP 尝试次数。
- **离线评测**：对比纯向量、混合检索、重排、固定单轮与自适应检索。

---

## 🏗️ 系统架构

### RAG Pipeline

```text
Markdown 政策文件
        ↓
读取 / 清洗 / 去重 / 按标题分块
        ↓
标题 + 小节 + 正文
        ↓
┌─────────────────────────────┐
│ BGE Dense Retrieval         │
│            +                │
│ 中文 BM25 Retrieval         │
│            ↓                │
│        RRF Fusion           │
└──────────────┬──────────────┘
               ↓
         Candidate Pool
               ↓
      Cross-Encoder Reranker
               ↓
          Final Evidence
               ↓
       证据充分性检查
         │           │
       充分         不充分
         │           ↓
         │     改写 / 补充检索
         │       或停止拒答
         ↓
     回答生成 + 来源引用
               ↓
       忠实度检查 / 修正
               ↓
       Answer + Sources
```

检索和校验围绕原问题进行。多轮检索按 `chunk_id` 累积去重，证据充分时提前停止；达到轮次上限或发生裁判故障时，记录停止原因。

### 自适应工作流

```text
浏览器 / CLI
      ↓
FastAPI
      ↓
ChatService
会话管理 / 历史加载 / 请求轨迹
      ↓
LangGraph
护栏 → 意图识别 → 策略路由
      │
      ├── DIRECT → 直接回答
      │
      ├── SINGLE → 单轮检索 + 证据检查
      │
      ├── MULTI  → 多轮检索 + 反馈循环
      │
      ├── AGENT  → 任务规划 + 业务工具
      │
      └── REFUSE → 拒答
      ↓
回答校验 / 上下文压缩
      ↓
响应与会话持久化
```

| 策略 | 适用场景 | 示例 |
|---|---|---|
| `DIRECT` | 问候等直接回复 | 你好 |
| `SINGLE` | 简单政策问题 | 七天无理由退货需要满足什么条件？ |
| `MULTI` | 多条件、跨政策问题 | 定制商品过了七天出现质量问题，还能退吗，运费谁承担？ |
| `AGENT` | 需要访问业务数据或执行操作 | 查询订单物流、取消订单、申请退款 |
| `REFUSE` | 护栏拦截或无法可靠回答 | 超出支持范围的问题 |

简单问题使用较短路径，复杂问题进入检索反馈循环；订单与售后请求通过工具访问业务数据。

详细数据流见 [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md)。

---

## 📊 Benchmark

评测集包含 **26 条政策问题**，划分为 **8 条开发集、18 条测试集**。

下表使用真实本地模型：

- Embedding：`BAAI/bge-small-zh-v1.5`
- Reranker：`BAAI/bge-reranker-base`
- 证据充分性检查：`demo_lexical` 词面裁判

**检索指标统计 16 道有答案测试题，按去重后的文档计算；证据决策准确率统计全部 18 道测试题。**

| 方法 | P@1 | R@3 | 证据决策准确率 | Mean Latency |
|---|---:|---:|---:|---:|
| Dense | 100.0% | 100.0% | — | 21.6 ms |
| Hybrid RRF | 100.0% | 100.0% | — | 20.4 ms |
| Hybrid RRF + Reranker | 100.0% | 100.0% | — | 402.4 ms |
| Fixed Single | 100.0% | 100.0% | 66.7% | 398.2 ms |
| Adaptive | 100.0% | 100.0% | 72.2% | 1015.3 ms |

主要结论：

- 真实 BGE 与 Cross-Encoder 的本地推理链路已验证。
- 当前测试集较小，三种检索方案的文档命中指标相同，尚未观察到融合或重排的命中增益。
- 自适应策略的词面证据决策准确率由 **66.7% 提升至 72.2%**，对应多判断正确 1 道题，同时增加了耗时。
- CPU Cross-Encoder 显著增加检索延迟，需要结合问题难度与收益选择检索路径。

本次评测未调用付费生成 API，**上述成绩不代表生成答案正确率，也不代表真实语义裁判的准确率**。耗时为本机实测，不包含模型首次加载与索引构建。

完整评测口径与逐题结果：

- [`docs/EVALUATION.md`](docs/EVALUATION.md)
- [`reports/eval_test_local_models.json`](reports/eval_test_local_models.json)

---

## 🚀 快速开始

### 安装

要求 **Python 3.11+**。以下命令使用 Windows PowerShell：

```powershell
git clone <your-repository-url> adaptive-rag
cd adaptive-rag

python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -e ".[dev]"
```

首次配置环境变量：

```powershell
Copy-Item .env.example .env
```

已有 `.env` 时保留自己的配置。

默认采用本地演示模式，使用 **SQLite + 本地向量快照**，无需启动 PostgreSQL、Redis、Qdrant 或 Docker，也不会自动调用付费模型或下载权重。

### 启动服务

```powershell
.\.venv\Scripts\python.exe -m uvicorn app.asgi:app --host 127.0.0.1 --port 8000
```

启动时自动加载知识库、构建索引并初始化演示订单。

- 聊天界面：[http://127.0.0.1:8000](http://127.0.0.1:8000)
- API 文档：[http://127.0.0.1:8000/docs](http://127.0.0.1:8000/docs)

本地数据位置：

```text
knowledge/                 Markdown 政策知识库
data/local/app.db          会话与模拟业务数据库
data/local/vectors.json    向量索引快照
```

当前版本在每次启动时全量重建向量索引，请使用单个服务 worker。

### 开始对话

可以按以下顺序体验：

| 场景 | 输入 |
|---|---|
| 简单政策问答 | 七天无理由退货需要满足什么条件？ |
| 复合政策问答 | 定制马克杯过了7天但是有质量问题，还能退吗，运费谁承担？ |
| 无答案问题 | 支持火星配送吗？ |
| 订单查询 | 查一下订单 ORD10001 |
| 连续追问 | 它的物流到哪了？ |
| 待确认操作 | 帮我取消订单 ORD10003 |
| 售后申请 | 给 ORD10002 申请退款，原因是质量问题 |

默认演示用户为 `u_demo`：

| 订单 | 商品 | 初始状态 |
|---|---|---|
| `ORD10001` | 耳机 | 已发货 |
| `ORD10002` | 定制马克杯 | 已签收 |
| `ORD10003` | T 恤 | 已付款、未发货 |

政策与业务数据均为虚构演示数据；取消、退款与退货操作仅写入本地数据库。

如果需要重新演示订单操作，可以使用新的数据库启动：

```powershell
.\scripts\start.ps1 -FreshDemo
```

该命令保留原数据库。

### 终端对话

服务启动后，在另一个终端运行：

```powershell
.\.venv\Scripts\python.exe -m app.cli
```

---

## 🧠 模型配置

### 默认演示模式

```dotenv
RUNTIME_MODE=demo
```

使用规则路由、中文特征哈希向量、BM25、词面重排与原文摘录，便于在没有模型密钥的情况下体验完整流程。

### 真实本地检索

安装模型依赖并下载权重：

```powershell
.\.venv\Scripts\python.exe -m pip install -e ".[rerank]"
.\.venv\Scripts\python.exe scripts/download_models.py
```

下载模型：

```text
BAAI/bge-small-zh-v1.5
BAAI/bge-reranker-base
```

将 [`configs/local-model.env.example`](configs/local-model.env.example) 中的配置合并到 `.env`，设置本地模型路径。

服务默认 `MODEL_DOWNLOAD_ALLOWED=false`，启动时使用已下载的权重。模型文件保存在 `data/models/`，不随 Git 提交。

### 真实生成模型

在本地检索配置基础上，配置 DeepSeek API：

```dotenv
RUNTIME_MODE=model
DEEPSEEK_API_KEY=your-api-key
DEEPSEEK_BASE_URL=https://api.deepseek.com
DEEPSEEK_MODEL=deepseek-chat
```

密钥保存在 `.env` 或环境变量中。

界面根据实际后端显示 **演示模式、真实模型模式或降级模式**。如果使用真实检索但仍采用演示生成，界面会显示降级状态；真实生成与语义裁判仍需进一步验证。

---

## 🔧 业务工具与操作确认

业务工具通过统一注册表组织，供工作流规划与执行节点调用。

| Tool | 功能 |
|---|---|
| `get_order` | 查询订单信息 |
| `get_order_items` | 查询订单商品明细 |
| `track_shipment` | 查询物流状态 |
| `cancel_order` | 取消符合条件的订单 |
| `create_refund_request` | 创建退款申请 |
| `create_return_request` | 创建退货申请 |
| `get_refund_status` | 查询退款状态 |

写操作采用确认流程：

```text
用户提出操作请求
        ↓
规划工具与具体参数
        ↓
服务端保存待确认动作
        ↓
界面展示操作内容
        ↓
用户确认
        ↓
校验动作归属与有效期
        ↓
执行操作并保存幂等结果
        ↓
返回业务执行结果
```

确认对应服务端保存的具体动作，确认消息不能替换原操作参数。重复确认返回已保存结果，避免重复执行业务写入。

项目还保留了本地 MCP 演示工具与 HTTP 适配入口，默认关闭；远程 MCP 集成尚未验证。

---

## 📁 项目结构

```text
adaptive-rag/
├── app/
│   ├── agents/          # LangGraph 工作流与节点
│   ├── api/             # 聊天、状态与来源接口
│   ├── cli/             # 终端连续对话
│   ├── config/          # 环境配置
│   ├── memory/          # 上下文预算与摘要压缩
│   ├── observability/   # 请求轨迹、日志与指标
│   ├── providers/       # LLM、Embedding 与模型后端
│   ├── repositories/    # 数据访问与持久化
│   ├── retrieval/       # 文档处理、检索、重排与反馈循环
│   ├── services/        # 依赖组装与聊天服务
│   ├── static/          # 浏览器聊天界面
│   └── tools/           # 订单、物流、售后等业务工具
├── knowledge/           # Markdown 政策知识库
├── configs/             # 本地模型配置示例
├── eval/                # 评测集与方案对比
├── reports/             # 评测结果与验证记录
├── scripts/             # 启动与模型下载脚本
├── tests/               # 单元、集成与端到端测试
├── docs/                # 架构、阅读顺序与评测说明
├── alembic/             # 数据库迁移
├── .env.example
├── pyproject.toml
├── LICENSE
└── README.md
```

建议从以下文档开始阅读：

- [架构与请求数据流](docs/ARCHITECTURE.md)
- [核心代码阅读顺序](docs/READING_ORDER.md)
- [功能检查与验证范围](docs/FEATURE_AUDIT.md)
- [评测结果与指标口径](docs/EVALUATION.md)
- [改进记录](docs/CHANGES.md)

---

## 🧪 测试与评测

### 自动化测试

```powershell
.\.venv\Scripts\python.exe -m pytest -q
```

仓库保存的最近一次完整测试结果：

```text
170 passed
```

覆盖政策检索、来源引用、自适应工作流、订单物流追问、操作确认、重复与并发确认、会话隔离及故障处理。

验证记录见 [`reports/VALIDATION.md`](reports/VALIDATION.md)。

### 代码检查

```powershell
.\.venv\Scripts\python.exe -m ruff check app eval tests scripts alembic
.\.venv\Scripts\python.exe -m ruff format --check app eval tests scripts alembic
.\.venv\Scripts\python.exe -m mypy app eval
```

### 离线评测

演示模式：

```powershell
.\.venv\Scripts\python.exe -m eval.compare --mode demo --split test --output reports/eval_test_demo.json
```

真实本地 Embedding 与 Reranker：

```powershell
.\.venv\Scripts\python.exe -m eval.compare --mode model --local-models --require-real --split test --output reports/eval_test_local_models.json
```

默认评测不调用付费生成 API。报告保存实际模型后端、检索证据、停止原因、耗时与调用统计，便于逐题复核。

---

## ⚠️ 已知限制

- 当前评测集规模较小，检索结果不能外推为真实用户场景下的答案质量。
- 真实本地检索已验证；真实生成、语义充分性与忠实度裁判尚需独立评测。
- CPU Cross-Encoder 会显著增加延迟。
- 索引采用启动时全量重建，尚未实现完整增量更新。
- 上下文预算使用启发式估计，短追问消歧依赖规则。
- 用户 ID 隔离尚未配套完整登录认证；会话锁目前适用于单进程运行。
- SSE 在完成回答检查后输出最终答案，尚未提供逐 token 的可见生成流。
- PostgreSQL、Redis、Qdrant 与远程 MCP 为可选扩展，本轮未验证外部服务集成。
- 订单、物流与售后均使用模拟业务数据，未连接真实交易系统。

---

## 📄 来源与许可

本项目基于 [JEen-wang/Adaptive-rag](https://github.com/JEen-wang/Adaptive-rag) 二次开发，保留原项目的 **MIT License** 与作者版权信息。

在原实现基础上，增加了浏览器聊天界面，并完善了待确认动作持久化、请求轨迹、评测集与回归测试。

来源说明见 [`docs/SOURCE.md`](docs/SOURCE.md)。
