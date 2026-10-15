"""Utility helpers."""

from etl import common


def normalize(rows):
    return [r for r in rows if r]


def dedupe(rows):
    seen = set()
    out = []
    for row in rows:
        if row not in seen:
            seen.add(row)
            out.append(row)
    return out


def summarize(cur):
    return common.report(cur)


def flag_row(row):
    return {"row": row, "ok": True}
