from dataclasses import fields

import pytest
from pyslang.ast import CallExpression, GenerateBlockArraySymbol, ProceduralBlockSymbol

from svcsp_compiler.semantic_frontend import parse_text
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
