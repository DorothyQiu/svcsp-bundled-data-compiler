from dataclasses import asdict

from svcsp_compiler import (
    ControllerKind, DependencyKind, Enable, StageKind, analyze_dependencies, lower_behavioral,
    normalize_communication, parse_text, select_microarchitecture, synthesize_pipeline,
)


def implement(source):
    behavioral = lower_behavioral(parse_text(source, 'microarchitecture.sv'))
    pipeline = synthesize_pipeline(analyze_dependencies(normalize_communication(behavioral)))
    return select_microarchitecture(pipeline)


def stage(graph, label):
    return next(item for item in graph.stages if item.body_operations[0].label == label)


def test_linear_operation_stage_selects_linear_controller():
    graph = implement('module m(interface A); logic x; always A.Receive(x); endmodule')
    assert stage(graph, 'receive').controller is ControllerKind.LINEAR


def test_join_stage_selects_join_controller():
    graph = implement('''module m(interface A, B); logic a, b; always fork
A.Receive(a); B.Receive(b); join endmodule''')
    join = next(item for item in graph.stages if item.body_operations[0].kind.name == 'PARALLEL_JOIN')
    assert join.controller is ControllerKind.JOIN


def test_conditional_receive_selects_conditional_recv_controller():
    graph = implement('''module m(interface A); logic c, x; always
if (c) A.Receive(x); endmodule''')
    stage_with_wrapper = next(item for item in graph.stages if item.wrapper_attachments)
    wrapper = graph.wrappers[0]
    assert stage_with_wrapper.controller is ControllerKind.LINEAR
    assert wrapper.controller is ControllerKind.CONDITIONAL_RECV
    assert wrapper.attached_to == stage_with_wrapper.id


def test_conditional_send_selects_conditional_send_controller():
    graph = implement('''module m(interface A); logic c, x; always
if (c) A.Send(x); endmodule''')
    stage_with_wrapper = next(item for item in graph.stages if item.wrapper_attachments)
    wrapper = graph.wrappers[0]
    assert stage_with_wrapper.controller is ControllerKind.LINEAR
    assert wrapper.controller is ControllerKind.CONDITIONAL_SEND
    assert wrapper.attached_to == stage_with_wrapper.id


def test_unconditional_communication_remains_linear():
    graph = implement('''module m(interface A, B); logic x; always begin
A.Receive(x); B.Send(x); end endmodule''')
    assert [stage(graph, label).controller for label in ('receive', 'send')] == [
        ControllerKind.LINEAR, ControllerKind.LINEAR,
    ]


def test_parallel_join_topology_is_preserved():
    graph = implement('''module m(interface A, B, C); logic a, b; always begin
fork A.Receive(a); B.Receive(b); join C.Send(a + b); end endmodule''')
    join = next(item for item in graph.stages if item.controller is ControllerKind.JOIN)
    send = stage(graph, 'send')
    assert any(edge.kind is DependencyKind.PARALLEL_JOIN and edge.target_stage == join.id
               for edge in graph.dependencies)
    assert any(edge.kind is DependencyKind.SEQUENCE and edge.source_stage == join.id and edge.target_stage == send.id
               for edge in graph.dependencies)


def test_selected_endpoint_is_preserved_by_conditional_controller():
    graph = implement('''module m(interface A[2]); logic c, x; always
if (c) A[1].Receive(x); endmodule''')
    wrapper = graph.wrappers[0]
    assert wrapper.endpoint.name == 'A'
    assert wrapper.endpoint.selectors[0].operands[0].value == '1'


def test_shadowed_variable_identity_is_preserved_by_microarchitecture_stage():
    graph = implement('''module m(interface A, B); logic x; always begin
begin logic x; A.Receive(x); end B.Send(x); end endmodule''')
    receive, send = stage(graph, 'receive'), stage(graph, 'send')
    assert receive.variable != send.body_operations[0].operation.value.variable


def test_datapath_stage_storage_and_matched_delay_remain_symbolic():
    micro_stage = stage(implement('module m; logic x; always x = x + 1; endmodule'), 'assign')
    assert micro_stage.storage.required is True
    assert micro_stage.storage.implementation is None
    assert micro_stage.combinational_logic[0].form == 'binary'
    assert micro_stage.matched_delay.symbol == f'matched_delay_{micro_stage.id}'
    assert micro_stage.matched_delay.value is None


