import asyncio

from sage.memory.engine import FailedMemoryEngine
from sage.memory.models import MemoryMode, MemoryRunRequest, RepositoryIdentity


class _Repository:
    def list_tree(self, *, path: str, max_depth: int) -> str:
        return f"{path}:{max_depth}"

    def read_file(self, *, path: str, start_line: int = 1, end_line=None) -> str:
        return path


def test_composition_failure_becomes_solve_local_fallback(tmp_path) -> None:
    asyncio.run(_exercise_composition_failure(tmp_path))


async def _exercise_composition_failure(tmp_path) -> None:
    engine = FailedMemoryEngine(_Repository(), ValueError("contains a secret"))
    session = await engine.begin(
        MemoryRunRequest(
            identity=RepositoryIdentity(
                namespace_kind="local", namespace_key="repo", display_name="repo"
            ),
            run_id="run-1",
            target_commit="a" * 40,
            workspace_path=tmp_path,
        )
    )

    assert session.mode is MemoryMode.FALLBACK
    assert await session.list_tree(path=".", max_depth=2) == ".:2"
    report = await session.finalize("completed")
    assert report.failure is not None
    assert "secret" not in report.model_dump_json()
