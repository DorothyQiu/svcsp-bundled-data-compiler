"""Phase 3: normalize conditional external communication.

The result remains a behavioral representation.  It introduces symbolic enable
identities and wrapper semantics, without selecting channels, controllers, or
any RTL implementation.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import TypeAlias

from . import behavioral_ir as behavioral


@dataclass(frozen=True)
class Enable:
    """A unique symbolic enable value for one conditional communication."""

    name: str
    occurrence: int
    condition: behavioral.Expression
    location: behavioral.SourceLocation | None = field(default=None, compare=False)


@dataclass(frozen=True)
class DummyToken:
    """An internal token supplied by a disabled conditional receive."""

    enable: Enable
    data_is_valid: bool = False


@dataclass(frozen=True)
class NormalizedReceive:
    """External receive wrapper with an unconditional BODY-side token."""

    enable: Enable
    endpoint: behavioral.ChannelEndpoint
    target: behavioral.Variable | behavioral.Expression
    disabled_token: DummyToken
    consumes_external_when_enabled: bool = True
    forwards_real_data_when_enabled: bool = True
    acknowledges_external_when_disabled: bool = False
    provides_body_token_when_disabled: bool = True
    location: behavioral.SourceLocation | None = field(default=None, compare=False)


@dataclass(frozen=True)
class NormalizedSend:
    """External send wrapper that always consumes its BODY-side token."""

    enable: Enable
    endpoint: behavioral.ChannelEndpoint
    value: behavioral.Expression
    consumes_body_token_always: bool = True
    communicates_externally_when_enabled: bool = True
    communicates_externally_when_disabled: bool = False
    location: behavioral.SourceLocation | None = field(default=None, compare=False)


Wrapper: TypeAlias = NormalizedReceive | NormalizedSend


@dataclass(frozen=True)
class NormalizedModule:
    """Phase 3 result: a communication-regular BODY plus external wrappers."""

    name: str
    body: behavioral.Process
    channels: tuple[behavioral.ChannelEndpoint, ...]
    variables: tuple[behavioral.Variable, ...]
    enables: tuple[Enable, ...]
    wrappers: tuple[Wrapper, ...]
    location: behavioral.SourceLocation | None = None


class NormalizationError(ValueError):
    """A Behavioral CSP IR object could not be normalized."""


def _not(expression: behavioral.Expression) -> behavioral.Expression:
    return behavioral.Expression('unary', operator='!', operands=(expression,), location=expression.location)


def _and(left: behavioral.Expression | None,
         right: behavioral.Expression) -> behavioral.Expression:
    if left is None:
        return right
    return behavioral.Expression('binary', operator='&&', operands=(left, right), location=right.location)


class _Normalizer:
    def __init__(self) -> None:
        self.enables: list[Enable] = []
        self.wrappers: list[Wrapper] = []

    def enable(self, condition: behavioral.Expression,
               location: behavioral.SourceLocation | None) -> Enable:
        occurrence = len(self.enables)
        enable = Enable(f'enable_{occurrence}', occurrence, condition, location)
        self.enables.append(enable)
        return enable

    def process(self, process: behavioral.Process,
                guard: behavioral.Expression | None = None) -> behavioral.Process:
        if isinstance(process, behavioral.Sequence):
            return behavioral.Sequence(tuple(self.process(item, guard) for item in process.items), process.location)
        if isinstance(process, behavioral.Parallel):
            return behavioral.Parallel(tuple(self.process(branch, guard) for branch in process.branches), process.location)
        if isinstance(process, behavioral.If):
            return behavioral.If(
                process.condition,
                self.process(process.then_branch, _and(guard, process.condition)),
                self.process(process.else_branch, _and(guard, _not(process.condition))),
                process.location,
            )
        if isinstance(process, behavioral.Receive) and guard is not None:
            enable = self.enable(guard, process.location)
            self.wrappers.append(NormalizedReceive(
                enable, process.channel, process.target, DummyToken(enable), location=process.location,
            ))
            return behavioral.Skip(process.location)
        if isinstance(process, behavioral.Send) and guard is not None:
            enable = self.enable(guard, process.location)
            self.wrappers.append(NormalizedSend(enable, process.channel, process.value, location=process.location))
            return behavioral.Skip(process.location)
        if isinstance(process, (behavioral.Send, behavioral.Receive, behavioral.Assign, behavioral.Skip)):
            return process
        raise NormalizationError(f'unsupported Behavioral CSP node {type(process).__name__}')


def normalize_communication(module: behavioral.BehavioralModule) -> NormalizedModule:
    """Isolate conditional external communication in enable-controlled wrappers."""
    if not isinstance(module, behavioral.BehavioralModule):
        raise NormalizationError('expected a BehavioralModule')
    normalizer = _Normalizer()
    return NormalizedModule(
        name=module.name,
        body=normalizer.process(module.body),
        channels=module.channels,
        variables=module.variables,
        enables=tuple(normalizer.enables),
        wrappers=tuple(normalizer.wrappers),
        location=module.location,
    )
