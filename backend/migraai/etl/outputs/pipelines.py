"""Pydantic schemas and graph validation for JSON ETL outputs."""

from __future__ import annotations

import json
import re
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator

from migraai.etl.outputs.graph import check_graph
from migraai.validation.findings import Finding, Severity


class _OpenModel(BaseModel):
    model_config = ConfigDict(extra="allow")


class _TaskDependency(_OpenModel):
    task_key: str = Field(min_length=1, max_length=100, pattern=r"^[\w\-]+$")


class _NotebookTask(_OpenModel):
    notebook_path: str = Field(min_length=1)


class _SqlTask(_OpenModel):
    warehouse_id: str = Field(min_length=1)
    query: dict | None = None
    file: dict | None = None
    alert: dict | None = None
    dashboard: dict | None = None

    @model_validator(mode="after")
    def exactly_one_sql_source(self) -> _SqlTask:
        present = sum(
            value is not None for value in (self.query, self.file, self.alert, self.dashboard)
        )
        if present != 1:
            raise ValueError("exactly one of query, file, alert, or dashboard must be present")
        return self


class _Schedule(_OpenModel):
    quartz_cron_expression: str = Field(min_length=1)
    timezone_id: str = Field(min_length=1)
    pause_status: Literal["PAUSED", "UNPAUSED"] | None = None


class _JobTask(_OpenModel):
    task_key: str = Field(min_length=1, max_length=100, pattern=r"^[\w\-]+$")
    depends_on: list[_TaskDependency] = Field(default_factory=list)
    notebook_task: _NotebookTask | None = None
    sql_task: _SqlTask | None = None


class _Job(_OpenModel):
    name: str = Field(min_length=1)
    tasks: list[_JobTask] = Field(min_length=1)
    schedule: _Schedule | None = None


class _Dependency(BaseModel):
    model_config = ConfigDict(extra="forbid")

    activity: str = Field(min_length=1)
    dependencyConditions: list[Literal["Succeeded", "Failed", "Skipped", "Completed"]] = Field(
        min_length=1
    )


class _ActivityBase(_OpenModel):
    name: str = Field(min_length=1)
    type: str = Field(min_length=1)
    dependsOn: list[_Dependency] = Field(default_factory=list)


class _FabricActivity(_ActivityBase):
    typeProperties: dict


class _SynapseActivity(_ActivityBase):
    typeProperties: dict | None = None


class _FabricPipelineProperties(_OpenModel):
    activities: list[_FabricActivity] = Field(min_length=1)


class _FabricPipeline(_OpenModel):
    name: str = Field(min_length=1)
    properties: _FabricPipelineProperties


class _SynapsePipelineProperties(_OpenModel):
    activities: list[_SynapseActivity] = Field(min_length=1)


class _SynapsePipeline(_OpenModel):
    name: str = Field(min_length=1)
    properties: _SynapsePipelineProperties


def _finding(
    format_id: str,
    name: str,
    message: str,
    line: int | None = None,
    severity: Severity = "error",
) -> Finding:
    return Finding("syntax", f"etl.{format_id}.{name}", severity, message, line)


def _schema_findings(format_id: str, error: ValidationError) -> list[Finding]:
    findings = []
    for item in error.errors():
        location = ".".join(str(part) for part in item["loc"]) or "<root>"
        findings.append(_finding(format_id, "schema", f"{location}: {item['msg']}"))
    return findings


def _decode(content: str, format_id: str) -> tuple[object | None, list[Finding]]:
    try:
        data = json.loads(content)
    except json.JSONDecodeError as error:
        return None, [_finding(format_id, "invalid_json", error.msg, error.lineno)]
    if not isinstance(data, dict):
        return None, [_finding(format_id, "schema", "top level must be an object")]
    return data, []


