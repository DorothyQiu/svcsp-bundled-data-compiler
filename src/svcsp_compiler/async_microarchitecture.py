"""M6 lowering from one semantically validated transaction to one async stage."""
from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

from . import behavioral_ir as behavioral
from .communication_decomposition import BodyReceive, BodySend, Enable, EnReceive, EnSend, InvalidPayload
from .semantic_analysis import SemanticallyValidatedTransaction
from .transaction import RegionOperation


class HandshakeProtocol(str, Enum):
    FOUR_PHASE = 'four_phase'


class TimingModel(str, Enum):
    BUNDLED_DATA = 'bundled_data'


class BufferStyle(str, Enum):
    HALF_BUFFER = 'half_buffer'


class EnableAvailability(str, Enum):
    PRE_INPUT = 'pre_input'
    POST_INPUT = 'post_input'


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
class EnableTokenProducer:
    """One unconditional BODY control send carrying an M4 Enable condition."""

    enable: Enable
    condition: behavioral.Expression
    transaction: SemanticallyValidatedTransaction
    unconditional: bool
    depends_on_input_join: bool
    available_before_controlled_body_input: bool
    is_body_control_output: bool
    participates_in_transaction_completion: bool
    participates_in_body_completion: bool


@dataclass(frozen=True)
class EnableChannel:
    """One concrete one-bit transport for one M4 logical Enable occurrence."""

    id: str
    enable: Enable
    width: behavioral.PayloadWidth
    availability: EnableAvailability
    producer: EnableTokenProducer


@dataclass(frozen=True)
class EnReceiveStage:
    """The pre-input stage that turns an enable token into one BODY input token."""

    en_receive: EnReceive
    enable_channel: EnableChannel
    body_receive: BodyReceive
    input_port: InputPort
    disabled_payload: InvalidPayload
    consumes_enable_first: bool = True
    external_receive_when_enabled: bool = True
    body_output_unconditional: bool = True
    requires_payload_storage: bool = True
    requires_body_output_matched_delay: bool = True


@dataclass(frozen=True)
class EnSendStage:
    """The post-input stage that always consumes BODY output and may suppress I/O."""

    en_send: EnSend
    enable_channel: EnableChannel
    body_send: BodySend
    output_port: OutputPort
    consumes_enable_first: bool = True
    body_input_unconditional: bool = True
    external_send_when_enabled: bool = True
    suppresses_external_when_disabled: bool = True
    locally_consumes_when_disabled: bool = True
    requires_payload_storage: bool = True
    requires_enabled_external_output_matched_delay: bool = True


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
    buffer_style: BufferStyle
    enable_channels: tuple[EnableChannel, ...]
    en_receive_stages: tuple[EnReceiveStage, ...]
    en_send_stages: tuple[EnSendStage, ...]


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
    enable_channels, en_receive_stages, en_send_stages = _enable_stages(
        validated, decomposed.en_receives, decomposed.en_sends, inputs, outputs,
    )
    return AsyncMicroarchitecture(
        validated,
        HandshakeProtocol.FOUR_PHASE,
        TimingModel.BUNDLED_DATA,
        input_join,
        combinational,
        storage,
        output_fork,
        matched_delays,
        BufferStyle.HALF_BUFFER,
        enable_channels,
        en_receive_stages,
        en_send_stages,
    )


def _enable_stages(validated: SemanticallyValidatedTransaction,
                   en_receives: tuple[EnReceive, ...], en_sends: tuple[EnSend, ...],
                   inputs: tuple[InputPort, ...], outputs: tuple[OutputPort, ...]) -> tuple[
                       tuple[EnableChannel, ...], tuple[EnReceiveStage, ...], tuple[EnSendStage, ...]]:
    """Lower each M4 Enable occurrence independently, even for equal guards."""

    channels: list[EnableChannel] = []
    receive_stages: list[EnReceiveStage] = []
    send_stages: list[EnSendStage] = []
    input_by_receive = {input_port.body_receive: input_port for input_port in inputs}
    output_by_send = {output_port.body_send: output_port for output_port in outputs}

    for adapter in en_receives:
        producer = EnableTokenProducer(
            adapter.enable, adapter.enable.condition, validated, True,
            False, True, True, True, False,
        )
        channel = EnableChannel(
            f'enable_channel_{len(channels)}', adapter.enable, behavioral.ONE_BIT,
            EnableAvailability.PRE_INPUT, producer,
        )
        channels.append(channel)
        body_receive = adapter.body_receive
        disabled_payload = body_receive.disabled_payload
        if disabled_payload is None:
            raise ValueError('conditional Receive requires InvalidPayload semantics')
        receive_stages.append(EnReceiveStage(
            adapter, channel, body_receive, input_by_receive[body_receive], disabled_payload,
        ))

    for adapter in en_sends:
        producer = EnableTokenProducer(
            adapter.enable, adapter.enable.condition, validated, True,
            True, False, True, True, True,
        )
        channel = EnableChannel(
            f'enable_channel_{len(channels)}', adapter.enable, behavioral.ONE_BIT,
            EnableAvailability.POST_INPUT, producer,
        )
        channels.append(channel)
        body_send = adapter.body_send
        send_stages.append(EnSendStage(
            adapter, channel, body_send, output_by_send[body_send],
        ))

    return tuple(channels), tuple(receive_stages), tuple(send_stages)


def _matched_delays(combinational: tuple[RegionOperation, ...], storage: StageStorage,
                    input_join: InputJoin) -> tuple[MatchedDelayRequirement, ...]:
    operations = [operation for operation in combinational
                  if isinstance(operation.operation, behavioral.Assign) and _nontrivial(operation.operation.value)]
    operations.extend(slot.body_send.source for slot in storage.slots
                      if _nontrivial(slot.body_send.source.operation.value))
    return tuple(
        MatchedDelayRequirement(
            f'matched_delay_{index}', tuple(operations), slot, input_join, slot.retained_until,
        )
        for index, slot in enumerate(storage.slots)
    )


def _nontrivial(expression: behavioral.Expression) -> bool:
    return expression.form not in {'literal', 'name', 'parameter'}
