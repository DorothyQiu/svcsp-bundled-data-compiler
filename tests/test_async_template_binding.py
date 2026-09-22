"""M7A structural-binding contract tests for the M6 async architecture."""
from __future__ import annotations

from svcsp_compiler.async_microarchitecture import lower_microarchitecture
from svcsp_compiler.async_template_binding import BoundAsyncModule, bind_async_templates
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


_ONE_BIT = PayloadType("logic", ONE_BIT)
_BYTE = PayloadType("logic", width=PayloadWidth(bits=8))


class _Program:
    def __init__(self) -> None:
        self.variables: dict[str, Variable] = {}

    def variable(self, name: str, payload_type: PayloadType = _ONE_BIT) -> Variable:
        return self.variables.setdefault(
            name, Variable(name, (), SourceLocation("async_binding.sv", 1, 1), payload_type),
        )

    def name(self, name: str) -> Expression:
        variable = self.variable(name)
        return Expression("name", value=name, variable=variable)

    def receive(self, channel: ChannelEndpoint | str, target: str, payload_type: PayloadType = _ONE_BIT) -> Receive:
        endpoint = channel if isinstance(channel, ChannelEndpoint) else ChannelEndpoint(channel, payload_type=payload_type)
        return Receive(endpoint, self.variable(target, payload_type))

    def send(self, channel: ChannelEndpoint | str, value: Expression,
             payload_type: PayloadType = _ONE_BIT) -> Send:
        endpoint = channel if isinstance(channel, ChannelEndpoint) else ChannelEndpoint(channel, payload_type=payload_type)
        return Send(endpoint, value)

    def module(self, body) -> BehavioralModule:
        return BehavioralModule("async_binding", body, (), tuple(self.variables.values()))


def _architecture(program: _Program, body):
    transaction = extract_transaction(program.module(body))
    return lower_microarchitecture(analyze_semantics(decompose_transaction(transaction)))


def _bind(program: _Program, body):
    architecture = _architecture(program, body)
    return architecture, bind_async_templates(architecture)


def _bound_for(bound: BoundAsyncModule, source: object):
    return next(item for item in bound.instances if item.source is source)


def _bindings_for(bound: BoundAsyncModule, instance) -> tuple:
    return tuple(binding for binding in bound.port_bindings if binding.instance_id == instance.id)


def _actual_signal(bound: BoundAsyncModule, binding):
    return next(signal for signal in bound.signals if signal.id == binding.actual_signal_id)


def _enable_channel_binding(bound: BoundAsyncModule, channel):
    return next(item for item in bound.enable_channels if item.source is channel)


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


def test_1r1s_binds_the_exact_m6_join_storage_and_fork_topology() -> None:
    program = _Program()
    architecture, bound = _bind(program, Sequence((
        program.receive("A", "a"),
        program.send("B", program.name("a")),
    )))

    assert isinstance(bound, BoundAsyncModule)
    assert bound.architecture is architecture
    assert _bound_for(bound, architecture.input_join).template == "four_phase_input_join"
    assert _bound_for(bound, architecture.storage.slots[0]).template == "bundled_data_storage"
    assert _bound_for(bound, architecture.output_fork).template == "four_phase_output_fork"
    assert not hasattr(bound, "pipeline")
    assert not hasattr(bound, "normalized")


def test_2r1s_binds_one_join_with_both_inputs_and_no_source_order_link() -> None:
    program = _Program()
    architecture, bound = _bind(program, Sequence((
        program.receive("A", "a"),
        program.receive("B", "b"),
        program.send("C", Expression("literal", value="1'b0")),
    )))

    join = _bound_for(bound, architecture.input_join)
    assert join.inputs == architecture.input_join.inputs
    assert join.serializes_inputs is False
    assert len([port for port in bound.module_ports if port.flow == "receive"]) == 2
    assert not any(connection.kind == "source_sequence" for connection in bound.connections)


