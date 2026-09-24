"""M7A structural-binding contract tests for the M6 async architecture."""
from __future__ import annotations

import pytest

from svcsp_compiler.async_microarchitecture import lower_microarchitecture
from svcsp_compiler.async_template_binding import (
    AsyncTemplateBindingError,
    BoundBodyAssignWrite,
    BoundBodyIf,
    BoundBodyLValue,
    BoundBodyParallel,
    BoundBodyReceiveWrite,
    BoundBodySequence,
    BoundBodySkip,
    BoundAsyncModule,
    bind_async_templates,
)
from svcsp_compiler.behavioral_ir import (
    Assign,
    BehavioralModule,
    ChannelEndpoint,
    Expression,
    If,
    ONE_BIT,
    Parameter,
    Parallel,
    PayloadType,
    PayloadWidth,
    Receive,
    Send,
    Sequence,
    Skip,
    SourceLocation,
    Variable,
    lower_behavioral,
)
from svcsp_compiler.communication_decomposition import decompose_transaction
from svcsp_compiler.frontend import parse_text
from svcsp_compiler.semantic_analysis import analyze_semantics
from svcsp_compiler.transaction import extract_transaction


_ONE_BIT = PayloadType("logic", ONE_BIT)
_NIBBLE = PayloadType("logic", width=PayloadWidth(bits=4))
_BYTE = PayloadType("logic", width=PayloadWidth(bits=8))


class _Program:
    def __init__(self) -> None:
        self.variables: dict[str, Variable] = {}
        self.external_inputs: dict[str, Variable] = {}

    def variable(self, name: str, payload_type: PayloadType = _ONE_BIT) -> Variable:
        return self.variables.setdefault(
            name, Variable(name, (), SourceLocation("async_binding.sv", 1, 1), payload_type),
        )

    def name(self, name: str, payload_type: PayloadType = _ONE_BIT) -> Expression:
        variable = self.variable(name, payload_type)
        return Expression("name", value=name, variable=variable)

    def external_name(self, name: str, payload_type: PayloadType = _ONE_BIT) -> Expression:
        if name in self.variables:
            raise ValueError(f"{name} is already a local variable")
        variable = self.external_inputs.setdefault(
            name, Variable(name, (), SourceLocation("async_binding.sv", 1, 1), payload_type),
        )
        return Expression("name", value=name, variable=variable)

    def receive(self, channel: ChannelEndpoint | str, target: str, payload_type: PayloadType = _ONE_BIT) -> Receive:
        endpoint = channel if isinstance(channel, ChannelEndpoint) else ChannelEndpoint(channel, payload_type=payload_type)
        return Receive(endpoint, self.variable(target, payload_type))

    def send(self, channel: ChannelEndpoint | str, value: Expression,
             payload_type: PayloadType = _ONE_BIT) -> Send:
        endpoint = channel if isinstance(channel, ChannelEndpoint) else ChannelEndpoint(channel, payload_type=payload_type)
        return Send(endpoint, value)

    def module(self, body) -> BehavioralModule:
        return BehavioralModule(
            "async_binding", body, (), tuple(self.variables.values()),
            external_inputs=tuple(self.external_inputs.values()),
        )


def _architecture(program: _Program, body):
    transaction = extract_transaction(program.module(body))
    return lower_microarchitecture(analyze_semantics(decompose_transaction(transaction)))


def _bind(program: _Program, body):
    architecture = _architecture(program, body)
    return architecture, bind_async_templates(architecture)


def _bind_module(module: BehavioralModule):
    architecture = lower_microarchitecture(analyze_semantics(decompose_transaction(extract_transaction(module))))
    return architecture, bind_async_templates(architecture)


def _bound_for(bound: BoundAsyncModule, source: object):
    return next(item for item in bound.instances if item.source is source)


def _bindings_for(bound: BoundAsyncModule, instance) -> tuple:
    return tuple(binding for binding in bound.port_bindings if binding.instance_id == instance.id)


def _actual_signal(bound: BoundAsyncModule, binding):
    return next(signal for signal in bound.signals if signal.id == binding.actual_signal_id)


def _enable_channel_binding(bound: BoundAsyncModule, channel):
    return next(item for item in bound.enable_channels if item.source is channel)


def _variable_signal_id(bound: BoundAsyncModule, variable: Variable) -> str:
    bindings = [item for item in bound.variable_bindings if item.variable is variable]
    assert len(bindings) == 1
    return bindings[0].signal_id


def _static_lvalue(variable: Variable, *values: int) -> Expression:
    selector = Expression(
        "index" if len(values) == 1 else "range",
        operands=tuple(Expression("literal", value=str(value)) for value in values),
    )
    return Expression("select", variable=variable, operands=(selector,))


def _assert_complete_typed_bindings(bound: BoundAsyncModule) -> None:
    declared_actuals = {signal.id for signal in bound.signals}
    declared_actuals.update(port.signal_id for port in bound.module_ports)
    for instance in bound.instances:
        assert instance.component
        assert instance.template
        bindings = _bindings_for(bound, instance)
        assert {binding.formal_name for binding in bindings} == set(instance.required_formals)
        assert len({binding.formal_name for binding in bindings}) == len(bindings)
        assert all(binding.actual_signal_id in declared_actuals for binding in bindings)
        parameters = [binding for binding in bound.parameter_bindings if binding.instance_id == instance.id]
        assert {binding.formal_name for binding in parameters} == set(instance.required_parameters)
        assert len({binding.formal_name for binding in parameters}) == len(parameters)


def _ordinary_instances(bound: BoundAsyncModule, template: str) -> list:
    return [instance for instance in bound.instances if instance.template == template]


def _assert_ordinary_body_topology(bound: BoundAsyncModule, *, inputs: int, outputs: int) -> None:
    templates = {instance.template for instance in bound.instances}
    assert not templates & {
        "four_phase_input_join", "four_phase_output_fork",
        "four_phase_output_completion", "bundled_data_storage",
    }
    assert len(_ordinary_instances(bound, "four_phase_half_buffer_controller")) == 1
    assert len(_ordinary_instances(bound, "bundled_data_latch_bank")) == outputs
    assert len(_ordinary_instances(bound, "bundled_data_matched_delay")) == outputs
    assert len(_ordinary_instances(bound, "four_phase_request_join")) == (inputs > 1)
    assert len(_ordinary_instances(bound, "four_phase_ack_fanout")) == (inputs > 1)
    assert len(_ordinary_instances(bound, "four_phase_request_fanout")) == (outputs > 1)
    assert len(_ordinary_instances(bound, "four_phase_ack_join")) == (outputs > 1)
    reset_ports = [port for port in bound.module_ports if port.name == "reset_n"]
    assert len(reset_ports) == 1 and reset_ports[0].direction == "input"
    control_instances = [instance for instance in bound.instances if instance.template in {
        "four_phase_half_buffer_controller", "four_phase_request_join", "four_phase_ack_join",
    }]
    for instance in control_instances:
        bindings = {binding.formal_name: binding for binding in _bindings_for(bound, instance)}
        assert bindings["reset_n"].actual_signal_id == "reset_n"


