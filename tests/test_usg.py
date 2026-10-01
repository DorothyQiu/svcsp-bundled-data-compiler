from dataclasses import fields

import pytest
from pyslang.ast import CallExpression, GenerateBlockArraySymbol, ParameterSymbol, ProceduralBlockSymbol

from svcsp_compiler.semantic_frontend import parse_text
from svcsp_compiler.analysis import AnalysisFacts, ValueOrigin, analyze_usg
from svcsp_compiler.usg import (
    AssignNode,
    ControlEdge,
    DataEdge,
    PredicateNode,
    ReceiveNode,
    SendNode,
    USGBuilderError,
    UnifiedSemanticGraph,
    build_process_usgs,
    build_straight_line_usg,
)


SOURCE = """\
module m(input logic a);
  logic x;
  always begin
    x = a;
    if (x) x = a;
  end
endmodule
"""

_SEMANTIC_CONTEXTS = []


def _semantic_objects():
    context = parse_text(SOURCE, "usg.sv")
    _SEMANTIC_CONTEXTS.append(context)
    module = context.root.topInstances[0].body
    visited = []
    module.visit(visited.append)
    procedure = next(item for item in visited if isinstance(item, ProceduralBlockSymbol))
    assignment, conditional = procedure.body.body.list
    return assignment.expr, conditional.conditions[0].expr, conditional


def test_nodes_retain_native_semantic_objects_and_edges_connect_them() -> None:
    assignment, predicate_expression, conditional = _semantic_objects()
    receive = ReceiveNode(assignment)
    assign = AssignNode(assignment)
    predicate = PredicateNode(predicate_expression)
    send = SendNode(conditional)
    graph = UnifiedSemanticGraph()

    for node in (receive, assign, predicate, send):
        graph.add_node(node)
    data = graph.add_data_edge(receive, assign)
    control = graph.add_control_edge(predicate, send, True)

    assert receive.semantic_object is assignment
    assert assign.semantic_object is assignment
    assert predicate.semantic_object is predicate_expression
    assert send.semantic_object is conditional
    assert isinstance(data, DataEdge)
    assert isinstance(control, ControlEdge)
    assert data.source is receive and data.target is assign
    assert control.source is predicate and control.target is send
    assert control.polarity is True
    assert graph.data_edges == (data,)
    assert graph.control_edges == (control,)


def test_node_insertion_order_is_deterministic() -> None:
    assignment, predicate_expression, conditional = _semantic_objects()
    nodes = (
        PredicateNode(predicate_expression),
        SendNode(conditional),
        ReceiveNode(assignment),
        AssignNode(assignment),
    )
    graph = UnifiedSemanticGraph()
    for node in nodes:
        graph.add_node(node)

    assert graph.nodes == nodes


@pytest.mark.parametrize("edge_type", (DataEdge, ControlEdge))
def test_edges_reject_nodes_outside_the_graph(edge_type) -> None:
    assignment, _, _ = _semantic_objects()
    inside = ReceiveNode(assignment)
    outside = AssignNode(assignment)
    graph = UnifiedSemanticGraph()
    graph.add_node(inside)

    with pytest.raises(ValueError, match="nodes in this graph"):
        edge = (
            edge_type(inside, outside)
            if edge_type is DataEdge
            else edge_type(inside, outside, True)
        )
        graph.add_edge(edge)


def test_model_contains_only_native_references_and_semantic_edge_endpoints() -> None:
    for node_type in (ReceiveNode, SendNode, AssignNode, PredicateNode):
        assert [field.name for field in fields(node_type)] == ["semantic_object"]
    for edge_type in (DataEdge, ControlEdge):
        expected = ["source", "target"]
        if edge_type is ControlEdge:
            expected.append("polarity")
        assert [field.name for field in fields(edge_type)] == expected

    graph = UnifiedSemanticGraph()
    assert not any(
        hasattr(graph, name)
        for name in ("stages", "registers", "handshakes", "templates", "enables", "communication_order")
    )


STRAIGHT_LINE_SOURCE = """\
interface Channel;
  task Receive(output logic [7:0] data); endtask
  task Send(input logic [7:0] data); endtask
endinterface

module straight(Channel A, B);
  logic [7:0] a, b, t;
  always begin
    A.Receive(a);
    t = a + b;
    B.Send(t);
  end
endmodule
"""


def _block(source: str):
    context = parse_text(source, "straight_line.sv")
    _SEMANTIC_CONTEXTS.append(context)
    module = context.root.topInstances[0].body
    visited = []
    module.visit(visited.append)
    return next(item for item in visited if isinstance(item, ProceduralBlockSymbol)).body


