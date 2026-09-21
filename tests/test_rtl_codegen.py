from dataclasses import asdict, replace

import pytest

from svcsp_compiler import (
    BoundTemplateParameterBinding, ModulePortRole, PayloadWidth, PortDirection, RTLCodegenError,
    SignalDriverKind, StructuralTemplate, TemplateBindingError,
    analyze_dependencies, bind_templates, emit_systemverilog,
    lower_behavioral, normalize_communication, parse_text, select_microarchitecture,
    synthesize_pipeline,
)


def bound(source):
    behavioral = lower_behavioral(parse_text(source, 'rtl_codegen.sv'))
    return bind_templates(select_microarchitecture(
        synthesize_pipeline(analyze_dependencies(normalize_communication(behavioral)))
    ))


def emit(source):
    return emit_systemverilog(bound(source))


def test_simple_linear_module_emits_scalar_signal_and_linear_template():
    rtl = emit('module m(interface C); logic x; always C.Send(x); endmodule')
    assert rtl.startswith('module m (')
    assert 'output logic channel_C_send_payload;' in rtl
    assert 'linear_controller stage_stage_0 (' in rtl


def test_concrete_and_symbolic_payload_widths_are_emitted_without_sizing():
    concrete = emit('module m(Channel #(8) C); logic [7:0] x; always C.Send(x); endmodule')
    symbolic = emit('''module m #(parameter int W = 8) (Channel #(W) C); logic [W-1:0] x; always
C.Send(x); endmodule''')
    assert 'output logic [7:0] channel_C_send_payload;' in concrete
    assert 'module m #(\n    parameter int W = 8\n) (' in symbolic
    assert 'output logic [W-1:0] channel_C_send_payload;' in symbolic


def test_join_fanin_is_emitted_as_deterministic_indexed_formals():
    rtl = emit('''module m(interface A, B); logic a, b; always fork
A.Receive(a); B.Receive(b); join endmodule''')
    assert 'join_controller join_stage_2 (' in rtl
    assert '.upstream_req_0(' in rtl
    assert '.upstream_req_1(' in rtl
    assert '.upstream_ack_0(' in rtl
    assert '.upstream_ack_1(' in rtl


def test_join_formal_names_are_resolved_by_phase_7a_bindings():
    graph = bound('''module m(interface A, B); logic a, b; always fork
A.Receive(a); B.Receive(b); join endmodule''')
    join = next(stage for stage in graph.body_stages if stage.controller_template.value == 'join_controller')
    bindings = graph.bindings_for(join.id, 'upstream_req')
    rtl = emit_systemverilog(graph)
    assert [item.formal_name for item in bindings] == ['upstream_req_0', 'upstream_req_1']
    assert all(f'.{item.formal_name}(' in rtl for item in bindings)


def test_body_controller_contracts_are_handshake_and_control_only():
    graph = bound('''module m(interface A, B); logic a, b; always fork
A.Receive(a); B.Receive(b); join endmodule''')
    controllers = [stage.contract for stage in graph.body_stages]
    assert all(port.semantic_kind.value != 'payload' for contract in controllers for port in contract.ports)


@pytest.mark.parametrize(('source', 'template'), [
    ('''module m(interface A); logic c, x; always if (c) A.Receive(x); endmodule''',
     'conditional_recv_wrapper'),
    ('''module m(interface A); logic c, x; always if (c) A.Send(x); endmodule''',
     'conditional_send_wrapper'),
])
def test_conditional_wrapper_templates_remain_separate_from_linear_body(source, template):
    rtl = emit(source)
    assert 'linear_controller stage_stage_0 (' in rtl
    assert f'{template} wrapper_' in rtl
    assert '.enable(' in rtl


def test_storage_and_matched_delay_are_emitted_only_when_bound():
    datapath = emit('''module m; logic x, increment; always
x = x + increment; endmodule''')
    anchor = emit('''module m(interface C); logic c, x; always
if (c) C.Send(x); endmodule''')
    assert 'abstract_storage #(\n    .WIDTH(1)\n  ) storage_storage_stage_0 (' in datapath
    assert 'symbolic_matched_delay delay_matched_delay_stage_0 (' in datapath
    assert 'abstract_storage' not in anchor
    assert 'symbolic_matched_delay' not in anchor


def test_storage_width_parameter_is_bound_before_codegen_for_scalar_and_concrete_payloads():
    scalar = emit('''module m; logic x, y; always x = x + y; endmodule''')
    concrete = emit('''module m; logic [7:0] x, y; always x = x + y; endmodule''')
    assert 'abstract_storage #(\n    .WIDTH(1)\n  ) storage_storage_stage_0 (' in scalar
    assert 'abstract_storage #(\n    .WIDTH(8)\n  ) storage_storage_stage_0 (' in concrete


