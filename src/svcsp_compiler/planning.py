"""Logical execution partitioning from dependency-analysis facts."""
from __future__ import annotations

from dataclasses import dataclass

from .analysis import AnalysisFacts, CommunicationNode
from .usg import ReceiveNode, SendNode


@dataclass(frozen=True, slots=True)
class LogicalExecutionPhases:
    """Logical phase assignments for communication occurrences only.

    Equal phase numbers do not imply concurrent communication. Communication
    ordering remains represented by the analysis facts that this plan consumes.
    """

    communication_phases: tuple[tuple[CommunicationNode, int], ...]

    def phase_of(self, communication: CommunicationNode) -> int:
        """Return the logical execution phase assigned to ``communication``."""

        for known, phase in self.communication_phases:
            if known is communication:
                return phase
        raise KeyError("communication node is not present in this logical execution plan")


def partition_logical_execution(facts: AnalysisFacts) -> LogicalExecutionPhases:
    """Assign communication phases from already-derived predecessor facts.

    An ordered Send -> Receive transition increments the phase. All other
    communication transitions preserve it. The predecessor facts encode path
    and join behavior, so this function neither traverses nor changes the USG.
    """

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

    return LogicalExecutionPhases(tuple(assignments))
