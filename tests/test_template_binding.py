from dataclasses import asdict

from svcsp_compiler import (
    PortDirection, PortSemanticKind, StructuralTemplate, analyze_dependencies, bind_templates,
    lower_behavioral, normalize_communication, parse_text, select_microarchitecture,
    synthesize_pipeline,
)


def bind(source):
    behavioral = lower_behavioral(parse_text(source, 'template_binding.sv'))
    microarchitecture = select_microarchitecture(
        synthesize_pipeline(analyze_dependencies(normalize_communication(behavioral)))
    )
    return bind_templates(microarchitecture)


def stage(graph, label):
    return next(item for item in graph.body_stages if item.operations[0].label == label)


def signals(graph):
    return {signal.id: signal for signal in graph.signals}


def test_linear_stage_binds_linear_controller_template():
    graph = bind('module m(interface A); logic x; always A.Receive(x); endmodule')
    body = stage(graph, 'receive')
    assert body.controller_template is StructuralTemplate.LINEAR_CONTROLLER
    ports = {port.name: port for port in body.contract.ports}
    assert ports['upstream_req'].direction is PortDirection.INPUT
    assert ports['upstream_req'].semantic_kind is PortSemanticKind.HANDSHAKE_REQUEST
    assert ports['downstream_ack'].direction is PortDirection.INPUT
    assert ports['local_control'].semantic_kind is PortSemanticKind.CONTROL


def test_join_stage_binds_join_controller_template():
    graph = bind('''module m(interface A, B); logic a, b; always fork
A.Receive(a); B.Receive(b); join endmodule''')
    join = next(item for item in graph.body_stages if item.operations[0].label == 'parallel_join')
    assert join.controller_template is StructuralTemplate.JOIN_CONTROLLER
    fanin = graph.bindings_for(join.id, 'upstream_req')
    fanin_signals = signals(graph)
    assert len(fanin) == 2
    assert {fanin_signals[binding.signal_id].source_stage for binding in fanin} == {'stage_0', 'stage_1'}
    assert all(fanin_signals[binding.signal_id].dependency_kind.name == 'PARALLEL_JOIN' for binding in fanin)


def test_conditional_receive_binds_separate_wrapper_template():
    graph = bind('''module m(interface A); logic c, x; always
if (c) A.Receive(x); endmodule''')
    wrapper = graph.wrappers[0]
    assert wrapper.controller_template is StructuralTemplate.CONDITIONAL_RECV_WRAPPER
    ports = {port.name: port for port in wrapper.contract.ports}
    assert ports['external_data'].direction is PortDirection.INPUT
    assert ports['body_data'].direction is PortDirection.OUTPUT
    assert signals(graph)[graph.bindings_for(wrapper.id, 'external_data')[0].signal_id].endpoint is wrapper.endpoint
    assert signals(graph)[graph.bindings_for(wrapper.id, 'body_data')[0].signal_id].target_stage == wrapper.attached_to


def test_conditional_send_binds_separate_wrapper_template():
    graph = bind('''module m(interface A); logic c, x; always
if (c) A.Send(x); endmodule''')
    wrapper = graph.wrappers[0]
    assert wrapper.controller_template is StructuralTemplate.CONDITIONAL_SEND_WRAPPER
    ports = {port.name: port for port in wrapper.contract.ports}
    assert ports['body_data'].direction is PortDirection.INPUT
    assert ports['external_data'].direction is PortDirection.OUTPUT
    assert signals(graph)[graph.bindings_for(wrapper.id, 'body_data')[0].signal_id].source_stage == wrapper.attached_to
    assert signals(graph)[graph.bindings_for(wrapper.id, 'external_data')[0].signal_id].endpoint is wrapper.endpoint


def test_body_and_wrapper_bindings_remain_separate():
    graph = bind('''module m(interface A); logic c, x; always
if (c) A.Send(x); endmodule''')
    wrapper = graph.wrappers[0]
    body = graph.stage(wrapper.attached_to)
    assert body.controller_template is StructuralTemplate.LINEAR_CONTROLLER
    assert wrapper.controller_template is StructuralTemplate.CONDITIONAL_SEND_WRAPPER
    assert wrapper.id in body.wrapper_attachments


def test_storage_is_bound_only_when_required():
    datapath = stage(bind('module m; logic x, increment; always x = x + increment; endmodule'), 'assign')
    topology = next(item for item in bind('''module m(interface A, B); logic a, b; always fork
A.Receive(a); B.Receive(b); join endmodule''').body_stages
                    if item.operations[0].label == 'parallel_join')
    assert datapath.storage.template is StructuralTemplate.ABSTRACT_STORAGE
    assert datapath.storage.requirement.required is True
    assert {port.name for port in datapath.storage.contract.ports} == {
        'data_in', 'data_out', 'control_in', 'control_out',
    }
    assert len(bind('module m; logic x, increment; always x = x + increment; endmodule').bindings_for(
        datapath.storage.id, 'data_in')) == 1
    assert topology.storage is None


