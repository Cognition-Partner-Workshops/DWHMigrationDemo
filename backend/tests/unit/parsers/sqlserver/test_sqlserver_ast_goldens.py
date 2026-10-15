from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from migraai.discovery.parsers.sqlserver import Parser

GOLDEN_DIR = Path(__file__).parent / "golden"
STATEMENTS = [
    ("select_top", "SELECT TOP (5) id FROM dbo.Orders WHERE id > 2"),
    ("declare", "DECLARE @id INT = 1, @name NVARCHAR(20) = N'item'"),
    ("if", "IF @id > 0 BEGIN PRINT @id; END ELSE SELECT 0"),
    ("insert_output", "INSERT INTO dbo.Target (id) OUTPUT inserted.id VALUES (1)"),
    ("update_output", "UPDATE dbo.Target SET id = 2 OUTPUT inserted.id WHERE id = 1"),
    ("delete_output", "DELETE FROM dbo.Target OUTPUT deleted.id WHERE id = 1"),
    (
        "merge",
        "MERGE INTO dbo.Target t USING dbo.Source s ON t.id = s.id "
        "WHEN MATCHED THEN UPDATE SET t.value = s.value;",
    ),
    (
        "create_table",
        "CREATE TABLE dbo.Child (id INT IDENTITY(1,1), parent_id INT REFERENCES dbo.Parent(id))",
    ),
    (
        "procedure",
        "CREATE PROCEDURE dbo.refresh @id INT AS BEGIN SELECT @id; END",
    ),
    (
        "trigger",
        "CREATE TRIGGER dbo.trg ON dbo.Orders AFTER INSERT AS BEGIN SELECT id FROM inserted; END",
    ),
    ("grant", "GRANT SELECT ON OBJECT::dbo.Orders TO analyst"),
    ("dynamic_sql", "EXEC ('SELECT id FROM dbo.Orders')"),
]


@pytest.mark.parametrize(("name", "sql"), STATEMENTS, ids=[name for name, _ in STATEMENTS])
def test_sqlserver_ast_golden_json(name: str, sql: str) -> None:
    result = Parser().parse(sql, f"{name}.sql")
    assert not result.issues, result.issues
    assert len(result.statements) == 1
    actual = result.statements[0].to_dict()
    golden_path = GOLDEN_DIR / f"{name}.json"
    if os.environ.get("MIGRAAI_REGEN_GOLDEN") == "1":
        GOLDEN_DIR.mkdir(parents=True, exist_ok=True)
        golden_path.write_text(
            json.dumps(actual, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
    assert json.loads(golden_path.read_text(encoding="utf-8")) == actual
