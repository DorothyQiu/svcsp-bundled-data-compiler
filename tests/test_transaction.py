"""M3 transaction extraction and structural-validation contract tests."""
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
    SourceLocation,
    Variable,
)
from svcsp_compiler.transaction import (
    RegionOperation,
    StructurallyValidatedTransaction,
    TransactionWarning,
    extract_transaction,
)


_TYPE = PayloadType("logic", ONE_BIT)


def _variable(name: str) -> Variable:
    return Variable(name, (), SourceLocation("transaction.sv", 1, 1), _TYPE)


def _channel(name: str, *selectors: str) -> ChannelEndpoint:
    return ChannelEndpoint(name, tuple(Expression("literal", value=value) for value in selectors))


def _module(body) -> BehavioralModule:
    return BehavioralModule("transaction", body, (), ())


def _receive(name: str, target: str, *selectors: str) -> Receive:
    return Receive(_channel(name, *selectors), _variable(target))


def _send(name: str, value: str = "1'b0", *selectors: str) -> Send:
    return Send(_channel(name, *selectors), Expression("literal", value=value))


def _assign(name: str) -> Assign:
    return Assign(_variable(name), Expression("literal", value="1'b0"))


def _extract(body) -> StructurallyValidatedTransaction:
    return extract_transaction(_module(body))


@pytest.mark.parametrize(
    ("body", "receive_count", "combinational_count", "send_count"),
    [
        (Sequence((_receive("A", "a"), _send("B"))), 1, 0, 1),
        (Sequence((_receive("A", "a"), _receive("B", "b"), _send("C"))), 2, 0, 1),
        (Sequence((_receive("A", "a"), _send("B"), _send("C"))), 1, 0, 2),
        (Sequence((_receive("A", "a"), _receive("B", "b"), _send("C"), _send("D"))), 2, 0, 2),
    ],
)
def test_extracts_receive_combinational_send_regions(
    body, receive_count: int, combinational_count: int, send_count: int
) -> None:
    transaction = _extract(body)

    assert isinstance(transaction, StructurallyValidatedTransaction)
    assert len(transaction.receives) == receive_count
    assert len(transaction.combinational) == combinational_count
    assert len(transaction.sends) == send_count
    assert all(isinstance(item, RegionOperation) for item in transaction.receives + transaction.combinational + transaction.sends)


def test_sequential_communications_preserve_source_order_and_warn() -> None:
    first_receive, second_receive = _receive("A", "a"), _receive("B", "b")
    first_send, second_send = _send("C"), _send("D")
    transaction = _extract(Sequence((first_receive, second_receive, first_send, second_send)))

    assert [item.operation for item in transaction.receives] == [first_receive, second_receive]
    assert [item.operation for item in transaction.sends] == [first_send, second_send]
    assert all(isinstance(warning, TransactionWarning) for warning in transaction.warnings)
    assert transaction.warnings
    assert all("fork" in warning.message.lower() and "join" in warning.message.lower()
               for warning in transaction.warnings)


def test_same_region_fork_join_is_accepted_without_sequential_warning() -> None:
    transaction = _extract(Sequence((
        Parallel((_receive("A", "a"), _receive("B", "b"))),
        Parallel((_send("C"), _send("D"))),
    )))

    assert len(transaction.receives) == 2
    assert len(transaction.sends) == 2
    assert not transaction.warnings


def test_selected_array_endpoints_are_distinct() -> None:
    transaction = _extract(Sequence((
        _receive("A", "a", "0"),
        _receive("A", "b", "1"),
        _send("B"),
    )))

    assert len(transaction.receives) == 2


def test_conditional_nested_m2_structure_is_preserved_on_transaction() -> None:
    nested = If(
        Expression("literal", value="1'b1"),
        If(Expression("literal", value="1'b1"), _receive("A", "a"), _receive("B", "b")),
        _receive("C", "c"),
    )
    behavioral = _module(Sequence((nested, If(Expression("literal", value="1'b1"), _send("D"), _send("E")))))

    transaction = extract_transaction(behavioral)

    assert transaction.behavioral is behavioral
    assert transaction.behavioral.body is behavioral.body


@pytest.mark.parametrize(
    "body",
    [
        Sequence((_receive("A", "a"), _send("B"), _receive("C", "c"))),
        Sequence((_receive("A", "a"), _assign("x"), _receive("B", "b"), _send("C"))),
        Sequence((_receive("A", "a"), _send("B"), _assign("x"))),
        Sequence((_receive("A", "a"), _receive("A", "b"), _send("B"))),
        Sequence((_receive("A", "a", "0"), _receive("A", "b", "0"), _send("B"))),
        Sequence((
            If(Expression("literal", value="1'b1"), _receive("A", "a"), _receive("A", "b")),
            _send("B"),
        )),
        Sequence((Parallel((_receive("A", "a"), _send("B"))), _send("C"))),
        Sequence((_send("B"),)),
        Sequence((_receive("A", "a"),)),
    ],
    ids=(
        "receive-send-receive",
        "receive-after-assignment",
        "assignment-after-send",
        "repeated-endpoint",
        "repeated-selected-endpoint",
        "repeated-endpoint-in-mutually-exclusive-branches",
        "fork-join-mixes-regions",
        "no-receive",
        "no-send",
    ),
)
def test_rejects_unsupported_transaction_structure(body) -> None:
    with pytest.raises(ValueError):
        _extract(body)
