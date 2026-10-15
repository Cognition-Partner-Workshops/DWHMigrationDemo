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

    reverse_adjacency: dict[str, list[str]] = {name: [] for name in ordered_names}
    for name in ordered_names:
        for dependency in adjacency[name]:
            reverse_adjacency[dependency].append(name)

    visited: set[str] = set()
    finish_order: list[str] = []
    for name in ordered_names:
        if name in visited:
            continue
        visited.add(name)
        stack = [(name, 0)]
        while stack:
            current, next_neighbor = stack[-1]
            if next_neighbor == len(adjacency[current]):
                finish_order.append(current)
                stack.pop()
                continue
            neighbor = adjacency[current][next_neighbor]
            stack[-1] = (current, next_neighbor + 1)
            if neighbor not in visited:
                visited.add(neighbor)
                stack.append((neighbor, 0))

    assigned: set[str] = set()
    components: list[list[str]] = []
    for name in reversed(finish_order):
        if name in assigned:
            continue
        assigned.add(name)
        component = []
        stack = [name]
        while stack:
            current = stack.pop()
            component.append(current)
            for neighbor in reversed(reverse_adjacency[current]):
                if neighbor not in assigned:
                    assigned.add(neighbor)
                    stack.append(neighbor)
        components.append(component)

    def cycle_path(start: str, component: set[str]) -> tuple[str, ...]:
        visited_in_component = {start}
        parent: dict[str, str] = {}
        stack = [(start, 0)]
        while stack:
            current, next_neighbor = stack[-1]
            if next_neighbor == len(adjacency[current]):
                stack.pop()
                continue
            neighbor = adjacency[current][next_neighbor]
            stack[-1] = (current, next_neighbor + 1)
            if neighbor not in component:
                continue
            if neighbor == start:
                path = [current]
                while path[-1] != start:
                    path.append(parent[path[-1]])
                path.reverse()
                return (*path, start)
            if neighbor not in visited_in_component:
                visited_in_component.add(neighbor)
                parent[neighbor] = current
                stack.append((neighbor, 0))
        raise RuntimeError("cyclic strongly connected component has no cycle path")

    component_by_node: dict[str, int] = {}
    cyclic_components: dict[int, set[str]] = {}
    for index, component in enumerate(components):
        members = set(component)
        component_by_node.update((name, index) for name in component)
        if len(component) > 1 or component[0] in adjacency[component[0]]:
            cyclic_components[index] = members

    cycles: list[tuple[tuple[str, ...], int | None]] = []
    reported_components: set[int] = set()
    for start in ordered_names:
        component_index = component_by_node[start]
        if component_index not in cyclic_components or component_index in reported_components:
            continue
        reported_components.add(component_index)
        cycles.append((cycle_path(start, cyclic_components[component_index]), first_lines[start]))

    return GraphChecks(duplicates, unknown, cycles)
