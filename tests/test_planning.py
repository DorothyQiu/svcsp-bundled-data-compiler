import pytest
from pyslang.ast import ProceduralBlockSymbol

from svcsp_compiler.analysis import analyze_usg
from svcsp_compiler.planning import (
    ControlSurvivalRequirement,
    DataSurvivalRequirement,
    PersistentStateRequirement,
    partition_logical_execution,
)
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


def test_receive_used_by_a_phase_one_assign_requires_data_survival() -> None:
    graph, facts, plan = _plan(_CHANNEL + """
module m(Channel A, B, C, D);
  logic [7:0] a, c, u;
  always begin
    A.Receive(a); B.Send(a); C.Receive(c); u = a + 1; D.Send(u);
  end
endmodule
""")
    receive_a, _, _, assign_u, _ = graph.nodes
    (used_a,) = facts.uses_of(assign_u)
    value_use = facts.value_use_of(assign_u, used_a)

    assert plan.data_survival_requirements == (
        DataSurvivalRequirement(receive_a, value_use, 0),
    )


def test_data_survival_carries_the_exact_t_definition_not_its_operands() -> None:
    graph, facts, plan = _plan(_CHANNEL + """
module m(Channel A, B, C, D);
  logic [7:0] a, c, t, u;
  always begin
    A.Receive(a); t = a + 1; B.Send(t); C.Receive(c);
    u = t + c; D.Send(u);
  end
endmodule
""")
    receive_a, assign_t, _, _, assign_u, _ = graph.nodes
    used_t, _ = facts.uses_of(assign_u)
    value_use = facts.value_use_of(assign_u, used_t)

    assert plan.data_survival_requirements == (
        DataSurvivalRequirement(assign_t, value_use, 0),
    )
    assert all(requirement.producer is not receive_a for requirement in plan.data_survival_requirements)


def test_data_survival_emits_every_boundary_to_a_phase_two_consumer() -> None:
    graph, facts, plan = _plan(_CHANNEL + """
module m(Channel A, B, C, D, E, F);
  logic [7:0] a, c, e, u;
  always begin
    A.Receive(a); B.Send(a); C.Receive(c); D.Send(c); E.Receive(e);
    u = a + e; F.Send(u);
  end
endmodule
""")
    receive_a, _, _, _, _, assign_u, _ = graph.nodes
    used_a, _ = facts.uses_of(assign_u)
    value_use = facts.value_use_of(assign_u, used_a)

    assert plan.data_survival_requirements == (
        DataSurvivalRequirement(receive_a, value_use, 0),
        DataSurvivalRequirement(receive_a, value_use, 1),
    )


def test_same_phase_data_use_requires_no_survival() -> None:
    _, _, plan = _plan(_CHANNEL + """
module m(Channel A, B);
  logic [7:0] a, t;
  always begin A.Receive(a); t = a + 1; B.Send(t); end
endmodule
""")

    assert plan.data_survival_requirements == ()


def test_parameter_use_requires_no_data_survival() -> None:
    _, _, plan = _plan(_CHANNEL + """
module m #(parameter logic [7:0] P = 1) (Channel A, B, C, D);
  logic [7:0] a, c, u;
  always begin
    A.Receive(a); B.Send(a); C.Receive(c); u = P + c; D.Send(u);
  end
endmodule
""")

    assert plan.data_survival_requirements == ()


def test_local_entry_use_requires_no_data_survival_yet() -> None:
    _, _, plan = _plan(_CHANNEL + """
module m(Channel A, B, C, D);
  logic [7:0] a, c, u, local_entry;
  always begin
    A.Receive(a); B.Send(a); C.Receive(c); u = local_entry + c; D.Send(u);
  end
endmodule
""")

    assert plan.data_survival_requirements == ()


def test_predicate_controlling_a_phase_one_operation_requires_control_survival() -> None:
    graph, _, plan = _plan(_CHANNEL + """
module m(Channel A, B, C, D);
  logic [7:0] a, c;
  always begin
    A.Receive(a);
    if (a[0]) begin B.Send(a); C.Receive(c); D.Send(c); end
  end
endmodule
""")
    _, predicate, _, receive_c, _ = graph.nodes

    assert ControlSurvivalRequirement(predicate, receive_c, 0) in plan.control_survival_requirements


