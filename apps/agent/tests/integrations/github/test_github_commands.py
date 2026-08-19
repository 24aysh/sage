import pytest

from sage.integrations.github.commands import SageCommand, parse_command


@pytest.mark.parametrize("body", ["/sage solve", "/sage fix"])
def test_parse_command_accepts_exact_supported_aliases(body: str) -> None:
    assert parse_command(body) is SageCommand.SOLVE


@pytest.mark.parametrize(
    "body",
    [
        "",
        " /sage solve",
        "/sage solve ",
        "/SAGE SOLVE",
        "/sage",
        "/sage solve now",
        "please /sage solve",
        "`/sage solve`",
        "```\n/sage solve\n```",
        "/sage\u00a0solve",
    ],
)
def test_parse_command_rejects_non_exact_input(body: str) -> None:
    assert parse_command(body) is None