def test_straight_line_builder_uses_native_objects_and_data_dependencies() -> None:
    block = _block(STRAIGHT_LINE_SOURCE)
    statements = block.body.list

    graph = build_straight_line_usg(block)

    receive, assign, send = graph.nodes
    assert isinstance(receive, ReceiveNode)
    assert isinstance(assign, AssignNode)
    assert isinstance(send, SendNode)
    assert receive.semantic_object is statements[0].expr
    assert assign.semantic_object is statements[1].expr
    assert send.semantic_object is statements[2].expr
    assert graph.data_edges == (
        DataEdge(receive, assign),
        DataEdge(assign, send),
    )
    assert graph.control_edges == ()


def test_builder_preserves_receive_occurrence_order_without_data_dependency() -> None:
    block = _block("""\
interface Channel;
  task Receive(output logic [7:0] data); endtask
endinterface

module receive_order(Channel A, B);
  logic [7:0] a, b;
  always begin
    A.Receive(a);
    B.Receive(b);
  end
endmodule
""")
    first_statement, second_statement = block.body.list

    graph = build_straight_line_usg(block)
    first_receive, second_receive = graph.nodes

    assert isinstance(first_receive, ReceiveNode)
    assert isinstance(second_receive, ReceiveNode)
    assert first_receive.semantic_object is first_statement.expr
    assert second_receive.semantic_object is second_statement.expr
    assert str(first_receive.semantic_object.syntax).strip() == "A.Receive(a)"
    assert str(second_receive.semantic_object.syntax).strip() == "B.Receive(b)"
    assert graph.data_edges == ()


def test_builder_processes_self_assignment_uses_before_its_definition() -> None:
    block = _block("""\
interface Channel;
  task Receive(output logic [7:0] data); endtask
  task Send(input logic [7:0] data); endtask
endinterface

module self_assign(Channel A, B, input logic [7:0] a);
  logic [7:0] x;
  always begin
    A.Receive(x);
    x = x + a;
    B.Send(x);
  end
endmodule
""")

    graph = build_straight_line_usg(block)
    receive, assign, send = graph.nodes

    assert graph.data_edges == (
        DataEdge(receive, assign),
        DataEdge(assign, send),
    )
    assert not any(edge.source is assign and edge.target is assign for edge in graph.data_edges)

    facts = analyze_usg(graph)
    self_use, input_use = facts.uses_of(assign)
    assert facts.producers_of(assign, self_use) == (receive,)
    assert facts.producers_of(assign, input_use) == ()


def test_builder_builds_simple_if_assignment() -> None:
    block = _block("""\
module unsupported(input logic a);
  logic x;
  always begin
    if (a) x = a;
  end
endmodule
""")

    graph = build_straight_line_usg(block)

    assert isinstance(graph.nodes[0], PredicateNode)
    assert isinstance(graph.nodes[1], AssignNode)
    assert graph.control_edges == (ControlEdge(graph.nodes[0], graph.nodes[1], True),)


CONDITIONAL_SOURCE_PREFIX = """\
interface Channel;
  task Receive(output logic [7:0] data); endtask
  task Send(input logic [7:0] data); endtask
endinterface
"""


def test_builder_builds_simple_if_with_predicate_data_and_true_control() -> None:
    block = _block(CONDITIONAL_SOURCE_PREFIX + """
module simple_if(Channel A, B);
  logic [7:0] a;
  always begin
    A.Receive(a);
    if (a[0]) B.Send(a);
  end
endmodule
""")
    receive_statement, conditional = block.body.list

    graph = build_straight_line_usg(block)
    receive, predicate, send = graph.nodes

    assert isinstance(receive, ReceiveNode)
    assert isinstance(predicate, PredicateNode)
    assert isinstance(send, SendNode)
    assert predicate.semantic_object is conditional.conditions[0].expr
    assert send.semantic_object is conditional.ifTrue.expr
    assert graph.data_edges == (
        DataEdge(receive, predicate),
        DataEdge(receive, send),
    )
    assert graph.control_edges == (ControlEdge(predicate, send, True),)
    assert receive.semantic_object is receive_statement.expr