def _job(format_id: str, content: str) -> list[Finding]:
    data, findings = _decode(content, format_id)
    if findings:
        return findings
    try:
        job = _Job.model_validate(data)
    except ValidationError as error:
        return _schema_findings(format_id, error)

    nodes: list[tuple[str, list[str], int | None]] = []
    for task in job.tasks:
        task_types = [task.notebook_task is not None, task.sql_task is not None]
        if sum(task_types) != 1:
            findings.append(
                _finding(
                    format_id,
                    "task_type",
                    f"Task {task.task_key!r} must have exactly one notebook_task or sql_task.",
                )
            )
        for key in task.model_extra or {}:
            if key.endswith("_task") and key not in {"notebook_task", "sql_task"}:
                findings.append(
                    _finding(
                        format_id,
                        "unsupported_task_type",
                        f"Task {task.task_key!r} has unsupported task type {key!r}.",
                    )
                )
        nodes.append((task.task_key, [dependency.task_key for dependency in task.depends_on], None))
    graph = check_graph(nodes)
    findings.extend(
        _finding(format_id, "duplicate_task_key", f"Duplicate task key {name!r}.", line)
        for name, line in graph.duplicates
    )
    findings.extend(
        _finding(
            format_id,
            "unknown_dependency",
            f"Task {name!r} depends on unknown task {dependency!r}.",
            line,
        )
        for name, dependency, line in graph.unknown_dependencies
    )
    findings.extend(
        _finding(
            format_id,
            "dependency_cycle",
            f"Task dependency cycle: {' -> '.join(cycle)}.",
            line,
        )
        for cycle, line in graph.cycles
    )
    return findings


_SYNAPSE_FORBIDDEN = re.compile(r"[.+?/<>\*%&: ]")


def _valid_synapse_name(name: str, maximum: int) -> bool:
    return (
        len(name) <= maximum
        and bool(name)
        and (name[0].isascii() and (name[0].isalnum() or name[0] == "_"))
        and _SYNAPSE_FORBIDDEN.search(name) is None
    )


def _pipeline(format_id: str, content: str, target: str) -> list[Finding]:
    data, findings = _decode(content, format_id)
    if findings:
        return findings
    model = {"fabric": _FabricPipeline, "synapse": _SynapsePipeline}[target]
    try:
        pipeline = model.model_validate(data)
    except ValidationError as error:
        return _schema_findings(format_id, error)

    activities = pipeline.properties.activities
    nodes = [
        (
            activity.name,
            [dependency.activity for dependency in activity.dependsOn],
            None,
        )
        for activity in activities
    ]
    graph = check_graph(nodes)
    findings.extend(
        _finding(format_id, "duplicate_activity_name", f"Duplicate activity name {name!r}.", line)
        for name, line in graph.duplicates
    )
    findings.extend(
        _finding(
            format_id,
            "unknown_dependency",
            f"Activity {name!r} depends on unknown activity {dependency!r}.",
            line,
        )
        for name, dependency, line in graph.unknown_dependencies
    )
    findings.extend(
        _finding(
            format_id,
            "dependency_cycle",
            f"Activity dependency cycle: {' -> '.join(cycle)}.",
            line,
        )
        for cycle, line in graph.cycles
    )
    if target == "synapse":
        if not _valid_synapse_name(pipeline.name, 140):
            findings.append(
                _finding(format_id, "invalid_pipeline_name", "Invalid Synapse pipeline name.")
            )
        for activity in activities:
            if not _valid_synapse_name(activity.name, 55):
                findings.append(
                    _finding(
                        format_id,
                        "invalid_activity_name",
                        f"Invalid Synapse activity name {activity.name!r}.",
                    )
                )
    return findings


def check_json_output(format_id: str, content: str) -> list[Finding]:
    if format_id == "databricks.workflows_job":
        return _job(format_id, content)
    if format_id == "fabric.pipeline":
        return _pipeline(format_id, content, "fabric")
    if format_id == "synapse.pipeline":
        return _pipeline(format_id, content, "synapse")
    raise ValueError(f"Unsupported JSON ETL output format: {format_id}")
