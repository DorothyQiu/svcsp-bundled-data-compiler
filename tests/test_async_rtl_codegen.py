"""M7B structural SystemVerilog-emission contract tests."""

from __future__ import annotations

import re

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
    Parameter,
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
        self.external_inputs: dict[str, Variable] = {}

    def variable(
        self,
        name: str,
        payload_type: PayloadType = _BIT,
    ) -> Variable:
        return self.variables.setdefault(
            name,
            Variable(
                name,
                (),
                SourceLocation("async_codegen.sv", 1, 1),
                payload_type,
            ),
        )

    def name(self, name: str) -> Expression:
        variable = self.variable(name)
        return Expression("name", value=name, variable=variable)

    def external_name(self, name: str, payload_type: PayloadType = _BIT) -> Expression:
        if name in self.variables:
            raise ValueError(f"{name} is already a local variable")
        variable = self.external_inputs.setdefault(
            name, Variable(name, (), SourceLocation("async_codegen.sv", 1, 1), payload_type),
        )
        return Expression("name", value=name, variable=variable)

    def receive(
        self,
        channel: str,
        target: str,
        payload_type: PayloadType = _BIT,
    ) -> Receive:
        return Receive(
            ChannelEndpoint(channel, payload_type=payload_type),
            self.variable(target, payload_type),
        )

    def send(
        self,
        channel: str,
        value: Expression,
        payload_type: PayloadType = _BIT,
    ) -> Send:
        return Send(
            ChannelEndpoint(channel, payload_type=payload_type),
            value,
        )

    def module(self, body) -> BehavioralModule:
        return BehavioralModule(
            "async_codegen",
            body,
            (),
            tuple(self.variables.values()),
            external_inputs=tuple(self.external_inputs.values()),
        )


def _bound(program: _Program, body):
    architecture = lower_microarchitecture(
        analyze_semantics(
            decompose_transaction(
                extract_transaction(program.module(body)),
            )
        )
    )
    return bind_async_templates(architecture)


def _width(width: PayloadWidth) -> str:
    assert width.bits is not None
    return "" if width.bits == 1 else f" [{width.bits - 1}:0]"


def _variable_signal_id(
    bound,
    variable: Variable,
) -> str:
    bindings = [
        item
        for item in bound.variable_bindings
        if item.variable is variable
    ]
    assert len(bindings) == 1
    return bindings[0].signal_id


def test_external_input_is_rendered_as_a_public_port_not_a_body_variable() -> None:
    program = _Program()
    select = program.external_name("select")
    rtl = emit_async_systemverilog(_bound(program, Sequence((
        If(select, program.receive("A", "a"), Skip()),
        program.send("B", Expression("literal", value="1'b0")),
    ))))

    assert "input logic select" in rtl
    assert "body_var_0_select" not in rtl


def _assert_exact_binding_rendered(
    bound,
    rtl: str,
) -> None:
    external = {
        port.signal_id
        for port in bound.module_ports
    }

    for port in bound.module_ports:
        assert (
            f"{port.direction} logic{_width(port.width)} {port.name}"
            in rtl
        )

    for signal in bound.signals:
        if signal.id not in external:
            declaration = (
                f"logic{_width(signal.width)} {signal.id};"
            )
            assert rtl.count(declaration) == 1

    for instance in bound.instances:
        assert (
            f"{instance.component} {instance.id}"
            in rtl
        )

    for binding in bound.parameter_bindings:
        value = (
            binding.value.bits
            if binding.value.bits is not None
            else binding.value.symbolic
        )
        assert (
            f".{binding.formal_name}({value})"
            in rtl
        )

    for binding in bound.port_bindings:
        assert (
            f".{binding.formal_name}"
            f"({binding.actual_signal_id})"
            in rtl
        )

    assert "source_sequence" not in rtl


def _instance_count(
    rtl: str,
    template: str,
) -> int:
    return sum(
        line.lstrip().startswith(f"{template} ")
        for line in rtl.splitlines()
    )


