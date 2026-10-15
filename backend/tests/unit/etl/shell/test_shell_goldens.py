"""AST goldens for representative shell constructs (§10)."""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from migraai.etl.shell.parse import parse

GOLDEN_DIR = Path(__file__).parent / "golden"

CASES = {
    "heredoc_bteq": (
        b"bteq <<'EOS'\n.LOGON td/u,pw\nSELECT 1 FROM t;\nEOS\n",
        "j/x.sh",
        "script",
    ),
    "case_for_while": (
        b"f() {\nfor x in a b; do echo $x; done\nwhile read l; do case $l in a) echo 1;; esac; done\n}\n",  # noqa: E501
        "j/x.sh",
        "function",
    ),
    "pipeline_redirects": (
        b"cat f | grep x > out.log 2>&1\n",
        "j/x.sh",
        "script",
    ),
    "spark_submit": (
        b"spark-submit --class com.a.M --master yarn app.py\n",
        "j/x.sh",
        "script",
    ),
}


@pytest.mark.parametrize("name", sorted(CASES))
def test_ast_golden(name: str) -> None:
    data, rel_path, obj_type = CASES[name]
    result = parse(data, rel_path)
    obj = next(o for o in result.objects if o.type == obj_type)
    actual = obj.node.to_dict()
    golden = GOLDEN_DIR / f"{name}.json"
    if os.environ.get("MIGRAAI_REGEN_GOLDEN") == "1" or not golden.exists():
        golden.parent.mkdir(parents=True, exist_ok=True)
        golden.write_text(json.dumps(actual, indent=2) + "\n", encoding="utf-8")
    assert json.loads(golden.read_text()) == actual