def test_builder_builds_if_else_in_expanded_occurrence_order() -> None:
    block = _block(CONDITIONAL_SOURCE_PREFIX + """
module if_else(Channel A, B, C);
  logic [7:0] a;
  always begin
    A.Receive(a);
    if (a[0]) begin
      B.Send(a);
    end
    else begin
      C.Send(a);
    end
  end
endmodule
""")

    graph = build_straight_line_usg(block)
    receive, predicate, true_send, false_send = graph.nodes

    assert tuple(type(node) for node in graph.nodes) == (
        ReceiveNode,
        PredicateNode,
        SendNode,
        SendNode,
    )
    assert graph.data_edges == (
        DataEdge(receive, predicate),
        DataEdge(receive, true_send),
        DataEdge(receive, false_send),
    )
    assert graph.control_edges == (
        ControlEdge(predicate, true_send, True),
        ControlEdge(predicate, false_send, False),
    )


def test_builder_recursively_builds_nested_if_with_parent_controls() -> None:
    block = _block(CONDITIONAL_SOURCE_PREFIX + """
module nested_if(Channel A, B, C, D);
  logic [7:0] a;
  always begin
    A.Receive(a);
    if (a[0]) begin
      if (a[1]) B.Send(a);
      else C.Send(a);
    end
    else D.Send(a);
  end
endmodule
""")

    graph = build_straight_line_usg(block)
    receive, outer_predicate, inner_predicate, inner_true, inner_false, outer_false = graph.nodes

    assert tuple(type(node) for node in graph.nodes) == (
        ReceiveNode,
        PredicateNode,
        PredicateNode,
        SendNode,
        SendNode,
        SendNode,
    )
    assert graph.data_edges == (
        DataEdge(receive, outer_predicate),
        DataEdge(receive, inner_predicate),
        DataEdge(receive, inner_true),
        DataEdge(receive, inner_false),
        DataEdge(receive, outer_false),
    )
    assert graph.control_edges == (
        ControlEdge(outer_predicate, inner_predicate, True),
        ControlEdge(outer_predicate, inner_true, True),
        ControlEdge(inner_predicate, inner_true, True),
        ControlEdge(outer_predicate, inner_false, True),
        ControlEdge(inner_predicate, inner_false, False),
        ControlEdge(outer_predicate, outer_false, False),
    )


def test_builder_merges_definitions_from_both_if_else_branches() -> None:
    block = _block(CONDITIONAL_SOURCE_PREFIX + """
module both_branch_definitions(Channel B, input logic select, input logic [7:0] a);
  logic [7:0] x;
  always begin
    if (select) begin
      x = a;
    end
    else begin
      x = a;
    end
    B.Send(x);
  end
endmodule
""")

    graph = build_straight_line_usg(block)
    predicate, true_assign, false_assign, send = graph.nodes

    assert tuple(type(node) for node in graph.nodes) == (
        PredicateNode,
        AssignNode,
        AssignNode,
        SendNode,
    )
    assert graph.data_edges == (
        DataEdge(true_assign, send),
        DataEdge(false_assign, send),
    )
    assert graph.control_edges == (
        ControlEdge(predicate, true_assign, True),
        ControlEdge(predicate, false_assign, False),
    )


def test_builder_merges_true_branch_with_pre_if_definition() -> None:
    block = _block(CONDITIONAL_SOURCE_PREFIX + """
module partial_branch_definition(Channel A, B, input logic select, input logic [7:0] a);
  logic [7:0] x;
  always begin
    A.Receive(x);
    if (select) x = a;
    B.Send(x);
  end
endmodule
""")

    graph = build_straight_line_usg(block)
    receive, predicate, true_assign, send = graph.nodes

    assert tuple(type(node) for node in graph.nodes) == (
        ReceiveNode,
        PredicateNode,
        AssignNode,
        SendNode,
    )
    assert graph.data_edges == (
        DataEdge(receive, send),
        DataEdge(true_assign, send),
    )
    assert graph.control_edges == (ControlEdge(predicate, true_assign, True),)