def _assert_ordinary_body_rtl_topology(
    rtl: str,
    *,
    inputs: int,
    outputs: int,
) -> None:
    assert "input logic reset_n" in rtl

    for stale in (
        "four_phase_input_join",
        "four_phase_output_fork",
        "four_phase_output_completion",
        "bundled_data_storage",
    ):
        assert stale not in rtl

    assert (
        _instance_count(
            rtl,
            "four_phase_half_buffer_controller",
        )
        == 1
    )

    assert (
        _instance_count(
            rtl,
            "bundled_data_latch_bank",
        )
        == outputs
    )

    assert (
        _instance_count(
            rtl,
            "bundled_data_matched_delay",
        )
        == outputs
    )

    assert (
        _instance_count(
            rtl,
            "four_phase_request_join",
        )
        == int(inputs > 1)
    )

    assert (
        _instance_count(
            rtl,
            "four_phase_ack_fanout",
        )
        == int(inputs > 1)
    )

    assert (
        _instance_count(
            rtl,
            "four_phase_request_fanout",
        )
        == int(outputs > 1)
    )

    assert (
        _instance_count(
            rtl,
            "four_phase_ack_join",
        )
        == int(outputs > 1)
    )

    assert (
        rtl.count(".reset_n(reset_n)")
        == 1 + int(inputs > 1) + int(outputs > 1)
    )


def test_1r1s_emits_all_bound_ports_signals_instances_and_bindings() -> None:
    program = _Program()

    bound = _bound(
        program,
        Sequence(
            (
                program.receive("A", "a"),
                program.send(
                    "B",
                    program.name("a"),
                ),
            )
        ),
    )

    rtl = emit_async_systemverilog(bound)

    assert rtl.startswith("module async_codegen (")
    _assert_exact_binding_rendered(bound, rtl)
    _assert_ordinary_body_rtl_topology(
        rtl,
        inputs=1,
        outputs=1,
    )


def test_2r1s_emits_each_input_handshake_without_source_order_topology() -> None:
    program = _Program()

    bound = _bound(
        program,
        Sequence(
            (
                program.receive("A", "a"),
                program.receive("B", "b"),
                program.send(
                    "C",
                    Expression(
                        "literal",
                        value="1'b0",
                    ),
                ),
            )
        ),
    )

    rtl = emit_async_systemverilog(bound)

    _assert_exact_binding_rendered(
        bound,
        rtl,
    )
    _assert_ordinary_body_rtl_topology(
        rtl,
        inputs=2,
        outputs=1,
    )


def test_1r2s_emits_request_fanout_ack_join_storage_and_matched_delays() -> None:
    program = _Program()

    expression = Expression(
        "binary",
        operator="+",
        operands=(
            program.name("a"),
            Expression(
                "literal",
                value="1'b1",
            ),
        ),
    )

    bound = _bound(
        program,
        Sequence(
            (
                program.receive(
                    "A",
                    "a",
                ),
                Assign(
                    program.variable("y"),
                    expression,
                ),
                program.send(
                    "B",
                    program.name("y"),
                ),
                program.send(
                    "C",
                    program.name("y"),
                ),
            )
        ),
    )

    rtl = emit_async_systemverilog(bound)

    _assert_exact_binding_rendered(
        bound,
        rtl,
    )
    _assert_ordinary_body_rtl_topology(
        rtl,
        inputs=1,
        outputs=2,
    )


def test_2r2s_emits_all_m6_join_fork_and_storage_connectivity() -> None:
    program = _Program()

    bound = _bound(
        program,
        Sequence(
            (
                program.receive(
                    "A",
                    "a",
                ),
                program.receive(
                    "B",
                    "b",
                ),
                program.send(
                    "C",
                    program.name("a"),
                ),
                program.send(
                    "D",
                    program.name("b"),
                ),
            )
        ),
    )

    rtl = emit_async_systemverilog(bound)

    _assert_exact_binding_rendered(
        bound,
        rtl,
    )
    _assert_ordinary_body_rtl_topology(
        rtl,
        inputs=2,
        outputs=2,
    )


