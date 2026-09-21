from dataclasses import replace

import pytest

from svcsp_compiler import (
    ONE_BIT, BehavioralIRError, ChannelEndpoint, PayloadType, PayloadWidth, TemplateBindingError, analyze_dependencies,
    bind_templates, lower_behavioral, normalize_communication, parse_text,
    select_microarchitecture, synthesize_pipeline,
)


def lower(source):
    return lower_behavioral(parse_text(source, 'widths.sv'))


def bound(source):
    behavioral = lower(source)
    normalized = normalize_communication(behavioral)
    dependencies = analyze_dependencies(normalized)
    pipeline = synthesize_pipeline(dependencies)
    microarchitecture = select_microarchitecture(pipeline)
    return behavioral, normalized, dependencies, pipeline, microarchitecture, bind_templates(microarchitecture)


def payload_signals(graph):
    return [signal for signal in graph.signals if signal.semantic_kind.value == 'payload']


def test_frontend_preserves_scalar_concrete_and_symbolic_declared_widths():
    scalar = parse_text('module m; logic x; reg y; bit z; always begin end endmodule')['variables']
    concrete = parse_text('module m; logic [7:0] x; always begin end endmodule')['variables'][0]['payload_type']
    symbolic = parse_text('''module m #(parameter int W = 8); logic [W-1:0] x;
always begin end endmodule''')['variables'][0]['payload_type']
    assert [variable['payload_type']['width']['bits'] for variable in scalar] == [1, 1, 1]
    assert concrete['width']['bits'] == 8
    assert symbolic['width']['bits'] is None
    assert symbolic['width']['symbolic'] == 'W'


def test_channel_width_parameter_is_preserved_when_declared():
    frontend = parse_text('''module m #(parameter int W = 8) (Channel #(W) C); logic [W-1:0] x;
always C.Send(x); endmodule''')
    assert frontend['channels'][0]['payload_type']['width']['symbolic'] == 'W'
    assert lower_behavioral(frontend).body.channel.payload_type.width.symbolic == 'W'


def test_widths_survive_all_existing_ir_passes():
    behavioral, normalized, dependencies, pipeline, microarchitecture, graph = bound('''module m(interface A, B); logic [7:0] x, y, increment; always begin
A.Receive(x); y = x + increment; B.Send(y); end endmodule''')
    assert behavioral.variables[0].payload_type.width.bits == 8
    assert normalized.variables[1].payload_type.width.bits == 8
    receive = next(node for node in dependencies.nodes if node.label == 'receive')
    assert receive.variable.payload_type.width.bits == 8
    receive_stage = next(stage for stage in pipeline.stages if stage.operations[0].label == 'receive')
    assert receive_stage.variable.payload_type.width.bits == 8
    send_stage = next(stage for stage in microarchitecture.stages if stage.body_operations[0].label == 'send')
    assert send_stage.endpoint.payload_type.width.bits == 8
    assert all(signal.width.bits == 8 for signal in payload_signals(graph))


def test_storage_payload_width_is_bound_from_its_stage_context():
    _, _, _, _, _, graph = bound('module m; logic [7:0] x, increment; always x = x + increment; endmodule')
    stage = next(stage for stage in graph.body_stages if stage.operations[0].label == 'assign')
    signal_map = {signal.id: signal for signal in graph.signals}
    storage_data = graph.bindings_for(stage.storage.id, 'data_in')[0]
    assert signal_map[storage_data.signal_id].width.bits == 8


def test_conditional_receive_wrapper_payload_width_is_bound():
    _, _, _, _, _, graph = bound('''module m(interface C); logic c; logic [7:0] x; always
if (c) C.Receive(x); endmodule''')
    wrapper = graph.wrappers[0]
    signal_map = {signal.id: signal for signal in graph.signals}
    assert signal_map[graph.bindings_for(wrapper.id, 'external_data')[0].signal_id].width.bits == 8
    assert signal_map[graph.bindings_for(wrapper.id, 'body_data')[0].signal_id].width.bits == 8


def test_conditional_send_wrapper_payload_width_is_bound():
    _, _, _, _, _, graph = bound('''module m(interface C); logic c; logic [7:0] x, y; always
if (c) C.Send(x + y); endmodule''')
    wrapper = graph.wrappers[0]
    signal_map = {signal.id: signal for signal in graph.signals}
    assert signal_map[graph.bindings_for(wrapper.id, 'body_data')[0].signal_id].width.bits == 8
    assert signal_map[graph.bindings_for(wrapper.id, 'external_data')[0].signal_id].width.bits == 8


