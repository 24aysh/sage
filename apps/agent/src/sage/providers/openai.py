"""OpenAI structured provider adapter for the V2 Solver."""

from langchain_openai import ChatOpenAI

from sage.providers.base import LangChainStructuredProvider


class OpenAIProvider(LangChainStructuredProvider):
    """GPT adapter using Responses structured outputs and controller retries."""

    def __init__(
        self,
        *,
        api_key: str,
        model_name: str,
        timeout_seconds: int,
    ) -> None:
        model = ChatOpenAI(
            model=model_name,
            api_key=api_key,
            max_retries=0,
            timeout=float(timeout_seconds),
            use_responses_api=True,
            reasoning_effort="medium",
        )
        super().__init__(model=model, provider_name="openai", model_name=model_name)

    def _structured_runnable(self, schema):
        return self._model.with_structured_output(
            schema,
            method="json_schema",
            include_raw=True,
            strict=True,
        )