def test_conditional_communications_emit_exact_en_recv_and_en_send_bindings() -> None:
    program = _Program()

    select = program.external_name("select")

    bound = _bound(
        program,
        Sequence(
            (
                If(
                    select,
                    program.receive(
                        "A",
                        "a",
                    ),
                    Skip(),
                ),
                If(
                    select,
                    program.send(
                        "B",
                        program.name("a"),
                    ),
                    Skip(),
                ),
            )
        ),
    )

    rtl = emit_async_systemverilog(bound)

    _assert_exact_binding_rendered(
        bound,
        rtl,
    )

    assert (
        "en_receive_controller en_receive_controller_0"
        in rtl
    )
    assert (
        "en_send_controller en_send_controller_0"
        in rtl
    )
    assert (
        ".enable_req(enable_channel_0_req)"
        in rtl
    )
    assert (
        ".enable_data(enable_channel_0_data)"
        in rtl
    )


def test_external_widths_and_emission_order_are_deterministic() -> None:
    first_program = _Program()
    second_program = _Program()

    first = _bound(
        first_program,
        Sequence(
            (
                first_program.receive(
                    "A",
                    "a",
                    _BYTE,
                ),
                first_program.send(
                    "B",
                    first_program.name("a"),
                    _BYTE,
                ),
            )
        ),
    )

    second = _bound(
        second_program,
        Sequence(
            (
                second_program.receive(
                    "A",
                    "a",
                    _BYTE,
                ),
                second_program.send(
                    "B",
                    second_program.name("a"),
                    _BYTE,
                ),
            )
        ),
    )

    first_rtl = emit_async_systemverilog(
        first
    )

    assert (
        first_rtl
        == emit_async_systemverilog(
            second
        )
    )

    assert (
        "input logic [7:0] "
        "channel_A_receive_payload"
        in first_rtl
    )

    assert (
        "output logic [7:0] "
        "channel_B_send_payload"
        in first_rtl
    )


def test_typed_binding_assignments_are_emitted_mechanically_for_body_send_and_enable_payload() -> None:
    program = _Program()

    expression = Expression(
        "binary",
        operator="+",
        operands=(
            program.name("a"),
            Expression(
                "literal",
                value="1'b1",
            ),
        ),
    )

    bound = _bound(
        program,
        Sequence(
            (
                program.receive(
                    "A",
                    "a",
                ),
                Assign(
                    program.variable("y"),
                    expression,
                ),
                If(
                    program.external_name("select"),
                    program.send(
                        "B",
                        program.name("y"),
                    ),
                    Skip(),
                ),
            )
        ),
    )

    rtl = emit_async_systemverilog(
        bound
    )

    for assignment in bound.assignments:
        assert (
            f"assign {assignment.target_signal_id} = "
            in rtl
        )

    a_signal = _variable_signal_id(
        bound,
        program.variable("a"),
    )

    y_signal = _variable_signal_id(
        bound,
        program.variable("y"),
    )

    assert (
        f"assign {y_signal} = "
        f"({a_signal} + 1'b1);"
        in rtl
    )

    assert (
        f"assign storage_0_data_in = "
        f"{y_signal};"
        in rtl
    )

    enable = bound.enable_channels[0]

    select_signal = _variable_signal_id(
        bound,
        program.external_inputs["select"],
    )

    assert (
        f"assign {enable.value_signal_id} = "
        f"{select_signal};"
        in rtl
    )

    assert (
        f"assign {enable.data_signal_id} = "
        f"{select_signal};"
        not in rtl
    )


