"""Read-only semantic facts derived from a Unified Semantic Graph."""
from __future__ import annotations

from dataclasses import dataclass

from .usg import (
    ControlEdge,
    PredicateNode,
    ReceiveNode,
    SendNode,
    USGNode,
    UnifiedSemanticGraph,
)


CommunicationNode = ReceiveNode | SendNode
_ControlPath = tuple[tuple[PredicateNode, bool], ...]


@dataclass(frozen=True, slots=True)
class AnalysisFacts:
    """Read-only facts collected during one USG occurrence-order traversal."""

    communication_predecessors: tuple[tuple[CommunicationNode, tuple[CommunicationNode, ...]], ...]
    incoming_communication_frontiers: tuple[
        tuple[USGNode, tuple[CommunicationNode, ...]], ...
    ]

    def predecessors_of(self, communication: CommunicationNode) -> tuple[CommunicationNode, ...]:
        """Return the possible preceding communications for ``communication``."""

        for known, predecessors in self.communication_predecessors:
            if known is communication:
                return predecessors
        raise KeyError("communication node is not present in these analysis facts")

    def incoming_frontier_of(self, node: USGNode) -> tuple[CommunicationNode, ...]:
        """Return possible immediately preceding communications before ``node``."""

        for known, frontier in self.incoming_communication_frontiers:
            if known is node:
                return frontier
        raise KeyError("USG node is not present in these analysis facts")


class USGAnalysisError(ValueError):
    """USG occurrence/control structure cannot be analyzed as structured paths."""


def analyze_usg(graph: UnifiedSemanticGraph) -> AnalysisFacts:
    """Derive path-aware communication predecessors without modifying ``graph``."""

    nodes = graph.nodes
    paths = _control_paths(nodes, graph.control_edges)
    analyzer = _OccurrenceAnalyzer(nodes, paths)
    end_index, _ = analyzer.visit_region(0, (), ())
    if end_index != len(nodes) or len(analyzer.consumed_ids) != len(nodes):
        raise USGAnalysisError("USG occurrence traversal did not consume every node once")
    return AnalysisFacts(
        tuple(analyzer.communication_predecessors),
        tuple(analyzer.incoming_communication_frontiers),
    )


def _control_paths(
    nodes: tuple[USGNode, ...], control_edges: tuple[ControlEdge, ...]
) -> dict[int, _ControlPath]:
    node_order = {id(node): index for index, node in enumerate(nodes)}
    incoming: dict[int, list[ControlEdge]] = {id(node): [] for node in nodes}
    for edge in control_edges:
        if not isinstance(edge.source, PredicateNode):
            raise USGAnalysisError("CONTROL edge source must be a PredicateNode")
        if node_order[id(edge.source)] >= node_order[id(edge.target)]:
            raise USGAnalysisError("CONTROL predicate must precede its controlled node")
        incoming[id(edge.target)].append(edge)

    paths: dict[int, _ControlPath] = {}
    for node in nodes:
        edges = sorted(incoming[id(node)], key=lambda edge: node_order[id(edge.source)])
        path: list[tuple[PredicateNode, bool]] = []
        for edge in edges:
            if any(predicate is edge.source for predicate, _ in path):
                raise USGAnalysisError("node has duplicate CONTROL edges from one predicate")
            path.append((edge.source, edge.polarity))
        paths[id(node)] = tuple(path)
    return paths


class _OccurrenceAnalyzer:
    def __init__(self, nodes: tuple[USGNode, ...], paths: dict[int, _ControlPath]) -> None:
        self.nodes = nodes
        self.paths = paths
        self.consumed_ids: set[int] = set()
        self.communication_predecessors: list[
            tuple[CommunicationNode, tuple[CommunicationNode, ...]]
        ] = []
        self.incoming_communication_frontiers: list[
            tuple[USGNode, tuple[CommunicationNode, ...]]
        ] = []

    def visit_region(
        self,
        index: int,
        active_path: _ControlPath,
        frontier: tuple[CommunicationNode, ...],
    ) -> tuple[int, tuple[CommunicationNode, ...]]:
        """Consume one structured path region and return its ending frontier."""

        while index < len(self.nodes):
            node = self.nodes[index]
            path = self.paths[id(node)]
            if not _has_prefix(path, active_path):
                return index, frontier
            if path != active_path:
                raise USGAnalysisError("CONTROL paths do not match structured occurrence order")

            self.incoming_communication_frontiers.append((node, frontier))
            self._consume(node)
            if isinstance(node, PredicateNode):
                true_path = active_path + ((node, True),)
                index, true_frontier = self.visit_region(index + 1, true_path, frontier)

                false_path = active_path + ((node, False),)
                if index < len(self.nodes) and _has_prefix(self.paths[id(self.nodes[index])], false_path):
                    index, false_frontier = self.visit_region(index, false_path, frontier)
                    frontier = _union_frontiers(true_frontier, false_frontier)
                else:
                    frontier = _union_frontiers(frontier, true_frontier)
                continue

            if isinstance(node, (ReceiveNode, SendNode)):
                self.communication_predecessors.append((node, frontier))
                frontier = (node,)
            index += 1
        return index, frontier

    def _consume(self, node: USGNode) -> None:
        if id(node) in self.consumed_ids:
            raise USGAnalysisError("USG occurrence node was consumed more than once")
        self.consumed_ids.add(id(node))


def _has_prefix(path: _ControlPath, prefix: _ControlPath) -> bool:
    return len(path) >= len(prefix) and all(
        predicate is expected_predicate and polarity is expected_polarity
        for (predicate, polarity), (expected_predicate, expected_polarity) in zip(path, prefix)
    )


def _union_frontiers(
    *frontiers: tuple[CommunicationNode, ...],
) -> tuple[CommunicationNode, ...]:
    merged: list[CommunicationNode] = []
    for frontier in frontiers:
        for communication in frontier:
            if not any(communication is existing for existing in merged):
                merged.append(communication)
    return tuple(merged)
