"""M4A decomposition of conditional communication into abstract adapters."""
from __future__ import annotations

from dataclasses import dataclass

from . import behavioral_ir as behavioral
from .transaction import RegionOperation, SourcePath, StructurallyValidatedTransaction


@dataclass(frozen=True)
class Enable:
    """The effective source guard for one conditional communication occurrence."""

    id: str
    condition: behavioral.Expression
    source: RegionOperation


@dataclass(frozen=True)
class InvalidPayload:
    """An abstract disabled-receive payload, deliberately without a value."""


@dataclass(frozen=True)
class BodyReceive:
    """An unconditional BODY-side receive corresponding to an M3 receive."""

    source: RegionOperation
    valid_when: Enable | None
    disabled_payload: InvalidPayload | None


@dataclass(frozen=True)
class BodySend:
    """An unconditional BODY-side send corresponding to an M3 send."""

    source: RegionOperation


@dataclass(frozen=True)
class EnReceive:
    """An abstract conditional external-receive adapter."""

    source: RegionOperation
    enable: Enable
    body_receive: BodyReceive


@dataclass(frozen=True)
class EnSend:
    """An abstract conditional external-send adapter."""

    source: RegionOperation
    enable: Enable
    body_send: BodySend


@dataclass(frozen=True)
class DecomposedTransaction:
    transaction: StructurallyValidatedTransaction
    body_receives: tuple[BodyReceive, ...]
    body_combinational: tuple[RegionOperation, ...]
    body_sends: tuple[BodySend, ...]
    enables: tuple[Enable, ...]
    en_receives: tuple[EnReceive, ...]
    en_sends: tuple[EnSend, ...]


def decompose_transaction(transaction: StructurallyValidatedTransaction) -> DecomposedTransaction:
    """Make all BODY communication unconditional without choosing an implementation."""

    if not isinstance(transaction, StructurallyValidatedTransaction):
        raise TypeError('expected a StructurallyValidatedTransaction')

    guards: dict[SourcePath, behavioral.Expression | None] = {}
    _record_guards(transaction.behavioral.body, (), None, guards)

    enables: list[Enable] = []
    body_receives: list[BodyReceive] = []
    body_sends: list[BodySend] = []
    en_receives: list[EnReceive] = []
    en_sends: list[EnSend] = []

    for source in transaction.receives:
        guard = guards[source.path]
        if guard is None:
            body_receives.append(BodyReceive(source, None, None))
            continue
        enable = _enable(source, guard, enables)
        body_receive = BodyReceive(source, enable, InvalidPayload())
        body_receives.append(body_receive)
        en_receives.append(EnReceive(source, enable, body_receive))

    for source in transaction.sends:
        guard = guards[source.path]
        if guard is None:
            body_sends.append(BodySend(source))
            continue
        enable = _enable(source, guard, enables)
        body_send = BodySend(source)
        body_sends.append(body_send)
        en_sends.append(EnSend(source, enable, body_send))

    return DecomposedTransaction(
        transaction,
        tuple(body_receives),
        transaction.combinational,
        tuple(body_sends),
        tuple(enables),
        tuple(en_receives),
        tuple(en_sends),
    )


def _enable(source: RegionOperation, condition: behavioral.Expression,
            enables: list[Enable]) -> Enable:
    enable = Enable(f'enable_{len(enables)}', condition, source)
    enables.append(enable)
    return enable


def _record_guards(process: behavioral.Process, path: SourcePath,
                   guard: behavioral.Expression | None,
                   guards: dict[SourcePath, behavioral.Expression | None]) -> None:
    if isinstance(process, (behavioral.Receive, behavioral.Send)):
        guards[path] = guard
        return
    if isinstance(process, (behavioral.Assign, behavioral.Skip)):
        return
    if isinstance(process, behavioral.Sequence):
        for index, item in enumerate(process.items):
            _record_guards(item, path + (index,), guard, guards)
        return
    if isinstance(process, behavioral.Parallel):
        for index, branch in enumerate(process.branches):
            _record_guards(branch, path + ('parallel', index), guard, guards)
        return
    if isinstance(process, behavioral.If):
        _record_guards(process.then_branch, path + ('then',), _and(guard, process.condition), guards)
        _record_guards(process.else_branch, path + ('else',), _and(guard, _not(process.condition)), guards)
        return
    raise TypeError(f'unsupported behavioral process {type(process).__name__}')


def _and(guard: behavioral.Expression | None,
         condition: behavioral.Expression) -> behavioral.Expression:
    if guard is None:
        return condition
    return behavioral.Expression(
        'binary', operator='&&', operands=(guard, condition), location=condition.location,
    )


def _not(condition: behavioral.Expression) -> behavioral.Expression:
    return behavioral.Expression('unary', operator='!', operands=(condition,), location=condition.location)