def test_expression_rendering_uses_only_m7a_bound_variable_signals() -> None:
    program = _Program()

    received = program.variable(
        "raw_source"
    )

    parameter = Parameter(
        "P",
        "async_codegen",
        SourceLocation(
            "async_codegen.sv",
            1,
            1,
        ),
        "1'b1",
    )

    name = program.name(
        "raw_source"
    )

    select = Expression(
        "select",
        value="raw_source",
        variable=received,
        operands=(
            Expression(
                "index",
                operands=(
                    Expression(
                        "literal",
                        value="0",
                    ),
                ),
            ),
        ),
    )

    unary = Expression(
        "unary",
        operator="~",
        operands=(name,),
    )

    binary = Expression(
        "binary",
        operator="&",
        operands=(
            name,
            unary,
        ),
    )

    conditional = Expression(
        "conditional",
        operands=(
            name,
            select,
            binary,
        ),
    )

    expression = Expression(
        "concatenate",
        operands=(
            conditional,
            Expression(
                "parameter",
                value="P",
                parameter=parameter,
            ),
            Expression(
                "literal",
                value="1'b0",
            ),
        ),
    )

    module = BehavioralModule(
        "expression_binding",
        Sequence(
            (
                Receive(
                    ChannelEndpoint(
                        "A",
                        payload_type=_BIT,
                    ),
                    received,
                ),
                Send(
                    ChannelEndpoint(
                        "B",
                        payload_type=_BIT,
                    ),
                    expression,
                ),
            )
        ),
        (),
        (received,),
        (parameter,),
    )

    bound = bind_async_templates(
        lower_microarchitecture(
            analyze_semantics(
                decompose_transaction(
                    extract_transaction(
                        module
                    )
                )
            )
        )
    )

    rtl = emit_async_systemverilog(
        bound
    )

    received_signal = (
        _variable_signal_id(
            bound,
            received,
        )
    )

    storage_assignment = next(
        line
        for line in rtl.splitlines()
        if line.startswith(
            "  assign storage_0_data_in ="
        )
    )

    assert (
        received_signal
        in storage_assignment
    )

    assert (
        "P" in storage_assignment
        and "1'b0" in storage_assignment
    )

    assert not re.search(
        r"(?<![A-Za-z0-9_$])"
        r"raw_source"
        r"(?![A-Za-z0-9_$])",
        storage_assignment,
    )

    assert (
        len(bound.variable_bindings)
        == 1
    )


def test_codegen_only_renders_prebound_variable_connectivity() -> None:
    program = _Program()

    received = program.variable("a")
    assigned = program.variable("y")

    bound = _bound(
        program,
        Sequence(
            (
                program.receive(
                    "A",
                    "a",
                ),
                Assign(
                    assigned,
                    Expression(
                        "binary",
                        operator="+",
                        operands=(
                            program.name(
                                "a"
                            ),
                            Expression(
                                "literal",
                                value="1'b1",
                            ),
                        ),
                    ),
                ),
                program.send(
                    "B",
                    program.name("y"),
                ),
            )
        ),
    )

    variable_bindings = tuple(
        bound.variable_bindings
    )

    signal_ids = tuple(
        signal.id
        for signal in bound.signals
    )

    rtl = emit_async_systemverilog(
        bound
    )

    assert (
        tuple(bound.variable_bindings)
        == variable_bindings
    )

    assert (
        tuple(
            signal.id
            for signal in bound.signals
        )
        == signal_ids
    )

    for binding in variable_bindings:
        assert (
            binding.signal_id
            in signal_ids
        )
        assert (
            f"logic {binding.signal_id};"
            in rtl
        )


def test_2r2s_control_storage_and_delay_formals_emit_exactly_as_bound() -> None:
    program = _Program()

    bound = _bound(
        program,
        Sequence(
            (
                program.receive(
                    "A",
                    "a",
                ),
                program.receive(
                    "B",
                    "b",
                ),
                program.send(
                    "C",
                    program.name("a"),
                ),
                program.send(
                    "D",
                    program.name("b"),
                ),
            )
        ),
    )

    rtl = emit_async_systemverilog(
        bound
    )

    _assert_ordinary_body_rtl_topology(
        rtl,
        inputs=2,
        outputs=2,
    )

    for index in range(2):
        assert (
            f"logic storage_{index}_data_in;"
            in rtl
        )

        assert (
            f"logic storage_{index}_data_out;"
            in rtl
        )

        assert (
            f".data_in(storage_{index}_data_in)"
            in rtl
        )

        assert (
            f".data_out(storage_{index}_data_out)"
            in rtl
        )

    assert ".data_path(" not in rtl

    assert (
        rtl.count(".control_in(")
        == 2
    )

    assert (
        rtl.count(".control_out(")
        == 2
    )