def test_storage_width_parameter_preserves_symbolic_parameter_identity():
    rtl = emit('''module m #(parameter int W = 8); logic [W-1:0] x, y; always
x = x + y; endmodule''')
    assert 'abstract_storage #(\n    .WIDTH(W)\n  ) storage_storage_stage_0 (' in rtl


def test_codegen_rejects_missing_required_storage_width_binding():
    graph = bound('''module m; logic [7:0] x, y; always x = x + y; endmodule''')
    incomplete = replace(graph, parameter_bindings=())
    with pytest.raises(RTLCodegenError, match='missing required parameter WIDTH'):
        emit_systemverilog(incomplete)


def test_codegen_rejects_duplicate_unknown_and_invalid_instance_parameter_bindings():
    graph = bound('''module m; logic [7:0] x, y; always x = x + y; endmodule''')
    width = graph.parameter_bindings[0]
    duplicate = replace(graph, parameter_bindings=graph.parameter_bindings + (width,))
    unknown = replace(graph, parameter_bindings=(
        BoundTemplateParameterBinding(width.instance_id, 'UNKNOWN', PayloadWidth(bits=8)),
    ))
    invalid_instance = replace(graph, parameter_bindings=(
        BoundTemplateParameterBinding('missing_instance', width.formal_name, PayloadWidth(bits=8)),
    ))
    with pytest.raises(RTLCodegenError, match='duplicate template parameter binding'):
        emit_systemverilog(duplicate)
    with pytest.raises(RTLCodegenError, match='unknown template parameter'):
        emit_systemverilog(unknown)
    with pytest.raises(RTLCodegenError, match='unknown instance'):
        emit_systemverilog(invalid_instance)


def test_selected_endpoints_and_shadowed_variables_get_legal_distinct_names():
    selected = emit('''module m(interface A[2]); logic c, x; always begin
if (c) A[0].Send(x); if (c) A[1].Send(x); end endmodule''')
    shadowed = emit('''module m(interface A, B); logic x; always begin
begin logic x; A.Receive(x); end B.Send(x); end endmodule''')
    assert 'A[0]' not in selected and 'A[1]' not in selected
    assert selected.count('conditional_send_wrapper') == 2
    variable_lines = [line for line in shadowed.splitlines() if line.startswith('  logic') and ' var_x_' in line]
    assert len(variable_lines) == 2
    assert len(set(variable_lines)) == 2


def test_receive_and_send_endpoints_have_explicit_directional_module_ports():
    receive = bound('module m(Channel #(8) C); logic [7:0] x; always C.Receive(x); endmodule')
    send = bound('module m(Channel #(8) C); logic [7:0] x; always C.Send(x); endmodule')
    receive_ports = {(port.role, port.direction) for port in receive.module_ports}
    send_ports = {(port.role, port.direction) for port in send.module_ports}
    assert receive_ports == {
        (ModulePortRole.REQUEST, PortDirection.INPUT),
        (ModulePortRole.ACKNOWLEDGE, PortDirection.OUTPUT),
        (ModulePortRole.PAYLOAD, PortDirection.INPUT),
    }
    assert send_ports == {
        (ModulePortRole.REQUEST, PortDirection.OUTPUT),
        (ModulePortRole.ACKNOWLEDGE, PortDirection.INPUT),
        (ModulePortRole.PAYLOAD, PortDirection.OUTPUT),
    }
    assert next(port for port in receive.module_ports if port.role is ModulePortRole.PAYLOAD).width.bits == 8


@pytest.mark.parametrize('source', [
    '''module m(interface C); logic c, x; always if (c) C.Receive(x); endmodule''',
    '''module m(interface C); logic c, x; always if (c) C.Send(x); endmodule''',
])
def test_wrapper_external_ports_are_module_ports_and_body_side_stays_internal(source):
    graph = bound(source)
    wrapper = graph.wrappers[0]
    external = {item.signal_id for item in graph.port_bindings
                if item.instance_id == wrapper.id and item.port_name.startswith('external_')}
    body = {item.signal_id for item in graph.port_bindings
            if item.instance_id == wrapper.id and item.port_name.startswith('body_')}
    module_signals = {port.signal_id for port in graph.module_ports}
    assert external <= module_signals
    assert not body & module_signals


def test_conditional_wrapper_driver_ownership_is_explicit():
    send = bound('''module m(interface C); logic c, x, y; always
if (c) C.Send(x + y); endmodule''')
    recv = bound('''module m(interface C); logic c, x; always
if (c) C.Receive(x); endmodule''')
    send_wrapper, recv_wrapper = send.wrappers[0], recv.wrappers[0]
    send_signals = {signal.id: signal for signal in send.signals}
    recv_signals = {signal.id: signal for signal in recv.signals}
    send_body = send_signals[send.bindings_for(send_wrapper.id, 'body_data')[0].signal_id]
    send_external = send_signals[send.bindings_for(send_wrapper.id, 'external_data')[0].signal_id]
    recv_body = recv_signals[recv.bindings_for(recv_wrapper.id, 'body_data')[0].signal_id]
    recv_external = recv_signals[recv.bindings_for(recv_wrapper.id, 'external_data')[0].signal_id]
    recv_ack = recv_signals[recv.bindings_for(recv_wrapper.id, 'external_ack')[0].signal_id]
    assert send_body.drivers[0].kind is SignalDriverKind.TEMPLATE_OUTPUT
    assert send_external.drivers[0].owner == f'{send_wrapper.id}.external_data'
    assert recv_external.drivers[0].kind is SignalDriverKind.MODULE_INPUT
    assert recv_body.drivers[0].owner == f'{recv_wrapper.id}.body_data'
    assert recv_ack.drivers[0].owner == f'{recv_wrapper.id}.external_ack'


