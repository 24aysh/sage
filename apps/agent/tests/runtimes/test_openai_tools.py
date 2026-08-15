from pathlib import Path

from issue_agent.config import Settings
from issue_agent.domain.requests import PreparedRun
from issue_agent.domain.runtime import RuntimeContext
from issue_agent.runtimes.openai_agents.tools import build_tools


def test_build_tools_exposes_only_v0_repository_capabilities(tmp_path: Path) -> None:
    prepared = PreparedRun(
        run_id="run-id",
        source_repo=tmp_path,
        run_dir=tmp_path,
        workspace_dir=tmp_path,
        base_ref="HEAD",
        base_sha="a" * 40,
    )
    context = RuntimeContext(
        prepared_run=prepared,
        sandbox=object(),
        repository=object(),
        settings=Settings(openai_api_key="test"),
    )

    tools = build_tools(context)

    assert {tool.name for tool in tools} == {
        "list_tree",
        "search_text",
        "read_file",
        "apply_patch",
        "show_diff",
        "run_command",
    }