def test_1r1s_binds_the_documented_direct_ordinary_body_topology() -> None:
    program = _Program()
    architecture, bound = _bind(program, Sequence((
        program.receive("A", "a"),
        program.send("B", program.name("a")),
    )))

    assert isinstance(bound, BoundAsyncModule)
    assert bound.architecture is architecture
    _assert_ordinary_body_topology(bound, inputs=1, outputs=1)


def test_2r1s_binds_request_join_ack_fanout_and_direct_output_topology() -> None:
    program = _Program()
    architecture, bound = _bind(program, Sequence((
        program.receive("A", "a"),
        program.receive("B", "b"),
        program.send("C", Expression("literal", value="1'b0")),
    )))

    _assert_ordinary_body_topology(bound, inputs=2, outputs=1)
    assert len([port for port in bound.module_ports if port.flow == "receive"]) == 2
    assert not any(connection.kind == "source_sequence" for connection in bound.connections)


def test_1r2s_binds_request_fanout_ack_join_and_per_output_storage_delay() -> None:
    program = _Program()
    expression = Expression("binary", operator="+", operands=(program.name("a"), Expression("literal", value="1'b1")))
    architecture, bound = _bind(program, Sequence((
        program.receive("A", "a"),
        Assign(program.variable("y"), expression),
        program.send("B", program.name("y")),
        program.send("C", program.name("y")),
    )))

    _assert_ordinary_body_topology(bound, inputs=1, outputs=2)
    assert not any(connection.kind == "source_sequence" for connection in bound.connections)


def test_2r2s_preserves_every_m6_input_output_and_storage_identity() -> None:
    program = _Program()
    architecture, bound = _bind(program, Sequence((
        program.receive("A", "a"),
        program.receive("B", "b"),
        program.send("C", program.name("a")),
        program.send("D", program.name("b")),
    )))

    _assert_ordinary_body_topology(bound, inputs=2, outputs=2)
    assert {_bound_for(bound, slot).source for slot in architecture.storage.slots} == set(architecture.storage.slots)


def test_every_m6_enable_channel_binds_a_real_one_bit_four_phase_channel_and_body_sender() -> None:
    program = _Program()
    select = program.external_name("select")
    architecture, bound = _bind(program, Sequence((
        If(select, program.receive("A", "a"), Skip()),
        If(select, program.send("B", program.name("a")), Skip()),
    )))

    assert len(bound.enable_channels) == len(architecture.enable_channels) == 2
    for channel in architecture.enable_channels:
        binding = _enable_channel_binding(bound, channel)
        assert binding.enable_channel is channel
        assert binding.source is channel
        assert binding.availability is channel.availability
        assert binding.producer_completion is channel.producer.participates_in_transaction_completion
        assert binding.body_sender.enable_channel is channel
        assert binding.body_sender.source is channel.producer
        request = next(signal for signal in bound.signals if signal.id == binding.request_signal_id)
        acknowledge = next(signal for signal in bound.signals if signal.id == binding.acknowledge_signal_id)
        data = next(signal for signal in bound.signals if signal.id == binding.data_signal_id)
        assert request.kind == "request" and request.width.bits == 1
        assert acknowledge.kind == "acknowledge" and acknowledge.width.bits == 1
        assert data.kind == "payload" and data.width.bits == 1
        if channel.availability.value == "pre_input":
            assert channel not in architecture.output_ports


def test_conditional_receive_binds_its_explicit_m6_storage_and_delay_resources() -> None:
    program = _Program()
    select = program.external_name("select")
    architecture, bound = _bind(program, Sequence((
        If(select, program.receive("A", "a"), Skip()),
        If(select, program.send("B", program.name("a")), Skip()),
    )))

    stage = architecture.en_receive_stages[0]
    channel_binding = _enable_channel_binding(bound, stage.enable_channel)
    instance = _bound_for(bound, stage)
    bindings = {item.formal_name: item for item in _bindings_for(bound, instance)}
    storage_resource = architecture.en_receive_storage[0]
    delay_resource = architecture.en_receive_matched_delays[0]
    storage = _bound_for(bound, storage_resource)
    delay = _bound_for(bound, delay_resource)
    storage_bindings = {item.formal_name: item for item in _bindings_for(bound, storage)}
    delay_bindings = {item.formal_name: item for item in _bindings_for(bound, delay)}
    assert instance.template == "en_receive_controller"
    assert {"enable_req", "enable_ack", "enable_data", "external_req", "external_ack", "external_data",
            "body_raw_req", "body_ack", "storage_data", "storage_enable"} == set(bindings)
    assert bindings["enable_req"].actual_signal_id == channel_binding.request_signal_id
    assert bindings["enable_ack"].actual_signal_id == channel_binding.acknowledge_signal_id
    assert bindings["enable_data"].actual_signal_id == channel_binding.data_signal_id
    assert not any(item.source is stage.input_port for item in bound.instances)
    assert storage.template == "bundled_data_latch_bank" and storage.source is storage_resource
    assert delay.template == "bundled_data_matched_delay" and delay.source is delay_resource
    assert storage_bindings["data_in"].actual_signal_id == bindings["storage_data"].actual_signal_id
    assert storage_bindings["storage_enable"].actual_signal_id == bindings["storage_enable"].actual_signal_id
    assert isinstance(bound.body_program, BoundBodySequence)
    receive_if = bound.body_program.items[0]
    assert isinstance(receive_if, BoundBodyIf)
    assert isinstance(receive_if.then_branch, BoundBodyReceiveWrite)
    assert storage_bindings["data_out"].actual_signal_id == receive_if.then_branch.receive_value_signal_id
    received_signal = _variable_signal_id(bound, program.variable("a"))
    assert storage_bindings["data_out"].actual_signal_id != received_signal
    assert not any(item.target_signal_id == received_signal for item in bound.assignments)
    assert delay_bindings["control_in"].actual_signal_id == bindings["body_raw_req"].actual_signal_id
    assert delay_bindings["control_out"].actual_signal_id != bindings["body_raw_req"].actual_signal_id
    assert delay_bindings["control_out"].actual_signal_id == "input_0_body_req"
    assert bindings["body_ack"].actual_signal_id == "input_0_body_ack"


