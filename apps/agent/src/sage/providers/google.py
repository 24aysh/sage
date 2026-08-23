"""Google Gemini structured provider adapter."""

from langchain_google_genai import ChatGoogleGenerativeAI

from sage.providers.base import LangChainStructuredProvider


class GoogleProvider(LangChainStructuredProvider):
    """Gemini adapter with provider-native retries disabled."""

    def __init__(
        self,
        *,
        api_key: str,
        model_name: str,
        timeout_seconds: int,
    ) -> None:
        model = ChatGoogleGenerativeAI(
            model=model_name,
            api_key=api_key,
            retries=0,
            request_timeout=float(timeout_seconds),
            temperature=0,
            include_thoughts=False,
        )
        super().__init__(model=model, provider_name="google", model_name=model_name)
