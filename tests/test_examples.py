"""Executable source examples for the supported compiler subset."""
from pathlib import Path

import pytest

from svcsp_compiler import compile_async_file


EXAMPLES = Path(__file__).resolve().parents[1] / "examples"


@pytest.mark.parametrize("source", sorted(EXAMPLES.glob("*.sv")), ids=lambda path: path.stem)
def test_example_compiles_through_the_complete_async_flow(source: Path) -> None:
    assert compile_async_file(source).startswith(f"module {source.stem}")
