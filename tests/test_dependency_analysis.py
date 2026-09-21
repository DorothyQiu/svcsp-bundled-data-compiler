from dataclasses import asdict, replace

import pytest

from svcsp_compiler import (
    DependencyAnalysisError,
    DependencyKind, NodeKind, SourceLocation, analyze_dependencies, lower_behavioral,
    normalize_communication, parse_text,
)


def analyze(source):
    behavioral = lower_behavioral(parse_text(source, 'dependency.sv'))
    return analyze_dependencies(normalize_communication(behavioral))


def node(graph, label):
    return next(item for item in graph.nodes if item.label == label)


def edges(graph, kind):
    return {(edge.source, edge.target) for edge in graph.edges if edge.kind is kind}


def test_linear_sequence_has_sequence_dependencies():
    graph = analyze('''module m(interface A, B); logic x; always begin
A.Receive(x); B.Send(x); end endmodule''')
    receive, send = [item for item in graph.nodes if item.label in {'receive', 'send'}]
    assert (receive.id, send.id) in edges(graph, DependencyKind.SEQUENCE)


def test_receive_assign_send_has_data_flow():
    graph = analyze('''module m(interface A, B); logic x, increment; always begin
A.Receive(x); x = x + increment; B.Send(x); end endmodule''')
    receive, assign, send = [item for item in graph.nodes if item.label in {'receive', 'assign', 'send'}]
    data = edges(graph, DependencyKind.DATA)
    assert (receive.id, assign.id) in data
    assert (assign.id, send.id) in data


def test_parallel_branches_do_not_gain_false_sequence_dependencies():
    graph = analyze('''module m(interface A, B); logic a, b; always fork
A.Receive(a); B.Receive(b); join endmodule''')
    receive_a, receive_b = [item for item in graph.nodes if item.label == 'receive']
    sequence = edges(graph, DependencyKind.SEQUENCE)
    assert (receive_a.id, receive_b.id) not in sequence
    assert (receive_b.id, receive_a.id) not in sequence


def test_fork_join_converges_before_later_operation():
    graph = analyze('''module m(interface A, B, C); logic a, b; always begin
fork A.Receive(a); B.Receive(b); join C.Send(a + b); end endmodule''')
    join = node(graph, 'parallel_join')
    send = node(graph, 'send')
    receives = [item for item in graph.nodes if item.label == 'receive']
    joined = edges(graph, DependencyKind.PARALLEL_JOIN)
    assert all((receive.id, join.id) in joined for receive in receives)
    assert (join.id, send.id) in edges(graph, DependencyKind.SEQUENCE)


def test_if_else_produces_control_dependencies():
    graph = analyze('''module m(interface A, B); logic c, x; always
if (c) A.Send(x); else B.Send(x); endmodule''')
    control = node(graph, 'if')
    sends = [item for item in graph.nodes if item.label == 'normalized_send']
    assert all((control.id, send.id) in edges(graph, DependencyKind.CONTROL) for send in sends)


def test_nested_control_dependencies_are_preserved():
    graph = analyze('''module m(interface A); logic a, b, x; always
if (a) if (b) A.Send(x); endmodule''')
    controls = [item for item in graph.nodes if item.kind is NodeKind.CONTROL]
    wrapper = node(graph, 'normalized_send')
    assert len(controls) == 2
    assert any((control.id, wrapper.id) in edges(graph, DependencyKind.CONTROL) for control in controls)


def test_conditional_receive_has_enable_wrapper_and_body_dependencies():
    normalized = normalize_communication(lower_behavioral(parse_text('''module m(interface A); logic c, x; always
if (c) A.Receive(x); endmodule''', 'identity.sv')))
    # The wrapper location deliberately differs from the BODY site.  The
    # identity link, not a source-location lookup, must drive Phase 4.
    wrapper_ir = replace(normalized.wrappers[0], location=SourceLocation('moved.sv', 99, 1))
    normalized = replace(normalized, wrappers=(wrapper_ir,))
    graph = analyze_dependencies(normalized)
    enable = node(graph, 'enable')
    wrapper = node(graph, 'normalized_receive')
    skip = node(graph, 'skip')
    assert (enable.id, wrapper.id) in edges(graph, DependencyKind.CONTROL)
    assert (wrapper.id, skip.id) in edges(graph, DependencyKind.COMMUNICATION)
    assert skip.operation is normalized.communication_sites[0]
    assert wrapper.operation is wrapper_ir
    assert wrapper.operation.site is skip.operation
    assert wrapper.operation.body_channel is normalized.body_channels[0]
    assert normalized.body_communications[0].channel is wrapper.operation.body_channel
    assert normalized.body_communications[0].disabled_token is wrapper.operation.disabled_token