def test_conditional_send_binds_its_explicit_m6_storage_and_delay_resources() -> None:
    program = _Program()
    architecture, bound = _bind(program, Sequence((
        program.receive("A", "a"),
        If(program.external_name("select"), program.send("B", Expression("literal", value="1'b0")), Skip()),
    )))

    stage = architecture.en_send_stages[0]
    channel_binding = _enable_channel_binding(bound, stage.enable_channel)
    instance = _bound_for(bound, stage)
    bindings = {item.formal_name: item for item in _bindings_for(bound, instance)}
    storage_resource = architecture.en_send_storage[0]
    delay_resource = architecture.en_send_matched_delays[0]
    storage = _bound_for(bound, storage_resource)
    delay = _bound_for(bound, delay_resource)
    storage_bindings = {item.formal_name: item for item in _bindings_for(bound, storage)}
    delay_bindings = {item.formal_name: item for item in _bindings_for(bound, delay)}
    assert instance.template == "en_send_controller"
    assert {"enable_req", "enable_ack", "enable_data", "body_req", "body_ack", "body_data",
            "external_raw_req", "external_ack", "storage_data", "storage_enable"} == set(bindings)
    assert bindings["enable_req"].actual_signal_id == channel_binding.request_signal_id
    assert bindings["enable_ack"].actual_signal_id == channel_binding.acknowledge_signal_id
    assert bindings["enable_data"].actual_signal_id == channel_binding.data_signal_id
    assert not any(item.source is stage.output_port for item in bound.instances)
    assert storage.template == "bundled_data_latch_bank" and storage.source is storage_resource
    assert delay.template == "bundled_data_matched_delay" and delay.source is delay_resource
    assert storage_bindings["data_in"].actual_signal_id == bindings["storage_data"].actual_signal_id
    assert storage_bindings["storage_enable"].actual_signal_id == bindings["storage_enable"].actual_signal_id
    assert delay_bindings["control_in"].actual_signal_id == bindings["external_raw_req"].actual_signal_id
    assert delay_bindings["control_out"].actual_signal_id != bindings["external_raw_req"].actual_signal_id
    external_request = next(port.signal_id for port in bound.module_ports
                            if port.endpoint is stage.body_send.source.operation.channel and
                            port.flow == "send_request")
    external_acknowledge = next(port.signal_id for port in bound.module_ports
                                if port.endpoint is stage.body_send.source.operation.channel and
                                port.flow == "send_acknowledge")
    assert delay_bindings["control_out"].actual_signal_id == external_request
    assert bindings["external_ack"].actual_signal_id == external_acknowledge
    ordinary_storage = _bound_for(bound, architecture.storage.slots[0])
    ordinary_delay = _bound_for(bound, architecture.matched_delays[0])
    assert ordinary_storage.template == "bundled_data_latch_bank"
    assert ordinary_delay.template == "bundled_data_matched_delay"


def test_selected_endpoints_and_payload_widths_are_bound_without_reinterpretation() -> None:
    program = _Program()
    index = Expression("literal", value="1")
    receive_endpoint = ChannelEndpoint("A", (Expression("index", operands=(index,)),), _BYTE)
    send_endpoint = ChannelEndpoint("B", (Expression("index", operands=(index,)),), _BYTE)
    architecture, bound = _bind(program, Sequence((
        program.receive(receive_endpoint, "a", _BYTE),
        program.send(send_endpoint, program.name("a"), _BYTE),
    )))

    payload_ports = [port for port in bound.module_ports if port.role == "payload"]
    assert {port.endpoint for port in payload_ports} == {receive_endpoint, send_endpoint}
    assert all(port.width == _BYTE.width for port in payload_ports)
    assert all(signal.width == _BYTE.width for signal in bound.signals if signal.endpoint in {receive_endpoint, send_endpoint}
               and signal.kind == "payload")
    assert architecture.input_ports[0].body_receive.source.operation.channel is receive_endpoint
    assert architecture.output_ports[0].body_send.source.operation.channel is send_endpoint


def test_each_m6_matched_delay_binds_its_exact_storage_join_and_output_port() -> None:
    program = _Program()
    expression = Expression("binary", operator="+", operands=(program.name("a"), Expression("literal", value="1'b1")))
    architecture, bound = _bind(program, Sequence((
        program.receive("A", "a"),
        Assign(program.variable("y"), expression),
        program.send("B", program.name("y")),
        program.send("C", program.name("y")),
    )))

    assert len(architecture.matched_delays) == 2
    for requirement in architecture.matched_delays:
        delay = _bound_for(bound, requirement)
        assert delay.template == "bundled_data_matched_delay"
        assert delay.storage_slot is requirement.storage_slot
        assert requirement.controller is architecture.base_controller
        assert delay.output_port is requirement.output_port
        assert delay.value is None


def test_binding_is_deterministic() -> None:
    first_program = _Program()
    second_program = _Program()
    first = _bind(first_program, Sequence((first_program.receive("A", "a"), first_program.send("B", first_program.name("a")))))[1]
    second = _bind(second_program, Sequence((second_program.receive("A", "a"), second_program.send("B", second_program.name("a")))))[1]

    assert first == second


def test_every_instance_has_complete_typed_formal_and_parameter_bindings() -> None:
    program = _Program()
    architecture, bound = _bind(program, Sequence((
        program.receive("A", "a", _BYTE),
        program.send("B", program.name("a"), _BYTE),
    )))

    _assert_complete_typed_bindings(bound)
    storage = _bound_for(bound, architecture.storage.slots[0])
    width = next(binding for binding in bound.parameter_bindings
                 if binding.instance_id == storage.id and binding.formal_name == "WIDTH")
    assert width.value == _BYTE.width


def test_conditional_en_receive_uses_the_m6_microstage_and_enable_channel_bindings() -> None:
    program = _Program()
    select = program.external_name("select")
    conditional_receive = program.receive("A", "a")
    architecture, bound = _bind(program, Sequence((
        If(select, conditional_receive, Skip()),
        If(select, program.send("B", program.name("a")), Skip()),
    )))

    stage = architecture.en_receive_stages[0]
    channel = _enable_channel_binding(bound, stage.enable_channel)
    instance = _bound_for(bound, stage)
    bindings = {binding.formal_name: binding for binding in _bindings_for(bound, instance)}
    assert set(bindings) == {
        "enable_req", "enable_ack", "enable_data",
        "external_req", "external_ack", "external_data", "body_raw_req", "body_ack",
        "storage_data", "storage_enable",
    }
    assert instance.template == "en_receive_controller"
    assert bindings["enable_req"].actual_signal_id == channel.request_signal_id
    assert bindings["enable_ack"].actual_signal_id == channel.acknowledge_signal_id
    assert bindings["enable_data"].actual_signal_id == channel.data_signal_id
    assert _actual_signal(bound, bindings["external_req"]).kind == "request"
    assert _actual_signal(bound, bindings["external_ack"]).kind == "acknowledge"
    assert _actual_signal(bound, bindings["external_data"]).kind == "payload"
    assert _actual_signal(bound, bindings["body_raw_req"]).kind == "request"
    assert _actual_signal(bound, bindings["body_ack"]).kind == "acknowledge"
    assert _actual_signal(bound, bindings["storage_data"]).kind == "payload"
    assert _actual_signal(bound, bindings["enable_data"]).kind == "payload"
    assert not any(item.source is stage.input_port for item in bound.instances)


def test_2r2s_ordinary_control_components_are_declared_and_bound() -> None:
    program = _Program()
    architecture, bound = _bind(program, Sequence((
        program.receive("A", "a"),
        program.receive("B", "b"),
        program.send("C", program.name("a")),
        program.send("D", program.name("b")),
    )))

    _assert_ordinary_body_topology(bound, inputs=2, outputs=2)

    for index, output in enumerate(architecture.output_ports):
        endpoint = output.body_send.source.operation.channel
        external_request = next(port.signal_id for port in bound.module_ports
                                if port.endpoint is endpoint and port.flow == "send_request")
        external_acknowledge = next(port.signal_id for port in bound.module_ports
                                    if port.endpoint is endpoint and port.flow == "send_acknowledge")
        assert any(assignment.target_signal_id == external_request for assignment in bound.assignments)
        assert any(assignment.source_signal_ids == (external_acknowledge,) for assignment in bound.assignments)
        assert not any(instance.source is output for instance in bound.instances)