def test_unconditional_send_with_nontrivial_value_has_body_matched_delay():
    micro_stage = stage(implement('''module m(interface C); logic a, b; always
C.Send(a + b); endmodule'''), 'send')
    assert micro_stage.combinational_logic[0].form == 'binary'
    assert micro_stage.matched_delay is not None


def test_unconditional_send_of_variable_has_no_combinational_matched_delay():
    micro_stage = stage(implement('module m(interface C); logic x; always C.Send(x); endmodule'), 'send')
    assert micro_stage.combinational_logic == ()
    assert micro_stage.matched_delay is None


def test_unconditional_send_of_literal_has_no_combinational_matched_delay():
    micro_stage = stage(implement('module m(interface C); always C.Send(5); endmodule'), 'send')
    assert micro_stage.combinational_logic == ()
    assert micro_stage.matched_delay is None


def test_controller_selection_is_deterministic():
    source = 'module m(interface A); logic x; always A.Send(x); endmodule'
    first, second = implement(source), implement(source)
    assert [(stage.id, stage.controller) for stage in first.stages] == [
        (stage.id, stage.controller) for stage in second.stages
    ]


def test_microarchitecture_selection_does_not_mutate_pipeline_graph():
    behavioral = lower_behavioral(parse_text('module m(interface A); logic x; always A.Send(x); endmodule'))
    pipeline = synthesize_pipeline(analyze_dependencies(normalize_communication(behavioral)))
    snapshot = asdict(pipeline)
    select_microarchitecture(pipeline)
    assert asdict(pipeline) == snapshot


def test_join_has_no_datapath_storage_or_matched_delay():
    graph = implement('''module m(interface A, B); logic a, b; always fork
A.Receive(a); B.Receive(b); join endmodule''')
    join = next(item for item in graph.stages if item.controller is ControllerKind.JOIN)
    assert join.storage.required is False
    assert join.matched_delay is None


def test_wrapper_anchor_has_no_datapath_storage_or_matched_delay():
    graph = implement('''module m(interface A); logic c, x; always
if (c) A.Receive(x); endmodule''')
    anchor = next(item for item in graph.stages if item.wrapper_attachments)
    assert anchor.body_operations[0].label == 'skip'
    assert anchor.storage.required is False
    assert anchor.matched_delay is None


def test_conditional_send_value_computation_belongs_to_body_stage():
    graph = implement('''module m(interface C); logic c, a, b; always
if (c) C.Send(a + b); endmodule''')
    body = next(item for item in graph.stages if item.wrapper_attachments)
    wrapper = graph.wrappers[0]
    assert body.controller is ControllerKind.LINEAR
    assert body.combinational_logic[0].form == 'binary'
    assert body.matched_delay is not None
    assert wrapper.controller is ControllerKind.CONDITIONAL_SEND
    assert wrapper.matched_delay is None


def test_conditional_receive_has_no_combinational_matched_delay():
    graph = implement('''module m(interface C); logic c, x; always
if (c) C.Receive(x); endmodule''')
    body = next(item for item in graph.stages if item.wrapper_attachments)
    wrapper = graph.wrappers[0]
    assert body.combinational_logic == ()
    assert body.matched_delay is None
    assert wrapper.matched_delay is None


def test_wrapper_enable_has_enable_identity_type():
    graph = implement('''module m(interface C); logic c, x; always
if (c) C.Send(x); endmodule''')
    assert isinstance(graph.wrappers[0].enable, Enable)


def test_wrapper_preserves_identity_and_source_location_separately_from_body():
    graph = implement('''module m(interface A); logic c, x; always begin
  if (c)
    A.Receive(x);
end endmodule''')
    wrapper = graph.wrappers[0]
    body = graph.stage(wrapper.attached_to)
    assert wrapper.id == body.wrapper_attachments[0]
    assert wrapper.enable.name == 'enable_0'
    assert (wrapper.location.line, wrapper.location.column) == (3, 5)
    assert body.controller is ControllerKind.LINEAR
