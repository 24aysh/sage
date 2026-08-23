from __future__ import annotations

import json
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from sage.providers.google import GoogleProvider, _google_wire_schema


class NestedResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    identifier: str = Field(min_length=1, max_length=40, pattern=r"^[a-z]+$")


class StructuredResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    nested: NestedResult | None = None
    route: Literal["single"] = "single"
    values: tuple[str, ...] = Field(default=(), min_length=1, max_length=3)


class RecordingModel:
    def __init__(self) -> None:
        self.calls: list[tuple[object, dict[str, object]]] = []

    def with_structured_output(self, schema: object, **kwargs: object) -> str:
        self.calls.append((schema, kwargs))
        return "structured-runnable"


def test_google_wire_schema_keeps_structure_and_removes_local_constraints() -> None:
    wire_schema = _google_wire_schema(StructuredResult)
    serialized = json.dumps(wire_schema)

    assert wire_schema["properties"]["route"]["enum"] == ["single"]
    assert wire_schema["properties"]["nested"]["anyOf"][1] == {"type": "null"}
    assert "$defs" in wire_schema
    for unsupported in (
        "const",
        "default",
        "description",
        "maxItems",
        "maxLength",
        "minItems",
        "minLength",
        "pattern",
        "title",
    ):
        assert f'"{unsupported}"' not in serialized


def test_google_provider_uses_default_sampling_and_compact_json_schema() -> None:
    provider = GoogleProvider(
        api_key="test-key",
        model_name="gemini-3.5-flash",
        timeout_seconds=30,
    )
    assert provider._model.temperature is None

    recording_model = RecordingModel()
    provider._model = recording_model  # type: ignore[assignment]

    assert provider._structured_runnable(StructuredResult) == "structured-runnable"
    assert recording_model.calls == [
        (
            _google_wire_schema(StructuredResult),
            {"method": "json_schema", "include_raw": True},
        )
    ]