def test_1r2s_binds_independent_output_branches_and_completion_structure() -> None:
    program = _Program()
    expression = Expression("binary", operator="+", operands=(program.name("a"), Expression("literal", value="1'b1")))
    architecture, bound = _bind(program, Sequence((
        program.receive("A", "a"),
        Assign(program.variable("y"), expression),
        program.send("B", program.name("y")),
        program.send("C", program.name("y")),
    )))

    fork = _bound_for(bound, architecture.output_fork)
    assert fork.outputs == architecture.output_fork.outputs
    assert fork.serializes_outputs is False
    assert len([item for item in bound.instances if item.template == "bundled_data_storage"]) == 2
    assert len([item for item in bound.instances if item.template == "four_phase_output_completion"]) == 1
    assert not any(connection.kind == "source_sequence" for connection in bound.connections)


def test_2r2s_preserves_every_m6_input_output_and_storage_identity() -> None:
    program = _Program()
    architecture, bound = _bind(program, Sequence((
        program.receive("A", "a"),
        program.receive("B", "b"),
        program.send("C", program.name("a")),
        program.send("D", program.name("b")),
    )))

    assert _bound_for(bound, architecture.input_join).source is architecture.input_join
    assert _bound_for(bound, architecture.output_fork).source is architecture.output_fork
    assert {_bound_for(bound, slot).source for slot in architecture.storage.slots} == set(architecture.storage.slots)


def test_every_m6_enable_channel_binds_a_real_one_bit_four_phase_channel_and_body_sender() -> None:
    program = _Program()
    select = program.name("select")
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
            assert channel not in architecture.output_fork.outputs


def test_conditional_receive_binds_only_the_en_receive_microstage_to_external_and_body_paths() -> None:
    program = _Program()
    select = program.name("select")
    architecture, bound = _bind(program, Sequence((
        If(select, program.receive("A", "a"), Skip()),
        If(select, program.send("B", program.name("a")), Skip()),
    )))

    stage = architecture.en_receive_stages[0]
    channel_binding = _enable_channel_binding(bound, stage.enable_channel)
    instance = _bound_for(bound, stage)
    bindings = {item.formal_name: item for item in _bindings_for(bound, instance)}
    assert instance.template == "en_receive_stage"
    assert {"enable_req", "enable_ack", "enable_data", "external_req", "external_ack", "external_data",
            "body_req", "body_ack", "body_data"} == set(bindings)
    assert bindings["enable_req"].actual_signal_id == channel_binding.request_signal_id
    assert bindings["enable_ack"].actual_signal_id == channel_binding.acknowledge_signal_id
    assert bindings["enable_data"].actual_signal_id == channel_binding.data_signal_id
    assert not any(item.source is stage.input_port and item.template == "four_phase_receive_port"
                   for item in bound.instances)
    assert bindings["body_req"].actual_signal_id in {
        item.actual_signal_id for item in _bindings_for(bound, _bound_for(bound, architecture.input_join))
    }


