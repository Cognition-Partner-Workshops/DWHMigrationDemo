"""Dependency graph checks shared by ETL output validators."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class GraphChecks:
    duplicates: list[tuple[str, int | None]]
    unknown_dependencies: list[tuple[str, str, int | None]]
    cycles: list[tuple[tuple[str, ...], int | None]]


def check_graph(nodes: Sequence[tuple[str, Sequence[str], int | None]]) -> GraphChecks:
    first_lines: dict[str, int | None] = {}
    ordered_names: list[str] = []
    duplicates: list[tuple[str, int | None]] = []
    for name, _, line in nodes:
        if name in first_lines:
            duplicates.append((name, line))
        else:
            first_lines[name] = line
            ordered_names.append(name)

    unknown: list[tuple[str, str, int | None]] = []
    adjacency: dict[str, list[str]] = {name: [] for name in ordered_names}
    for name, dependencies, line in nodes:
        for dependency in dependencies:
            if dependency not in first_lines:
                unknown.append((name, dependency, line))
            elif dependency not in adjacency[name]:
                adjacency[name].append(dependency)

    order = {name: index for index, name in enumerate(ordered_names)}
    cycles: list[tuple[tuple[str, ...], int | None]] = []
    seen_cycles: set[tuple[str, ...]] = set()

    def visit(start: str, current: str, path: list[str]) -> None:
        for dependency in adjacency[current]:
            if dependency == start:
                cycle = (*path, start)
                if cycle not in seen_cycles:
                    seen_cycles.add(cycle)
                    cycles.append((cycle, first_lines[start]))
            elif order[dependency] >= order[start] and dependency not in path:
                visit(start, dependency, [*path, dependency])

    for name in ordered_names:
        visit(name, name, [name])

    return GraphChecks(duplicates, unknown, cycles)
