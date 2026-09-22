"""M6 asynchronous microarchitecture-lowering contract tests."""
from __future__ import annotations

from svcsp_compiler.async_microarchitecture import (
    AsyncMicroarchitecture,
    CombinationalBlock,
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
