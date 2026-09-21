from dataclasses import asdict

from svcsp_compiler import (
    DependencyKind, StageKind, analyze_dependencies, lower_behavioral,
    normalize_communication, parse_text, synthesize_pipeline,
)


def synthesize(source):
    behavioral = lower_behavioral(parse_text(source, 'pipeline.sv'))
    dependencies = analyze_dependencies(normalize_communication(behavioral))
    return synthesize_pipeline(dependencies)


def stage(graph, label):
    return next(item for item in graph.stages if item.operations[0].label == label)


def stage_edges(graph, kind):
    return {(edge.source_stage, edge.target_stage) for edge in graph.dependencies
            if edge.kind is kind and edge.source_stage and edge.target_stage}


def test_linear_pipeline_has_one_stage_per_operation():
    graph = synthesize('''module m(interface A, B); logic x; always begin
A.Receive(x); B.Send(x); end endmodule''')
    receive, send = stage(graph, 'receive'), stage(graph, 'send')
    assert all(len(item.operations) == 1 for item in graph.stages)
    assert (receive.id, send.id) in stage_edges(graph, DependencyKind.SEQUENCE)


def test_receive_assign_send_preserves_data_dependencies():
    graph = synthesize('''module m(interface A, B); logic x, increment; always begin
A.Receive(x); x = x + increment; B.Send(x); end endmodule''')
    receive, assign, send = stage(graph, 'receive'), stage(graph, 'assign'), stage(graph, 'send')
    data = stage_edges(graph, DependencyKind.DATA)
    assert (receive.id, assign.id) in data
    assert (assign.id, send.id) in data


def test_fork_join_branches_are_independent_and_have_join_stage():
    graph = synthesize('''module m(interface A, B); logic a, b; always fork
A.Receive(a); B.Receive(b); join endmodule''')
    receives = [item for item in graph.stages if item.operations[0].label == 'receive']
    join = next(item for item in graph.stages if item.kind is StageKind.JOIN)
    sequence = stage_edges(graph, DependencyKind.SEQUENCE)
    assert (receives[0].id, receives[1].id) not in sequence
    assert (receives[1].id, receives[0].id) not in sequence
    assert all((receive.id, join.id) in stage_edges(graph, DependencyKind.PARALLEL_JOIN) for receive in receives)


def test_join_converges_before_following_stage():
    graph = synthesize('''module m(interface A, B, C); logic a, b; always begin
fork A.Receive(a); B.Receive(b); join C.Send(a + b); end endmodule''')
    join = next(item for item in graph.stages if item.kind is StageKind.JOIN)
    send = stage(graph, 'send')
    assert (join.id, send.id) in stage_edges(graph, DependencyKind.SEQUENCE)


def test_if_else_control_constraints_remain_metadata():
    graph = synthesize('''module m(interface A, B); logic c, x; always
if (c) A.Send(x); else B.Send(x); endmodule''')
    controls = [edge for edge in graph.dependencies if edge.kind is DependencyKind.CONTROL]
    assert controls
    assert any(edge.source_stage is None or edge.target_stage is None for edge in controls)


def test_conditional_receive_wrapper_attaches_to_body_stage():
    graph = synthesize('''module m(interface A); logic c, x; always
if (c) A.Receive(x); endmodule''')
    attachment = graph.attachments[0]
    assert attachment.direction == 'into_body'
    assert graph.stage_for(attachment.wrapper_node) is None
    assert stage(graph, 'skip').id == attachment.body_stage


def test_conditional_send_wrapper_attaches_to_body_stage():
    graph = synthesize('''module m(interface A); logic c, x; always
if (c) A.Send(x); endmodule''')
    attachment = graph.attachments[0]
    assert attachment.direction == 'from_body'
    assert stage(graph, 'skip').id == attachment.body_stage


def test_selected_channel_endpoints_remain_distinct_in_attachments():
    graph = synthesize('''module m(interface A[2]); logic c, x; always begin
if (c) A[0].Send(x); if (c) A[1].Receive(x); end endmodule''')
    first, second = graph.attachments
    assert first.endpoint != second.endpoint
    assert [item.endpoint.selectors[0].operands[0].value for item in graph.attachments] == ['0', '1']


def test_shadowed_variable_identity_is_preserved_by_stages():
    graph = synthesize('''module m(interface A, B); logic x; always begin
begin logic x; A.Receive(x); end B.Send(x); end endmodule''')
    receive, send = stage(graph, 'receive'), stage(graph, 'send')
    assert receive.variable != send.operations[0].operation.value.variable


def test_stage_identity_and_order_are_deterministic():
    source = '''module m(interface A, B); logic x; always begin A.Receive(x); B.Send(x); end endmodule'''
    first, second = synthesize(source), synthesize(source)
    assert [item.id for item in first.stages] == [item.id for item in second.stages]
    assert [item.operations[0].label for item in first.stages] == ['receive', 'send']


def test_pipeline_synthesis_does_not_mutate_dependency_graph():
    behavioral = lower_behavioral(parse_text('module m(interface A); logic x; always A.Send(x); endmodule'))
    dependencies = analyze_dependencies(normalize_communication(behavioral))
    snapshot = asdict(dependencies)
    synthesize_pipeline(dependencies)
    assert asdict(dependencies) == snapshot