def test_post_input_enable_is_not_an_ordinary_output_port_and_matched_delays_remain_typed() -> None:
    program = _Program()
    expression = Expression("binary", operator="+", operands=(program.name("a"), Expression("literal", value="1'b1")))
    conditional_send = program.send("B", program.name("y"))
    architecture, bound = _bind(program, Sequence((
        program.receive("A", "a"),
        Assign(program.variable("y"), expression),
        If(program.external_name("select"), conditional_send, Skip()),
        program.send("C", program.name("y")),
    )))

    post_input = next(channel for channel in architecture.enable_channels
                      if channel.availability.value == "post_input")
    binding = _enable_channel_binding(bound, post_input)
    assert binding.producer_completion is True
    assert post_input not in architecture.output_ports
    for requirement in architecture.matched_delays:
        delay = _bound_for(bound, requirement)
        bindings = {binding.formal_name: binding for binding in _bindings_for(bound, delay)}
        assert set(bindings) == {"control_in", "control_out"}
        assert bindings["control_in"].actual_signal_id in {signal.id for signal in bound.signals}
        assert bindings["control_out"].actual_signal_id in {signal.id for signal in bound.signals}
        assert delay.storage_slot is requirement.storage_slot
        assert delay.output_port is requirement.output_port

    _assert_complete_typed_bindings(bound)


def test_request_join_uses_parameterized_vector_handshakes() -> None:
    program = _Program()
    architecture, bound = _bind(program, Sequence((
        program.receive("A", "a"),
        program.receive("B", "b"),
        program.send("C", Expression("literal", value="1'b0")),
    )))

    join = _bound_for(bound, architecture.input_request)
    parameters = {item.formal_name: item.value for item in bound.parameter_bindings if item.instance_id == join.id}
    bindings = {item.formal_name: item for item in _bindings_for(bound, join)}
    assert parameters["N"].bits == 2
    assert _actual_signal(bound, bindings["input_req"]).width.bits == 2
    assert _actual_signal(bound, bindings["base_Lreq"]).kind == "request"
    assert bindings["reset_n"].actual_signal_id == "reset_n"


def test_request_fanout_and_ack_join_use_parameterized_vector_branch_controls() -> None:
    program = _Program()
    architecture, bound = _bind(program, Sequence((
        program.receive("A", "a"),
        program.send("B", program.name("a")),
        program.send("C", program.name("a")),
    )))

    request_fanout = _bound_for(bound, architecture.output_request)
    ack_join = _bound_for(bound, architecture.output_ack)
    fanout_parameters = {item.formal_name: item.value for item in bound.parameter_bindings
                          if item.instance_id == request_fanout.id}
    join_parameters = {item.formal_name: item.value for item in bound.parameter_bindings
                       if item.instance_id == ack_join.id}
    assert fanout_parameters["M"].bits == 2
    assert join_parameters["M"].bits == 2
    assert {binding.formal_name for binding in _bindings_for(bound, ack_join)} >= {"reset_n", "output_ack", "base_Rack"}


def test_body_datapath_uses_distinct_storage_signals_and_procedural_assignments() -> None:
    program = _Program()
    expression = Expression("binary", operator="+", operands=(program.name("a"), Expression("literal", value="1'b1")))
    architecture, bound = _bind(program, Sequence((
        program.receive("A", "a"),
        Assign(program.variable("y"), expression),
        program.send("B", program.name("y")),
    )))

    slot = architecture.storage.slots[0]
    storage = _bound_for(bound, slot)
    storage_bindings = {item.formal_name: item for item in _bindings_for(bound, storage)}
    assert storage_bindings["data_in"].actual_signal_id != storage_bindings["data_out"].actual_signal_id
    assert not any(assignment.source is architecture.combinational.operations[0]
                   for assignment in bound.assignments)
    assert isinstance(bound.body_program, BoundBodySequence)
    assign_write = bound.body_program.items[1]
    assert isinstance(assign_write, BoundBodyAssignWrite)
    assert assign_write.source is architecture.combinational.operations[0]
    assert assign_write.expression is expression
    body_send = architecture.output_ports[0].body_send
    assert any(assignment.source is body_send and assignment.expression is body_send.source.operation.value
               and assignment.target_signal_id == storage_bindings["data_in"].actual_signal_id
               for assignment in bound.assignments)
    endpoint = architecture.output_ports[0].body_send.source.operation.channel
    external_payload = next(port.signal_id for port in bound.module_ports
                            if port.endpoint is endpoint and port.flow == "send")
    assert any(assignment.target_signal_id == external_payload and
               assignment.source_signal_ids == (storage_bindings["data_out"].actual_signal_id,)
               for assignment in bound.assignments)


def test_receive_target_has_one_exact_variable_binding_written_from_a_dedicated_receive_value() -> None:
    program = _Program()
    received = program.variable("a", _BYTE)
    architecture, bound = _bind(program, Sequence((
        program.receive("A", "a", _BYTE),
        program.send("B", program.name("a"), _BYTE),
    )))

    signal_id = _variable_signal_id(bound, received)
    signal = next(item for item in bound.signals if item.id == signal_id)
    input_port = architecture.input_ports[0]
    receive_payload = next(port.signal_id for port in bound.module_ports
                           if port.endpoint is input_port.body_receive.source.operation.channel and
                           port.flow == "receive")
    send_assignment = next(assignment for assignment in bound.assignments
                           if assignment.source is architecture.output_ports[0].body_send)

    assert signal.width == received.payload_type.width
    assert isinstance(bound.body_program, BoundBodySequence)
    receive_write = bound.body_program.items[0]
    assert isinstance(receive_write, BoundBodyReceiveWrite)
    assert receive_write.input_port is input_port
    assert receive_write.target is received
    assert receive_write.receive_value_signal_id != signal_id
    assert any(assignment.target_signal_id == receive_write.receive_value_signal_id and
               assignment.source_signal_ids == (receive_payload,)
               for assignment in bound.assignments)
    assert not any(assignment.target_signal_id == signal_id for assignment in bound.assignments)
    assert signal_id in send_assignment.source_signal_ids


def test_every_input_port_owns_one_distinct_dedicated_receive_value_signal() -> None:
    program = _Program()
    architecture, bound = _bind(program, Sequence((
        program.receive("A", "a", _BYTE),
        program.receive("B", "b", _BYTE),
        program.send("C", program.name("a", _BYTE), _BYTE),
        program.send("D", program.name("b", _BYTE), _BYTE),
    )))

    assert isinstance(bound.body_program, BoundBodySequence)
    writes = [item for item in bound.body_program.items if isinstance(item, BoundBodyReceiveWrite)]
    assert len(writes) == len(architecture.input_ports) == 2
    assert all(sum(item.input_port is port for item in writes) == 1 for port in architecture.input_ports)
    assert len({item.receive_value_signal_id for item in writes}) == 2
    for write in writes:
        signal = next(item for item in bound.signals if item.id == write.receive_value_signal_id)
        assert signal.width == write.target.payload_type.width
        assert not any(item.target_signal_id == _variable_signal_id(bound, write.target)
                       for item in bound.assignments)


