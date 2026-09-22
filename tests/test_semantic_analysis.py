"""M5 semantic dependency and conditional-validity contract tests."""
from __future__ import annotations

import pytest

from svcsp_compiler.behavioral_ir import (
    Assign,
    BehavioralModule,
    ChannelEndpoint,
    Expression,
    If,
    ONE_BIT,
    Parallel,
    PayloadType,
    Receive,
    Send,
    Sequence,
    Skip,
    SourceLocation,
    Variable,
)
from svcsp_compiler.communication_decomposition import decompose_transaction
from svcsp_compiler.semantic_analysis import (
    ReceiveValidity,
    SemanticDependencyKind,
    SemanticValidationError,
    SemanticallyValidatedTransaction,
    analyze_semantics,
)
from svcsp_compiler.transaction import extract_transaction


_TYPE = PayloadType("logic", ONE_BIT)


class _Program:
    def __init__(self) -> None:
        self.variables: dict[str, Variable] = {}

    def variable(self, name: str) -> Variable:
        return self.variables.setdefault(
            name, Variable(name, (), SourceLocation("semantic.sv", 1, 1), _TYPE),
        )

    def name(self, name: str) -> Expression:
        variable = self.variable(name)
        return Expression("name", value=name, variable=variable)

    def condition(self, name: str) -> Expression:
        return self.name(name)

    def receive(self, channel: str, target: str) -> Receive:
        return Receive(ChannelEndpoint(channel), self.variable(target))

    def send(self, channel: str, value: Expression) -> Send:
        return Send(ChannelEndpoint(channel), value)

    def assign(self, target: str, value: Expression) -> Assign:
        return Assign(self.variable(target), value)

    def module(self, body) -> BehavioralModule:
        return BehavioralModule("semantic", body, (), tuple(self.variables.values()))


def _binary(operator: str, left: Expression, right: Expression) -> Expression:
    return Expression("binary", operator=operator, operands=(left, right))


def _select_bit(expression: Expression) -> Expression:
    return Expression(
        "select", value=expression.value, variable=expression.variable,
        operands=(Expression("index", operands=(Expression("literal", value="0"),)),),
    )


def _decompose(program: _Program, body):
    return decompose_transaction(extract_transaction(program.module(body)))


def _analyze(program: _Program, body) -> SemanticallyValidatedTransaction:
    return analyze_semantics(_decompose(program, body))


def test_independent_sequential_receives_have_dataflow_without_receive_ordering() -> None:
    program = _Program()
    receive_a, receive_b = program.receive("A", "a"), program.receive("B", "b")
    assignment = program.assign("c", _binary("+", program.name("a"), program.name("b")))
    send_c = program.send("C", program.name("c"))
    decomposed = _decompose(program, Sequence((receive_a, receive_b, assignment, send_c)))
    validated = analyze_semantics(decomposed)

    assert isinstance(validated, SemanticallyValidatedTransaction)
    assert validated.decomposed is decomposed
    dependencies = {(edge.source, edge.target, edge.kind) for edge in validated.dependencies}
    assert (decomposed.transaction.receives[0], decomposed.transaction.combinational[0], SemanticDependencyKind.DATA) in dependencies
    assert (decomposed.transaction.receives[1], decomposed.transaction.combinational[0], SemanticDependencyKind.DATA) in dependencies
    assert (decomposed.transaction.combinational[0], decomposed.transaction.sends[0], SemanticDependencyKind.DATA) in dependencies
    assert not any(edge.source is decomposed.transaction.receives[0] and edge.target is decomposed.transaction.receives[1]
                   for edge in validated.dependencies)


def test_conditional_receive_use_under_same_or_stronger_guard_is_valid() -> None:
    program = _Program()
    select, extra = program.condition("sel"), program.condition("extra")
    conditional_receive = program.receive("A", "a")
    use_when_extra = program.assign("y", program.name("a"))
    use_without_extra = program.assign("y", program.name("a"))
    fallback = program.assign("y", Expression("literal", value="1'b0"))
    body = Sequence((
        If(select, conditional_receive, Skip()),
        If(select, If(extra, use_when_extra, use_without_extra), fallback),
        program.send("B", program.name("y")),
    ))

    validated = _analyze(program, body)

    assert any(isinstance(fact, ReceiveValidity) and fact.body_receive.source.operation is conditional_receive
               for fact in validated.receive_validity)


@pytest.mark.parametrize("unrelated", [False, True], ids=("unguarded", "unrelated-guard"))
def test_conditional_receive_use_requires_proven_guard(unrelated: bool) -> None:
    program = _Program()
    select = program.condition("sel")
    conditional_receive = program.receive("A", "a")
    use = program.assign("y", program.name("a"))
    use_site = If(program.condition("other"), use, program.assign("y", Expression("literal", value="1'b0"))) if unrelated else use
    body = Sequence((
        If(select, conditional_receive, Skip()),
        use_site,
        program.send("B", program.name("y")),
    ))

    with pytest.raises(SemanticValidationError, match="valid"):
        _analyze(program, body)


def test_receive_enable_cannot_depend_on_another_receive_payload() -> None:
    program = _Program()
    first = program.receive("A", "a")
    dependent = program.receive("B", "b")
    body = Sequence((
        first,
        If(_select_bit(program.name("a")), dependent, Skip()),
        program.send("C", Expression("literal", value="1'b0")),
    ))

    with pytest.raises(SemanticValidationError, match="Receive"):
        _analyze(program, body)


