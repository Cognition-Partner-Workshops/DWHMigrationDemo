from __future__ import annotations

import heapq
import math
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any


def per_session_acu_limit(cap: float | None, max_sessions: int) -> int | None:
    if cap is None:
        return None
    if max_sessions < 1:
        raise ValueError("max_sessions must be at least 1")
    return max(1, math.floor(cap / max_sessions))


@dataclass(slots=True)
class Batch:
    index: int
    objects: list[Any]
    external_dependencies: list[str] = field(default_factory=list)
    oversized: bool = False
    partial_objects: list[str] = field(default_factory=list)
    unparsed_objects: list[str] = field(default_factory=list)

    @property
    def object_ids(self) -> list[str]:
        return [_object_id(item) for item in self.objects]


def _value(item: Any, *names: str, default: Any = None) -> Any:
    for name in names:
        if isinstance(item, Mapping):
            if name in item:
                return item[name]
        elif hasattr(item, name):
            return getattr(item, name)
    return default


def _object_id(item: Any) -> str:
    return str(_value(item, "id", "object_id", "qualified_name", default=""))


def _qualified_name(item: Any) -> str:
    return str(_value(item, "qualified_name", "name", "id", "object_id", default=""))


def _is_external(item: Any) -> bool:
    value = _value(item, "external", default=False)
    return value is True or value == 1 or str(value).casefold() == "true"


def _sort_key(item: Any) -> tuple[str, int, str]:
    return (
        str(_value(item, "file", "file_path", default="")),
        int(_value(item, "start_line", default=0) or 0),
        _object_id(item),
    )


def _edge_value(edge: Any, *names: str, default: Any = None) -> Any:
    return _value(edge, *names, default=default)


def _components(
    nodes: list[str],
    adjacency: dict[str, set[str]],
    node_key: dict[str, tuple[str, int, str]],
) -> list[list[str]]:
    index = 0
    stack: list[str] = []
    on_stack: set[str] = set()
    indexes: dict[str, int] = {}
    lowlinks: dict[str, int] = {}
    components: list[list[str]] = []

    def visit(node: str) -> None:
        nonlocal index
        indexes[node] = lowlinks[node] = index
        index += 1
        stack.append(node)
        on_stack.add(node)
        for neighbor in sorted(adjacency[node], key=node_key.__getitem__):
            if neighbor not in indexes:
                visit(neighbor)
                lowlinks[node] = min(lowlinks[node], lowlinks[neighbor])
            elif neighbor in on_stack:
                lowlinks[node] = min(lowlinks[node], indexes[neighbor])
        if lowlinks[node] == indexes[node]:
            component: list[str] = []
            while True:
                member = stack.pop()
                on_stack.remove(member)
                component.append(member)
                if member == node:
                    break
            components.append(sorted(component, key=node_key.__getitem__))

    for node in sorted(nodes, key=node_key.__getitem__):
        if node not in indexes:
            visit(node)
    return components


def _topological_components(
    components: list[list[str]],
    edges: set[tuple[str, str]],
    node_key: dict[str, tuple[str, int, str]],
) -> list[list[str]]:
    component_for = {
        node: component_index
        for component_index, component in enumerate(components)
        for node in component
    }
    dependents: dict[int, set[int]] = {i: set() for i in range(len(components))}
    indegree = {i: 0 for i in range(len(components))}
    for dependency, dependent in edges:
        source = component_for[dependency]
        target = component_for[dependent]
        if source != target and target not in dependents[source]:
            dependents[source].add(target)
            indegree[target] += 1

    def component_key(component_index: int) -> tuple[str, int, str]:
        return min(node_key[node] for node in components[component_index])

    ready: list[tuple[tuple[str, int, str], int]] = []
    for component_index, degree in indegree.items():
        if degree == 0:
            heapq.heappush(ready, (component_key(component_index), component_index))

    ordered: list[list[str]] = []
    preferred_file: str | None = None
    while ready:
        selected = 0
        if preferred_file is not None:
            same_file = [
                index
                for index, (_, candidate) in enumerate(ready)
                if all(node_key[node][0] == preferred_file for node in components[candidate])
            ]
            if same_file:
                selected = min(same_file, key=lambda index: ready[index][0])
        _, component_index = ready.pop(selected)
        heapq.heapify(ready)
        ordered.append(components[component_index])
        preferred_file = node_key[components[component_index][0]][0]
        for dependent in sorted(dependents[component_index], key=component_key):
            indegree[dependent] -= 1
            if indegree[dependent] == 0:
                heapq.heappush(ready, (component_key(dependent), dependent))
    return ordered