def test_process_builder_enumerates_elaborated_generate_processes_independently() -> None:
    context = parse_text(CONDITIONAL_SOURCE_PREFIX + """
module generated_processes(Channel channels [0:2], input logic [7:0] data);
  for (genvar g = 0; g < 3; g++) begin : generated_loop
    always begin
      channels[g].Send(data);
    end
  end
endmodule
""", "generated_processes.sv")
    visited = []
    context.root.visit(visited.append)
    generate_array = next(
        item for item in visited if isinstance(item, GenerateBlockArraySymbol)
    )
    expected_processes = []
    expected_calls = []
    for entry in generate_array.entries:
        entry_items = []
        entry.visit(entry_items.append)
        expected_processes.append(
            next(item for item in entry_items if isinstance(item, ProceduralBlockSymbol))
        )
        expected_calls.append(
            next(item for item in entry_items if isinstance(item, CallExpression))
        )

    first = build_process_usgs(context.root)
    second = build_process_usgs(context.root)

    assert len(first) == len(expected_processes) == 3
    assert [entry.arrayIndex for entry in generate_array.entries] == [0, 1, 2]
    assert all(process is expected for (process, _), expected in zip(first, expected_processes))
    assert all(process is expected for (process, _), expected in zip(second, expected_processes))
    assert len({id(process) for process, _ in first}) == 3
    assert len({id(graph) for _, graph in first}) == 3

    for (_, graph), expected_call in zip(first, expected_calls):
        assert len(graph.nodes) == 1
        assert isinstance(graph.nodes[0], SendNode)
        assert graph.nodes[0].semantic_object is expected_call
        assert graph.data_edges == ()
        assert graph.control_edges == ()


def test_analysis_tracks_straight_line_communication_predecessors_read_only() -> None:
    graph = build_straight_line_usg(_block(CONDITIONAL_SOURCE_PREFIX + """
module straight_analysis(Channel A, B, C);
  logic [7:0] a, b;
  always begin
    A.Receive(a);
    B.Receive(b);
    C.Send(a);
  end
endmodule
"""))
    before = (graph.nodes, graph.data_edges, graph.control_edges)

    facts = analyze_usg(graph)
    first, second, third = graph.nodes

    assert [field.name for field in fields(AnalysisFacts)] == [
        "communication_predecessors",
        "incoming_communication_frontiers",
        "definitions",
        "uses",
        "reaching_producers",
        "uses_without_graph_local_producer",
        "value_uses",
        "definition_consumers",
    ]
    assert facts.predecessors_of(first) == ()
    assert facts.predecessors_of(second) == (first,)
    assert facts.predecessors_of(third) == (second,)
    assert facts.incoming_frontier_of(first) == ()
    assert facts.incoming_frontier_of(second) == (first,)
    assert facts.incoming_frontier_of(third) == (second,)
    assert (graph.nodes, graph.data_edges, graph.control_edges) == before


def test_analysis_merges_simple_if_communication_frontiers() -> None:
    graph = build_straight_line_usg(_block(CONDITIONAL_SOURCE_PREFIX + """
module simple_if_analysis(Channel A, B, C);
  logic [7:0] a, b;
  always begin
    A.Receive(a);
    if (a[0]) B.Receive(b);
    C.Send(a);
  end
endmodule
"""))

    facts = analyze_usg(graph)
    receive_a, _, receive_b, send = graph.nodes

    assert facts.predecessors_of(receive_a) == ()
    assert facts.predecessors_of(receive_b) == (receive_a,)
    assert facts.predecessors_of(send) == (receive_a, receive_b)


def test_analysis_merges_if_else_communication_frontiers() -> None:
    graph = build_straight_line_usg(_block(CONDITIONAL_SOURCE_PREFIX + """
module if_else_analysis(Channel A, B, C, D);
  logic [7:0] a, b, c;
  always begin
    A.Receive(a);
    if (a[0]) begin
      B.Receive(b);
    end
    else begin
      C.Receive(c);
    end
    D.Send(a);
  end
endmodule
"""))

    facts = analyze_usg(graph)
    receive_a, _, receive_b, receive_c, send = graph.nodes

    assert facts.predecessors_of(receive_a) == ()
    assert facts.predecessors_of(receive_b) == (receive_a,)
    assert facts.predecessors_of(receive_c) == (receive_a,)
    assert facts.predecessors_of(send) == (receive_b, receive_c)


def test_analysis_merges_nested_conditional_communication_frontiers() -> None:
    graph = build_straight_line_usg(_block(CONDITIONAL_SOURCE_PREFIX + """
module nested_analysis(Channel A, B, C, D, E);
  logic [7:0] a, b, c, d;
  always begin
    A.Receive(a);
    if (a[0]) begin
      if (a[1]) B.Receive(b);
      else C.Receive(c);
    end
    else D.Receive(d);
    E.Send(a);
  end
endmodule
"""))

    facts = analyze_usg(graph)
    receive_a, _, _, receive_b, receive_c, receive_d, send = graph.nodes

    assert facts.predecessors_of(receive_a) == ()
    assert facts.predecessors_of(receive_b) == (receive_a,)
    assert facts.predecessors_of(receive_c) == (receive_a,)
    assert facts.predecessors_of(receive_d) == (receive_a,)
    assert facts.predecessors_of(send) == (receive_b, receive_c, receive_d)


