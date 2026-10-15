"""AST goldens for representative python constructs (§10)."""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from migraai.etl.python.parse import parse

GOLDEN_DIR = Path(__file__).parent / "golden"

CASES = {
    "execute_constant": (
        b"Q = 'SELECT a FROM src.t'\ndef f(cur):\n    cur.execute(Q)\n",
        "m/x.py",
        "function",
    ),
    "table_io": (
        b"""import pandas as pd
def f(c):
    df = pd.read_sql_table('t', c, schema='s')
    df.to_sql('w', c, schema='m')
""",
        "m/x.py",
        "function",
    ),
    "class_method": (
        b"class E:\n    def run(self, cur):\n        cur.execute('SELECT 1')\n",
        "m/x.py",
        "function",
    ),
    "recovered": (
        b"def good():\n    return 1\n\ndef broken(:\n    pass\n",
        "m/x.py",
        "module",
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