def test_assignment_targets_and_rhs_use_distinct_exact_variable_bindings() -> None:
    program = _Program()
    received = program.variable("a")
    assigned = program.variable("y")
    expression = Expression("binary", operator="+", operands=(
        program.name("a"), Expression("literal", value="1'b1"),
    ))
    assignment = Assign(assigned, expression)
    architecture, bound = _bind(program, Sequence((
        program.receive("A", "a"),
        assignment,
        program.send("B", program.name("y")),
    )))

    received_signal = _variable_signal_id(bound, received)
    assigned_signal = _variable_signal_id(bound, assigned)
    receive_payload = next(port.signal_id for port in bound.module_ports if port.flow == "receive")
    send_assignment = next(assignment for assignment in bound.assignments
                           if assignment.source is architecture.output_ports[0].body_send)

    assert received_signal != assigned_signal
    assert isinstance(bound.body_program, BoundBodySequence)
    receive_write, assign_write, _ = bound.body_program.items
    assert isinstance(receive_write, BoundBodyReceiveWrite)
    assert isinstance(assign_write, BoundBodyAssignWrite)
    assert any(item.target_signal_id == receive_write.receive_value_signal_id
               and item.source_signal_ids == (receive_payload,)
               for item in bound.assignments)
    assert receive_write.target is received
    assert assign_write.target is assigned
    assert assign_write.expression is expression
    assert not any(item.target_signal_id in {received_signal, assigned_signal}
                   for item in bound.assignments)
    assert assigned_signal in send_assignment.source_signal_ids


def test_distinct_behavioral_variable_identities_never_alias_by_source_spelling() -> None:
    left = Variable("a", ("left",), SourceLocation("async_binding.sv", 1, 1), _ONE_BIT)
    right = Variable("a", ("right",), SourceLocation("async_binding.sv", 2, 1), _ONE_BIT)
    module = BehavioralModule("distinct_variables", Sequence((
        Receive(ChannelEndpoint("A", payload_type=_ONE_BIT), left),
        Receive(ChannelEndpoint("B", payload_type=_ONE_BIT), right),
        Send(ChannelEndpoint("C", payload_type=_ONE_BIT), Expression("literal", value="1'b0")),
    )), (), (left, right))
    architecture = lower_microarchitecture(analyze_semantics(decompose_transaction(extract_transaction(module))))
    bound = bind_async_templates(architecture)

    left_signal = _variable_signal_id(bound, left)
    right_signal = _variable_signal_id(bound, right)
    assert left_signal != right_signal
    assert {left_signal, right_signal} <= {signal.id for signal in bound.signals}


def test_source_module_parameters_bind_in_order_and_remain_distinct_from_instance_parameters() -> None:
    module = lower_behavioral(parse_text('''module parameterized #(
parameter int W = 8, parameter int V) (Channel #(W) A, B);
logic [W-1:0] x;
always begin A.Receive(x); B.Send(x); end
endmodule''', 'parameters.sv'))
    _, bound = _bind_module(module)

    assert [item.name for item in bound.module_parameters] == ['W', 'V']
    assert bound.module_parameters[0].source is module.parameters[0]
    assert bound.module_parameters[1].source is module.parameters[1]
    assert bound.module_parameters[0].source is not bound.module_parameters[1].source

    width_binding = next(item for item in bound.parameter_bindings if item.formal_name == 'WIDTH')
    assert width_binding.value.symbolic == 'W'
    assert width_binding.value.parameters[0] is bound.module_parameters[0].source


def test_symbolic_external_input_local_and_channel_widths_use_the_exact_module_parameter() -> None:
    module = lower_behavioral(parse_text('''module parameterized #(parameter int W = 8) (
input logic [W-1:0] control, Channel #(W) A, B);
logic [W-1:0] x;
always begin A.Receive(x); B.Send(x); end
endmodule''', 'parameters.sv'))
    _, bound = _bind_module(module)

    source_w = module.parameters[0]
    assert bound.module_parameters[0].source is source_w
    assert module.external_inputs[0].payload_type.width.parameters[0] is source_w
    assert module.variables[0].payload_type.width.parameters[0] is source_w
    assert module.channels[0].payload_type.width.parameters[0] is source_w
    assert any(signal.width.parameters and signal.width.parameters[0] is source_w
               for signal in bound.signals)


def test_structurally_equal_but_unowned_expression_parameter_is_rejected() -> None:
    canonical = Parameter('W', 'manual', SourceLocation('manual.sv', 1, 1), '8')
    foreign = Parameter('W', 'manual', SourceLocation('manual.sv', 1, 1), '8')
    payload = _ONE_BIT
    value = Variable('x', (), SourceLocation('manual.sv', 2, 1), payload)
    module = BehavioralModule('manual', Sequence((
        If(Expression('parameter', value='W', parameter=foreign),
           Receive(ChannelEndpoint('A', payload_type=payload), value), Skip()),
        Send(ChannelEndpoint('B', payload_type=payload), Expression('literal', value="1'b0")),
    )), (), (value,), parameters=(canonical,))

    with pytest.raises(AsyncTemplateBindingError, match='module parameter binding'):
        _bind_module(module)


def test_structurally_equal_but_unowned_payload_width_parameter_is_rejected() -> None:
    canonical = Parameter('W', 'manual', SourceLocation('manual.sv', 1, 1), '8')
    foreign = Parameter('W', 'manual', SourceLocation('manual.sv', 1, 1), '8')
    payload = PayloadType('logic', PayloadWidth(symbolic='W', parameters=(foreign,)))
    value = Variable('x', (), SourceLocation('manual.sv', 2, 1), payload)
    module = BehavioralModule('manual', Sequence((
        Receive(ChannelEndpoint('A', payload_type=payload), value),
        Send(ChannelEndpoint('B', payload_type=payload), Expression('name', value='x', variable=value)),
    )), (), (value,), parameters=(canonical,))

    with pytest.raises(AsyncTemplateBindingError, match='module parameter binding'):
        _bind_module(module)


def test_duplicate_source_module_parameter_names_are_rejected() -> None:
    first = Parameter('W', 'manual', SourceLocation('manual.sv', 1, 1), '8')
    second = Parameter('W', 'manual', SourceLocation('manual.sv', 2, 1), '4')
    value = Variable('x', (), SourceLocation('manual.sv', 3, 1), _ONE_BIT)
    module = BehavioralModule('manual', Sequence((
        Receive(ChannelEndpoint('A', payload_type=_ONE_BIT), value),
        Send(ChannelEndpoint('B', payload_type=_ONE_BIT), Expression('literal', value="1'b0")),
    )), (), (value,), parameters=(first, second))

    with pytest.raises(AsyncTemplateBindingError, match='duplicate module parameter'):
        _bind_module(module)


