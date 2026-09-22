"""M7B structural SystemVerilog-emission contract tests."""
from __future__ import annotations

from svcsp_compiler.async_microarchitecture import lower_microarchitecture
from svcsp_compiler.async_rtl_codegen import emit_async_systemverilog
from svcsp_compiler.async_template_binding import bind_async_templates
from svcsp_compiler.behavioral_ir import (
    Assign,
    BehavioralModule,
    ChannelEndpoint,
    Expression,
    If,
    ONE_BIT,
    PayloadType,
    PayloadWidth,
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


_BIT = PayloadType("logic", ONE_BIT)
_BYTE = PayloadType("logic", PayloadWidth(bits=8))


class _Program:
    def __init__(self) -> None:
        self.variables: dict[str, Variable] = {}

    def variable(self, name: str, payload_type: PayloadType = _BIT) -> Variable:
        return self.variables.setdefault(
            name, Variable(name, (), SourceLocation("async_codegen.sv", 1, 1), payload_type),
        )

    def name(self, name: str) -> Expression:
        variable = self.variable(name)
        return Expression("name", value=name, variable=variable)

    def receive(self, channel: str, target: str, payload_type: PayloadType = _BIT) -> Receive:
        return Receive(ChannelEndpoint(channel, payload_type=payload_type), self.variable(target, payload_type))

    def send(self, channel: str, value: Expression, payload_type: PayloadType = _BIT) -> Send:
        return Send(ChannelEndpoint(channel, payload_type=payload_type), value)

    def module(self, body) -> BehavioralModule:
        return BehavioralModule("async_codegen", body, (), tuple(self.variables.values()))


def _bound(program: _Program, body):
    architecture = lower_microarchitecture(analyze_semantics(decompose_transaction(
        extract_transaction(program.module(body)),
    )))
    return bind_async_templates(architecture)


def _width(width: PayloadWidth) -> str:
    assert width.bits is not None
    return "" if width.bits == 1 else f" [{width.bits - 1}:0]"


def _assert_exact_binding_rendered(bound, rtl: str) -> None:
    external = {port.signal_id for port in bound.module_ports}
    for port in bound.module_ports:
        assert f"{port.direction} logic{_width(port.width)} {port.name}" in rtl
    for signal in bound.signals:
        if signal.id not in external:
            declaration = f"logic{_width(signal.width)} {signal.id};"
            assert rtl.count(declaration) == 1
    for instance in bound.instances:
        assert f"{instance.component} {instance.id}" in rtl
    for binding in bound.parameter_bindings:
        value = binding.value.bits if binding.value.bits is not None else binding.value.symbolic
        assert f".{binding.formal_name}({value})" in rtl
    for binding in bound.port_bindings:
        assert f".{binding.formal_name}({binding.actual_signal_id})" in rtl
    assert "PipelineGraph" not in rtl
    assert "NormalizedModule" not in rtl
    assert "source_sequence" not in rtl


def test_1r1s_emits_all_bound_ports_signals_instances_and_bindings() -> None:
    program = _Program()
    bound = _bound(program, Sequence((program.receive("A", "a"), program.send("B", program.name("a")))))
    rtl = emit_async_systemverilog(bound)

    assert rtl.startswith("module async_codegen (")
    _assert_exact_binding_rendered(bound, rtl)


def test_2r1s_emits_each_input_handshake_without_source_order_topology() -> None:
    program = _Program()
    bound = _bound(program, Sequence((
        program.receive("A", "a"),
        program.receive("B", "b"),
        program.send("C", Expression("literal", value="1'b0")),
    )))
    rtl = emit_async_systemverilog(bound)

    _assert_exact_binding_rendered(bound, rtl)
    assert ".N(2)" in rtl
    assert ".input_req(input_join_req)" in rtl
    assert ".input_ack(input_join_ack)" in rtl
    assert ".input_req_0(" not in rtl and ".input_ack_0(" not in rtl


def test_1r2s_emits_independent_output_branches_storage_and_completion() -> None:
    program = _Program()
    expression = Expression("binary", operator="+", operands=(program.name("a"), Expression("literal", value="1'b1")))
    bound = _bound(program, Sequence((
        program.receive("A", "a"),
        Assign(program.variable("y"), expression),
        program.send("B", program.name("y")),
        program.send("C", program.name("y")),
    )))
    rtl = emit_async_systemverilog(bound)

    _assert_exact_binding_rendered(bound, rtl)
    assert "four_phase_output_fork output_fork" in rtl
    assert "four_phase_output_completion output_completion" in rtl
    assert rtl.count("bundled_data_storage storage_") == 2
    assert rtl.count("bundled_data_matched_delay matched_delay_") == 2
    assert rtl.count(".M(2)") == 2
    assert ".launch(output_fork_launch)" in rtl
    assert ".complete(output_fork_complete)" in rtl
    assert ".launch_0(" not in rtl and ".complete_0(" not in rtl


def test_2r2s_emits_all_m6_join_fork_and_storage_connectivity() -> None:
    program = _Program()
    bound = _bound(program, Sequence((
        program.receive("A", "a"),
        program.receive("B", "b"),
        program.send("C", program.name("a")),
        program.send("D", program.name("b")),
    )))

    _assert_exact_binding_rendered(bound, emit_async_systemverilog(bound))


def test_conditional_communications_emit_exact_en_recv_and_en_send_bindings() -> None:
    program = _Program()
    select = program.name("select")
    bound = _bound(program, Sequence((
        If(select, program.receive("A", "a"), Skip()),
        If(select, program.send("B", program.name("a")), Skip()),
    )))
    rtl = emit_async_systemverilog(bound)

    _assert_exact_binding_rendered(bound, rtl)
    assert "en_receive_stage en_receive_stage_0" in rtl
    assert "en_send_stage en_send_stage_0" in rtl
    assert ".enable_req(enable_channel_0_req)" in rtl
    assert ".enable_data(enable_channel_0_data)" in rtl


def test_external_widths_and_emission_order_are_deterministic() -> None:
    first_program = _Program()
    second_program = _Program()
    first = _bound(first_program, Sequence((
        first_program.receive("A", "a", _BYTE),
        first_program.send("B", first_program.name("a"), _BYTE),
    )))
    second = _bound(second_program, Sequence((
        second_program.receive("A", "a", _BYTE),
        second_program.send("B", second_program.name("a"), _BYTE),
    )))

    first_rtl = emit_async_systemverilog(first)
    assert first_rtl == emit_async_systemverilog(second)
    assert "input logic [7:0] channel_A_receive_payload" in first_rtl
    assert "output logic [7:0] channel_B_send_payload" in first_rtl
