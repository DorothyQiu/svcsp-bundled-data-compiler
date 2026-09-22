"""M6 asynchronous microarchitecture-lowering contract tests."""
from __future__ import annotations

from svcsp_compiler.async_microarchitecture import (
    AsyncMicroarchitecture,
    BufferStyle,
    CombinationalBlock,
    EnableAvailability,
    EnableChannel,
    EnReceiveStage,
    EnSendStage,
    HandshakeProtocol,
    InputJoin,
    InputPort,
    MatchedDelayRequirement,
    OutputFork,
    OutputPort,
    StageStorage,
    StorageSlot,
    TimingModel,
    lower_microarchitecture,
)
from svcsp_compiler.behavioral_ir import (
    Assign,
    BehavioralModule,
    ChannelEndpoint,
    Expression,
    If,
    ONE_BIT,
    PayloadType,
    Receive,
    Send,
    Sequence,
    Skip,
    SourceLocation,
    Variable,
)
from svcsp_compiler.communication_decomposition import decompose_transaction
from svcsp_compiler.semantic_analysis import analyze_semantics
from svcsp_compiler.transaction import extract_transaction


_TYPE = PayloadType("logic", ONE_BIT)


class _Program:
    def __init__(self) -> None:
        self.variables: dict[str, Variable] = {}

    def variable(self, name: str) -> Variable:
        return self.variables.setdefault(
            name, Variable(name, (), SourceLocation("microarchitecture.sv", 1, 1), _TYPE),
        )

    def name(self, name: str) -> Expression:
        variable = self.variable(name)
        return Expression("name", value=name, variable=variable)

    def receive(self, channel: str, target: str) -> Receive:
        return Receive(ChannelEndpoint(channel), self.variable(target))

    def send(self, channel: str, value: Expression) -> Send:
        return Send(ChannelEndpoint(channel), value)

    def assign(self, target: str, value: Expression) -> Assign:
        return Assign(self.variable(target), value)

    def module(self, body) -> BehavioralModule:
        return BehavioralModule("microarchitecture", body, (), tuple(self.variables.values()))


def _validated(program: _Program, body):
    return analyze_semantics(decompose_transaction(extract_transaction(program.module(body))))


def _lower(program: _Program, body) -> AsyncMicroarchitecture:
    return lower_microarchitecture(_validated(program, body))


def test_1r1s_is_one_bundled_data_four_phase_async_stage() -> None:
    program = _Program()
    validated = _validated(program, Sequence((program.receive("A", "a"), program.send("B", program.name("a")))))
    architecture = lower_microarchitecture(validated)

    assert isinstance(architecture, AsyncMicroarchitecture)
    assert architecture.validated is validated
    assert architecture.protocol is HandshakeProtocol.FOUR_PHASE
    assert architecture.timing_model is TimingModel.BUNDLED_DATA
    assert isinstance(architecture.input_join, InputJoin)
    assert isinstance(architecture.output_fork, OutputFork)
    assert isinstance(architecture.storage, StageStorage)
    assert len(architecture.input_join.inputs) == 1
    assert len(architecture.output_fork.outputs) == 1


def test_2r1s_input_join_waits_for_both_arrivals_without_serializing_them() -> None:
    program = _Program()
    architecture = _lower(program, Sequence((
        program.receive("A", "a"),
        program.receive("B", "b"),
        program.send("C", Expression("literal", value="1'b0")),
    )))

    join = architecture.input_join
    first, second = join.inputs
    assert isinstance(first, InputPort) and isinstance(second, InputPort)
    assert join.serializes_inputs is False
    assert join.is_ready((first,)) is False
    assert join.is_ready((second,)) is False
    assert join.is_ready((first, second)) is True
    assert join.is_ready((second, first)) is True


def test_1r2s_output_fork_launches_branches_independently_and_completes_after_all() -> None:
    program = _Program()
    architecture = _lower(program, Sequence((
        program.receive("A", "a"),
        program.send("B", program.name("a")),
        program.send("C", program.name("a")),
    )))

    fork = architecture.output_fork
    first, second = fork.outputs
    assert isinstance(first, OutputPort) and isinstance(second, OutputPort)
    assert fork.serializes_outputs is False
    assert fork.can_launch(first, ()) is True
    assert fork.can_launch(second, ()) is True
    assert fork.can_launch(second, (first,)) is True
    assert fork.is_complete((first,)) is False
    assert fork.is_complete((first, second)) is True


def test_2r2s_has_one_two_input_join_and_two_independent_output_branches() -> None:
    program = _Program()
    architecture = _lower(program, Sequence((
        program.receive("A", "a"),
        program.receive("B", "b"),
        program.send("C", program.name("a")),
        program.send("D", program.name("b")),
    )))

    assert len(architecture.input_join.inputs) == 2
    assert len(architecture.output_fork.outputs) == 2
    assert architecture.input_join.serializes_inputs is False
    assert architecture.output_fork.serializes_outputs is False


