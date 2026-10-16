"""Execution scripts for the converted ZIP: order.txt, run.sh, run.ps1, README.md.

Pure function of ``PackageInput`` (see docs/packaging-design.md). Runners never copy
SQL and never hold credentials: they reference ``../../converted/<package_path>`` and
invoke the target CLI, which reads its connection from its own profile/config/env.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

import yaml

from migraai.etl.outputs.sources import FORMAT_SOURCES
from migraai.packaging.model import PackagedFile, PackageInput

MASKED_TOKEN = "***MASKED***"
RUNNERS_DIR = Path(__file__).resolve().parent / "runners"

TARGETS: tuple[str, ...] = (
    "snowflake",
    "databricks",
    "bigquery",
    "redshift",
    "fabric",
    "synapse",
    "clickhouse",
)

_DRIVE_LETTER = re.compile(r"^[A-Za-z]:")
_BAD_CHARS = re.compile(r'[\x00-\x1f\x7f"]')
_NON_ASCII = re.compile(r"[^\x00-\x7f]")


@dataclass(frozen=True, slots=True)
class _PlanEntry:
    path: str
    review: bool


@dataclass(frozen=True, slots=True)
class _EtlEntry:
    file: PackagedFile


@dataclass(frozen=True, slots=True)
class _Collected:
    runnable: tuple[_PlanEntry, ...]
    etl: tuple[_EtlEntry, ...]
    masked: tuple[str, ...]


def _validate_package_path(path: str) -> None:
    """Reject paths that could escape converted/ or break the runners."""
    if (
        not path
        or path.startswith("/")
        or "\\" in path
        or _DRIVE_LETTER.match(path)
        or _BAD_CHARS.search(path)
        or any(seg in ("", ".", "..") for seg in path.split("/"))
    ):
        raise ValueError(f"invalid package_path: {path!r}")


def _collect(pkg: PackageInput) -> _Collected:
    """First-appearance-deduped file selection over pkg.included()."""
    runnable_order: list[str] = []
    review: dict[str, bool] = {}
    etl: dict[str, _EtlEntry] = {}
    masked: list[str] = []
    for obj in pkg.included():
        for f in obj.files:
            path = f.package_path
            _validate_package_path(path)
            if MASKED_TOKEN in f.content and path not in masked:
                masked.append(path)
            if f.language == "sql" and f.etl_format_id is None:
                if path not in review:
                    runnable_order.append(path)
                    review[path] = False
                if obj.status == "expert_review":
                    review[path] = True
            elif path not in etl:
                etl[path] = _EtlEntry(f)
    return _Collected(
        runnable=tuple(_PlanEntry(p, review[p]) for p in runnable_order),
        etl=tuple(etl[p] for p in etl),
        masked=tuple(masked),
    )


def _load_spec(target: str) -> dict:
    spec = yaml.safe_load((RUNNERS_DIR / target / "target.yaml").read_text(encoding="utf-8"))
    if not isinstance(spec, dict):
        raise ValueError(f"bad target spec for {target!r}")
    return spec


def _fill(template: str, values: dict[str, str]) -> str:
    for name, value in values.items():
        template = template.replace(f"@@{name}@@", value)
    assert "@@" not in template, "unfilled template placeholder"
    return template


def _doc_lines(spec: dict) -> str:
    return "\n".join(f"# Docs: {d['title']}: {d['url']}" for d in spec["docs"])


def _command_display(spec: dict) -> str:
    parts = [spec["cli"], *spec.get("base_args", []), "<args>"]
    parts += [a.replace("{file}", "<file>") for a in spec.get("file_args", [])]
    cmd = " ".join(parts)
    if spec.get("stdin"):
        cmd += " < <file>"
    return cmd


def _sh_quote(s: str) -> str:
    return "'" + s.replace("'", "'\\''") + "'"


def _ps_quote(s: str) -> str:
    # PowerShell single-quoted string: the curly-quote lookalikes are doubled too
    # so a smart-quote edit cannot break the literal.
    for ch in ("'", "‘", "’", "‚", "‛"):
        s = s.replace(ch, ch * 2)
    return f"'{s}'"


def _sh_invoke(spec: dict) -> str:
    tokens = ['"$CLI"', *(_sh_quote(a) for a in spec.get("base_args", []))]
    tokens.append('${EXTRA[@]+"${EXTRA[@]}"}')
    for a in spec.get("file_args", []):
        tokens.append('"$f"' if a == "{file}" else _sh_quote(a))
    line = " ".join(tokens)
    if spec.get("stdin"):
        line += ' < "$f"'
    return line


def _ps_array(items: list[str]) -> str:
    return "@(" + ", ".join(items) + ")"


def _ps_invoke(spec: dict) -> str:
    base = _ps_array([_ps_quote(a) for a in spec.get("base_args", [])])
    file_args = _ps_array(
        ["$f" if a == "{file}" else _ps_quote(a) for a in spec.get("file_args", [])]
    )
    lines = [f"$argList = {base} + $CliArgs + {file_args}"]
    if spec.get("stdin"):
        lines.append("$OutputEncoding = New-Object System.Text.UTF8Encoding $false")
        lines.append("Get-Content -LiteralPath $f -Raw -Encoding UTF8 | & $cli @argList")
    else:
        lines.append("& $cli @argList")
    return "\n        ".join(lines)


def _order_txt(pkg: PackageInput, col: _Collected) -> str:
    lines = [
        f"# MigraAI execution order: {pkg.target_platform} (run {pkg.run_id}, {pkg.created_at})",
        "# Paths are relative to converted/. REVIEW = expert review, skipped unless "
        "--include-review / -IncludeReview.",
    ]
    if col.runnable:
        for i, e in enumerate(col.runnable, 1):
            lines.append(f"{i:03d}\t{'REVIEW' if e.review else 'RUN'}\t{e.path}")
    else:
        lines.append("# (no SQL files to run)")
    for path in col.masked:
        lines.append(f"# MASKED secrets in: {path}")
    for e in col.etl:
        lines.append(f"# Not run automatically (ETL, see README.md): {e.file.package_path}")
    return "\n".join(lines) + "\n"


def _render_steps(steps: str, package_path: str) -> str:
    return steps.replace("{path}", f'"../../converted/{package_path}"').rstrip()


def _etl_section(spec: dict, entry: _EtlEntry) -> list[str]:
    """README section for one ETL/manual file."""
    f = entry.file
    path = f.package_path
    fmt_id = f.etl_format_id
    formats = spec.get("etl_formats") or {}
    if fmt_id is not None and fmt_id in formats:
        fmt = formats[fmt_id]
        lines = [f"### `{path}`", "", f"_{fmt['title']}_", ""]
        lines += _render_steps(fmt["steps"], path).splitlines()
        doc_lines = [f"- [{d['title']}]({d['url']})" for d in fmt.get("docs", [])]
    else:
        # Unknown to this target (or another target's format): generic deploy step.
        lines = [f"### `{path}`", "", f'- Deploy `"../../converted/{path}"` manually.']
        if fmt_id is not None and fmt_id in FORMAT_SOURCES:
            urls = list(dict.fromkeys(c["url"] for c in FORMAT_SOURCES[fmt_id]))
            doc_lines = [f"- [Documentation]({u})" for u in urls]
        else:
            doc_lines = []
    if doc_lines:
        lines += ["", "Docs:", *doc_lines]
    lines.append("")
    return lines


def _readme(pkg: PackageInput, spec: dict, col: _Collected) -> str:
    cli = spec["cli"]
    display = spec["display"]
    lines = [
        f"# Apply the converted SQL on {display}",
        "",
        "## Safety",
        "",
        "- MigraAI never connects to a target warehouse. These runners only invoke "
        f"the `{cli}` CLI, which reads connection details from its own profile, config "
        "file or environment variables.",
        "- No credentials are embedded in this package.",
        "- The runners default to a dry run. Nothing is applied without `--execute` / `-Execute`.",
        "",
        "## Prerequisites",
        "",
        f"- Install `{cli}`: {spec['install_url']}",
        "- `run.sh` needs bash 3.2+ (on Windows: Git Bash or WSL).",
        "- `run.ps1` needs Windows PowerShell 5.1 or PowerShell 7+; no administrator "
        "rights are required.",
        "",
        "## Connection setup",
        "",
        spec["connection"].rstrip(),
        "",
        "## Usage",
        "",
        "### Bash",
        "",
        "```",
        "bash run.sh                          # dry run: print the ordered plan",
        "bash run.sh --execute                # apply the plan",
        "bash run.sh --execute --include-review   # include expert-review files",
        "bash run.sh --execute -- <cli args>  # e.g. -c <connection>; never passwords",
        "```",
        "",
        "### PowerShell",
        "",
        "```",
        "powershell -NoProfile -ExecutionPolicy Bypass -File .\\run.ps1",
        "powershell -NoProfile -ExecutionPolicy Bypass -File .\\run.ps1 -Execute",
        "powershell -NoProfile -ExecutionPolicy Bypass -File .\\run.ps1 -Execute -IncludeReview",
        "powershell -NoProfile -ExecutionPolicy Bypass -File .\\run.ps1 -Execute "
        "-CliArgs '<arg1>','<arg2>'",
        "```",
        "",
        "If the ZIP was downloaded, run `Unblock-File .\\run.ps1` first (or unblock the "
        "ZIP before extracting).",
        "",
        "## Exit codes",
        "",
        "| Code | Meaning |",
        "|---|---|",
        "| 0 | Dry run printed, or all selected files applied |",
        "| 1 | A converted file in the plan is missing |",
        "| 2 | Unknown runner argument |",
        "| 3 | Masked secret placeholders found (see below) |",
        f"| 127 | The `{cli}` CLI was not found on PATH |",
        "| other | The CLI's own exit code; printed as `FAILED: <path> (exit N)` |",
        "",
        "## Execution order",
        "",
        "| # | Action | File |",
        "|---|---|---|",
    ]
    if col.runnable:
        for i, e in enumerate(col.runnable, 1):
            action = "REVIEW (skipped unless requested)" if e.review else "RUN"
            lines.append(f"| {i} | {action} | `{e.path}` |")
    else:
        lines.append("| - | - | (no SQL files to run) |")

    review = [e.path for e in col.runnable if e.review]
    if review:
        lines += ["", "## Expert review", ""]
        for path in review:
            lines.append(
                f"- `{path}` — skipped unless `--include-review` / `-IncludeReview` is given."
            )

    if col.masked:
        lines += ["", "## Masked secrets", ""]
        lines.append(
            "These files contain `***MASKED***` placeholders. Replace each placeholder "
            "with a secret reference before running with `--execute` / `-Execute` "
            "(the runner refuses to apply them)."
        )
        lines.append("")
        for path in col.masked:
            lines.append(f"- `{path}`")

    excluded = pkg.excluded()
    if excluded:
        lines += ["", "## Not included", ""]
        for obj in excluded:
            lines.append(f"- `{obj.source_qualified_name}` ({obj.status})")

    if col.etl:
        lines += ["", "## ETL and manual deploy steps", ""]
        for entry in col.etl:
            lines += _etl_section(spec, entry)

    if spec.get("notes"):
        lines += ["## Notes", "", spec["notes"].rstrip(), ""]

    lines += ["## Docs", ""]
    lines += [f"- [{d['title']}]({d['url']})" for d in spec["docs"]]
    return "\n".join(lines) + "\n"


def _encode(text: str) -> bytes:
    if _NON_ASCII.search(text):
        return text.encode("utf-8-sig")
    return text.encode("utf-8")


def build_execution_scripts(pkg: PackageInput) -> dict[str, bytes]:
    """Build execution/<target>/{order.txt,run.sh,run.ps1,README.md}."""
    target = pkg.target_platform
    if target not in TARGETS:
        raise ValueError(f"unknown target platform: {target!r}")
    spec = _load_spec(target)
    col = _collect(pkg)

    common = RUNNERS_DIR / "common"
    sh_tmpl = (common / "run.sh.tmpl").read_text(encoding="utf-8")
    ps_tmpl = (common / "run.ps1.tmpl").read_text(encoding="utf-8")

    shared = {
        "DISPLAY": spec["display"],
        "CLI": spec["cli"],
        "COMMAND": _command_display(spec),
        "DOC_LINES": _doc_lines(spec),
    }
    run_sh = _fill(
        sh_tmpl,
        {
            **shared,
            "PLAN_PATHS": "\n".join(f"    {_sh_quote(e.path)}" for e in col.runnable),
            "PLAN_REVIEW": "\n".join(f"    {1 if e.review else 0}" for e in col.runnable),
            "INVOKE": _sh_invoke(spec),
        },
    )
    ps_items = ",\n    ".join(
        "@{ Path = "
        + _ps_quote(e.path)
        + "; Review = "
        + ("$true" if e.review else "$false")
        + " }"
        for e in col.runnable
    )
    run_ps1 = _fill(
        ps_tmpl,
        {
            **shared,
            "PS_PLAN": ("    " + ps_items) if ps_items else "",
            "PS_INVOKE": _ps_invoke(spec),
        },
    )

    return {
        f"{target}/order.txt": _order_txt(pkg, col).encode("utf-8"),
        f"{target}/run.sh": _encode(run_sh),
        f"{target}/run.ps1": _encode(run_ps1),  # BOM if non-ASCII paths require it
        f"{target}/README.md": _readme(pkg, spec, col).encode("utf-8"),
    }
