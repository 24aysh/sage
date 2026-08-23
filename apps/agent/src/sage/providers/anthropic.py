"""Anthropic Claude structured provider adapter."""

from langchain_anthropic import ChatAnthropic

from sage.providers.base import LangChainStructuredProvider


class AnthropicProvider(LangChainStructuredProvider):
    """Claude adapter with project-owned retry/fallback policy."""

    def __init__(
        self,
        *,
        api_key: str,
        model_name: str,
        timeout_seconds: int,
    ) -> None:
        model = ChatAnthropic(
            model_name=model_name,
            api_key=api_key,
            max_retries=0,
            timeout=float(timeout_seconds),
            temperature=0,
        )
        super().__init__(model=model, provider_name="anthropic", model_name=model_name)