def test_module_parameter_generated_internal_signal_name_conflict_is_rejected() -> None:
    parameter = Parameter('body_var_0_x', 'manual', SourceLocation('manual.sv', 1, 1), '1')
    value = Variable('x', (), SourceLocation('manual.sv', 2, 1), _ONE_BIT)
    module = BehavioralModule('manual', Sequence((
        Receive(ChannelEndpoint('A', payload_type=_ONE_BIT), value),
        Send(ChannelEndpoint('B', payload_type=_ONE_BIT), Expression('name', value='x', variable=value)),
    )), (), (value,), parameters=(parameter,))

    with pytest.raises(AsyncTemplateBindingError, match='conflicts with generated signal body_var_0_x'):
        _bind_module(module)


@pytest.mark.parametrize('parameter_name, external_name', [('reset_n', None), ('select', 'select')])
def test_module_parameter_public_port_name_conflicts_are_rejected(parameter_name, external_name) -> None:
    parameter = Parameter(parameter_name, 'manual', SourceLocation('manual.sv', 1, 1), '1')
    payload = _ONE_BIT
    value = Variable('x', (), SourceLocation('manual.sv', 2, 1), payload)
    external_inputs = ()
    condition = Expression('literal', value="1'b1")
    if external_name is not None:
        external = Variable(external_name, (), SourceLocation('manual.sv', 3, 1), payload)
        external_inputs = (external,)
        condition = Expression('name', value=external_name, variable=external)
    module = BehavioralModule('manual', Sequence((
        If(condition, Receive(ChannelEndpoint('A', payload_type=payload), value), Skip()),
        Send(ChannelEndpoint('B', payload_type=payload), Expression('literal', value="1'b0")),
    )), (), (value,), parameters=(parameter,), external_inputs=external_inputs)

    with pytest.raises(AsyncTemplateBindingError, match='conflicts with public port'):
        _bind_module(module)


def test_external_input_binds_to_an_exact_public_signal_not_a_body_variable() -> None:
    program = _Program()
    select = program.external_name("select")
    _, bound = _bind(program, Sequence((
        If(select, program.receive("A", "a"), Skip()),
        If(select, program.send("B", program.name("a")), Skip()),
    )))

    binding = next(item for item in bound.variable_bindings if item.variable is select.variable)
    port = next(item for item in bound.module_ports if item.name == "select")

    assert binding.variable is select.variable
    assert binding.signal_id == port.signal_id == "select"
    assert port.direction == "input"
    assert not any(signal.id.startswith("body_var_") and signal.id.endswith("_select")
                   for signal in bound.signals)
    assert any(signal.id.startswith("body_var_") and signal.id.endswith("_a")
               for signal in bound.signals)


def test_packed_external_input_preserves_its_public_port_width() -> None:
    program = _Program()
    select = program.external_name("select", _BYTE)
    _, bound = _bind(program, Sequence((
        If(select, program.receive("A", "a"), Skip()),
        program.send("B", Expression("literal", value="1'b0")),
    )))

    port = next(item for item in bound.module_ports if item.name == "select")
    assert port.width == _BYTE.width
    assert next(signal for signal in bound.signals if signal.id == "select").width == _BYTE.width


def test_pre_input_enable_expression_reads_the_public_external_signal() -> None:
    program = _Program()
    select = program.external_name("select")
    architecture, bound = _bind(program, Sequence((
        If(select, program.receive("A", "a"), Skip()),
        program.send("B", Expression("literal", value="1'b0")),
    )))

    channel = next(item for item in architecture.enable_channels if item.availability.value == "pre_input")
    value_assignment = next(item for item in bound.assignments
                            if item.expression is channel.enable.condition)
    assert value_assignment.source_signal_ids == ("select",)


def test_enable_payload_is_driven_by_its_exact_m4_condition_not_a_scalar_wire() -> None:
    program = _Program()
    select = program.external_name("select")
    architecture, bound = _bind(program, Sequence((
        If(select, program.receive("A", "a"), Skip()),
        If(select, program.send("B", program.name("a")), Skip()),
    )))

    for channel in architecture.enable_channels:
        binding = _enable_channel_binding(bound, channel)
        sender_bindings = {item.formal_name: item for item in _bindings_for(bound, binding.body_sender)}
        assert sender_bindings["value"].actual_signal_id == binding.value_signal_id
        assert sender_bindings["data"].actual_signal_id == binding.data_signal_id
        assert binding.value_signal_id != binding.data_signal_id
        assert any(assignment.target_signal_id == binding.value_signal_id
                   and assignment.expression is channel.enable.condition
                   for assignment in bound.assignments)
        assert not any(assignment.target_signal_id == binding.data_signal_id
                       and assignment.expression is channel.enable.condition
                       for assignment in bound.assignments)
        assert _actual_signal(bound, next(item for item in _bindings_for(bound, binding.body_sender)
                                          if item.formal_name == "req")).kind == "request"
        assert _actual_signal(bound, next(item for item in _bindings_for(bound, binding.body_sender)
                                          if item.formal_name == "ack")).kind == "acknowledge"


def test_pre_input_enable_sender_has_a_dedicated_transaction_launch_and_value_path() -> None:
    program = _Program()
    select = program.external_name("select")
    architecture, bound = _bind(program, Sequence((
        If(select, program.receive("A", "a"), Skip()),
        If(select, program.send("B", program.name("a")), Skip()),
    )))

    channel = next(item for item in architecture.enable_channels if item.availability.value == "pre_input")
    sender = _enable_channel_binding(bound, channel).body_sender
    bindings = {item.formal_name: item for item in _bindings_for(bound, sender)}

    assert sender.template == "four_phase_enable_sender"
    assert set(bindings) == {"value", "launch", "req", "ack", "data"}
    value = bindings["value"].actual_signal_id
    launch = bindings["launch"].actual_signal_id
    data = bindings["data"].actual_signal_id
    assert value != data
    assert _actual_signal(bound, bindings["launch"]).kind == "control"
    assert any(assignment.target_signal_id == value and assignment.expression is channel.enable.condition
               for assignment in bound.assignments)
    assert not any(assignment.target_signal_id == data and assignment.expression is channel.enable.condition
                   for assignment in bound.assignments)
    assert channel.producer.available_before_controlled_body_input is True
    assert channel.producer.participates_in_transaction_completion is True
    assert channel.producer.participates_in_body_completion is False


def test_post_input_enable_sender_launches_with_body_and_preserves_enable_completion() -> None:
    program = _Program()
    architecture, bound = _bind(program, Sequence((
        program.receive("A", "a"),
        If(program.external_name("select"), program.send("B", program.name("a")), Skip()),
    )))

    channel = next(item for item in architecture.enable_channels if item.availability.value == "post_input")
    sender = _enable_channel_binding(bound, channel).body_sender
    bindings = {item.formal_name: item for item in _bindings_for(bound, sender)}
    stage_bindings = {item.formal_name: item
                      for item in _bindings_for(bound, _bound_for(bound, architecture.en_send_stages[0]))}
    assert set(bindings) == {"value", "launch", "req", "ack", "data"}
    assert _actual_signal(bound, bindings["launch"]).kind == "control"
    assert bindings["value"].actual_signal_id != bindings["data"].actual_signal_id
    assert any(assignment.target_signal_id == bindings["value"].actual_signal_id
               and assignment.expression is channel.enable.condition for assignment in bound.assignments)
    assert channel.producer.depends_on_input_join is True
    assert channel.producer.participates_in_body_completion is True

    assert _actual_signal(bound, stage_bindings["body_ack"]).kind == "acknowledge"
    assert _actual_signal(bound, bindings["ack"]).kind == "acknowledge"
    assert len({item.actual_signal_id for item in _bindings_for(bound, sender)
                if item.formal_name == "launch"}) == 1


