"""docs/data-dictionary.md is generated from the models: it must be current."""

from __future__ import annotations

import importlib.util
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from types import ModuleType

ROOT = Path(__file__).parent.parent
SCRIPT = ROOT / "scripts" / "generate_data_dictionary.py"


def generator() -> ModuleType:
    spec = importlib.util.spec_from_file_location("generate_data_dictionary", SCRIPT)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_data_dictionary_is_up_to_date() -> None:
    expected = generator().render()
    actual = (ROOT / "docs" / "data-dictionary.md").read_text(encoding="utf-8")

    assert actual == expected, (
        "docs/data-dictionary.md is out of date: "
        "run `uv run python scripts/generate_data_dictionary.py`"
    )


def test_every_column_has_a_meaning() -> None:
    for line in (ROOT / "docs" / "data-dictionary.md").read_text("utf-8").splitlines():
        if line.startswith("| `"):
            assert not line.rstrip().endswith("|  |"), line
