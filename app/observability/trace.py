"""每个请求独立的轨迹，避免并发会话共享计数器。"""

from contextvars import ContextVar
from dataclasses import dataclass, field
from time import perf_counter

from app.schemas.common import TokenUsage


@dataclass
class Trace:
    calls: list[dict] = field(default_factory=list)
    backend_calls: list[dict] = field(default_factory=list)
    network_attempts: int = 0
    retrieval: list[dict] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    usage: TokenUsage = field(default_factory=TokenUsage)
    stop_reason: str = ""


current_trace: ContextVar[Trace | None] = ContextVar("trace", default=None)


class TrackedLLM:
    def __init__(self, provider):
        self.provider = provider

    async def generate(self, messages, **kwargs):
        trace = current_trace.get()
        started = perf_counter()
        event = {"stage": messages[0]["content"][:60], "backend": type(self.provider).__name__}
        try:
            response = await self.provider.generate(messages, **kwargs)
            event["model"] = response.model
            if trace:
                for key in ("prompt_tokens", "completion_tokens", "total_tokens"):
                    setattr(
                        trace.usage, key, getattr(trace.usage, key) + getattr(response.usage, key)
                    )
            return response
        except Exception as exc:
            event["error"] = type(exc).__name__
            if trace:
                trace.warnings.append("模型调用失败：" + type(exc).__name__)
            raise
        finally:
            event["ms"] = round((perf_counter() - started) * 1000, 2)
            if trace:
                trace.calls.append(event)

    async def ping(self):
        return await self.provider.ping()

    async def stream(self, messages, **kwargs):
        # 完整响应后输出，usage 由同一个 generate 包装器计数。
        response = await self.generate(messages, **kwargs)
        yield response.content

    async def aclose(self):
        closer = getattr(self.provider, "aclose", None)
        if closer:
            await closer()