def test_analysis_records_frontiers_for_straight_line_non_communication_nodes() -> None:
    graph = build_straight_line_usg(_block(STRAIGHT_LINE_SOURCE))

    facts = analyze_usg(graph)
    receive, assign, send = graph.nodes

    assert facts.incoming_frontier_of(receive) == ()
    assert facts.incoming_frontier_of(assign) == (receive,)
    assert facts.incoming_frontier_of(send) == (receive,)
    assert facts.predecessors_of(send) == (receive,)


def test_analysis_tracks_straight_line_definitions_uses_and_availability() -> None:
    graph = build_straight_line_usg(_block(STRAIGHT_LINE_SOURCE))
    receive, assign, send = graph.nodes

    facts = analyze_usg(graph)
    receive_definition = facts.definitions_of(receive)[0]
    assign_definition = facts.definitions_of(assign)[0]
    assign_uses = facts.uses_of(assign)
    send_use = facts.uses_of(send)[0]

    assert receive_definition.name == "a"
    assert assign_definition.name == "t"
    assert tuple(variable.name for variable in assign_uses) == ("a", "b")
    assert send_use.name == "t"
    assert facts.producers_of(assign, assign_uses[0]) == (receive,)
    assert facts.producers_of(assign, assign_uses[1]) == ()
    assert facts.producers_of(send, send_use) == (assign,)
    assert facts.uses_without_graph_local_producer == ((assign, assign_uses[1]),)
    assert receive_definition is receive.semantic_object.arguments[0].left.getSymbolReference()
    assert assign_definition is assign.semantic_object.left.getSymbolReference()
    assert facts.consumers_of(receive) == (facts.value_use_of(assign, assign_uses[0]),)
    assert facts.consumers_of(assign) == (facts.value_use_of(send, send_use),)


def test_analysis_merges_conditional_reaching_producers_for_a_later_use() -> None:
    graph = build_straight_line_usg(_block(CONDITIONAL_SOURCE_PREFIX + """
module conditional_availability(Channel B, input logic select, input logic [7:0] a);
  logic [7:0] x;
  always begin
    if (select) x = a;
    else x = a;
    B.Send(x);
  end
endmodule
"""))
    predicate, true_assign, false_assign, send = graph.nodes

    facts = analyze_usg(graph)
    predicate_use = facts.uses_of(predicate)[0]
    true_use = facts.uses_of(true_assign)[0]
    false_use = facts.uses_of(false_assign)[0]
    send_use = facts.uses_of(send)[0]

    assert predicate_use.name == "select"
    assert true_use.name == false_use.name == "a"
    assert facts.producers_of(predicate, predicate_use) == ()
    assert facts.producers_of(true_assign, true_use) == ()
    assert facts.producers_of(false_assign, false_use) == ()
    assert facts.producers_of(send, send_use) == (true_assign, false_assign)
    assert facts.uses_without_graph_local_producer == (
        (predicate, predicate_use),
        (true_assign, true_use),
        (false_assign, false_use),
    )
    assert facts.consumers_of(true_assign) == (facts.value_use_of(send, send_use),)
    assert facts.consumers_of(false_assign) == (facts.value_use_of(send, send_use),)


def test_analysis_classifies_single_statement_module_input_as_port_entry() -> None:
    graph = build_straight_line_usg(_block("""\
module port_entry(input logic [7:0] a);
  logic [7:0] y;
  always y = a;
endmodule
"""))
    (assign,) = graph.nodes

    facts = analyze_usg(graph)
    (input_symbol,) = facts.uses_of(assign)
    value_use = facts.value_use_of(assign, input_symbol)

    assert input_symbol.name == "a"
    assert value_use.symbol is input_symbol
    assert value_use.origin is ValueOrigin.PORT_ENTRY
    assert value_use.reaching_producers == ()


def test_analysis_classifies_local_read_before_definition_as_local_entry() -> None:
    graph = build_straight_line_usg(_block("""\
module local_entry;
  logic [7:0] x, y;
  always begin
    y = x;
  end
endmodule
"""))
    (assign,) = graph.nodes

    facts = analyze_usg(graph)
    (local_symbol,) = facts.uses_of(assign)
    value_use = facts.value_use_of(assign, local_symbol)

    assert local_symbol.name == "x"
    assert value_use.origin is ValueOrigin.LOCAL_ENTRY
    assert value_use.reaching_producers == ()


