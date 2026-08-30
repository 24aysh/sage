from sage.runtimes.v2.prompts import SOLVER_INSTRUCTIONS


def test_solver_instructions_use_memory_without_rediscovery() -> None:
    normalized = " ".join(SOLVER_INSTRUCTIONS.split())

    assert (
        "Treat source in a supplied memory context forest as already read" in normalized
    )
    assert "without re-reading or rediscovering those paths" in normalized
    assert "repository paths go in relevant_paths" in normalized
