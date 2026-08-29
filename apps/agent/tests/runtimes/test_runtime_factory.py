from sage.config import Settings
from sage.runtimes.factory import build_runtime
from sage.runtimes.v2 import V2GraphRuntime


def test_factory_builds_the_only_v2_runtime() -> None:
    settings = Settings(
        runtime="v2",
        openai_api_key="openai-test",
        gemini_api_key="gemini-test",
    )

    assert isinstance(build_runtime(settings), V2GraphRuntime)
