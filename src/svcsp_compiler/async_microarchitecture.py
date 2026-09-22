"""M6 lowering from one semantically validated transaction to one async stage."""
from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

from . import behavioral_ir as behavioral
from .communication_decomposition import BodyReceive, BodySend, EnReceive, EnSend
from .semantic_analysis import SemanticallyValidatedTransaction
from .transaction import RegionOperation


class HandshakeProtocol(str, Enum):
    FOUR_PHASE = 'four_phase'


class TimingModel(str, Enum):
    BUNDLED_DATA = 'bundled_data'


@dataclass(frozen=True)
class InputPort:
    body_receive: BodyReceive
    en_receive: EnReceive | None


@dataclass(frozen=True)
class InputJoin:
    inputs: tuple[InputPort, ...]
    serializes_inputs: bool = False

    def is_ready(self, arrived: tuple[InputPort, ...]) -> bool:
        """All inputs are required, but their arrival order has no meaning."""

        return len(arrived) == len(self.inputs) and set(arrived) == set(self.inputs)


@dataclass(frozen=True)
class CombinationalBlock:
    operations: tuple[RegionOperation, ...]


@dataclass(frozen=True)
class OutputPort:
    body_send: BodySend
    en_send: EnSend | None


@dataclass(frozen=True)
class OutputFork:
    outputs: tuple[OutputPort, ...]
    serializes_outputs: bool = False

    def can_launch(self, output: OutputPort, completed: tuple[OutputPort, ...]) -> bool:
        """An uncompleted output branch can launch without waiting for siblings."""

        return output in self.outputs and output not in completed and set(completed) <= set(self.outputs)

    def is_complete(self, completed: tuple[OutputPort, ...]) -> bool:
        """Stage storage can be released only after every output branch completes."""

        return set(completed) == set(self.outputs)


@dataclass(frozen=True)
class StorageSlot:
    body_send: BodySend
    retained_until: OutputPort


@dataclass(frozen=True)
class StageStorage:
    slots: tuple[StorageSlot, ...]


@dataclass(frozen=True)
class MatchedDelayRequirement:
    id: str
    operations: tuple[RegionOperation, ...]
    storage_slot: StorageSlot
    input_join: InputJoin
    output_port: OutputPort
    value: None = None


@dataclass(frozen=True)
class AsyncMicroarchitecture:
    """One fully selected asynchronous stage for one validated transaction."""

    validated: SemanticallyValidatedTransaction
    protocol: HandshakeProtocol
    timing_model: TimingModel
    input_join: InputJoin
    combinational: CombinationalBlock
    storage: StageStorage
    output_fork: OutputFork
    matched_delays: tuple[MatchedDelayRequirement, ...]


def lower_microarchitecture(validated: SemanticallyValidatedTransaction) -> AsyncMicroarchitecture:
    """Select the bundled-data, four-phase architecture for one transaction."""

    if not isinstance(validated, SemanticallyValidatedTransaction):
        raise TypeError('expected a SemanticallyValidatedTransaction')

    decomposed = validated.decomposed
    en_receives = {adapter.body_receive: adapter for adapter in decomposed.en_receives}
    en_sends = {adapter.body_send: adapter for adapter in decomposed.en_sends}
    inputs = tuple(InputPort(body_receive, en_receives.get(body_receive))
                   for body_receive in decomposed.body_receives)
    input_join = InputJoin(inputs)
    outputs = tuple(OutputPort(body_send, en_sends.get(body_send))
                    for body_send in decomposed.body_sends)
    output_fork = OutputFork(outputs)
    storage = StageStorage(tuple(
        StorageSlot(body_send, output)
        for body_send, output in zip(decomposed.body_sends, outputs)
    ))
    combinational = CombinationalBlock(decomposed.body_combinational)
    matched_delays = _matched_delays(combinational.operations, storage, input_join)
    return AsyncMicroarchitecture(
        validated,
        HandshakeProtocol.FOUR_PHASE,
        TimingModel.BUNDLED_DATA,
        input_join,
        combinational,
        storage,
        output_fork,
        matched_delays,
    )


def _matched_delays(combinational: tuple[RegionOperation, ...], storage: StageStorage,
                    input_join: InputJoin) -> tuple[MatchedDelayRequirement, ...]:
    operations = [operation for operation in combinational
                  if isinstance(operation.operation, behavioral.Assign) and _nontrivial(operation.operation.value)]
    operations.extend(slot.body_send.source for slot in storage.slots
                      if _nontrivial(slot.body_send.source.operation.value))
    if not operations:
        return ()
    return tuple(
        MatchedDelayRequirement(
            f'matched_delay_{index}', tuple(operations), slot, input_join, slot.retained_until,
        )
        for index, slot in enumerate(storage.slots)
    )


def _nontrivial(expression: behavioral.Expression) -> bool:
    return expression.form not in {'literal', 'name', 'parameter'}
