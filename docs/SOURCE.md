# 来源与许可

本项目从用户提供的本地 Adaptive-rag 仓库复制 app、tests、eval、knowledge、alembic、pyproject.toml、LICENSE 与必要配置后修改。参考目录保持不变，目标目录开始时为空。没有复制原仓库的大量教学/面试文档；本目录说明针对最终实现。

保留原 MIT LICENSE，其中原作者版权信息仍在。新增浏览器界面、确认动作存储、请求轨迹、评估集与回归测试在此基础上开发。不是独立从零复现，也没有继承原 README 的历史性能成绩。

学习联系参考用户已学的本地 all-in-rag 教程，未把其完整内容复制到交付目录。

本地模型来源：
- [BAAI/bge-small-zh-v1.5 官方模型卡](https://huggingface.co/BAAI/bge-small-zh-v1.5)
- [BAAI/bge-reranker-base 官方模型卡](https://huggingface.co/BAAI/bge-reranker-base)

两份官方模型卡标注 MIT；具体模型 revision 与文件清单在 reports/model_manifest.json。权重放在 data/models/，已加入 .gitignore。第三方 Python 包保留各自许可，安装版本记入 reports/environment.json。
