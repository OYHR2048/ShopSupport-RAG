"""离线演示：规则路由 + 原文摘录；不是生成式模型或语义 RAG 验证。"""

from app.providers.fake import FakeLLMProvider


class DemoLLMProvider(FakeLLMProvider):
    async def generate(self, messages, **kwargs):
        # 故意输出无法解析的占位标记，由各模块使用显式规则/摘录后备路径。
        from app.providers.base import LLMResponse

        self.calls.append(messages)
        return LLMResponse(content="DEMO_OFFLINE", model="demo-rules-extractive")