def test_matched_delay_is_bound_only_when_present():
    delayed = stage(bind('''module m(interface C); logic a, b; always
C.Send(a + b); endmodule'''), 'send')
    forwarding = stage(bind('module m(interface C); logic x; always C.Send(x); endmodule'), 'send')
    assert delayed.matched_delay.template is StructuralTemplate.SYMBOLIC_MATCHED_DELAY
    assert delayed.matched_delay.requirement.symbol.startswith('matched_delay_')
    delayed_graph = bind('''module m(interface C); logic a, b; always
C.Send(a + b); endmodule''')
    delayed = stage(delayed_graph, 'send')
    assert len(delayed_graph.bindings_for(delayed.matched_delay.id, 'control_in')) == 1
    assert len(delayed_graph.bindings_for(delayed.matched_delay.id, 'control_out')) == 1
    assert forwarding.matched_delay is None


def test_binding_is_deterministic():
    source = 'module m(interface A); logic x; always A.Send(x); endmodule'
    first, second = bind(source), bind(source)
    assert first == second
    assert first.port_bindings == second.port_bindings


def test_binding_preserves_operation_expression_and_identity():
    source = '''module m(interface A[2]); logic c, x, y; always begin
if (c) A[1].Send(x + y); end endmodule'''
    behavioral = lower_behavioral(parse_text(source, 'template_binding.sv'))
    microarchitecture = select_microarchitecture(
        synthesize_pipeline(analyze_dependencies(normalize_communication(behavioral)))
    )
    bound = bind_templates(microarchitecture)
    original_body = next(item for item in microarchitecture.stages if item.wrapper_attachments)
    original_wrapper = microarchitecture.wrappers[0]
    body = bound.stage(original_body.id)
    wrapper = bound.wrappers[0]
    assert body.operations is original_body.body_operations
    assert body.combinational_logic is original_body.combinational_logic
    assert body.location is original_body.location
    assert wrapper.endpoint is original_wrapper.endpoint
    assert wrapper.enable is original_wrapper.enable
    assert wrapper.location is original_wrapper.location
    assert wrapper.endpoint.selectors[0].operands[0].value == '1'


def test_enable_and_payload_connections_preserve_their_identities():
    graph = bind('''module m(interface A); logic c, x, y; always
if (c) A.Send(x + y); endmodule''')
    wrapper = graph.wrappers[0]
    signal_map = signals(graph)
    enable = signal_map[graph.bindings_for(wrapper.id, 'enable')[0].signal_id]
    payload = signal_map[graph.bindings_for(wrapper.id, 'body_data')[0].signal_id]
    assert enable.enable is wrapper.enable
    assert payload.expression is None
    assert payload.drivers[0].owner.startswith('storage_')


def test_data_dependency_has_explicit_payload_connection_mapping():
    graph = bind('''module m(interface A, B); logic x, y, increment; always begin
A.Receive(x); y = x + increment; B.Send(y); end endmodule''')
    signal_map = signals(graph)
    storage_inputs = [signal_map[binding.signal_id] for binding in graph.port_bindings
                      if binding.port_name == 'data_in']
    storage_outputs = [signal_map[binding.signal_id] for binding in graph.port_bindings
                       if binding.port_name == 'data_out']
    assert any(signal.expression is not None and signal.expression.form == 'binary' for signal in storage_inputs)
    assert all(len(signal.drivers) == 1 for signal in storage_outputs)


def test_every_required_template_port_has_a_connection():
    graph = bind('''module m(interface A); logic c, x; always
if (c) A.Receive(x); endmodule''')
    for instance, contract in [
        *((stage.id, stage.contract) for stage in graph.body_stages),
        *((wrapper.id, wrapper.contract) for wrapper in graph.wrappers),
        *((stage.storage.id, stage.storage.contract) for stage in graph.body_stages if stage.storage),
        *((stage.matched_delay.id, stage.matched_delay.contract)
          for stage in graph.body_stages if stage.matched_delay),
    ]:
        for port in contract.ports:
            assert len(graph.bindings_for(instance, port.name)) >= port.minimum


def test_binding_does_not_mutate_microarchitecture_graph():
    behavioral = lower_behavioral(parse_text('module m(interface A); logic x; always A.Send(x); endmodule'))
    microarchitecture = select_microarchitecture(
        synthesize_pipeline(analyze_dependencies(normalize_communication(behavioral)))
    )
    snapshot = asdict(microarchitecture)
    bind_templates(microarchitecture)
    assert asdict(microarchitecture) == snapshot