def test_analysis_classifies_self_assignment_rhs_before_its_new_definition() -> None:
    graph = build_straight_line_usg(_block(CONDITIONAL_SOURCE_PREFIX + """
module self_entry(Channel B, input logic [7:0] a);
  logic [7:0] x;
  always begin
    x = x + a;
    B.Send(x);
  end
endmodule
"""))
    assign, send = graph.nodes

    facts = analyze_usg(graph)
    x_symbol, input_symbol = facts.uses_of(assign)
    (sent_x,) = facts.uses_of(send)

    assert x_symbol.name == "x"
    assert facts.value_use_of(assign, x_symbol).origin is ValueOrigin.LOCAL_ENTRY
    assert facts.value_use_of(assign, x_symbol).reaching_producers == ()
    assert facts.value_use_of(assign, input_symbol).origin is ValueOrigin.PORT_ENTRY
    assert facts.definitions_of(assign) == (x_symbol,)
    assert facts.consumers_of(assign) == (facts.value_use_of(send, sent_x),)


def test_analysis_classifies_parameter_and_localparam_uses() -> None:
    graph = build_straight_line_usg(_block("""\
module parameter_values #(parameter int P = 3) ();
  localparam int LP = 2;
  logic [7:0] y;
  always begin
    y = P + LP;
  end
endmodule
"""))
    (assign,) = graph.nodes

    facts = analyze_usg(graph)
    parameter, localparam = facts.uses_of(assign)

    assert isinstance(parameter, ParameterSymbol)
    assert isinstance(localparam, ParameterSymbol)
    assert (parameter.name, localparam.name) == ("P", "LP")
    assert facts.value_use_of(assign, parameter).origin is ValueOrigin.PARAMETER
    assert facts.value_use_of(assign, localparam).origin is ValueOrigin.PARAMETER
    assert facts.producers_of(assign, parameter) == ()
    assert facts.producers_of(assign, localparam) == ()
    assert facts.uses_without_graph_local_producer == ((assign, parameter), (assign, localparam))


def test_analysis_classifies_receive_defined_value_as_graph_local() -> None:
    graph = build_straight_line_usg(_block(CONDITIONAL_SOURCE_PREFIX + """
module receive_value(Channel A);
  logic [7:0] x, y;
  always begin
    A.Receive(x);
    y = x;
  end
endmodule
"""))
    receive, assign = graph.nodes

    facts = analyze_usg(graph)
    (received_symbol,) = facts.uses_of(assign)
    value_use = facts.value_use_of(assign, received_symbol)

    assert received_symbol is facts.definitions_of(receive)[0]
    assert value_use.origin is ValueOrigin.GRAPH_LOCAL
    assert value_use.reaching_producers == (receive,)
    assert facts.consumers_of(receive) == (value_use,)
    assert facts.consumers_of(assign) == ()


def test_analysis_records_predicate_and_join_frontiers() -> None:
    graph = build_straight_line_usg(_block(CONDITIONAL_SOURCE_PREFIX + """
module join_frontiers(Channel A, B, C, D);
  logic [7:0] a, b, c;
  always begin
    A.Receive(a);
    if (a[0]) begin
      B.Receive(b);
    end
    else begin
      C.Receive(c);
    end
    D.Send(a);
  end
endmodule
"""))

    facts = analyze_usg(graph)
    receive_a, predicate, receive_b, receive_c, send = graph.nodes

    assert facts.incoming_frontier_of(receive_a) == ()
    assert facts.incoming_frontier_of(predicate) == (receive_a,)
    assert facts.incoming_frontier_of(receive_b) == (receive_a,)
    assert facts.incoming_frontier_of(receive_c) == (receive_a,)
    assert facts.incoming_frontier_of(send) == (receive_b, receive_c)
    assert facts.predecessors_of(send) == (receive_b, receive_c)


@pytest.mark.parametrize(
    ("task_name", "direction"),
    (("UnrelatedOutput", "output"), ("UnrelatedInput", "input")),
)
def test_builder_rejects_unrelated_interface_tasks(
    task_name: str, direction: str
) -> None:
    block = _block(
        f"""
        interface Other;
          task {task_name}({direction} logic [7:0] payload);
          endtask
        endinterface

        module top(Other endpoint);
          logic [7:0] value;
          always begin
            endpoint.{task_name}(value);
          end
        endmodule
        """
    )

    with pytest.raises(USGBuilderError, match="expected Receive or Send"):
        build_straight_line_usg(block)