def test_predicate_controlling_a_phase_two_operation_requires_each_boundary() -> None:
    graph, _, plan = _plan(_CHANNEL + """
module m(Channel A, B, C, D, E, F);
  logic [7:0] a, c, e;
  always begin
    A.Receive(a);
    if (a[0]) begin
      B.Send(a); C.Receive(c); D.Send(c); E.Receive(e); F.Send(e);
    end
  end
endmodule
""")
    _, predicate, _, _, _, receive_e, _ = graph.nodes
    requirements = tuple(
        requirement
        for requirement in plan.control_survival_requirements
        if requirement.controlled_operation is receive_e
    )

    assert requirements == (
        ControlSurvivalRequirement(predicate, receive_e, 0),
        ControlSurvivalRequirement(predicate, receive_e, 1),
    )


def test_same_phase_predicate_control_requires_no_survival() -> None:
    _, _, plan = _plan(_CHANNEL + """
module m(Channel A, B);
  logic [7:0] a;
  always begin A.Receive(a); if (a[0]) B.Send(a); end
endmodule
""")

    assert plan.control_survival_requirements == ()


def test_persistent_state_records_self_assignment_entry_use_and_exit_definition() -> None:
    graph, facts, plan = _plan(_CHANNEL + """
module m(Channel B, input logic [7:0] a);
  logic [7:0] x;
  always begin x = x + a; B.Send(x); end
endmodule
""")
    assign, _ = graph.nodes
    x_symbol, _ = facts.uses_of(assign)
    old_x_use = facts.value_use_of(assign, x_symbol)

    assert plan.persistent_state_requirements == (
        PersistentStateRequirement(x_symbol, (old_x_use,), (assign,), False),
    )


def test_persistent_state_keeps_conditional_assignment_and_retained_entry() -> None:
    graph, facts, plan = _plan(_CHANNEL + """
module m(Channel B, input logic p);
  logic [7:0] x;
  always begin if (p) x = x + 1; B.Send(x); end
endmodule
""")
    _, assign, send = graph.nodes
    (x_symbol,) = facts.definitions_of(assign)
    old_x_use = facts.value_use_of(assign, facts.uses_of(assign)[0])
    sent_x_use = facts.value_use_of(send, facts.uses_of(send)[0])

    assert plan.persistent_state_requirements == (
        PersistentStateRequirement(
            x_symbol, (old_x_use, sent_x_use), (assign,), True
        ),
    )


def test_persistent_state_includes_exit_definition_without_an_in_process_consumer() -> None:
    graph, facts, plan = _plan(_CHANNEL + """
module m(Channel B, input logic [7:0] a);
  logic [7:0] x;
  always begin B.Send(x); x = a + 1; end
endmodule
""")
    send, assign = graph.nodes
    (x_symbol,) = facts.uses_of(send)
    sent_x_use = facts.value_use_of(send, x_symbol)

    assert plan.persistent_state_requirements == (
        PersistentStateRequirement(x_symbol, (sent_x_use,), (assign,), False),
    )


def test_persistent_state_records_read_only_local_entry() -> None:
    graph, facts, plan = _plan(_CHANNEL + """
module m(Channel B);
  logic [7:0] x;
  always B.Send(x);
endmodule
""")
    (send,) = graph.nodes
    (x_symbol,) = facts.uses_of(send)
    sent_x_use = facts.value_use_of(send, x_symbol)

    assert plan.persistent_state_requirements == (
        PersistentStateRequirement(x_symbol, (sent_x_use,), (), True),
    )


def test_definition_before_use_without_an_entry_dependent_use_is_not_persistent_state() -> None:
    _, _, plan = _plan(_CHANNEL + """
module m(Channel B, input logic [7:0] a);
  logic [7:0] x;
  always begin x = a + 1; B.Send(x); end
endmodule
""")

    assert plan.persistent_state_requirements == ()


def test_port_entry_and_parameter_uses_are_not_persistent_state() -> None:
    _, _, plan = _plan(_CHANNEL + """
module m #(parameter logic [7:0] P = 1) (Channel B, input logic [7:0] port_x);
  always begin B.Send(port_x); B.Send(P); end
endmodule
""")

    assert plan.persistent_state_requirements == ()