def test_if_else_definitions_cover_an_unguarded_later_use() -> None:
    program = _Program()
    body = Sequence((
        program.receive("A", "a"),
        If(program.condition("sel"),
           program.assign("y", program.name("a")),
           program.assign("y", Expression("literal", value="1'b0"))),
        program.send("B", program.name("y")),
    ))

    _analyze(program, body)


def test_conditional_input_alternatives_support_guarded_ternary_arms() -> None:
    program = _Program()
    select = program.condition("sel")
    body = Sequence((
        If(select, program.receive("A", "a"), program.receive("B", "b")),
        program.assign("y", Expression("conditional", operands=(select, program.name("a"), program.name("b")))),
        program.send("C", program.name("y")),
    ))

    _analyze(program, body)


def test_conditional_send_payload_is_checked_under_its_send_enable() -> None:
    program = _Program()
    select = program.condition("sel")
    conditional_receive = program.receive("A", "a")
    conditional_send = program.send("B", program.name("a"))
    body = Sequence((
        If(select, conditional_receive, Skip()),
        If(select, conditional_send, Skip()),
    ))

    validated = _analyze(program, body)

    assert validated.decomposed.en_sends[0].source.operation is conditional_send


def test_enable_condition_data_dependencies_are_recorded() -> None:
    program = _Program()
    receive = program.receive("A", "a")
    conditional_send = program.send("B", Expression("literal", value="1'b0"))
    decomposed = _decompose(program, Sequence((
        receive,
        If(_select_bit(program.name("a")), conditional_send, Skip()),
    )))
    validated = analyze_semantics(decomposed)

    assert any(edge.source is decomposed.transaction.receives[0] and
               edge.target is decomposed.enables[0] and
               edge.kind is SemanticDependencyKind.DATA
               for edge in validated.dependencies)


def test_analysis_preserves_exact_decomposed_transaction_and_m4_identities() -> None:
    program = _Program()
    condition = program.condition("sel")
    conditional_receive = program.receive("A", "a")
    decomposed = _decompose(program, Sequence((
        If(condition, conditional_receive, Skip()),
        program.send("B", Expression("literal", value="1'b0")),
    )))

    validated = analyze_semantics(decomposed)

    fact = validated.receive_validity[0]
    assert validated.decomposed is decomposed
    assert fact.body_receive is decomposed.body_receives[0]
    assert fact.valid_when is decomposed.enables[0]
    assert fact.target is conditional_receive.target


def test_nested_en_recv_enable_cannot_depend_on_receive_data() -> None:
    program = _Program()
    dependent_receive = program.receive("B", "b")
    body = Sequence((
        program.receive("A", "a"),
        If(program.condition("c"), If(_select_bit(program.name("a")), dependent_receive, Skip()), Skip()),
        program.send("C", Expression("literal", value="1'b0")),
    ))

    with pytest.raises(SemanticValidationError, match="Receive"):
        _analyze(program, body)


def test_conditional_receive_data_cannot_drive_later_if_predicate_outside_guard() -> None:
    program = _Program()
    body = Sequence((
        If(program.condition("sel"), program.receive("A", "a"), Skip()),
        If(_select_bit(program.name("a")),
           program.assign("y", Expression("literal", value="1'b1")),
           program.assign("y", Expression("literal", value="1'b0"))),
        program.send("B", program.name("y")),
    ))

    with pytest.raises(SemanticValidationError, match="valid"):
        _analyze(program, body)


def test_conditional_send_accepts_receive_payload_under_composed_stronger_guard() -> None:
    program = _Program()
    select = program.condition("sel")
    conditional_send = program.send("B", program.name("a"))
    body = Sequence((
        If(select, program.receive("A", "a"), Skip()),
        If(_binary("&&", select, _select_bit(program.name("a"))), conditional_send, Skip()),
    ))

    _analyze(program, body)


def test_composed_enable_records_receive_data_dependency() -> None:
    program = _Program()
    conditional_send = program.send("B", Expression("literal", value="1'b0"))
    decomposed = _decompose(program, Sequence((
        program.receive("A", "a"),
        If(program.condition("c"),
           If(_select_bit(program.name("a")), conditional_send, Skip()),
           Skip()),
    )))

    validated = analyze_semantics(decomposed)

    assert any(edge.source is decomposed.transaction.receives[0] and
               edge.target is decomposed.enables[0] and
               edge.kind is SemanticDependencyKind.DATA
               for edge in validated.dependencies)


def test_logical_and_rhs_reads_conditional_receive_only_when_lhs_is_true() -> None:
    program = _Program()
    select = program.condition("sel")
    body = Sequence((
        If(select, program.receive("A", "a"), Skip()),
        If(_binary("&&", select, _select_bit(program.name("a"))),
           program.send("B", Expression("literal", value="1'b0")), Skip()),
    ))

    _analyze(program, body)


def test_logical_or_rhs_reads_conditional_receive_only_when_lhs_is_false() -> None:
    program = _Program()
    select = program.condition("sel")
    not_select = Expression("unary", operator="!", operands=(select,))
    body = Sequence((
        If(not_select, program.receive("A", "a"), Skip()),
        If(_binary("||", select, _select_bit(program.name("a"))),
           program.send("B", Expression("literal", value="1'b0")), Skip()),
    ))

    _analyze(program, body)


def test_parallel_receive_enable_cannot_depend_on_another_receive_payload() -> None:
    program = _Program()
    dependent_receive = program.receive("B", "b")
    body = Sequence((
        Parallel((
            program.receive("A", "a"),
            If(_select_bit(program.name("a")), dependent_receive, Skip()),
        )),
        program.send("C", Expression("literal", value="1'b0")),
    ))

    with pytest.raises(SemanticValidationError, match="Receive"):
        _analyze(program, body)