def test_conditional_send_binds_only_the_en_send_microstage_to_external_and_body_paths() -> None:
    program = _Program()
    architecture, bound = _bind(program, Sequence((
        program.receive("A", "a"),
        If(program.name("select"), program.send("B", Expression("literal", value="1'b0")), Skip()),
    )))

    stage = architecture.en_send_stages[0]
    channel_binding = _enable_channel_binding(bound, stage.enable_channel)
    instance = _bound_for(bound, stage)
    bindings = {item.formal_name: item for item in _bindings_for(bound, instance)}
    assert instance.template == "en_send_stage"
    assert {"enable_req", "enable_ack", "enable_data", "body_req", "body_ack", "body_data",
            "external_req", "external_ack", "external_data"} == set(bindings)
    assert bindings["enable_req"].actual_signal_id == channel_binding.request_signal_id
    assert bindings["enable_ack"].actual_signal_id == channel_binding.acknowledge_signal_id
    assert bindings["enable_data"].actual_signal_id == channel_binding.data_signal_id
    assert not any(item.source is stage.output_port and item.template == "four_phase_send_port"
                   for item in bound.instances)


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
    assert architecture.input_join.inputs[0].body_receive.source.operation.channel is receive_endpoint
    assert architecture.output_fork.outputs[0].body_send.source.operation.channel is send_endpoint


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
        assert delay.input_join is requirement.input_join
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
    select = program.name("select")
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
        "external_req", "external_ack", "external_data", "body_req", "body_ack", "body_data",
    }
    assert instance.template == "en_receive_stage"
    assert bindings["enable_req"].actual_signal_id == channel.request_signal_id
    assert bindings["enable_ack"].actual_signal_id == channel.acknowledge_signal_id
    assert bindings["enable_data"].actual_signal_id == channel.data_signal_id
    assert _actual_signal(bound, bindings["external_req"]).kind == "request"
    assert _actual_signal(bound, bindings["external_ack"]).kind == "acknowledge"
    assert _actual_signal(bound, bindings["external_data"]).kind == "payload"
    assert _actual_signal(bound, bindings["body_req"]).kind == "request"
    assert _actual_signal(bound, bindings["body_ack"]).kind == "acknowledge"
    assert _actual_signal(bound, bindings["body_data"]).kind == "payload"
    assert _actual_signal(bound, bindings["enable_data"]).kind == "payload"
    assert not any(item.source is stage.input_port and item.template == "four_phase_receive_port"
                   for item in bound.instances)


def test_join_output_and_completion_handshakes_are_declared_and_bound() -> None:
    program = _Program()
    architecture, bound = _bind(program, Sequence((
        program.receive("A", "a"),
        program.receive("B", "b"),
        program.send("C", program.name("a")),
        program.send("D", program.name("b")),
    )))

    join = _bound_for(bound, architecture.input_join)
    join_bindings = {binding.formal_name: binding for binding in _bindings_for(bound, join)}
    assert {"input_req", "input_ack", "stage_release"} <= set(join_bindings)
    assert all(_actual_signal(bound, join_bindings[name]).kind in {"request", "acknowledge", "control"}
               for name in join_bindings)

    for output in architecture.output_fork.outputs:
        branch = _bound_for(bound, output)
        branch_bindings = {binding.formal_name: binding for binding in _bindings_for(bound, branch)}
        assert {"external_req", "external_ack", "payload", "launch", "complete"} <= set(branch_bindings)
        assert _actual_signal(bound, branch_bindings["external_req"]).kind == "request"
        assert _actual_signal(bound, branch_bindings["external_ack"]).kind == "acknowledge"

    completion = next(instance for instance in bound.instances if instance.template == "four_phase_output_completion")
    completion_bindings = {binding.formal_name: binding for binding in _bindings_for(bound, completion)}
    assert {"complete", "stage_complete"} <= set(completion_bindings)
    assert _actual_signal(bound, completion_bindings["stage_complete"]).kind == "control"
    assert join_bindings["stage_release"].actual_signal_id == completion_bindings["stage_complete"].actual_signal_id


def test_post_input_enable_is_not_an_output_fork_member_and_matched_delays_remain_typed() -> None:
    program = _Program()
    expression = Expression("binary", operator="+", operands=(program.name("a"), Expression("literal", value="1'b1")))
    conditional_send = program.send("B", program.name("y"))
    architecture, bound = _bind(program, Sequence((
        program.receive("A", "a"),
        Assign(program.variable("y"), expression),
        If(program.name("select"), conditional_send, Skip()),
        program.send("C", program.name("y")),
    )))

    post_input = next(channel for channel in architecture.enable_channels
                      if channel.availability.value == "post_input")
    binding = _enable_channel_binding(bound, post_input)
    assert binding.producer_completion is True
    assert post_input not in architecture.output_fork.outputs
    for requirement in architecture.matched_delays:
        delay = _bound_for(bound, requirement)
        bindings = {binding.formal_name: binding for binding in _bindings_for(bound, delay)}
        assert set(bindings) == {"control_in", "control_out"}
        assert bindings["control_in"].actual_signal_id in {signal.id for signal in bound.signals}
        assert bindings["control_out"].actual_signal_id in {signal.id for signal in bound.signals}
        assert delay.storage_slot is requirement.storage_slot
        assert delay.output_port is requirement.output_port

    _assert_complete_typed_bindings(bound)