def plan_batches(
    objects: Sequence[Any],
    edges: Sequence[Any],
    batch_size: int,
) -> list[Batch]:
    if batch_size < 1:
        raise ValueError("batch_size must be at least 1.")
    all_objects = list(objects)
    external_names = {
        _object_id(item): _qualified_name(item)
        for item in all_objects
        if _is_external(item) and _object_id(item)
    }
    external_names.update(
        {
            _qualified_name(item): _qualified_name(item)
            for item in all_objects
            if _is_external(item) and _qualified_name(item)
        }
    )
    local_objects = [item for item in all_objects if not _is_external(item)]
    local_objects.sort(key=_sort_key)
    if not local_objects:
        return []

    key_for: dict[int, str] = {}
    object_for: dict[str, Any] = {}
    aliases: dict[str, list[str]] = {}
    node_key: dict[str, tuple[str, int, str]] = {}
    for index, item in enumerate(local_objects):
        object_key = _object_id(item) or f"object-{index}"
        if object_key in object_for:
            object_key = f"{object_key}#{index}"
        key_for[id(item)] = object_key
        object_for[object_key] = item
        node_key[object_key] = _sort_key(item)
        for alias in {object_key, _object_id(item), _qualified_name(item)}:
            if alias:
                aliases.setdefault(alias, []).append(object_key)

    dependency_names: dict[str, set[str]] = {key: set() for key in object_for}
    graph_edges: set[tuple[str, str]] = set()
    for edge in edges:
        source = str(_edge_value(edge, "source_id", "source", default=""))
        target = str(_edge_value(edge, "target_id", "target", default=""))
        source_nodes = aliases.get(source, [])
        target_nodes = aliases.get(target, [])
        is_external = _edge_value(edge, "external", default=False) in (True, 1, "true")
        for source_node in source_nodes:
            for target_node in target_nodes:
                dependency_names[source_node].add(_qualified_name(object_for[target_node]))
                graph_edges.add((target_node, source_node))
            if is_external or not target_nodes:
                dependency_names[source_node].add(external_names.get(target, target))

    graph_adjacency: dict[str, set[str]] = {key: set() for key in object_for}
    for dependency, dependent in graph_edges:
        graph_adjacency[dependency].add(dependent)
    components = _components(list(object_for), graph_adjacency, node_key)
    topological = _topological_components(components, graph_edges, node_key)
    ordered_components = topological

    batches: list[Batch] = []
    current: list[Any] = []
    for component in ordered_components:
        component_objects = [object_for[key] for key in component]
        if len(component) > batch_size:
            if current:
                batches.append(Batch(index=len(batches), objects=current))
                current = []
            batches.append(Batch(index=len(batches), objects=component_objects, oversized=True))
            continue
        if current and (len(current) + len(component_objects) > batch_size):
            batches.append(Batch(index=len(batches), objects=current))
            current = []
        current.extend(component_objects)
    if current:
        batches.append(Batch(index=len(batches), objects=current))

    batch_for_node = {key_for[id(item)]: batch.index for batch in batches for item in batch.objects}
    for batch in batches:
        dependency_set: set[str] = set()
        for item in batch.objects:
            object_key = key_for[id(item)]
            for dependency in dependency_names[object_key]:
                dependency_nodes = aliases.get(dependency, [])
                if not dependency_nodes or any(
                    batch_for_node.get(target_node) != batch.index
                    for target_node in dependency_nodes
                ):
                    dependency_set.add(dependency)
            status = str(_value(item, "status", default="")).casefold()
            if status == "partial":
                batch.partial_objects.append(_qualified_name(item))
            elif status == "unparsed":
                batch.unparsed_objects.append(_qualified_name(item))
        batch.external_dependencies = sorted(dependency_set)
    return batches


__all__ = ["Batch", "plan_batches"]