def test_storage_has_one_slot_per_body_send_and_retains_each_payload_until_its_output_completes() -> None:
    program = _Program()
    validated = _validated(program, Sequence((
        program.receive("A", "a"),
        program.send("B", program.name("a")),
        program.send("C", program.name("a")),
    )))
    architecture = lower_microarchitecture(validated)

    assert len(architecture.storage.slots) == len(validated.decomposed.body_sends) == 2
    for slot, output, body_send in zip(architecture.storage.slots, architecture.output_fork.outputs,
                                       validated.decomposed.body_sends):
        assert isinstance(slot, StorageSlot)
        assert slot.body_send is body_send
        assert slot.retained_until is output


def test_en_recv_and_en_send_are_placed_on_their_exact_body_ports() -> None:
    program = _Program()
    select = program.name("sel")
    conditional_receive = program.receive("A", "a")
    conditional_send = program.send("B", program.name("a"))
    validated = _validated(program, Sequence((
        If(select, conditional_receive, Skip()),
        If(select, conditional_send, Skip()),
    )))
    architecture = lower_microarchitecture(validated)

    input_port = architecture.input_join.inputs[0]
    output_port = architecture.output_fork.outputs[0]
    assert input_port.body_receive is validated.decomposed.body_receives[0]
    assert input_port.en_receive is validated.decomposed.en_receives[0]
    assert input_port.en_receive.body_receive is input_port.body_receive
    assert output_port.body_send is validated.decomposed.body_sends[0]
    assert output_port.en_send is validated.decomposed.en_sends[0]
    assert output_port.en_send.body_send is output_port.body_send


def test_matched_delay_is_symbolic_for_combinational_and_output_payload_logic() -> None:
    program = _Program()
    expression = Expression("binary", operator="+", operands=(program.name("a"), Expression("literal", value="1'b1")))
    architecture = _lower(program, Sequence((
        program.receive("A", "a"),
        program.assign("y", expression),
        program.send("B", program.name("y")),
    )))

    assert isinstance(architecture.combinational, CombinationalBlock)
    assert architecture.combinational.operations[0].operation.value is expression
    assert architecture.matched_delays
    assert all(isinstance(requirement, MatchedDelayRequirement) and requirement.value is None
               for requirement in architecture.matched_delays)


def test_each_output_has_a_matched_delay_tied_to_join_storage_and_its_own_launch() -> None:
    program = _Program()
    expression = Expression(
        "binary", operator="+",
        operands=(program.name("a"), Expression("literal", value="1'b1")),
    )
    architecture = _lower(program, Sequence((
        program.receive("A", "a"),
        program.assign("y", expression),
        program.send("B", program.name("y")),
        program.send("C", program.name("y")),
    )))

    assert len(architecture.matched_delays) == 2
    for slot, output in zip(architecture.storage.slots, architecture.output_fork.outputs):
        requirement = next(
            delay for delay in architecture.matched_delays if delay.output_port is output
        )
        assert requirement.storage_slot is slot
        assert requirement.input_join is architecture.input_join
        assert requirement.value is None


def test_lowering_preserves_exact_m5_m4_identities_without_pipeline_structure() -> None:
    program = _Program()
    validated = _validated(program, Sequence((program.receive("A", "a"), program.send("B", program.name("a")))))
    architecture = lower_microarchitecture(validated)

    assert architecture.validated is validated
    assert architecture.input_join.inputs[0].body_receive is validated.decomposed.body_receives[0]
    assert architecture.output_fork.outputs[0].body_send is validated.decomposed.body_sends[0]
    assert architecture.combinational.operations is validated.decomposed.body_combinational
    assert not hasattr(architecture, "pipeline")
    assert not hasattr(architecture, "stage_order")


def test_architecture_explicitly_selects_half_buffer_style() -> None:
    program = _Program()
    architecture = _lower(program, Sequence((program.receive("A", "a"), program.send("B", program.name("a")))))

    assert architecture.buffer_style is BufferStyle.HALF_BUFFER


def test_conditional_receive_has_a_pre_input_enable_channel_and_en_receive_stage() -> None:
    program = _Program()
    select = program.name("select")
    receive = program.receive("A", "a")
    validated = _validated(program, Sequence((
        If(select, receive, Skip()),
        If(select, program.send("B", program.name("a")), Skip()),
    )))
    architecture = lower_microarchitecture(validated)

    body_receive = validated.decomposed.body_receives[0]
    en_receive = validated.decomposed.en_receives[0]
    assert len(architecture.enable_channels) == 2
    channel = next(item for item in architecture.enable_channels if item.enable is en_receive.enable)
    assert isinstance(channel, EnableChannel)
    assert channel.enable is en_receive.enable
    assert channel.width is ONE_BIT
    assert channel.availability is EnableAvailability.PRE_INPUT
    stage = next(item for item in architecture.en_receive_stages if item.en_receive is en_receive)
    assert isinstance(stage, EnReceiveStage)
    assert stage.enable_channel is channel
    assert stage.body_receive is body_receive
    assert stage.input_port is architecture.input_join.inputs[0]
    assert stage.disabled_payload is body_receive.disabled_payload


