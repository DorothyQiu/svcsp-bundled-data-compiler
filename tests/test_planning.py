from pyslang.ast import ProceduralBlockSymbol

from svcsp_compiler.analysis import analyze_usg
from svcsp_compiler.planning import partition_logical_execution
from svcsp_compiler.semantic_frontend import parse_text
from svcsp_compiler.usg import build_straight_line_usg


_SEMANTIC_CONTEXTS = []


def _phases(source: str) -> tuple[int, ...]:
    context = parse_text(source, "planning.sv")
    _SEMANTIC_CONTEXTS.append(context)
    module = context.root.topInstances[0].body
    procedure = next(
        item for item in _visited(module) if isinstance(item, ProceduralBlockSymbol)
    )
    graph = build_straight_line_usg(procedure.body)
    facts = analyze_usg(graph)
    plan = partition_logical_execution(facts)
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
