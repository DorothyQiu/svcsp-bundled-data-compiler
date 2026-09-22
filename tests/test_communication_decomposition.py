"""M4A conditional-communication decomposition contract tests."""
from __future__ import annotations

from svcsp_compiler.behavioral_ir import (
    Assign,
    BehavioralModule,
    ChannelEndpoint,
    Expression,
    If,
    ONE_BIT,
    PayloadType,
    Receive,
    Send,
    Sequence,
    Skip,
    SourceLocation,
    Variable,
)
from svcsp_compiler.communication_decomposition import (
    BodyReceive,
    BodySend,
    DecomposedTransaction,
    EnReceive,
    EnSend,
    Enable,
    InvalidPayload,
    decompose_transaction,
)
from svcsp_compiler.transaction import extract_transaction


_TYPE = PayloadType("logic", ONE_BIT)


def _variable(name: str) -> Variable:
    return Variable(name, (), SourceLocation("decomposition.sv", 1, 1), _TYPE)


def _condition(name: str) -> Expression:
    variable = _variable(name)
    return Expression("name", value=name, variable=variable)


def _channel(name: str, *selectors: str) -> ChannelEndpoint:
    return ChannelEndpoint(name, tuple(Expression("literal", value=value) for value in selectors))


def _receive(name: str, target: str, *selectors: str) -> Receive:
    return Receive(_channel(name, *selectors), _variable(target))


def _send(name: str, value: str = "1'b0", *selectors: str) -> Send:
    return Send(_channel(name, *selectors), Expression("literal", value=value))


def _transaction(body):
    behavioral = BehavioralModule("decomposition", body, (), ())
    return extract_transaction(behavioral)


def _decompose(body) -> DecomposedTransaction:
    return decompose_transaction(_transaction(body))


def _condition_text(expression: Expression) -> str:
    if expression.form in {"name", "literal"}:
        return expression.value
    if expression.form == "unary":
        return expression.operator + _condition_text(expression.operands[0])
    return "(" + _condition_text(expression.operands[0]) + expression.operator + _condition_text(expression.operands[1]) + ")"


def test_unconditional_1r1s_has_only_unconditional_body_communication() -> None:
    transaction = _transaction(Sequence((_receive("A", "a"), _send("B"))))
    decomposed = decompose_transaction(transaction)

    assert isinstance(decomposed, DecomposedTransaction)
    assert decomposed.transaction is transaction
    assert len(decomposed.body_receives) == len(decomposed.body_sends) == 1
    assert decomposed.body_receives[0].source is transaction.receives[0]
    assert decomposed.body_sends[0].source is transaction.sends[0]
    assert decomposed.body_receives[0].valid_when is None
    assert decomposed.body_receives[0].disabled_payload is None
    assert decomposed.enables == ()
    assert decomposed.en_receives == ()
    assert decomposed.en_sends == ()


def test_conditional_receive_has_unconditional_body_receive_and_en_recv() -> None:
    condition = _condition("c")
    source_receive = _receive("A", "a")
    transaction = _transaction(Sequence((If(condition, source_receive, Skip()), _send("B"))))
    decomposed = decompose_transaction(transaction)

    body_receive = decomposed.body_receives[0]
    enable = decomposed.enables[0]
    en_receive = decomposed.en_receives[0]
    assert isinstance(body_receive, BodyReceive)
    assert isinstance(enable, Enable)
    assert isinstance(en_receive, EnReceive)
    assert body_receive.source is transaction.receives[0]
    assert enable.source is transaction.receives[0]
    assert enable.condition is condition
    assert en_receive.source is transaction.receives[0]
    assert en_receive.enable is enable
    assert en_receive.body_receive is body_receive
    assert body_receive.valid_when is enable
    assert isinstance(body_receive.disabled_payload, InvalidPayload)
    assert body_receive.source.operation is source_receive
    assert body_receive.source.operation.channel is source_receive.channel
    assert body_receive.source.operation.target is source_receive.target


def test_conditional_send_has_unconditional_body_send_and_en_send() -> None:
    condition = _condition("c")
    source_send = _send("B", "1'b1")
    transaction = _transaction(Sequence((_receive("A", "a"), If(condition, source_send, Skip()))))
    decomposed = decompose_transaction(transaction)

    body_send = decomposed.body_sends[0]
    enable = decomposed.enables[0]
    en_send = decomposed.en_sends[0]
    assert isinstance(body_send, BodySend)
    assert isinstance(en_send, EnSend)
    assert body_send.source is transaction.sends[0]
    assert enable.source is transaction.sends[0]
    assert en_send.source is transaction.sends[0]
    assert en_send.enable is enable
    assert en_send.body_send is body_send
    assert body_send.source.operation is source_send
    assert body_send.source.operation.channel is source_send.channel
    assert body_send.source.operation.value is source_send.value


def test_if_else_alternatives_have_complementary_enable_guards() -> None:
    select = _condition("sel")
    decomposed = _decompose(Sequence((
        _receive("A", "a"),
        If(select, _send("B"), _send("C")),
    )))

    assert [_condition_text(enable.condition) for enable in decomposed.enables] == ["sel", "!sel"]


def test_nested_conditions_compose_enable_guards() -> None:
    x, y = _condition("x"), _condition("y")
    decomposed = _decompose(Sequence((
        If(x, If(y, _receive("A", "a"), _receive("B", "b")), _receive("C", "c")),
        _send("D"),
    )))

    assert [_condition_text(enable.condition) for enable in decomposed.enables] == [
        "(x&&y)", "(x&&!y)", "!x",
    ]


def test_same_guard_on_distinct_communications_has_distinct_enable_identities() -> None:
    condition = _condition("c")
    decomposed = _decompose(Sequence((
        _receive("A", "a"),
        If(condition, _send("B"), Skip()),
        If(condition, _send("C"), Skip()),
    )))

    first, second = decomposed.enables
    assert first.condition is second.condition is condition
    assert first is not second
    assert first.id != second.id
    assert decomposed.en_sends[0].enable is first
    assert decomposed.en_sends[1].enable is second


def test_selected_endpoint_identity_is_preserved() -> None:
    condition = _condition("c")
    first, second = _receive("A", "a", "0"), _receive("A", "b", "1")
    decomposed = _decompose(Sequence((If(condition, first, second), _send("B"))))

    first_body, second_body = decomposed.body_receives
    assert first_body.source.operation is first
    assert second_body.source.operation is second
    assert first_body.source.operation.channel is first.channel
    assert second_body.source.operation.channel is second.channel
    assert first_body.source.operation.channel != second_body.source.operation.channel


def test_body_combinational_preserves_exact_m3_region_operations() -> None:
    assignment = Assign(_variable("y"), Expression("literal", value="1'b0"))
    transaction = _transaction(Sequence((_receive("A", "a"), assignment, _send("B"))))
    decomposed = decompose_transaction(transaction)

    assert decomposed.body_combinational == transaction.combinational
    assert decomposed.body_combinational[0] is transaction.combinational[0]
    assert decomposed.body_combinational[0].operation is assignment


def test_constant_true_conditional_communication_is_not_constant_folded() -> None:
    condition = Expression("literal", value="1'b1")
    source_send = _send("B")
    decomposed = _decompose(Sequence((
        _receive("A", "a"),
        If(condition, source_send, Skip()),
    )))

    assert len(decomposed.enables) == len(decomposed.en_sends) == 1
    assert decomposed.enables[0].condition is condition
    assert decomposed.en_sends[0].source.operation is source_send