def test_handshake_and_enable_signals_are_one_bit():
    _, _, _, _, _, graph = bound('''module m(interface C); logic c, x; always
if (c) C.Send(x); endmodule''')
    assert all(signal.width == ONE_BIT for signal in graph.signals if signal.semantic_kind.value != 'payload')


def test_selected_endpoints_preserve_width_and_identity():
    behavioral, _, _, _, _, graph = bound('''module m(interface A[2]); logic c; logic [3:0] x; always begin
if (c) A[0].Send(x); if (c) A[1].Send(x); end endmodule''')
    first, second = behavioral.body.items[0].then_branch.channel, behavioral.body.items[1].then_branch.channel
    assert first != second
    assert first.payload_type.width.bits == second.payload_type.width.bits == 4
    assert {wrapper.endpoint.selectors[0].operands[0].value for wrapper in graph.wrappers} == {'0', '1'}


def test_shadowed_variable_widths_remain_distinct():
    behavioral = lower('''module m(interface A, B); logic [7:0] x; always begin
begin logic [3:0] x; A.Receive(x); end B.Send(x); end endmodule''')
    inner, outer = behavioral.body.items[0].items[0].target, behavioral.body.items[1].value.variable
    assert inner != outer
    assert inner.payload_type.width.bits == 4
    assert outer.payload_type.width.bits == 8


def test_unknown_required_payload_width_fails_closed_at_template_binding():
    behavioral = lower('module m(interface C); always C.Send(5); endmodule')
    microarchitecture = select_microarchitecture(
        synthesize_pipeline(analyze_dependencies(normalize_communication(behavioral)))
    )
    with pytest.raises(TemplateBindingError, match='cannot establish payload width'):
        bind_templates(microarchitecture)


def test_equal_concrete_channel_and_variable_widths_are_accepted():
    _, _, _, _, _, graph = bound('''module m(Channel #(8) C); logic [7:0] x; always
C.Receive(x); endmodule''')
    assert payload_signals(graph)[0].width.bits == 8


@pytest.mark.parametrize('source, operation', [
    ('module m(Channel #(8) C); logic [15:0] x; always C.Receive(x); endmodule', 'Receive'),
    ('module m(Channel #(16) C); logic [7:0] x; always C.Send(x); endmodule', 'Send'),
])
def test_incompatible_concrete_payload_widths_are_rejected(source, operation):
    with pytest.raises(BehavioralIRError, match=f'incompatible payload widths for {operation}'):
        lower(source)


def test_same_parameter_owned_symbolic_widths_are_accepted_and_preserved():
    _, _, _, _, _, graph = bound('''module m #(parameter int W = 8) (Channel #(W) C); logic [W-1:0] x; always
C.Send(x); endmodule''')
    payload = next(signal for signal in payload_signals(graph) if signal.endpoint is not None)
    assert payload.width.symbolic == 'W'
    assert payload.width.parameters[0].name == 'W'
    assert payload.width.parameters[0].module == 'm'


def test_different_symbolic_parameter_identities_are_rejected():
    source = '''module m #(parameter int W = 8, parameter int V = 8) (Channel #(W) C); logic [V-1:0] x; always
C.Send(x); endmodule'''
    with pytest.raises(BehavioralIRError, match='incompatible payload widths for Send'):
        lower(source)


def test_undeclared_symbolic_width_parameter_is_rejected():
    with pytest.raises(Exception, match='undeclared symbolic width parameter: W'):
        parse_text('module m; logic [W-1:0] x; always begin end endmodule')


def test_incomplete_payload_width_object_is_rejected():
    with pytest.raises(ValueError, match='payload width requires exactly one'):
        PayloadWidth()


def test_conditional_wrapper_payload_width_mismatch_is_rejected_without_conversion():
    source = '''module m(Channel #(8) C); logic c; logic [15:0] x; always
if (c) C.Send(x); endmodule'''
    with pytest.raises(BehavioralIRError, match='incompatible payload widths for Send'):
        lower(source)


def test_storage_payload_width_mismatch_is_rejected_without_conversion():
    behavioral = lower('module m; logic [7:0] x, increment; always x = x + increment; endmodule')
    microarchitecture = select_microarchitecture(
        synthesize_pipeline(analyze_dependencies(normalize_communication(behavioral)))
    )
    wrong_endpoint = ChannelEndpoint('synthetic', payload_type=PayloadType('logic', PayloadWidth(bits=16)))
    malformed_stage = replace(microarchitecture.stages[0], endpoint=wrong_endpoint)
    malformed = replace(microarchitecture, stages=(malformed_stage,))
    with pytest.raises(TemplateBindingError, match='incompatible payload widths for payload connection'):
        bind_templates(malformed)
