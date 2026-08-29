import asyncio

import pytest

from sage.errors import MemoryIntegrityError
from sage.memory.models import FileSemanticPayload, FileStructure
from sage.memory.summarizer import ProviderSemanticSummarizer
from sage.providers.base import ProviderResult
from sage.providers.errors import ProviderErrorCategory, ProviderInvocationError


class _Provider:
    provider_name = "fake"
    model_name = "fake-v1"

    def __init__(self, *, fail: bool = False) -> None:
        self.fail = fail
        self.messages = None
        self.calls = 0

    async def invoke_structured(self, *, messages, **kwargs):
        self.calls += 1
        self.messages = messages
        if self.fail:
            raise ProviderInvocationError(
                ProviderErrorCategory.PROVIDER_5XX,
                provider=self.provider_name,
                model=self.model_name,
                retryable=True,
            )
        return ProviderResult(
            parsed=FileSemanticPayload(
                summary="Parses memory files",
                responsibilities=["parse files"],
            ),
            provider=self.provider_name,
            model=self.model_name,
            input_tokens=10,
            output_tokens=5,
            cached_tokens=0,
            latency_ms=1,
        )


def test_file_summarizer_receives_only_path_structure_and_committed_source() -> None:
    asyncio.run(_exercise_source_only_summarizer())


async def _exercise_source_only_summarizer() -> None:
    provider = _Provider()
    summarizer = ProviderSemanticSummarizer(
        provider, timeout_seconds=10, max_retries=1
    )
    payload = await summarizer.summarize_file(
        path="src/memory.py",
        source="def parse():\n    pass\n",
        structure=FileStructure(
            language="python",
            symbols=["parse"],
            parser_version="test",
            parse_status="parsed",
        ),
    )

    rendered = "\n".join(str(message.content) for message in provider.messages)
    assert payload.summary == "Parses memory files"
    assert "<untrusted-issue>" not in rendered
    assert "<saved-plan>" not in rendered
    assert "<current-diff>" not in rendered
    assert "<untrusted-committed-source>" in rendered
    assert summarizer.calls == 1
    assert summarizer.input_tokens == 10
    assert summarizer.output_tokens == 5
    assert summarizer.latency_ms == 1


def test_summarizer_exhaustion_is_a_safe_memory_failure() -> None:
    async def exercise() -> None:
        provider = _Provider(fail=True)
        summarizer = ProviderSemanticSummarizer(
            provider, timeout_seconds=10, max_retries=1
        )
        with pytest.raises(MemoryIntegrityError, match="failed safely"):
            await summarizer.summarize_file(
                path="src/memory.py",
                source="pass\n",
                structure=FileStructure(
                    language="python",
                    parser_version="test",
                    parse_status="parsed",
                ),
            )
        assert provider.calls == 2

    asyncio.run(exercise())