def test_symbolic_expression_drives_only_storage_input():
    graph = bound('''module m; logic x, y; always x = x + y; endmodule''')
    signals = {signal.id: signal for signal in graph.signals}
    storage_input = next(signals[item.signal_id] for item in graph.port_bindings if item.port_name == 'data_in')
    assert storage_input.expression is not None
    assert storage_input.drivers[0].kind is SignalDriverKind.EXPRESSION
    assert len(storage_input.drivers) == 1


def test_module_input_fanout_is_allowed_for_multiple_readers():
    graph = bound('module m(interface C); logic x; always C.Receive(x); endmodule')
    stage = graph.body_stages[0]
    first = next(item for item in graph.bindings_for(stage.id, 'upstream_req'))
    extra = replace(first, index=1, formal_name=stage.contract.port('upstream_req').resolved_formal_name(1))
    expanded = replace(graph, port_bindings=graph.port_bindings + (extra,))
    rtl = emit_systemverilog(expanded)
    assert '.upstream_req_0(' in rtl and '.upstream_req_1(' in rtl


@pytest.mark.parametrize('source', [
    '''module m(interface C); logic x, y; always begin C.Send(x); C.Send(y); end endmodule''',
    '''module m(interface C); logic c, x, y; always begin
if (c) C.Receive(x); if (c) C.Receive(y); end endmodule''',
])
def test_repeated_endpoint_outputs_fail_closed_without_arbitration(source):
    behavioral = lower_behavioral(parse_text(source, 'repeated_endpoint.sv'))
    microarchitecture = select_microarchitecture(
        synthesize_pipeline(analyze_dependencies(normalize_communication(behavioral)))
    )
    with pytest.raises(TemplateBindingError, match='multiple drivers'):
        bind_templates(microarchitecture)


def test_module_ports_are_not_redeclared_as_internal_logic_and_are_deterministic():
    graph = bound('''module m(Channel #(8) A, B); logic c; logic [7:0] x; always begin
A.Receive(x); if (c) B.Send(x); end endmodule''')
    first, second = emit_systemverilog(graph), emit_systemverilog(graph)
    assert first == second
    assert [port.name for port in graph.module_ports] == sorted(port.name for port in graph.module_ports)
    for port in graph.module_ports:
        assert f'logic sig_module_port_{port.name}' not in first


def test_expression_assignments_and_enable_conditions_are_emitted_mechanically():
    rtl = emit('''module m(interface C); logic c, x, y; always begin
x = x + y; if (c) C.Send(x + y); end endmodule''')
    assert 'assign sig_' in rtl
    assert ' + ' in rtl
    assert ' = var_c_' in rtl


def test_emission_is_deterministic_and_does_not_mutate_bound_graph():
    graph = bound('''module m(interface C); logic c, x; always
if (c) C.Send(x); endmodule''')
    snapshot = asdict(graph)
    assert emit_systemverilog(graph) == emit_systemverilog(graph)
    assert asdict(graph) == snapshot


def test_incomplete_bound_graph_fails_closed():
    graph = bound('module m(interface C); logic x; always C.Send(x); endmodule')
    incomplete = replace(graph, port_bindings=tuple(
        item for item in graph.port_bindings if item.port_name != 'local_control'
    ))
    with pytest.raises(RTLCodegenError, match='missing or excess required binding'):
        emit_systemverilog(incomplete)


def test_codegen_rejects_preexisting_internal_multiple_driver_conflict():
    graph = bound('module m; logic x, y; always x = x + y; endmodule')
    conflicted = next(signal for signal in graph.signals if signal.expression is not None)
    invalid = replace(graph, signals=tuple(
        replace(signal, drivers=signal.drivers + signal.drivers) if signal.id == conflicted.id else signal
        for signal in graph.signals
    ))
    with pytest.raises(RTLCodegenError, match='multiple bound drivers'):
        emit_systemverilog(invalid)


def test_missing_external_channel_module_port_fails_closed():
    graph = bound('module m(interface C); logic x; always C.Send(x); endmodule')
    with pytest.raises(RTLCodegenError, match='module-port signal is missing'):
        emit_systemverilog(replace(graph, module_ports=()))
