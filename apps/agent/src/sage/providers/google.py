"""Google Gemini structured provider adapter."""

from typing import Any

from langchain_google_genai import ChatGoogleGenerativeAI
from pydantic import BaseModel

from sage.providers.base import LangChainStructuredProvider

_GOOGLE_STRUCTURAL_SCHEMA_KEYS = frozenset(
    {
        "$defs",
        "$ref",
        "additionalProperties",
        "anyOf",
        "enum",
        "items",
        "oneOf",
        "prefixItems",
        "properties",
        "required",
        "type",
    }
)


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
            temperature=None,
            include_thoughts=False,
        )
        super().__init__(model=model, provider_name="google", model_name=model_name)

    def _structured_runnable(self, schema: type[BaseModel]) -> Any:
        return self._model.with_structured_output(
            _google_wire_schema(schema),
            method="json_schema",
            include_raw=True,
        )


def _google_wire_schema(schema: type[BaseModel]) -> dict[str, Any]:
    """Return a compact Gemini-compatible schema for provider-side structure."""

    return _filter_schema_node(schema.model_json_schema())


def _filter_schema_node(node: dict[str, Any]) -> dict[str, Any]:
    filtered: dict[str, Any] = {}
    for key, value in node.items():
        if key == "const":
            filtered["enum"] = [value]
        elif key not in _GOOGLE_STRUCTURAL_SCHEMA_KEYS:
            continue
        elif key in {"$defs", "properties"}:
            filtered[key] = {
                name: _filter_schema_node(child) for name, child in value.items()
            }
        elif key == "items" and isinstance(value, dict):
            filtered[key] = _filter_schema_node(value)
        elif key in {"anyOf", "oneOf", "prefixItems"}:
            filtered[key] = [_filter_schema_node(child) for child in value]
        else:
            filtered[key] = value
    return filtered
