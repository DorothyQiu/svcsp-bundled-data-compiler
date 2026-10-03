"""Logical execution partitioning from dependency-analysis facts."""
from __future__ import annotations

from dataclasses import dataclass

from pyslang.ast import VariableSymbol
from pyslang.syntax import ImplicitAnsiPortSyntax

from .analysis import (
    AnalysisFacts,
    CommunicationNode,
    DefinitionNode,
    ValueOrigin,
    ValueUse,
)
from .usg import AssignNode, ControlEdge, PredicateNode, ReceiveNode, SendNode, USGNode


CombinationalNode = AssignNode | PredicateNode


@dataclass(frozen=True, slots=True)
class PersistentStateRequirement:
    """One local value's semantic process-entry and process-exit state facts."""

    symbol: VariableSymbol
    entry_dependent_uses: tuple[ValueUse, ...]
    exit_reaching_definitions: tuple[DefinitionNode, ...]
    entry_value_may_reach_at_exit: bool


@dataclass(frozen=True, slots=True)
class StateOutputSurvivalRequirement:
    """One exit-reaching definition's semantic survival to process exit."""

    symbol: VariableSymbol
    definition: DefinitionNode
    boundary_phase: int


@dataclass(frozen=True, slots=True)
class DataSurvivalRequirement:
    """One graph-local definition's semantic survival across one phase boundary."""

    producer: DefinitionNode
    consumer: ValueUse
    boundary_phase: int


@dataclass(frozen=True, slots=True)
class ControlSurvivalRequirement:
    """One predicate result's semantic survival across one phase boundary."""

    predicate: PredicateNode
    controlled_operation: USGNode
    boundary_phase: int


@dataclass(frozen=True, slots=True)
class LogicalExecutionPhases:
    """Logical phase assignments for communication and placed combinational nodes.

    Equal phase numbers do not imply concurrent communication. Communication
    ordering remains represented by the analysis facts that this plan consumes.
    """

    communication_phases: tuple[tuple[CommunicationNode, int], ...]
    operation_phases: tuple[tuple[CombinationalNode, int], ...]
    process_exit_phase: int
    data_survival_requirements: tuple[DataSurvivalRequirement, ...]
    control_survival_requirements: tuple[ControlSurvivalRequirement, ...]
    persistent_state_requirements: tuple[PersistentStateRequirement, ...]
    state_output_survival_requirements: tuple[StateOutputSurvivalRequirement, ...]

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
    process_exit_phase = max(
        (phase for _, phase in communication_phases), default=0
    )
    exit_reaching_definitions = _local_exit_reaching_definitions(facts)
    operation_phases = _combinational_operation_phases(
        facts,
        communication_phases,
        control_edges,
        tuple(definition for _, definition in exit_reaching_definitions),
        process_exit_phase,
    )
    phases_by_node_id = {
        id(node): phase for node, phase in communication_phases + operation_phases
    }
    data_requirements = _data_survival_requirements(facts, phases_by_node_id)
    control_requirements = _control_survival_requirements(
        control_edges, phases_by_node_id
    )
    persistent_state_requirements = _persistent_state_requirements(facts)
    state_output_survival_requirements = _state_output_survival_requirements(
        exit_reaching_definitions, phases_by_node_id, process_exit_phase
    )
    return LogicalExecutionPhases(
        communication_phases,
        operation_phases,
        process_exit_phase,
        data_requirements,
        control_requirements,
        persistent_state_requirements,
        state_output_survival_requirements,
    )


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
    exit_reaching_definitions: tuple[DefinitionNode, ...],
    process_exit_phase: int,
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
    exit_reaching_assignments = {
        id(definition)
        for definition in exit_reaching_definitions
        if isinstance(definition, AssignNode)
    }

    changed = True
    while changed:
        changed = False
        for definition, consumers in facts.definition_consumers:
            if not isinstance(definition, AssignNode):
                continue
            requirement = _earliest_consumer_phase(consumers, phases_by_node_id)
            if id(definition) in exit_reaching_assignments:
                requirement = min(
                    process_exit_phase,
                    requirement if requirement is not None else process_exit_phase,
                )
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


