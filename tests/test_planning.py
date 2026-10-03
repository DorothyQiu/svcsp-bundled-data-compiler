import pytest
from pyslang.ast import ProceduralBlockSymbol

from svcsp_compiler.analysis import analyze_usg
from svcsp_compiler.planning import partition_logical_execution
from svcsp_compiler.semantic_frontend import parse_text
from svcsp_compiler.usg import build_straight_line_usg


_SEMANTIC_CONTEXTS = []


def _plan(source: str):
    context = parse_text(source, "planning.sv")
    _SEMANTIC_CONTEXTS.append(context)
    module = context.root.topInstances[0].body
    procedure = next(
        item for item in _visited(module) if isinstance(item, ProceduralBlockSymbol)
    )
    graph = build_straight_line_usg(procedure.body)
    facts = analyze_usg(graph)
    plan = partition_logical_execution(facts, graph.control_edges)
    return graph, facts, plan


def _phases(source: str) -> tuple[int, ...]:
    _, facts, plan = _plan(source)
    return tuple(plan.phase_of(node) for node, _ in facts.communication_predecessors)


def _visited(symbol: object) -> list[object]:
    items: list[object] = []
    symbol.visit(items.append)
    return items


_CHANNEL = """\
interface Channel;
  task Receive(output logic [7:0] data); endtask
  task Send(input logic [7:0] data); endtask
endinterface
"""


def test_receive_then_send_stays_in_phase_zero() -> None:
    assert _phases(_CHANNEL + """
module m(Channel A, B);
  logic [7:0] a;
  always begin A.Receive(a); B.Send(a); end
endmodule
""") == (0, 0)


def test_receive_receive_send_stays_in_phase_zero() -> None:
    assert _phases(_CHANNEL + """
module m(Channel A, B, C);
  logic [7:0] a, b;
  always begin A.Receive(a); B.Receive(b); C.Send(a); end
endmodule
""") == (0, 0, 0)


def test_receive_send_send_stays_in_phase_zero() -> None:
    assert _phases(_CHANNEL + """
module m(Channel A, B, C);
  logic [7:0] a;
  always begin A.Receive(a); B.Send(a); C.Send(a); end
endmodule
""") == (0, 0, 0)


def test_send_then_later_receive_starts_a_new_phase() -> None:
    assert _phases(_CHANNEL + """
module m(Channel A, B, C, D);
  logic [7:0] a, c;
  always begin A.Receive(a); B.Send(a); C.Receive(c); D.Send(c); end
endmodule
""") == (0, 0, 1, 1)


def test_conditional_send_advances_the_shared_receive_to_the_later_phase() -> None:
    assert _phases(_CHANNEL + """
module m(Channel A, B, C);
  logic [7:0] a, c;
  always begin
    A.Receive(a);
    if (a[0]) B.Send(a);
    C.Receive(c);
  end
endmodule
""") == (0, 0, 1)


def test_mutually_exclusive_sends_remain_in_the_same_phase() -> None:
    assert _phases(_CHANNEL + """
module m(Channel A, B, C);
  logic [7:0] a;
  always begin
    A.Receive(a);
    if (a[0]) B.Send(a);
    else C.Send(a);
  end
endmodule
""") == (0, 0, 0)


def test_assign_between_receive_and_send_is_placed_in_phase_zero() -> None:
    graph, _, plan = _plan(_CHANNEL + """
module m(Channel A, B);
  logic [7:0] a, t;
  always begin A.Receive(a); t = a + 1; B.Send(t); end
endmodule
""")
    _, assign, _ = graph.nodes

    assert plan.phase_of(assign) == 0


def test_assign_needed_only_after_a_later_receive_is_placed_in_phase_one() -> None:
    graph, _, plan = _plan(_CHANNEL + """
module m(Channel A, B, C, D);
  logic [7:0] a, c, u;
  always begin
    A.Receive(a); B.Send(a); C.Receive(c);
    u = a + 1; D.Send(u);
  end
endmodule
""")
    _, _, _, assign, _ = graph.nodes

    assert plan.phase_of(assign) == 1


def test_assigns_are_placed_at_their_earliest_required_phases() -> None:
    graph, _, plan = _plan(_CHANNEL + """
module m(Channel A, B, C, D);
  logic [7:0] a, c, t, u;
  always begin
    A.Receive(a); t = a + 1; B.Send(t); C.Receive(c);
    u = t + c; D.Send(u);
  end
endmodule
""")
    _, first_assign, _, _, second_assign, _ = graph.nodes

    assert plan.phase_of(first_assign) == 0
    assert plan.phase_of(second_assign) == 1


def test_assign_with_consumers_in_two_phases_is_placed_in_the_earlier_phase() -> None:
    graph, _, plan = _plan(_CHANNEL + """
module m(Channel A, B, C, D);
  logic [7:0] a, c, t;
  always begin
    A.Receive(a); t = a + 1; B.Send(t); C.Receive(c); D.Send(t);
  end
endmodule
""")
    _, assign, _, _, _ = graph.nodes

    assert plan.phase_of(assign) == 0


def test_predicate_for_a_phase_one_send_is_placed_in_phase_one() -> None:
    graph, _, plan = _plan(_CHANNEL + """
module m(Channel A, B, C, D);
  logic [7:0] a, c;
  always begin
    A.Receive(a); B.Send(a); C.Receive(c);
    if (a[0]) D.Send(c);
  end
endmodule
""")
    _, _, _, predicate, _ = graph.nodes

    assert plan.phase_of(predicate) == 1


def test_predicate_with_controlled_operations_in_two_phases_is_placed_in_phase_zero() -> None:
    graph, _, plan = _plan(_CHANNEL + """
module m(Channel A, B, C, D);
  logic [7:0] a, c;
  always begin
    A.Receive(a);
    if (a[0]) begin B.Send(a); C.Receive(c); D.Send(c); end
  end
endmodule
""")
    _, predicate, _, _, _ = graph.nodes

    assert plan.phase_of(predicate) == 0


def test_assign_chain_feeding_only_a_phase_one_send_is_placed_in_phase_one() -> None:
    graph, _, plan = _plan(_CHANNEL + """
module m(Channel A, B, C, D);
  logic [7:0] a, c, t, u;
  always begin
    A.Receive(a); B.Send(a); C.Receive(c);
    t = a + 1; u = t + c; D.Send(u);
  end
endmodule
""")
    _, _, _, first_assign, second_assign, _ = graph.nodes

    assert plan.phase_of(first_assign) == 1
    assert plan.phase_of(second_assign) == 1


def test_unused_assign_remains_unplaced() -> None:
    graph, _, plan = _plan(_CHANNEL + """
module m(Channel A);
  logic [7:0] a, t;
  always begin A.Receive(a); t = a + 1; end
endmodule
""")
    _, assign = graph.nodes

    with pytest.raises(KeyError, match="not placed"):
        plan.phase_of(assign)
