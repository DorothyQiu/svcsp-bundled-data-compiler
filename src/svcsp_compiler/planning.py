"""Logical execution partitioning from dependency-analysis facts."""
from __future__ import annotations

from dataclasses import dataclass

from .analysis import AnalysisFacts, CommunicationNode, ValueUse
from .usg import AssignNode, ControlEdge, PredicateNode, ReceiveNode, SendNode, USGNode


CombinationalNode = AssignNode | PredicateNode


@dataclass(frozen=True, slots=True)
class LogicalExecutionPhases:
    """Logical phase assignments for communication and placed combinational nodes.

    Equal phase numbers do not imply concurrent communication. Communication
    ordering remains represented by the analysis facts that this plan consumes.
    """

    communication_phases: tuple[tuple[CommunicationNode, int], ...]
    operation_phases: tuple[tuple[CombinationalNode, int], ...]

    def phase_of(self, node: USGNode) -> int:
        """Return the logical execution phase assigned to a placed ``node``."""

        for known, phase in self.communication_phases + self.operation_phases:
            if known is node:
                return phase
        raise KeyError("USG node is not placed in this logical execution plan")


def partition_logical_execution(
    facts: AnalysisFacts, control_edges: tuple[ControlEdge, ...] = ()
) -> LogicalExecutionPhases:
    """Assign communication and constrained combinational-operation phases.

    Communication phases come only from existing predecessor facts. Assign and
    Predicate phases are then propagated backward from their already-placed
    consumers, using exact reaching-definition facts and supplied CONTROL
    edges. Nodes with no phase-constrained consumer remain unplaced.
    """

    communication_phases = _communication_phases(facts)
    operation_phases = _combinational_operation_phases(
        facts, communication_phases, control_edges
    )
    return LogicalExecutionPhases(communication_phases, operation_phases)


def _communication_phases(
    facts: AnalysisFacts,
) -> tuple[tuple[CommunicationNode, int], ...]:
    """Assign communication phases from already-derived predecessor facts."""

    phases_by_node_id: dict[int, int] = {}
    assignments: list[tuple[CommunicationNode, int]] = []

    for communication, predecessors in facts.communication_predecessors:
        predecessor_phases: list[int] = []
        for predecessor in predecessors:
            try:
                predecessor_phase = phases_by_node_id[id(predecessor)]
            except KeyError as error:
                raise ValueError(
                    "communication predecessor is not assigned before its consumer"
                ) from error
            if isinstance(predecessor, SendNode) and isinstance(communication, ReceiveNode):
                predecessor_phase += 1
            predecessor_phases.append(predecessor_phase)

        phase = max(predecessor_phases, default=0)
        phases_by_node_id[id(communication)] = phase
        assignments.append((communication, phase))

    return tuple(assignments)


def _combinational_operation_phases(
    facts: AnalysisFacts,
    communication_phases: tuple[tuple[CommunicationNode, int], ...],
    control_edges: tuple[ControlEdge, ...],
) -> tuple[tuple[CombinationalNode, int], ...]:
    """Propagate placed-consumer requirements backward to Assigns and Predicates."""

    phases_by_node_id = {id(node): phase for node, phase in communication_phases}
    operation_phases: dict[int, int] = {}
    operations: list[CombinationalNode] = [
        definition
        for definition, _ in facts.definition_consumers
        if isinstance(definition, AssignNode)
    ]
    for edge in control_edges:
        if isinstance(edge.source, PredicateNode) and all(
            known is not edge.source for known in operations
        ):
            operations.append(edge.source)

    changed = True
    while changed:
        changed = False
        for definition, consumers in facts.definition_consumers:
            if not isinstance(definition, AssignNode):
                continue
            requirement = _earliest_consumer_phase(consumers, phases_by_node_id)
            if requirement is not None and _set_earlier_phase(
                definition, requirement, phases_by_node_id, operation_phases
            ):
                changed = True

        for edge in control_edges:
            if not isinstance(edge.source, PredicateNode):
                continue
            target_phase = phases_by_node_id.get(id(edge.target))
            if target_phase is not None and _set_earlier_phase(
                edge.source, target_phase, phases_by_node_id, operation_phases
            ):
                changed = True

    return tuple(
        (operation, operation_phases[id(operation)])
        for operation in operations
        if id(operation) in operation_phases
    )


def _earliest_consumer_phase(
    consumers: tuple[ValueUse, ...], phases_by_node_id: dict[int, int]
) -> int | None:
    phases = [
        phases_by_node_id[id(consumer.node)]
        for consumer in consumers
        if id(consumer.node) in phases_by_node_id
    ]
    return min(phases, default=None)


def _set_earlier_phase(
    node: CombinationalNode,
    requirement: int,
    phases_by_node_id: dict[int, int],
    operation_phases: dict[int, int],
) -> bool:
    current = phases_by_node_id.get(id(node))
    if current is not None and current <= requirement:
        return False
    phases_by_node_id[id(node)] = requirement
    operation_phases[id(node)] = requirement
    return True