def test_pre_input_enable_launch_is_explicit_transaction_entry_control() -> None:
    program = _Program()
    select = program.external_name("select")
    architecture, bound = _bind(program, Sequence((
        If(select, program.receive("A", "a"), Skip()),
        If(select, program.send("B", program.name("a")), Skip()),
    )))

    channel = next(item for item in architecture.enable_channels if item.availability.value == "pre_input")
    enable = _enable_channel_binding(bound, channel)
    sender_bindings = {item.formal_name: item for item in _bindings_for(bound, enable.body_sender)}
    launch = sender_bindings["launch"].actual_signal_id
    drivers = [item for item in bound.assignments if item.target_signal_id == launch]

    assert launch == enable.launch_signal_id
    assert len(drivers) == 1
    assert drivers[0].kind == "transaction_entry_launch"
    assert drivers[0].source is architecture.validated
    assert not any(item.source is channel for item in drivers)


@pytest.mark.parametrize("payload", (_ONE_BIT, _BYTE))
def test_en_stages_bind_widths_from_their_exact_payloads(payload: PayloadType) -> None:
    program = _Program()
    select = program.external_name("select")
    architecture, bound = _bind(program, Sequence((
        If(select, program.receive("A", "a", payload), Skip()),
        If(select, program.send("B", program.name("a"), payload), Skip()),
    )))

    for stage in (*architecture.en_receive_stages, *architecture.en_send_stages):
        instance = _bound_for(bound, stage)
        width = next(item.value for item in bound.parameter_bindings
                     if item.instance_id == instance.id and item.formal_name == "WIDTH")
        endpoint = (
            stage.body_receive.source.operation.channel
            if hasattr(stage, "body_receive") else stage.body_send.source.operation.channel
        )
        assert width == endpoint.payload_type.width


def test_conditional_send_has_real_body_and_enable_handshake_lanes() -> None:
    program = _Program()
    architecture, bound = _bind(program, Sequence((
        program.receive("A", "a"),
        If(program.external_name("select"), program.send("B", program.name("a")), Skip()),
    )))

    stage = architecture.en_send_stages[0]
    stage_instance = _bound_for(bound, stage)
    stage_bindings = {item.formal_name: item for item in _bindings_for(bound, stage_instance)}
    channel = stage.enable_channel
    enable_sender = _enable_channel_binding(bound, channel).body_sender
    assert _actual_signal(bound, stage_bindings["body_req"]).kind == "request"
    assert _actual_signal(bound, stage_bindings["body_ack"]).kind == "acknowledge"
    assert any(item.formal_name == "ack" for item in _bindings_for(bound, enable_sender))


def test_body_program_preserves_ordered_receive_assign_and_send_skip_identities() -> None:
    program = _Program()
    receive = program.receive("A", "a")
    expression = Expression("unary", operator="~", operands=(program.name("a"),))
    assign = Assign(program.variable("y"), expression)
    send = program.send("B", program.name("y"))
    architecture, bound = _bind(program, Sequence((receive, assign, send)))

    assert isinstance(bound.body_program, BoundBodySequence)
    receive_write, assign_write, send_skip = bound.body_program.items
    assert isinstance(receive_write, BoundBodyReceiveWrite)
    assert receive_write.source is architecture.input_ports[0].body_receive
    assert receive_write.source.source.operation is receive
    assert receive_write.input_port is architecture.input_ports[0]
    assert receive_write.target is receive.target
    assert isinstance(assign_write, BoundBodyAssignWrite)
    assert assign_write.source is architecture.combinational.operations[0]
    assert assign_write.source.operation is assign
    assert assign_write.target is assign.target
    assert assign_write.expression is expression
    assert isinstance(send_skip, BoundBodySkip)
    assert send_skip.source is send


def test_body_program_preserves_receive_then_same_variable_rewrite() -> None:
    program = _Program()
    receive = program.receive("A", "y")
    expression = Expression("unary", operator="~", operands=(program.name("y"),))
    assign = Assign(program.variable("y"), expression)
    _, bound = _bind(program, Sequence((receive, assign, program.send("B", program.name("y")))))

    assert isinstance(bound.body_program, BoundBodySequence)
    receive_write, assign_write, _ = bound.body_program.items
    assert isinstance(receive_write, BoundBodyReceiveWrite)
    assert isinstance(assign_write, BoundBodyAssignWrite)
    assert receive_write.target is assign_write.target is program.variable("y")


def test_body_program_preserves_sequential_reassignment_order() -> None:
    program = _Program()
    first = Assign(program.variable("y"), program.name("a"))
    second_expression = Expression("unary", operator="~", operands=(program.name("y"),))
    second = Assign(program.variable("y"), second_expression)
    _, bound = _bind(program, Sequence((
        program.receive("A", "a"), first, second, program.send("B", program.name("y")),
    )))

    assert isinstance(bound.body_program, BoundBodySequence)
    first_write = bound.body_program.items[1]
    second_write = bound.body_program.items[2]
    assert isinstance(first_write, BoundBodyAssignWrite)
    assert isinstance(second_write, BoundBodyAssignWrite)
    assert first_write.source.operation is first
    assert second_write.source.operation is second
    assert first_write.target is second_write.target is program.variable("y")
    assert second_write.expression is second_expression


def test_body_program_preserves_if_else_nested_sequence_and_exact_condition() -> None:
    program = _Program()
    select = program.external_name("select")
    then_assign = Assign(program.variable("y"), program.name("a"))
    else_expression = Expression("unary", operator="~", operands=(program.name("a"),))
    else_assign = Assign(program.variable("y"), else_expression)
    conditional = If(
        select,
        Sequence((Skip(), then_assign)),
        Sequence((else_assign, Skip())),
    )
    _, bound = _bind(program, Sequence((
        program.receive("A", "a"), conditional, program.send("B", program.name("y")),
    )))

    assert isinstance(bound.body_program, BoundBodySequence)
    bound_if = bound.body_program.items[1]
    assert isinstance(bound_if, BoundBodyIf)
    assert bound_if.condition is select
    assert isinstance(bound_if.then_branch, BoundBodySequence)
    assert isinstance(bound_if.else_branch, BoundBodySequence)
    assert isinstance(bound_if.then_branch.items[0], BoundBodySkip)
    assert isinstance(bound_if.then_branch.items[1], BoundBodyAssignWrite)
    assert isinstance(bound_if.else_branch.items[0], BoundBodyAssignWrite)
    assert isinstance(bound_if.else_branch.items[1], BoundBodySkip)
    assert bound_if.then_branch.items[1].source.operation is then_assign
    assert bound_if.else_branch.items[0].source.operation is else_assign


