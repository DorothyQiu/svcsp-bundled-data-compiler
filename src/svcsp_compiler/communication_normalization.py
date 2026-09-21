"""Phase 3: normalize conditional external communication.

The result remains a behavioral representation.  It introduces symbolic enable
identities and wrapper semantics, without selecting channels, controllers, or
any RTL implementation.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
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


class BodyChannelDirection(str, Enum):
    """Direction of an internal token channel relative to the normalized BODY."""

    FROM_BODY = 'from_body'
    INTO_BODY = 'into_body'


@dataclass(frozen=True)
class CommunicationSite:
    """A source-order anchor for one hoisted conditional communication.

    It is a Phase 3 marker, not a behavioral ``Skip``. Its identity, rather
    than its source location, is the link to the BODY channel and wrapper.
    """

    id: str
    enable: Enable
    location: behavioral.SourceLocation | None = field(default=None, compare=False)

    def __post_init__(self) -> None:
        if not self.id:
            raise ValueError('communication site requires an identity and enable')


@dataclass(frozen=True)
class BodyChannel:
    """An internal unconditional token channel shared by BODY and wrapper."""

    id: str
    site: CommunicationSite
    enable: Enable
    endpoint: behavioral.ChannelEndpoint
    direction: BodyChannelDirection
    location: behavioral.SourceLocation | None = field(default=None, compare=False)

    def __post_init__(self) -> None:
        if self.site.enable is not self.enable:
            raise ValueError('body channel enable must be the site enable')


@dataclass(frozen=True)
class BodySend:
    """An authoritative every-iteration BODY token sent to a SEND wrapper."""

    site: CommunicationSite
    channel: BodyChannel
    enable: Enable
    value: behavioral.Expression
    payload_valid_when: Enable
    location: behavioral.SourceLocation | None = field(default=None, compare=False)

    def __post_init__(self) -> None:
        if (self.channel.direction is not BodyChannelDirection.FROM_BODY or
                self.site is not self.channel.site or self.enable is not self.channel.enable or
                self.payload_valid_when is not self.enable):
            raise ValueError('body send must share its site, channel, and enable identities')


@dataclass(frozen=True)
class BodyReceive:
    """An authoritative every-iteration BODY token received from a RECV wrapper."""

    site: CommunicationSite
    channel: BodyChannel
    enable: Enable
    target: behavioral.Variable | behavioral.Expression
    disabled_token: DummyToken
    data_valid_when: Enable
    location: behavioral.SourceLocation | None = field(default=None, compare=False)

    def __post_init__(self) -> None:
        if (self.channel.direction is not BodyChannelDirection.INTO_BODY or
                self.site is not self.channel.site or self.enable is not self.channel.enable or
                self.data_valid_when is not self.enable or
                self.disabled_token.enable is not self.enable):
            raise ValueError('body receive must share its site, channel, and enable identities')


@dataclass(frozen=True)
class NormalizedReceive:
    """External receive wrapper with an unconditional BODY-side token."""

    enable: Enable
    endpoint: behavioral.ChannelEndpoint
    target: behavioral.Variable | behavioral.Expression
    site: CommunicationSite
    body_channel: BodyChannel
    disabled_token: DummyToken
    consumes_external_when_enabled: bool = True
    forwards_real_data_when_enabled: bool = True
    acknowledges_external_when_disabled: bool = False
    provides_body_token_when_disabled: bool = True
    location: behavioral.SourceLocation | None = field(default=None, compare=False)

    def __post_init__(self) -> None:
        if (self.site.enable is not self.enable or self.body_channel.site is not self.site or
                self.body_channel.enable is not self.enable or self.body_channel.endpoint is not self.endpoint or
                self.body_channel.direction is not BodyChannelDirection.INTO_BODY or
                self.disabled_token.enable is not self.enable):
            raise ValueError('normalized receive must share its site, channel, and enable identities')


@dataclass(frozen=True)
class NormalizedSend:
    """External send wrapper that always consumes its BODY-side token."""

    enable: Enable
    endpoint: behavioral.ChannelEndpoint
    value: behavioral.Expression
    site: CommunicationSite
    body_channel: BodyChannel
    consumes_body_token_always: bool = True
    communicates_externally_when_enabled: bool = True
    communicates_externally_when_disabled: bool = False
    location: behavioral.SourceLocation | None = field(default=None, compare=False)

    def __post_init__(self) -> None:
        if (self.site.enable is not self.enable or self.body_channel.site is not self.site or
                self.body_channel.enable is not self.enable or self.body_channel.endpoint is not self.endpoint or
                self.body_channel.direction is not BodyChannelDirection.FROM_BODY):
            raise ValueError('normalized send must share its site, channel, and enable identities')


Wrapper: TypeAlias = NormalizedReceive | NormalizedSend
BodyCommunication: TypeAlias = BodySend | BodyReceive
NormalizedProcess: TypeAlias = behavioral.Process | CommunicationSite


@dataclass(frozen=True)
class NormalizedModule:
    """Phase 3 result: a communication-regular BODY plus external wrappers."""

    name: str
    body: NormalizedProcess
    channels: tuple[behavioral.ChannelEndpoint, ...]
    variables: tuple[behavioral.Variable, ...]
    parameters: tuple[behavioral.Parameter, ...]
    enables: tuple[Enable, ...]
    wrappers: tuple[Wrapper, ...]
    communication_sites: tuple[CommunicationSite, ...] = ()
    body_channels: tuple[BodyChannel, ...] = ()
    body_communications: tuple[BodyCommunication, ...] = ()
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
        self.sites: list[CommunicationSite] = []
        self.body_channels: list[BodyChannel] = []
        self.body_communications: list[BodyCommunication] = []

    def enable(self, condition: behavioral.Expression,
               location: behavioral.SourceLocation | None) -> Enable:
        occurrence = len(self.enables)
        enable = Enable(f'enable_{occurrence}', occurrence, condition, location)
        self.enables.append(enable)
        return enable

    def site(self, enable: Enable, location: behavioral.SourceLocation | None) -> CommunicationSite:
        site = CommunicationSite(f'communication_site_{enable.occurrence}', enable, location)
        self.sites.append(site)
        return site

    def channel(self, site: CommunicationSite, endpoint: behavioral.ChannelEndpoint,
                direction: BodyChannelDirection,
                location: behavioral.SourceLocation | None) -> BodyChannel:
        channel = BodyChannel(f'body_channel_{site.enable.occurrence}', site, site.enable,
                              endpoint, direction, location)
        self.body_channels.append(channel)
        return channel

    def process(self, process: behavioral.Process,
                guard: behavioral.Expression | None = None) -> NormalizedProcess:
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
            site = self.site(enable, process.location)
            channel = self.channel(site, process.channel, BodyChannelDirection.INTO_BODY, process.location)
            dummy = DummyToken(enable)
            self.wrappers.append(NormalizedReceive(
                enable, process.channel, process.target, site, channel, dummy, location=process.location,
            ))
            self.body_communications.append(BodyReceive(
                site, channel, enable, process.target, dummy, enable, process.location,
            ))
            return site
        if isinstance(process, behavioral.Send) and guard is not None:
            enable = self.enable(guard, process.location)
            site = self.site(enable, process.location)
            channel = self.channel(site, process.channel, BodyChannelDirection.FROM_BODY, process.location)
            self.wrappers.append(NormalizedSend(
                enable, process.channel, process.value, site, channel, location=process.location,
            ))
            self.body_communications.append(BodySend(
                site, channel, enable, process.value, enable, process.location,
            ))
            return site
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
        parameters=module.parameters,
        enables=tuple(normalizer.enables),
        wrappers=tuple(normalizer.wrappers),
        communication_sites=tuple(normalizer.sites),
        body_channels=tuple(normalizer.body_channels),
        body_communications=tuple(normalizer.body_communications),
        location=module.location,
    )