def test_conditional_send_has_a_post_input_enable_channel_and_en_send_stage() -> None:
    program = _Program()
    select = program.name("select")
    send = program.send("B", Expression("literal", value="1'b0"))
    validated = _validated(program, Sequence((
        program.receive("A", "a"),
        If(select, send, Skip()),
    )))
    architecture = lower_microarchitecture(validated)

    body_send = validated.decomposed.body_sends[0]
    en_send = validated.decomposed.en_sends[0]
    channel = next(item for item in architecture.enable_channels if item.enable is en_send.enable)
    assert isinstance(channel, EnableChannel)
    assert channel.enable is en_send.enable
    assert channel.width is ONE_BIT
    assert channel.availability is EnableAvailability.POST_INPUT
    stage = next(item for item in architecture.en_send_stages if item.en_send is en_send)
    assert isinstance(stage, EnSendStage)
    assert stage.enable_channel is channel
    assert stage.body_send is body_send
    assert stage.output_port is architecture.output_fork.outputs[0]


def test_en_send_consumes_the_unconditional_body_output_without_dummy_payload_semantics() -> None:
    program = _Program()
    send = program.send("B", Expression("literal", value="1'b0"))
    validated = _validated(program, Sequence((
        program.receive("A", "a"),
        If(program.name("select"), send, Skip()),
    )))
    architecture = lower_microarchitecture(validated)

    stage = architecture.en_send_stages[0]
    assert stage.body_send is validated.decomposed.body_sends[0]
    assert stage.output_port.body_send is stage.body_send
    assert not hasattr(stage, "disabled_payload")
    assert not hasattr(stage, "invalid_payload")


def test_nested_and_multiple_conditional_communications_get_distinct_enable_channels() -> None:
    program = _Program()
    outer = program.name("outer")
    inner = program.name("inner")
    nested_receive = program.receive("A", "a")
    nested_send = program.send("B", Expression("literal", value="1'b0"))
    conditional_send = program.send("C", Expression("literal", value="1'b1"))
    architecture = _lower(program, Sequence((
        If(outer, If(inner, nested_receive, Skip()), Skip()),
        If(outer, If(inner, nested_send, Skip()), Skip()),
        If(outer, conditional_send, Skip()),
    )))

    enables = tuple(channel.enable for channel in architecture.enable_channels)
    assert len(enables) == 3
    assert len({id(enable) for enable in enables}) == 3
    assert {channel.availability for channel in architecture.enable_channels} == {
        EnableAvailability.PRE_INPUT,
        EnableAvailability.POST_INPUT,
    }


def test_body_stage_has_one_matched_delay_per_output_including_trivial_payloads() -> None:
    program = _Program()
    architecture = _lower(program, Sequence((
        program.receive("A", "a"),
        program.send("B", program.name("a")),
        program.send("C", Expression("literal", value="1'b0")),
    )))

    assert len(architecture.matched_delays) == len(architecture.output_fork.outputs) == 2
    for slot, output in zip(architecture.storage.slots, architecture.output_fork.outputs):
        requirement = next(item for item in architecture.matched_delays if item.output_port is output)
        assert requirement.storage_slot is slot
        assert requirement.input_join is architecture.input_join
        assert requirement.output_port is output


def test_en_receive_stage_contract_is_enable_first_conditional_external_and_unconditional_body() -> None:
    program = _Program()
    select = program.name("select")
    receive = program.receive("A", "a")
    architecture = _lower(program, Sequence((
        If(select, receive, Skip()),
        If(select, program.send("B", program.name("a")), Skip()),
    )))

    stage = architecture.en_receive_stages[0]
    assert stage.consumes_enable_first is True
    assert stage.external_receive_when_enabled is True
    assert stage.body_output_unconditional is True
    assert stage.disabled_payload is stage.body_receive.disabled_payload
    assert stage.requires_payload_storage is True
    assert stage.requires_body_output_matched_delay is True


def test_en_send_stage_contract_always_consumes_body_and_only_delays_enabled_external_output() -> None:
    program = _Program()
    send = program.send("B", Expression("literal", value="1'b0"))
    architecture = _lower(program, Sequence((
        program.receive("A", "a"),
        If(program.name("select"), send, Skip()),
    )))

    stage = architecture.en_send_stages[0]
    assert stage.consumes_enable_first is True
    assert stage.body_input_unconditional is True
    assert stage.external_send_when_enabled is True
    assert stage.suppresses_external_when_disabled is True
    assert stage.locally_consumes_when_disabled is True
    assert not hasattr(stage, "disabled_payload")
    assert not hasattr(stage, "invalid_payload")
    assert stage.requires_payload_storage is True
    assert stage.requires_enabled_external_output_matched_delay is True
    assert not hasattr(stage, "requires_disabled_output_matched_delay")


def test_enable_stage_contracts_preserve_the_selected_half_buffer_four_phase_bundled_data_target() -> None:
    program = _Program()
    select = program.name("select")
    architecture = _lower(program, Sequence((
        If(select, program.receive("A", "a"), Skip()),
        If(select, program.send("B", program.name("a")), Skip()),
    )))

    assert architecture.buffer_style is BufferStyle.HALF_BUFFER
    assert architecture.protocol is HandshakeProtocol.FOUR_PHASE
    assert architecture.timing_model is TimingModel.BUNDLED_DATA
