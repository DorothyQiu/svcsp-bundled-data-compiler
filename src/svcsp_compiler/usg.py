"""Minimal Unified Semantic Graph data model."""
from __future__ import annotations

from dataclasses import dataclass
from typing import TypeAlias


@dataclass(frozen=True, slots=True, eq=False)
class ReceiveNode:
    semantic_object: object


@dataclass(frozen=True, slots=True, eq=False)
class SendNode:
    semantic_object: object


@dataclass(frozen=True, slots=True, eq=False)
class AssignNode:
    semantic_object: object


@dataclass(frozen=True, slots=True, eq=False)
class PredicateNode:
    semantic_object: object


USGNode: TypeAlias = ReceiveNode | SendNode | AssignNode | PredicateNode


@dataclass(frozen=True, slots=True)
class DataEdge:
    source: USGNode
    target: USGNode


@dataclass(frozen=True, slots=True)
class ControlEdge:
    source: USGNode
    target: USGNode


USGEdge: TypeAlias = DataEdge | ControlEdge


class UnifiedSemanticGraph:
    """An insertion-ordered collection of semantic nodes and relations."""

    __slots__ = ("_nodes", "_data_edges", "_control_edges")

    def __init__(self) -> None:
        self._nodes: list[USGNode] = []
        self._data_edges: list[DataEdge] = []
        self._control_edges: list[ControlEdge] = []

    @property
    def nodes(self) -> tuple[USGNode, ...]:
        return tuple(self._nodes)

    @property
    def data_edges(self) -> tuple[DataEdge, ...]:
        return tuple(self._data_edges)

    @property
    def control_edges(self) -> tuple[ControlEdge, ...]:
        return tuple(self._control_edges)

    def add_node(self, node: USGNode) -> USGNode:
        if not isinstance(node, (ReceiveNode, SendNode, AssignNode, PredicateNode)):
            raise TypeError("expected a USG node")
        if self._contains(node):
            raise ValueError("node is already in this graph")
        self._nodes.append(node)
        return node

    def add_edge(self, edge: USGEdge) -> USGEdge:
        if not isinstance(edge, (DataEdge, ControlEdge)):
            raise TypeError("expected a DATA or CONTROL edge")
        if not self._contains(edge.source) or not self._contains(edge.target):
            raise ValueError("edge endpoints must be nodes in this graph")
        if isinstance(edge, DataEdge):
            self._data_edges.append(edge)
        else:
            self._control_edges.append(edge)
        return edge

    def add_data_edge(self, source: USGNode, target: USGNode) -> DataEdge:
        return self.add_edge(DataEdge(source, target))

    def add_control_edge(self, source: USGNode, target: USGNode) -> ControlEdge:
        return self.add_edge(ControlEdge(source, target))

    def _contains(self, node: USGNode) -> bool:
        return any(existing is node for existing in self._nodes)
