from dataclasses import fields

import pytest
from pyslang.ast import ProceduralBlockSymbol

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


def _semantic_objects():
    context = parse_text(SOURCE, "usg.sv")
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
    control = graph.add_control_edge(predicate, send)

    assert receive.semantic_object is assignment
    assert assign.semantic_object is assignment
    assert predicate.semantic_object is predicate_expression
    assert send.semantic_object is conditional
    assert isinstance(data, DataEdge)
    assert isinstance(control, ControlEdge)
    assert data.source is receive and data.target is assign
    assert control.source is predicate and control.target is send
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
        graph.add_edge(edge_type(inside, outside))


def test_model_contains_only_native_references_and_semantic_edge_endpoints() -> None:
    for node_type in (ReceiveNode, SendNode, AssignNode, PredicateNode):
        assert [field.name for field in fields(node_type)] == ["semantic_object"]
    for edge_type in (DataEdge, ControlEdge):
        assert [field.name for field in fields(edge_type)] == ["source", "target"]

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


def test_builder_rejects_unsupported_executable_statement_forms() -> None:
    block = _block("""\
module unsupported(input logic a);
  logic x;
  always begin
    if (a) x = a;
  end
endmodule
""")

    with pytest.raises(USGBuilderError, match="unsupported executable statement"):
        build_straight_line_usg(block)


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