def test_conditional_send_has_body_wrapper_and_enable_dependencies():
    normalized = normalize_communication(lower_behavioral(parse_text('''module m(interface A); logic c, x; always
if (c) A.Send(x); endmodule''', 'send_identity.sv')))
    graph = analyze_dependencies(normalized)
    enable = node(graph, 'enable')
    wrapper = node(graph, 'normalized_send')
    skip = node(graph, 'skip')
    assert (enable.id, wrapper.id) in edges(graph, DependencyKind.CONTROL)
    assert (skip.id, wrapper.id) in edges(graph, DependencyKind.COMMUNICATION)
    assert skip.operation is normalized.communication_sites[0]
    assert wrapper.operation.site is skip.operation
    assert normalized.body_communications[0].channel is wrapper.operation.body_channel
    assert normalized.body_communications[0].payload_valid_when is wrapper.operation.enable


def test_selected_endpoints_remain_distinct_in_graph_nodes():
    graph = analyze('''module m(interface A[2]); logic c, x; always begin
if (c) A[0].Send(x); if (c) A[1].Receive(x); end endmodule''')
    wrappers = [item for item in graph.nodes if item.kind is NodeKind.WRAPPER]
    assert wrappers[0].endpoint != wrappers[1].endpoint
    assert [item.endpoint.selectors[0].operands[0].value for item in wrappers] == ['0', '1']


def test_shadowed_variables_remain_distinct_in_data_dependencies():
    graph = analyze('''module m(interface A, B); logic x; always begin
begin logic x; A.Receive(x); end B.Send(x); end endmodule''')
    receive = node(graph, 'receive')
    send = node(graph, 'send')
    assert receive.variable != send.operation.value.variable
    assert (receive.id, send.id) not in edges(graph, DependencyKind.DATA)


def test_conditional_receive_data_is_valid_in_its_enabled_branch():
    graph = analyze('''module m(interface A, B); logic c, x; always
if (c) begin A.Receive(x); B.Send(x); end endmodule''')
    assert any(item.label == 'normalized_receive' for item in graph.nodes)
    assert any(item.label == 'normalized_send' for item in graph.nodes)


def test_conditional_receive_data_is_valid_under_a_stronger_guard():
    graph = analyze('''module m(interface A, B); logic c, d, x; always
if (c) begin A.Receive(x); if (d) B.Send(x); end endmodule''')
    assert any(item.label == 'normalized_send' for item in graph.nodes)


@pytest.mark.parametrize('source', [
    '''module m(interface A, B); logic c, x; always begin
if (c) A.Receive(x); B.Send(x); end endmodule''',
    '''module m(interface A, B); logic c, x; always
if (c) A.Receive(x); else B.Send(x); endmodule''',
    '''module m(interface A, B); logic c, d, x; always begin
if (c) A.Receive(x); if (d) B.Send(x); end endmodule''',
])
def test_conditional_receive_data_requires_a_proven_validity_guard(source):
    with pytest.raises(DependencyAnalysisError, match='does not prove'):
        analyze(source)


def test_graph_preserves_normalized_wrapper_and_enable_locations():
    graph = analyze('''module m(interface A); logic c, x; always begin
  if (c)
    A.Receive(x);
end endmodule''')
    wrapper = node(graph, 'normalized_receive')
    enable = node(graph, 'enable')
    assert (wrapper.location.line, wrapper.location.column) == (3, 5)
    assert (enable.location.line, enable.location.column) == (3, 5)


def test_dependency_analysis_does_not_mutate_normalized_ir():
    behavioral = lower_behavioral(parse_text('''module m(interface A); logic c, x; always
if (c) A.Send(x); endmodule'''))
    normalized = normalize_communication(behavioral)
    snapshot = asdict(normalized)
    analyze_dependencies(normalized)
    assert asdict(normalized) == snapshot