def test_input_join_uses_parameterized_vector_handshakes_and_typed_pack_assignments() -> None:
    program = _Program()
    architecture, bound = _bind(program, Sequence((
        program.receive("A", "a"),
        program.receive("B", "b"),
        program.send("C", Expression("literal", value="1'b0")),
    )))

    join = _bound_for(bound, architecture.input_join)
    parameters = {item.formal_name: item.value for item in bound.parameter_bindings if item.instance_id == join.id}
    bindings = {item.formal_name: item for item in _bindings_for(bound, join)}
    assert parameters["N"].bits == 2
    assert _actual_signal(bound, bindings["input_req"]).width.bits == 2
    assert _actual_signal(bound, bindings["input_ack"]).width.bits == 2
    assert not any(name.startswith("input_req_") or name.startswith("input_ack_") for name in bindings)
    assert {assignment.kind for assignment in bound.assignments} >= {"pack_input_handshakes", "unpack_input_handshakes"}


def test_output_fork_and_completion_use_parameterized_vector_branch_controls() -> None:
    program = _Program()
    architecture, bound = _bind(program, Sequence((
        program.receive("A", "a"),
        program.send("B", program.name("a")),
        program.send("C", program.name("a")),
    )))

    fork = _bound_for(bound, architecture.output_fork)
    completion = next(item for item in bound.instances if item.template == "four_phase_output_completion")
    fork_parameters = {item.formal_name: item.value for item in bound.parameter_bindings if item.instance_id == fork.id}
    completion_parameters = {item.formal_name: item.value for item in bound.parameter_bindings
                             if item.instance_id == completion.id}
    fork_bindings = {item.formal_name: item for item in _bindings_for(bound, fork)}
    completion_bindings = {item.formal_name: item for item in _bindings_for(bound, completion)}
    assert fork_parameters["M"].bits == 2
    assert completion_parameters["M"].bits == 2
    assert _actual_signal(bound, fork_bindings["launch"]).width.bits == 2
    assert _actual_signal(bound, fork_bindings["complete"]).width.bits == 2
    assert _actual_signal(bound, completion_bindings["complete"]).width.bits == 2
    assert not any(name.startswith("launch_") or name.startswith("complete_") for name in fork_bindings)


def test_body_datapath_uses_distinct_storage_signals_and_explicit_assignments() -> None:
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
    assert any(assignment.source is architecture.combinational.operations[0]
               and assignment.expression is expression for assignment in bound.assignments)
    body_send = architecture.output_fork.outputs[0].body_send
    assert any(assignment.source is body_send and assignment.expression is body_send.source.operation.value
               and assignment.target_signal_id == storage_bindings["data_in"].actual_signal_id
               for assignment in bound.assignments)
    output = _bound_for(bound, architecture.output_fork.outputs[0])
    output_bindings = {item.formal_name: item for item in _bindings_for(bound, output)}
    assert output_bindings["payload"].actual_signal_id == storage_bindings["data_out"].actual_signal_id


def test_enable_payload_is_driven_by_its_exact_m4_condition_not_a_scalar_wire() -> None:
    program = _Program()
    select = program.name("select")
    architecture, bound = _bind(program, Sequence((
        If(select, program.receive("A", "a"), Skip()),
        If(select, program.send("B", program.name("a")), Skip()),
    )))

    for channel in architecture.enable_channels:
        binding = _enable_channel_binding(bound, channel)
        assert any(assignment.target_signal_id == binding.data_signal_id
                   and assignment.expression is channel.enable.condition
                   for assignment in bound.assignments)
        assert _actual_signal(bound, next(item for item in _bindings_for(bound, binding.body_sender)
                                          if item.formal_name == "req")).kind == "request"
        assert _actual_signal(bound, next(item for item in _bindings_for(bound, binding.body_sender)
                                          if item.formal_name == "ack")).kind == "acknowledge"
