import json
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]
NOTEBOOKS = sorted((ROOT / "examples").rglob("*.ipynb")) + sorted((ROOT / "tool-benchmark").glob("*.ipynb"))


@pytest.mark.parametrize("path", NOTEBOOKS, ids=lambda p: p.name)
def test_example_notebooks_are_committed_without_outputs(path):
    notebook = json.loads(path.read_text(encoding="utf-8"))
    for cell in notebook["cells"]:
        if cell["cell_type"] == "code":
            assert cell["outputs"] == [], f"{path.name} has committed outputs"
            assert cell["execution_count"] is None
    # Interpreter versions from the author's machine are noise in the repo.
    assert "version" not in notebook["metadata"].get("language_info", {})