def test_body_program_preserves_conditional_receive_and_parallel_branches() -> None:
    program = _Program()
    select = program.external_name("select")
    receive = program.receive("A", "a")
    first = Assign(program.variable("y"), program.name("a"))
    second = Assign(program.variable("z"), program.name("a"))
    body = Sequence((
        If(select, receive, Skip()),
        If(select, Parallel((first, second)), Skip()),
        If(select, program.send("B", program.name("y")), Skip()),
        If(select, program.send("C", program.name("z")), Skip()),
    ))
    architecture, bound = _bind(program, body)

    assert isinstance(bound.body_program, BoundBodySequence)
    receive_if = bound.body_program.items[0]
    assert isinstance(receive_if, BoundBodyIf)
    assert receive_if.condition is select
    assert isinstance(receive_if.then_branch, BoundBodyReceiveWrite)
    assert receive_if.then_branch.source is architecture.input_ports[0].body_receive
    parallel_if = bound.body_program.items[1]
    assert isinstance(parallel_if, BoundBodyIf)
    assert isinstance(parallel_if.then_branch, BoundBodyParallel)
    parallel = parallel_if.then_branch
    assert len(parallel.branches) == 2
    assert all(isinstance(branch, BoundBodyAssignWrite) for branch in parallel.branches)
    assert parallel.branches[0].source.operation is first
    assert parallel.branches[1].source.operation is second


@pytest.mark.parametrize(("values", "payload"), (((0,), _ONE_BIT), ((3, 0), _NIBBLE)))
def test_body_receive_write_preserves_exact_static_selected_lvalue(
    values: tuple[int, ...], payload: PayloadType,
) -> None:
    program = _Program()
    base = program.variable("x", _BYTE)
    target = _static_lvalue(base, *values)
    receive = Receive(ChannelEndpoint("A", payload_type=payload), target)
    body = Sequence((receive, program.send("B", target, payload)))
    architecture, bound = _bind(program, body)

    assert isinstance(bound.body_program, BoundBodySequence)
    write = bound.body_program.items[0]
    assert isinstance(write, BoundBodyReceiveWrite)
    assert isinstance(write.lvalue, BoundBodyLValue)
    assert write.target is base and write.lvalue.variable is base
    assert write.lvalue.source is target
    assert write.lvalue.selector_form == ("index" if len(values) == 1 else "range")
    assert write.lvalue.selector_values == values
    signal = next(item for item in bound.signals if item.id == write.receive_value_signal_id)
    assert signal.width == payload.width
    assert write.input_port is architecture.input_ports[0]


@pytest.mark.parametrize(("values", "payload"), (((0,), _ONE_BIT), ((3, 0), _NIBBLE)))
def test_body_assign_write_preserves_exact_static_selected_lvalue(
    values: tuple[int, ...], payload: PayloadType,
) -> None:
    program = _Program()
    base = program.variable("x", _BYTE)
    target = _static_lvalue(base, *values)
    value = program.external_name("value", payload)
    assign = Assign(target, value)
    body = Sequence((
        program.receive("A", "a"),
        assign,
        program.send("B", target, payload),
    ))
    _, bound = _bind(program, body)

    assert isinstance(bound.body_program, BoundBodySequence)
    write = bound.body_program.items[1]
    assert isinstance(write, BoundBodyAssignWrite)
    assert write.target is base and write.lvalue.variable is base
    assert write.lvalue.source is target
    assert write.lvalue.selector_values == values
    assert write.expression is value


@pytest.mark.parametrize("kind", ("receive", "assign"))
def test_body_program_rejects_malformed_dynamic_selected_lvalue_targets(kind: str) -> None:
    program = _Program()
    if kind == "receive":
        body = Sequence((program.receive("A", "x"), program.send("B", program.name("x"))))
    else:
        body = Sequence((
            program.receive("A", "a"),
            Assign(program.variable("x"), program.name("a")),
            program.send("B", program.name("x")),
        ))
    architecture = _architecture(program, body)
    selected = Expression(
        "select",
        variable=program.variable("x"),
        operands=(
            Expression(
                "index",
                operands=(program.name("x"),),
            ),
        ),
    )
    if kind == "receive":
        malformed_body = Sequence((
            Receive(ChannelEndpoint("A", payload_type=_ONE_BIT), selected),
            program.send("B", program.name("x")),
        ))
    else:
        malformed_body = Sequence((
            program.receive("A", "a"),
            Assign(selected, program.name("a")),
            program.send("B", program.name("x")),
        ))
    from dataclasses import replace
    malformed_transaction = replace(
        architecture.validated.decomposed.transaction,
        behavioral=replace(
            architecture.validated.decomposed.transaction.behavioral,
            body=malformed_body,
        ),
    )
    malformed_decomposed = replace(
        architecture.validated.decomposed,
        transaction=malformed_transaction,
    )
    malformed_architecture = replace(
        architecture,
        validated=replace(architecture.validated, decomposed=malformed_decomposed),
    )

    with pytest.raises(AsyncTemplateBindingError, match="literal integer"):
        bind_async_templates(malformed_architecture)


@pytest.mark.parametrize("kind", ("receive", "assign"))
def test_body_program_rejects_malformed_external_input_write_targets(kind: str) -> None:
    program = _Program()
    program.external_name("sel")
    if kind == "receive":
        body = Sequence((program.receive("A", "x"), program.send("B", program.name("x"))))
    else:
        body = Sequence((
            program.receive("A", "a"),
            Assign(program.variable("x"), program.name("a")),
            program.send("B", program.name("x")),
        ))
    architecture = _architecture(program, body)
    external = program.external_inputs["sel"]
    if kind == "receive":
        malformed_body = Sequence((
            Receive(ChannelEndpoint("A", payload_type=_ONE_BIT), external),
            program.send("B", program.name("x")),
        ))
    else:
        malformed_body = Sequence((
            program.receive("A", "a"),
            Assign(external, program.name("a")),
            program.send("B", program.name("x")),
        ))
    from dataclasses import replace
    malformed_transaction = replace(
        architecture.validated.decomposed.transaction,
        behavioral=replace(
            architecture.validated.decomposed.transaction.behavioral,
            body=malformed_body,
        ),
    )
    malformed_decomposed = replace(
        architecture.validated.decomposed,
        transaction=malformed_transaction,
    )
    malformed_architecture = replace(
        architecture,
        validated=replace(architecture.validated, decomposed=malformed_decomposed),
    )

    with pytest.raises(AsyncTemplateBindingError, match="exact local Variable"):
        bind_async_templates(malformed_architecture)


def test_body_program_replaces_user_assignments_but_preserves_structural_wiring() -> None:
    program = _Program()
    assign = Assign(program.variable("y"), program.name("a"))
    architecture, bound = _bind(program, Sequence((
        program.receive("A", "a"), assign, program.send("B", program.name("y")),
    )))

    assert not any(item.source is architecture.combinational.operations[0] for item in bound.assignments)
    assert isinstance(bound.body_program, BoundBodySequence)
    assert isinstance(bound.body_program.items[1], BoundBodyAssignWrite)
    assert bound.body_program.items[1].expression is assign.value
    received = program.variable("a")
    received_signal = _variable_signal_id(bound, received)
    assert any(
        item.target_signal_id == bound.body_program.items[0].receive_value_signal_id
        and item.source_signal_ids
        for item in bound.assignments
    )
    assert not any(item.target_signal_id == received_signal for item in bound.assignments)
