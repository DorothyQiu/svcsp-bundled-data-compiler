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
    UnifiedSemanticGraph,
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
