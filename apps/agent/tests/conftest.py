from pathlib import Path

import pytest

from sage.config import Settings


@pytest.fixture
def settings(tmp_path: Path) -> Settings:
    return Settings(
        openai_api_key="test-key",
        runs_dir=tmp_path / "runs",
        command_timeout_seconds=10,
        max_tool_output_chars=1_000,
    )