def _data_survival_requirements(
    facts: AnalysisFacts, phases_by_node_id: dict[int, int]
) -> tuple[DataSurvivalRequirement, ...]:
    """Derive per-boundary semantic lifetimes from exact reaching-use facts."""

    requirements: list[DataSurvivalRequirement] = []
    for consumer in facts.value_uses:
        if consumer.origin is not ValueOrigin.GRAPH_LOCAL:
            continue
        consumer_phase = phases_by_node_id.get(id(consumer.node))
        if consumer_phase is None:
            continue
        for producer in consumer.reaching_producers:
            if not isinstance(producer, (ReceiveNode, AssignNode)):
                continue
            producer_phase = phases_by_node_id.get(id(producer))
            if producer_phase is None or producer_phase >= consumer_phase:
                continue
            requirements.extend(
                DataSurvivalRequirement(producer, consumer, boundary_phase)
                for boundary_phase in range(producer_phase, consumer_phase)
            )
    return tuple(requirements)


def _control_survival_requirements(
    control_edges: tuple[ControlEdge, ...], phases_by_node_id: dict[int, int]
) -> tuple[ControlSurvivalRequirement, ...]:
    """Derive per-boundary semantic lifetimes from existing CONTROL edges."""

    requirements: list[ControlSurvivalRequirement] = []
    for edge in control_edges:
        if not isinstance(edge.source, PredicateNode):
            continue
        predicate_phase = phases_by_node_id.get(id(edge.source))
        controlled_phase = phases_by_node_id.get(id(edge.target))
        if (
            predicate_phase is None
            or controlled_phase is None
            or predicate_phase >= controlled_phase
        ):
            continue
        requirements.extend(
            ControlSurvivalRequirement(edge.source, edge.target, boundary_phase)
            for boundary_phase in range(predicate_phase, controlled_phase)
        )
    return tuple(requirements)


def _persistent_state_requirements(
    facts: AnalysisFacts,
) -> tuple[PersistentStateRequirement, ...]:
    """Derive semantic persistent-state candidates from existing analysis facts."""

    entry_dependent_uses: list[tuple[VariableSymbol, list[ValueUse]]] = []
    for value_use in facts.value_uses:
        if (
            not isinstance(value_use.symbol, VariableSymbol)
            or not value_use.entry_value_may_reach
            or not _is_local_variable(value_use.symbol)
        ):
            continue
        for symbol, uses in entry_dependent_uses:
            if symbol is value_use.symbol:
                uses.append(value_use)
                break
        else:
            entry_dependent_uses.append((value_use.symbol, [value_use]))

    return tuple(
        PersistentStateRequirement(
            symbol,
            tuple(uses),
            facts.exit_reaching_of(symbol).graph_local_definitions,
            facts.exit_reaching_of(symbol).entry_value_may_reach,
        )
        for symbol, uses in entry_dependent_uses
    )


def _local_exit_reaching_definitions(
    facts: AnalysisFacts,
) -> tuple[tuple[VariableSymbol, DefinitionNode], ...]:
    """Return exact local graph definitions that may be consumed at process exit."""

    return tuple(
        (value.symbol, definition)
        for value in facts.exit_reaching_values
        if _is_local_variable(value.symbol)
        for definition in value.graph_local_definitions
        if isinstance(definition, (ReceiveNode, AssignNode))
    )


def _state_output_survival_requirements(
    exit_reaching_definitions: tuple[tuple[VariableSymbol, DefinitionNode], ...],
    phases_by_node_id: dict[int, int],
    process_exit_phase: int,
) -> tuple[StateOutputSurvivalRequirement, ...]:
    """Derive semantic survival of graph-local definitions consumed at exit."""

    requirements: list[StateOutputSurvivalRequirement] = []
    for symbol, definition in exit_reaching_definitions:
        definition_phase = phases_by_node_id.get(id(definition))
        if definition_phase is None or definition_phase >= process_exit_phase:
            continue
        requirements.extend(
            StateOutputSurvivalRequirement(symbol, definition, boundary_phase)
            for boundary_phase in range(definition_phase, process_exit_phase)
        )
    return tuple(requirements)


def _is_local_variable(symbol: VariableSymbol) -> bool:
    return not isinstance(symbol.syntax.parent, ImplicitAnsiPortSyntax)