def test_enable_channel_is_emitted_as_req_ack_data_without_scalar_enable_interface() -> None:
    program = _Program()

    select = program.external_name(
        "select"
    )

    bound = _bound(
        program,
        Sequence(
            (
                If(
                    select,
                    program.receive(
                        "A",
                        "a",
                    ),
                    Skip(),
                ),
                If(
                    select,
                    program.send(
                        "B",
                        program.name("a"),
                    ),
                    Skip(),
                ),
            )
        ),
    )

    rtl = emit_async_systemverilog(
        bound
    )

    select_signal = (
        _variable_signal_id(
            bound,
            program.external_inputs["select"],
        )
    )

    for channel in bound.enable_channels:
        assert (
            f"logic {channel.request_signal_id};"
            in rtl
        )

        assert (
            f"logic {channel.acknowledge_signal_id};"
            in rtl
        )

        assert (
            f"logic {channel.data_signal_id};"
            in rtl
        )

        assert (
            f"logic {channel.value_signal_id};"
            in rtl
        )

        assert (
            f"assign {channel.value_signal_id} = "
            f"{select_signal};"
            in rtl
        )

        assert (
            f"assign {channel.data_signal_id} = "
            f"{select_signal};"
            not in rtl
        )

        assert (
            f".req({channel.request_signal_id})"
            in rtl
        )

        assert (
            f".ack({channel.acknowledge_signal_id})"
            in rtl
        )

        assert (
            f".data({channel.data_signal_id})"
            in rtl
        )

    assert ".enable(" not in rtl


def test_assignments_and_rtl_are_byte_deterministic_for_identical_bound_modules() -> None:
    first_program = _Program()
    second_program = _Program()

    first = _bound(
        first_program,
        Sequence(
            (
                first_program.receive(
                    "A",
                    "a",
                ),
                If(
                    first_program.external_name(
                        "select"
                    ),
                    first_program.send(
                        "B",
                        first_program.name("a"),
                    ),
                    Skip(),
                ),
            )
        ),
    )

    second = _bound(
        second_program,
        Sequence(
            (
                second_program.receive(
                    "A",
                    "a",
                ),
                If(
                    second_program.external_name(
                        "select"
                    ),
                    second_program.send(
                        "B",
                        second_program.name("a"),
                    ),
                    Skip(),
                ),
            )
        ),
    )

    assert (
        first.assignments
        == second.assignments
    )

    assert (
        emit_async_systemverilog(first)
        == emit_async_systemverilog(second)
    )


def test_conditional_enable_channel_acknowledgement_is_emitted_once_exactly_as_bound() -> None:
    program = _Program()

    bound = _bound(
        program,
        Sequence(
            (
                program.receive(
                    "A",
                    "a",
                ),
                If(
                    program.external_name(
                        "select"
                    ),
                    program.send(
                        "B",
                        program.name("a"),
                    ),
                    Skip(),
                ),
            )
        ),
    )

    rtl = emit_async_systemverilog(
        bound
    )

    post_channels = [
        item
        for item in bound.enable_channels
        if item.availability.value
        == "post_input"
    ]

    for channel in post_channels:
        sender_bindings = [
            item
            for item in bound.port_bindings
            if item.instance_id
            == channel.body_sender.id
        ]

        assert any(
            item.formal_name == "ack"
            and item.actual_signal_id
            == channel.acknowledge_signal_id
            for item in sender_bindings
        )

        assert (
            f".ack({channel.acknowledge_signal_id})"
            in rtl
        )
