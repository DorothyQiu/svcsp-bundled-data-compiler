"""Phase 7A: bind Phase 6 choices to abstract structural template contracts.

This pass records template identities, interface contracts, and logical port
connections. It deliberately does not emit RTL or choose concrete controller,
storage, or delay implementations.
"""
from __future__ import annotations

from dataclasses import dataclass, replace
from enum import Enum
import hashlib
import re

from . import behavioral_ir as behavioral
from . import communication_normalization as normalization
from . import dependency_analysis as dependency
from . import microarchitecture_ir as microarchitecture


class StructuralTemplate(str, Enum):
    LINEAR_CONTROLLER = 'linear_controller'
    JOIN_CONTROLLER = 'join_controller'
    CONDITIONAL_RECV_WRAPPER = 'conditional_recv_wrapper'
    CONDITIONAL_SEND_WRAPPER = 'conditional_send_wrapper'
    ABSTRACT_STORAGE = 'abstract_storage'
    SYMBOLIC_MATCHED_DELAY = 'symbolic_matched_delay'


class PortDirection(str, Enum):
    INPUT = 'input'
    OUTPUT = 'output'


class PortSemanticKind(str, Enum):
    HANDSHAKE_REQUEST = 'handshake_request'
    HANDSHAKE_ACKNOWLEDGE = 'handshake_acknowledge'
    PAYLOAD = 'payload'
    ENABLE = 'enable'
    CONTROL = 'control'


class ModulePortRole(str, Enum):
    REQUEST = 'request'
    ACKNOWLEDGE = 'acknowledge'
    PAYLOAD = 'payload'


class SignalDriverKind(str, Enum):
    EXPRESSION = 'expression'
    TEMPLATE_OUTPUT = 'template_output'
    MODULE_INPUT = 'module_input'


@dataclass(frozen=True)
class BoundSignalDriver:
    """The one architectural source permitted to drive a logical signal."""

    kind: SignalDriverKind
    owner: str


@dataclass(frozen=True)
class TemplatePort:
    """A syntax-independent formal template port.

    ``maximum=None`` denotes a topology-sized port family. ``minimum`` records
    required connections without imposing a concrete HDL array representation.
    """

    name: str
    role: str
    direction: PortDirection
    semantic_kind: PortSemanticKind
    minimum: int = 0
    maximum: int | None = None
    formal_name: str | None = None
    family_formal_pattern: str | None = '{name}_{index}'

    def resolved_formal_name(self, index: int) -> str:
        """Return the exact template formal name for this bound member."""
        if index < 0:
            raise TemplateBindingError(f'{self.name} has a negative port-family index')
        if self.maximum is not None:
            if index:
                raise TemplateBindingError(f'{self.name} is not a port family')
            return self.formal_name or self.name
        if self.family_formal_pattern is None:
            raise TemplateBindingError(f'{self.name} has no formal-name pattern')
        try:
            return self.family_formal_pattern.format(name=self.formal_name or self.name, index=index)
        except (KeyError, ValueError) as error:
            raise TemplateBindingError(f'{self.name} has an invalid formal-name pattern') from error


@dataclass(frozen=True)
class TemplateContract:
    template: StructuralTemplate
    ports: tuple[TemplatePort, ...]

    def port(self, name: str) -> TemplatePort | None:
        return next((port for port in self.ports if port.name == name), None)


def _ports(*ports: TemplatePort) -> tuple[TemplatePort, ...]:
    return ports


TEMPLATE_CONTRACTS: tuple[TemplateContract, ...] = (
    TemplateContract(StructuralTemplate.LINEAR_CONTROLLER, _ports(
        TemplatePort('upstream_req', 'upstream handshake request', PortDirection.INPUT,
                     PortSemanticKind.HANDSHAKE_REQUEST),
        TemplatePort('upstream_ack', 'upstream handshake acknowledge', PortDirection.OUTPUT,
                     PortSemanticKind.HANDSHAKE_ACKNOWLEDGE),
        TemplatePort('downstream_req', 'downstream handshake request', PortDirection.OUTPUT,
                     PortSemanticKind.HANDSHAKE_REQUEST),
        TemplatePort('downstream_ack', 'downstream handshake acknowledge', PortDirection.INPUT,
                     PortSemanticKind.HANDSHAKE_ACKNOWLEDGE),
        TemplatePort('local_control', 'local stage control', PortDirection.OUTPUT,
                     PortSemanticKind.CONTROL, minimum=1, maximum=1),
        TemplatePort('storage_control', 'storage control return', PortDirection.INPUT,
                     PortSemanticKind.CONTROL, maximum=1),
        TemplatePort('delayed_control', 'matched-delay control return', PortDirection.INPUT,
                     PortSemanticKind.CONTROL, maximum=1),
    )),
    TemplateContract(StructuralTemplate.JOIN_CONTROLLER, _ports(
        TemplatePort('upstream_req', 'join fan-in handshake request', PortDirection.INPUT,
                     PortSemanticKind.HANDSHAKE_REQUEST),
        TemplatePort('upstream_ack', 'join fan-in handshake acknowledge', PortDirection.OUTPUT,
                     PortSemanticKind.HANDSHAKE_ACKNOWLEDGE),
        TemplatePort('downstream_req', 'downstream handshake request', PortDirection.OUTPUT,
                     PortSemanticKind.HANDSHAKE_REQUEST),
        TemplatePort('downstream_ack', 'downstream handshake acknowledge', PortDirection.INPUT,
                     PortSemanticKind.HANDSHAKE_ACKNOWLEDGE),
        TemplatePort('local_control', 'local join control', PortDirection.OUTPUT,
                     PortSemanticKind.CONTROL, minimum=1, maximum=1),
    )),
    TemplateContract(StructuralTemplate.CONDITIONAL_RECV_WRAPPER, _ports(
        TemplatePort('external_req', 'external channel request', PortDirection.INPUT,
                     PortSemanticKind.HANDSHAKE_REQUEST, minimum=1, maximum=1),
        TemplatePort('external_ack', 'external channel acknowledge', PortDirection.OUTPUT,
                     PortSemanticKind.HANDSHAKE_ACKNOWLEDGE, minimum=1, maximum=1),
        TemplatePort('external_data', 'external channel payload', PortDirection.INPUT,
                     PortSemanticKind.PAYLOAD, minimum=1, maximum=1),
        TemplatePort('body_req', 'BODY-side request', PortDirection.OUTPUT,
                     PortSemanticKind.HANDSHAKE_REQUEST, minimum=1, maximum=1),
        TemplatePort('body_ack', 'BODY-side acknowledge', PortDirection.INPUT,
                     PortSemanticKind.HANDSHAKE_ACKNOWLEDGE, minimum=1, maximum=1),
        TemplatePort('body_data', 'BODY-side payload', PortDirection.OUTPUT,
                     PortSemanticKind.PAYLOAD, minimum=1, maximum=1),
        TemplatePort('enable', 'conditional communication enable', PortDirection.INPUT,
                     PortSemanticKind.ENABLE, minimum=1, maximum=1),
    )),
    TemplateContract(StructuralTemplate.CONDITIONAL_SEND_WRAPPER, _ports(
        TemplatePort('body_req', 'BODY-side request', PortDirection.INPUT,
                     PortSemanticKind.HANDSHAKE_REQUEST, minimum=1, maximum=1),
        TemplatePort('body_ack', 'BODY-side acknowledge', PortDirection.OUTPUT,
                     PortSemanticKind.HANDSHAKE_ACKNOWLEDGE, minimum=1, maximum=1),
        TemplatePort('body_data', 'BODY-side payload', PortDirection.INPUT,
                     PortSemanticKind.PAYLOAD, minimum=1, maximum=1),
        TemplatePort('external_req', 'external channel request', PortDirection.OUTPUT,
                     PortSemanticKind.HANDSHAKE_REQUEST, minimum=1, maximum=1),
        TemplatePort('external_ack', 'external channel acknowledge', PortDirection.INPUT,
                     PortSemanticKind.HANDSHAKE_ACKNOWLEDGE, minimum=1, maximum=1),
        TemplatePort('external_data', 'external channel payload', PortDirection.OUTPUT,
                     PortSemanticKind.PAYLOAD, minimum=1, maximum=1),
        TemplatePort('enable', 'conditional communication enable', PortDirection.INPUT,
                     PortSemanticKind.ENABLE, minimum=1, maximum=1),
    )),
    TemplateContract(StructuralTemplate.ABSTRACT_STORAGE, _ports(
        TemplatePort('data_in', 'storage data input', PortDirection.INPUT,
                     PortSemanticKind.PAYLOAD, minimum=1, maximum=1),
        TemplatePort('data_out', 'storage data output', PortDirection.OUTPUT,
                     PortSemanticKind.PAYLOAD, minimum=1, maximum=1),
        TemplatePort('control_in', 'stage control input', PortDirection.INPUT,
                     PortSemanticKind.CONTROL, minimum=1, maximum=1),
        TemplatePort('control_out', 'storage control output', PortDirection.OUTPUT,
                     PortSemanticKind.CONTROL, minimum=1, maximum=1),
    )),
    TemplateContract(StructuralTemplate.SYMBOLIC_MATCHED_DELAY, _ports(
        TemplatePort('control_in', 'control input to delay', PortDirection.INPUT,
                     PortSemanticKind.CONTROL, minimum=1, maximum=1),
        TemplatePort('control_out', 'delayed control output', PortDirection.OUTPUT,
                     PortSemanticKind.CONTROL, minimum=1, maximum=1),
    )),
)


def template_contract(template: StructuralTemplate) -> TemplateContract:
    contract = next((contract for contract in TEMPLATE_CONTRACTS if contract.template is template), None)
    if contract is None:
        raise TemplateBindingError(f'no contract for template {template.value}')
    return contract


@dataclass(frozen=True)
class BoundLogicalSignal:
    """A logical signal identity independent of a concrete HDL net or type."""

    id: str
    semantic_kind: PortSemanticKind
    source_stage: str | None = None
    target_stage: str | None = None
    dependency_kind: dependency.DependencyKind | None = None
    endpoint: behavioral.ChannelEndpoint | None = None
    variable: behavioral.Variable | None = None
    expression: behavioral.Expression | None = None
    enable: normalization.Enable | None = None
    payload_type: behavioral.PayloadType | None = None
    width: behavioral.PayloadWidth | None = None
    location: behavioral.SourceLocation | None = None
    is_module_port: bool = False
    drivers: tuple[BoundSignalDriver, ...] = ()


@dataclass(frozen=True)
class BoundPortBinding:
    """One indexed formal-template port connected to a logical signal."""

    instance_id: str
    port_name: str
    signal_id: str
    index: int = 0
    formal_name: str = ''


@dataclass(frozen=True)
class BoundModulePort:
    """An exact top-level SystemVerilog port already resolved by Phase 7A."""

    name: str
    direction: PortDirection
    role: ModulePortRole
    semantic_kind: PortSemanticKind
    signal_id: str
    endpoint: behavioral.ChannelEndpoint
    payload_type: behavioral.PayloadType | None = None
    width: behavioral.PayloadWidth | None = None
    location: behavioral.SourceLocation | None = None


@dataclass(frozen=True)
class BoundStorage:
    """An abstract storage-template binding with no primitive choice."""

    id: str
    template: StructuralTemplate
    contract: TemplateContract
    requirement: microarchitecture.StorageRequirement


@dataclass(frozen=True)
class BoundMatchedDelay:
    """A symbolic matched-delay-template binding with no timing value."""

    id: str
    template: StructuralTemplate
    contract: TemplateContract
    requirement: microarchitecture.MatchedDelayRequirement


@dataclass(frozen=True)
class BoundBodyStage:
    """One BODY stage bound to a controller contract independently of wrappers."""

    id: str
    controller_template: StructuralTemplate
    contract: TemplateContract
    operations: tuple[dependency.DependencyNode, ...]
    combinational_logic: tuple[behavioral.Expression, ...]
    storage: BoundStorage | None
    matched_delay: BoundMatchedDelay | None
    handshake_inputs: tuple[microarchitecture.HandshakePort, ...]
    handshake_outputs: tuple[microarchitecture.HandshakePort, ...]
    wrapper_attachments: tuple[str, ...]
    endpoint: behavioral.ChannelEndpoint | None = None
    variable: behavioral.Variable | None = None
    location: behavioral.SourceLocation | None = None


@dataclass(frozen=True)
class BoundWrapper:
    """A conditional communication wrapper bound separately from its BODY stage."""

    id: str
    controller_template: StructuralTemplate
    contract: TemplateContract
    attached_to: str
    endpoint: behavioral.ChannelEndpoint
    enable: normalization.Enable
    location: behavioral.SourceLocation | None = None


@dataclass(frozen=True)
class BoundStructuralDependency:
    """Structural topology retained without adding controller circuitry."""

    source_node: str
    target_node: str
    kind: dependency.DependencyKind
    source_stage: str | None = None
    target_stage: str | None = None


@dataclass(frozen=True)
class BoundStructuralGraph:
    module: str
    contracts: tuple[TemplateContract, ...]
    body_stages: tuple[BoundBodyStage, ...]
    wrappers: tuple[BoundWrapper, ...]
    dependencies: tuple[BoundStructuralDependency, ...]
    metadata: tuple[microarchitecture.MicroarchitectureMetadata, ...]
    signals: tuple[BoundLogicalSignal, ...]
    port_bindings: tuple[BoundPortBinding, ...]
    parameters: tuple[behavioral.Parameter, ...] = ()
    module_ports: tuple[BoundModulePort, ...] = ()

    def stage(self, stage_id: str) -> BoundBodyStage | None:
        return next((stage for stage in self.body_stages if stage.id == stage_id), None)

    def bindings_for(self, instance_id: str, port_name: str) -> tuple[BoundPortBinding, ...]:
        return tuple(binding for binding in self.port_bindings
                     if binding.instance_id == instance_id and binding.port_name == port_name)


class TemplateBindingError(ValueError):
    """A Phase 6 graph cannot be represented by the available abstract templates."""


def _body_template(controller: microarchitecture.ControllerKind) -> StructuralTemplate:
    templates = {
        microarchitecture.ControllerKind.LINEAR: StructuralTemplate.LINEAR_CONTROLLER,
        microarchitecture.ControllerKind.JOIN: StructuralTemplate.JOIN_CONTROLLER,
    }
    try:
        return templates[controller]
    except KeyError as error:
        raise TemplateBindingError(f'{controller.value} is not a BODY controller') from error


def _wrapper_template(controller: microarchitecture.ControllerKind) -> StructuralTemplate:
    templates = {
        microarchitecture.ControllerKind.CONDITIONAL_RECV: StructuralTemplate.CONDITIONAL_RECV_WRAPPER,
        microarchitecture.ControllerKind.CONDITIONAL_SEND: StructuralTemplate.CONDITIONAL_SEND_WRAPPER,
    }
    try:
        return templates[controller]
    except KeyError as error:
        raise TemplateBindingError(f'{controller.value} is not a conditional wrapper controller') from error


def _storage(stage: microarchitecture.MicroarchitectureStage) -> BoundStorage | None:
    if stage.storage.required is not True:
        return None
    return BoundStorage(f'storage_{stage.id}', StructuralTemplate.ABSTRACT_STORAGE,
                        template_contract(StructuralTemplate.ABSTRACT_STORAGE), stage.storage)


def _matched_delay(stage: microarchitecture.MicroarchitectureStage) -> BoundMatchedDelay | None:
    if stage.matched_delay is None:
        return None
    return BoundMatchedDelay(f'matched_delay_{stage.id}', StructuralTemplate.SYMBOLIC_MATCHED_DELAY,
                             template_contract(StructuralTemplate.SYMBOLIC_MATCHED_DELAY), stage.matched_delay)


def _expression_variables(expression: behavioral.Expression) -> tuple[behavioral.Variable, ...]:
    variables: list[behavioral.Variable] = []
    if expression.variable is not None:
        variables.append(expression.variable)
    for operand in expression.operands:
        variables.extend(_expression_variables(operand))
    return tuple(dict.fromkeys(variables))


def _operation_payload(operation: object | None) -> tuple[behavioral.Variable | None, behavioral.Expression | None]:
    if isinstance(operation, behavioral.Assign):
        target = operation.target if isinstance(operation.target, behavioral.Variable) else operation.target.variable
        return target, operation.value
    if isinstance(operation, behavioral.Send):
        variables = _expression_variables(operation.value)
        return (variables[0] if len(variables) == 1 else None), operation.value
    if isinstance(operation, behavioral.Receive):
        target = operation.target if isinstance(operation.target, behavioral.Variable) else operation.target.variable
        return target, None
    if isinstance(operation, normalization.NormalizedSend):
        variables = _expression_variables(operation.value)
        return (variables[0] if len(variables) == 1 else None), operation.value
    if isinstance(operation, normalization.NormalizedReceive):
        target = operation.target if isinstance(operation.target, behavioral.Variable) else operation.target.variable
        return target, None
    return None, None


def _payload_type(variable: behavioral.Variable | None,
                  endpoint: behavioral.ChannelEndpoint | None) -> behavioral.PayloadType | None:
    if variable is not None and endpoint is not None and endpoint.payload_type is not None:
        _require_payload_compatibility('payload connection', endpoint.payload_type, variable.payload_type)
    if endpoint is not None and endpoint.payload_type is not None:
        return endpoint.payload_type
    return variable.payload_type if variable is not None else None


def _require_payload_compatibility(context: str, left: behavioral.PayloadType | None,
                                   right: behavioral.PayloadType | None) -> None:
    if left is not None and right is None:
        raise TemplateBindingError(f'cannot prove payload width for {context}')
    if left is not None and right is not None and not behavioral.payload_types_compatible(left, right):
        raise TemplateBindingError(f'incompatible payload widths for {context}')


def _validate_payload_contexts(graph: microarchitecture.MicroarchitectureGraph) -> None:
    for stage in graph.stages:
        for node in stage.body_operations:
            operation = node.operation
            if isinstance(operation, behavioral.Receive):
                target = operation.target if isinstance(operation.target, behavioral.Variable) else operation.target.variable
                _require_payload_compatibility('Receive', operation.channel.payload_type,
                                               target.payload_type if target else None)
            elif isinstance(operation, behavioral.Send):
                _require_payload_compatibility('Send', operation.channel.payload_type,
                                               behavioral.expression_payload_type(operation.value))
    for item in graph.metadata:
        operation = item.operation
        if isinstance(operation, normalization.NormalizedReceive):
            target = operation.target if isinstance(operation.target, behavioral.Variable) else operation.target.variable
            _require_payload_compatibility('conditional Receive', operation.endpoint.payload_type,
                                           target.payload_type if target else None)
        elif isinstance(operation, normalization.NormalizedSend):
            _require_payload_compatibility('conditional Send', operation.endpoint.payload_type,
                                           behavioral.expression_payload_type(operation.value))


class _Bindings:
    def __init__(self, contracts: dict[str, TemplateContract]) -> None:
        self.contracts = contracts
        self.signals: list[BoundLogicalSignal] = []
        self.bindings: list[BoundPortBinding] = []
        self._signal_ids: set[str] = set()
        self._port_indexes: dict[tuple[str, str], int] = {}

    def signal(self, signal: BoundLogicalSignal) -> BoundLogicalSignal:
        if signal.semantic_kind is PortSemanticKind.PAYLOAD:
            if signal.payload_type is None:
                raise TemplateBindingError(f'cannot establish payload width for {signal.id}')
            if signal.width is not None and signal.width != signal.payload_type.width:
                raise TemplateBindingError(f'payload signal {signal.id} has inconsistent width metadata')
            signal = replace(signal, width=signal.payload_type.width)
        else:
            if signal.width is not None and signal.width != behavioral.ONE_BIT:
                raise TemplateBindingError(f'non-payload signal {signal.id} must be one bit')
            signal = replace(signal, width=behavioral.ONE_BIT)
        if signal.id in self._signal_ids:
            raise TemplateBindingError(f'duplicate logical signal identity {signal.id}')
        self._signal_ids.add(signal.id)
        self.signals.append(signal)
        return signal

    def bind(self, instance_id: str, port_name: str, signal: BoundLogicalSignal) -> None:
        contract = self.contracts.get(instance_id)
        port = contract.port(port_name) if contract else None
        if port is None:
            raise TemplateBindingError(f'{instance_id} has no port {port_name}')
        key = (instance_id, port_name)
        index = self._port_indexes.get(key, 0)
        self._port_indexes[key] = index + 1
        self.bindings.append(BoundPortBinding(instance_id, port_name, signal.id, index,
                                              port.resolved_formal_name(index)))


_PORT_IDENTIFIER = re.compile(r'[^A-Za-z0-9_$]')


class _ModulePorts:
    """Create shared external endpoint signals and their module-port contracts."""

    def __init__(self, bindings: _Bindings) -> None:
        self.bindings = bindings
        self.ports: list[BoundModulePort] = []
        self._by_identity: dict[tuple[behavioral.ChannelEndpoint, str, ModulePortRole], BoundLogicalSignal] = {}
        self._names: set[str] = set()

    @staticmethod
    def _selector_text(expression: behavioral.Expression) -> str:
        if expression.form == 'literal' and expression.value is not None:
            return expression.value
        if expression.form == 'parameter' and expression.parameter is not None:
            return expression.parameter.name
        return expression.form

    def _name(self, endpoint: behavioral.ChannelEndpoint, flow: str, role: ModulePortRole) -> str:
        selector = '_'.join(self._selector_text(item) for item in endpoint.selectors)
        raw = '_'.join(part for part in ('channel', endpoint.name, selector, flow, role.value) if part)
        candidate = _PORT_IDENTIFIER.sub('_', raw)
        if not candidate or candidate[0].isdigit():
            candidate = f'channel_{candidate}'
        if candidate not in self._names:
            self._names.add(candidate)
            return candidate
        digest = hashlib.sha1(repr((endpoint, flow, role)).encode('utf-8')).hexdigest()[:10]
        candidate = f'{candidate}_{digest}'
        if candidate in self._names:
            raise TemplateBindingError(f'cannot create collision-safe module port for {endpoint.name}')
        self._names.add(candidate)
        return candidate

    def external(self, endpoint: behavioral.ChannelEndpoint, flow: str, role: ModulePortRole,
                 direction: PortDirection, semantic_kind: PortSemanticKind,
                 payload_type: behavioral.PayloadType | None,
                 location: behavioral.SourceLocation | None,
                 *, variable: behavioral.Variable | None = None,
                 expression: behavioral.Expression | None = None) -> BoundLogicalSignal:
        key = (endpoint, flow, role)
        existing = self._by_identity.get(key)
        if existing is not None:
            if existing.semantic_kind is not semantic_kind or existing.payload_type != payload_type:
                raise TemplateBindingError(f'external endpoint {endpoint.name} has inconsistent port binding')
            return existing
        name = self._name(endpoint, flow, role)
        signal = self.bindings.signal(BoundLogicalSignal(
            f'module_port_{name}', semantic_kind, endpoint=endpoint, variable=variable,
            expression=expression, payload_type=payload_type, location=location, is_module_port=True,
        ))
        self.ports.append(BoundModulePort(name, direction, role, semantic_kind, signal.id, endpoint,
                                          payload_type, signal.width, location))
        self._by_identity[key] = signal
        return signal


def _handshake_signals(graph: microarchitecture.MicroarchitectureGraph,
                       bindings: _Bindings) -> dict[str, tuple[BoundLogicalSignal, BoundLogicalSignal]]:
    outputs = {port.identity: (stage.id, port) for stage in graph.stages for port in stage.handshake_outputs}
    inputs = {port.identity: (stage.id, port) for stage in graph.stages for port in stage.handshake_inputs}
    if outputs.keys() != inputs.keys() or len(outputs) != sum(len(stage.handshake_outputs) for stage in graph.stages):
        raise TemplateBindingError('handshake topology must have one output and one input per identity')
    signals = {}
    for identity in sorted(outputs):
        source_stage, output = outputs[identity]
        target_stage, input_ = inputs[identity]
        if output.peer_stage != target_stage or input_.peer_stage != source_stage:
            raise TemplateBindingError(f'handshake {identity} has inconsistent peers')
        if output.dependency_kind is not input_.dependency_kind:
            raise TemplateBindingError(f'handshake {identity} has inconsistent dependency kinds')
        request = bindings.signal(BoundLogicalSignal(
            f'{identity}_req', PortSemanticKind.HANDSHAKE_REQUEST, source_stage, target_stage,
            output.dependency_kind,
        ))
        acknowledge = bindings.signal(BoundLogicalSignal(
            f'{identity}_ack', PortSemanticKind.HANDSHAKE_ACKNOWLEDGE, target_stage, source_stage,
            output.dependency_kind,
        ))
        signals[identity] = request, acknowledge
    return signals


def _bind_stage_topology(graph: microarchitecture.MicroarchitectureGraph, bindings: _Bindings,
                         handshakes: dict[str, tuple[BoundLogicalSignal, BoundLogicalSignal]]) -> None:
    for stage in graph.stages:
        local_control = bindings.signal(BoundLogicalSignal(
            f'{stage.id}_local_control', PortSemanticKind.CONTROL, source_stage=stage.id,
            location=stage.location,
        ))
        bindings.bind(stage.id, 'local_control', local_control)
        for port in stage.handshake_inputs:
            request, acknowledge = handshakes[port.identity]
            bindings.bind(stage.id, 'upstream_req', request)
            bindings.bind(stage.id, 'upstream_ack', acknowledge)
        for port in stage.handshake_outputs:
            request, acknowledge = handshakes[port.identity]
            bindings.bind(stage.id, 'downstream_req', request)
            bindings.bind(stage.id, 'downstream_ack', acknowledge)


def _bind_unconditional_external_operations(graph: microarchitecture.MicroarchitectureGraph,
                                            bindings: _Bindings, module_ports: _ModulePorts) -> dict[str, BoundLogicalSignal]:
    payloads: dict[str, BoundLogicalSignal] = {}
    for stage in graph.stages:
        for node in stage.body_operations:
            operation = node.operation
            if isinstance(operation, behavioral.Receive):
                target = operation.target if isinstance(operation.target, behavioral.Variable) else operation.target.variable
                request = module_ports.external(operation.channel, 'receive', ModulePortRole.REQUEST,
                                                PortDirection.INPUT, PortSemanticKind.HANDSHAKE_REQUEST,
                                                None, operation.location)
                acknowledge = module_ports.external(operation.channel, 'receive', ModulePortRole.ACKNOWLEDGE,
                                                    PortDirection.OUTPUT, PortSemanticKind.HANDSHAKE_ACKNOWLEDGE,
                                                    None, operation.location)
                payload = module_ports.external(operation.channel, 'receive', ModulePortRole.PAYLOAD,
                                                PortDirection.INPUT, PortSemanticKind.PAYLOAD,
                                                operation.channel.payload_type, operation.location, variable=target)
                bindings.bind(stage.id, 'upstream_req', request)
                bindings.bind(stage.id, 'upstream_ack', acknowledge)
                payloads[stage.id] = payload
            if isinstance(operation, behavioral.Send):
                variable, expression = _operation_payload(operation)
                request = module_ports.external(operation.channel, 'send', ModulePortRole.REQUEST,
                                                PortDirection.OUTPUT, PortSemanticKind.HANDSHAKE_REQUEST,
                                                None, operation.location)
                acknowledge = module_ports.external(operation.channel, 'send', ModulePortRole.ACKNOWLEDGE,
                                                    PortDirection.INPUT, PortSemanticKind.HANDSHAKE_ACKNOWLEDGE,
                                                    None, operation.location)
                payload = module_ports.external(operation.channel, 'send', ModulePortRole.PAYLOAD,
                                                PortDirection.OUTPUT, PortSemanticKind.PAYLOAD,
                                                operation.channel.payload_type, operation.location,
                                                variable=variable)
                bindings.bind(stage.id, 'downstream_req', request)
                bindings.bind(stage.id, 'downstream_ack', acknowledge)
                payloads[stage.id] = payload
    return payloads


def _bind_wrapper_ports(graph: microarchitecture.MicroarchitectureGraph, bindings: _Bindings,
                        module_ports: _ModulePorts,
                        stages: dict[str, BoundBodyStage]) -> dict[str, tuple[BoundLogicalSignal, behavioral.Expression]]:
    operations = {item.id: item.operation for item in graph.metadata if item.kind is dependency.NodeKind.WRAPPER}
    send_payloads: dict[str, tuple[BoundLogicalSignal, behavioral.Expression]] = {}
    for wrapper in graph.wrappers:
        operation = operations.get(wrapper.id)
        if not isinstance(operation, (normalization.NormalizedReceive, normalization.NormalizedSend)):
            raise TemplateBindingError(f'wrapper {wrapper.id} has no normalized communication operation')
        variable, expression = _operation_payload(operation)
        enable = bindings.signal(BoundLogicalSignal(
            f'{wrapper.id}_enable', PortSemanticKind.ENABLE, enable=wrapper.enable, location=wrapper.location,
        ))
        bindings.bind(wrapper.id, 'enable', enable)
        receiving = isinstance(operation, normalization.NormalizedReceive)
        flow = 'receive' if receiving else 'send'
        external_req = module_ports.external(wrapper.endpoint, flow, ModulePortRole.REQUEST,
                                             PortDirection.INPUT if receiving else PortDirection.OUTPUT,
                                             PortSemanticKind.HANDSHAKE_REQUEST, None, wrapper.location)
        external_ack = module_ports.external(wrapper.endpoint, flow, ModulePortRole.ACKNOWLEDGE,
                                             PortDirection.OUTPUT if receiving else PortDirection.INPUT,
                                             PortSemanticKind.HANDSHAKE_ACKNOWLEDGE, None, wrapper.location)
        external_payload = module_ports.external(wrapper.endpoint, flow, ModulePortRole.PAYLOAD,
                                                 PortDirection.INPUT if receiving else PortDirection.OUTPUT,
                                                 PortSemanticKind.PAYLOAD, wrapper.endpoint.payload_type,
                                                 wrapper.location, variable=variable)
        body_req = bindings.signal(BoundLogicalSignal(
            f'{wrapper.id}_body_req', PortSemanticKind.HANDSHAKE_REQUEST,
            source_stage=wrapper.attached_to if not receiving else None,
            target_stage=wrapper.attached_to if receiving else None,
            endpoint=wrapper.endpoint, location=wrapper.location,
        ))
        body_ack = bindings.signal(BoundLogicalSignal(
            f'{wrapper.id}_body_ack', PortSemanticKind.HANDSHAKE_ACKNOWLEDGE,
            source_stage=wrapper.attached_to if receiving else None,
            target_stage=wrapper.attached_to if not receiving else None,
            endpoint=wrapper.endpoint, location=wrapper.location,
        ))
        body_payload = bindings.signal(BoundLogicalSignal(
            f'{wrapper.id}_body_payload', PortSemanticKind.PAYLOAD,
            source_stage=wrapper.attached_to if not receiving else None,
            target_stage=wrapper.attached_to if receiving else None,
            endpoint=wrapper.endpoint, variable=variable,
            expression=None if (not receiving and stages[wrapper.attached_to].storage) else expression,
            location=wrapper.location,
            payload_type=wrapper.endpoint.payload_type,
        ))
        if receiving:
            for port, signal in (('external_req', external_req), ('external_ack', external_ack),
                                 ('external_data', external_payload), ('body_req', body_req),
                                 ('body_ack', body_ack), ('body_data', body_payload)):
                bindings.bind(wrapper.id, port, signal)
            bindings.bind(wrapper.attached_to, 'upstream_req', body_req)
            bindings.bind(wrapper.attached_to, 'upstream_ack', body_ack)
        else:
            for port, signal in (('body_req', body_req), ('body_ack', body_ack), ('body_data', body_payload),
                                 ('external_req', external_req), ('external_ack', external_ack),
                                 ('external_data', external_payload)):
                bindings.bind(wrapper.id, port, signal)
            bindings.bind(wrapper.attached_to, 'downstream_req', body_req)
            bindings.bind(wrapper.attached_to, 'downstream_ack', body_ack)
            assert expression is not None
            send_payloads[wrapper.attached_to] = (body_payload, expression)
    return send_payloads


def _bind_storage_and_delays(stages: tuple[BoundBodyStage, ...], wrappers: tuple[BoundWrapper, ...],
                             bindings: _Bindings,
                             external_payloads: dict[str, BoundLogicalSignal],
                             wrapper_send_payloads: dict[str, tuple[BoundLogicalSignal, behavioral.Expression]]) -> None:
    wrapper_endpoints = {wrapper.attached_to: wrapper.endpoint for wrapper in wrappers}
    for stage in stages:
        control = next(signal for signal in bindings.signals if signal.id == f'{stage.id}_local_control')
        if stage.storage:
            payload_type = _payload_type(stage.variable, stage.endpoint or wrapper_endpoints.get(stage.id))
            variable = stage.variable
            operation = stage.operations[0].operation if stage.operations else None
            wrapper_payload = wrapper_send_payloads.get(stage.id)
            if wrapper_payload is not None:
                data_out, expression = wrapper_payload
                variables = _expression_variables(expression)
                variable = variables[0] if len(variables) == 1 else None
                data_in = bindings.signal(BoundLogicalSignal(
                    f'{stage.storage.id}_data_in', PortSemanticKind.PAYLOAD, source_stage=stage.id,
                    variable=variable, expression=expression, payload_type=payload_type, location=stage.location,
                ))
            elif isinstance(operation, behavioral.Receive):
                data_in = external_payloads.get(stage.id)
                if data_in is None:
                    raise TemplateBindingError(f'{stage.id} receive has no external payload binding')
            else:
                variable, expression = _operation_payload(operation)
                if expression is None:
                    raise TemplateBindingError(f'{stage.id} storage has no explicit datapath source')
                data_in = bindings.signal(BoundLogicalSignal(
                    f'{stage.storage.id}_data_in', PortSemanticKind.PAYLOAD, source_stage=stage.id,
                    variable=variable, expression=expression, payload_type=payload_type, location=stage.location,
                ))
            if wrapper_payload is not None:
                pass
            elif isinstance(operation, behavioral.Send):
                data_out = external_payloads.get(stage.id)
                if data_out is None:
                    raise TemplateBindingError(f'{stage.id} send has no external payload binding')
            else:
                data_out = bindings.signal(BoundLogicalSignal(
                    f'{stage.storage.id}_data_out', PortSemanticKind.PAYLOAD, target_stage=stage.id,
                    variable=stage.variable, payload_type=payload_type, location=stage.location,
                ))
            control_out = bindings.signal(BoundLogicalSignal(
                f'{stage.storage.id}_control_out', PortSemanticKind.CONTROL, target_stage=stage.id,
                location=stage.location,
            ))
            bindings.bind(stage.id, 'storage_control', control_out)
            bindings.bind(stage.storage.id, 'data_in', data_in)
            bindings.bind(stage.storage.id, 'data_out', data_out)
            bindings.bind(stage.storage.id, 'control_in', control)
            bindings.bind(stage.storage.id, 'control_out', control_out)
        if stage.matched_delay:
            delayed_control = bindings.signal(BoundLogicalSignal(
                f'{stage.matched_delay.id}_control_out', PortSemanticKind.CONTROL, target_stage=stage.id,
                location=stage.location,
            ))
            bindings.bind(stage.id, 'delayed_control', delayed_control)
            bindings.bind(stage.matched_delay.id, 'control_in', control)
            bindings.bind(stage.matched_delay.id, 'control_out', delayed_control)


def _validate_bindings(stages: tuple[BoundBodyStage, ...], wrappers: tuple[BoundWrapper, ...],
                       bindings: _Bindings) -> None:
    contracts = {stage.id: stage.contract for stage in stages}
    contracts.update({wrapper.id: wrapper.contract for wrapper in wrappers})
    for stage in stages:
        if stage.storage:
            contracts[stage.storage.id] = stage.storage.contract
        if stage.matched_delay:
            contracts[stage.matched_delay.id] = stage.matched_delay.contract
    signals = {signal.id: signal for signal in bindings.signals}
    counts: dict[tuple[str, str], int] = {}
    seen: set[tuple[str, str, int]] = set()
    for binding in bindings.bindings:
        contract = contracts.get(binding.instance_id)
        if contract is None:
            raise TemplateBindingError(f'port binding refers to unknown instance {binding.instance_id}')
        port = contract.port(binding.port_name)
        if port is None:
            raise TemplateBindingError(f'{binding.instance_id} has no port {binding.port_name}')
        signal = signals.get(binding.signal_id)
        if signal is None:
            raise TemplateBindingError(f'port binding refers to unknown signal {binding.signal_id}')
        if signal.semantic_kind is not port.semantic_kind:
            raise TemplateBindingError(f'{binding.instance_id}.{binding.port_name} has incompatible signal kind')
        if binding.formal_name != port.resolved_formal_name(binding.index):
            raise TemplateBindingError(f'{binding.instance_id}.{binding.port_name} has an unresolved formal name')
        key = (binding.instance_id, binding.port_name, binding.index)
        if key in seen:
            raise TemplateBindingError(f'duplicate port binding for {binding.instance_id}.{binding.port_name}[{binding.index}]')
        seen.add(key)
        counts[(binding.instance_id, binding.port_name)] = counts.get((binding.instance_id, binding.port_name), 0) + 1
    for instance_id, contract in contracts.items():
        for port in contract.ports:
            count = counts.get((instance_id, port.name), 0)
            if count < port.minimum or (port.maximum is not None and count > port.maximum):
                raise TemplateBindingError(f'{instance_id}.{port.name} has {count} bindings outside its contract')


def _resolve_driver_ownership(signals: tuple[BoundLogicalSignal, ...],
                              bindings: tuple[BoundPortBinding, ...],
                              contracts: dict[str, TemplateContract],
                              module_ports: tuple[BoundModulePort, ...]) -> tuple[BoundLogicalSignal, ...]:
    """Record and validate every source before RTL emission.

    A port may have many readers. A logical signal must have one source at
    most; module outputs require exactly one source because this compiler has
    no shared-channel mux or arbitration architecture yet.
    """
    drivers: dict[str, list[BoundSignalDriver]] = {signal.id: [] for signal in signals}
    for signal in signals:
        if signal.expression is not None or signal.enable is not None:
            drivers[signal.id].append(BoundSignalDriver(SignalDriverKind.EXPRESSION, signal.id))
    for port in module_ports:
        if port.direction is PortDirection.INPUT:
            drivers[port.signal_id].append(BoundSignalDriver(SignalDriverKind.MODULE_INPUT, port.name))
    for item in bindings:
        port = contracts[item.instance_id].port(item.port_name)
        assert port is not None
        if port.direction is PortDirection.OUTPUT:
            drivers[item.signal_id].append(BoundSignalDriver(
                SignalDriverKind.TEMPLATE_OUTPUT, f'{item.instance_id}.{item.formal_name}',
            ))
    for signal_id, sources in drivers.items():
        if len(sources) > 1:
            owners = ', '.join(source.owner for source in sources)
            raise TemplateBindingError(f'signal {signal_id} has multiple drivers: {owners}')
    for port in module_ports:
        if port.direction is PortDirection.OUTPUT and len(drivers[port.signal_id]) != 1:
            raise TemplateBindingError(f'module output {port.name} must have exactly one internal driver')
    return tuple(replace(signal, drivers=tuple(drivers[signal.id])) for signal in signals)


def bind_templates(graph: microarchitecture.MicroarchitectureGraph) -> BoundStructuralGraph:
    """Bind a microarchitecture graph to abstract template contracts and logical ports."""
    if not isinstance(graph, microarchitecture.MicroarchitectureGraph):
        raise TemplateBindingError('expected a MicroarchitectureGraph')
    _validate_payload_contexts(graph)
    stage_ids = {stage.id for stage in graph.stages}
    if len(stage_ids) != len(graph.stages):
        raise TemplateBindingError('microarchitecture graph has duplicate stage identities')
    if any(wrapper.attached_to not in stage_ids for wrapper in graph.wrappers):
        raise TemplateBindingError('wrapper refers to an unknown BODY stage')
    if any(wrapper.id not in graph.stage(wrapper.attached_to).wrapper_attachments for wrapper in graph.wrappers):
        raise TemplateBindingError('wrapper attachment is missing from its BODY stage')

    body_stages = tuple(BoundBodyStage(
        id=stage.id,
        controller_template=_body_template(stage.controller),
        contract=template_contract(_body_template(stage.controller)),
        operations=stage.body_operations,
        combinational_logic=stage.combinational_logic,
        storage=_storage(stage),
        matched_delay=_matched_delay(stage),
        handshake_inputs=stage.handshake_inputs,
        handshake_outputs=stage.handshake_outputs,
        wrapper_attachments=stage.wrapper_attachments,
        endpoint=stage.endpoint,
        variable=stage.variable,
        location=stage.location,
    ) for stage in graph.stages)
    wrappers = tuple(BoundWrapper(
        id=wrapper.id,
        controller_template=_wrapper_template(wrapper.controller),
        contract=template_contract(_wrapper_template(wrapper.controller)),
        attached_to=wrapper.attached_to,
        endpoint=wrapper.endpoint,
        enable=wrapper.enable,
        location=wrapper.location,
    ) for wrapper in graph.wrappers)
    contracts = {stage.id: stage.contract for stage in body_stages}
    contracts.update({wrapper.id: wrapper.contract for wrapper in wrappers})
    for stage in body_stages:
        if stage.storage:
            contracts[stage.storage.id] = stage.storage.contract
        if stage.matched_delay:
            contracts[stage.matched_delay.id] = stage.matched_delay.contract
    bindings = _Bindings(contracts)
    module_ports = _ModulePorts(bindings)
    handshakes = _handshake_signals(graph, bindings)
    _bind_stage_topology(graph, bindings, handshakes)
    external_payloads = _bind_unconditional_external_operations(graph, bindings, module_ports)
    wrapper_send_payloads = _bind_wrapper_ports(graph, bindings, module_ports,
                                                {stage.id: stage for stage in body_stages})
    _bind_storage_and_delays(body_stages, wrappers, bindings, external_payloads, wrapper_send_payloads)
    _validate_bindings(body_stages, wrappers, bindings)
    resolved_signals = _resolve_driver_ownership(tuple(bindings.signals), tuple(bindings.bindings), contracts,
                                                  tuple(module_ports.ports))
    dependencies = tuple(BoundStructuralDependency(
        edge.source_node, edge.target_node, edge.kind, edge.source_stage, edge.target_stage,
    ) for edge in graph.dependencies)
    return BoundStructuralGraph(graph.module, TEMPLATE_CONTRACTS, body_stages, wrappers, dependencies,
                                graph.metadata, resolved_signals, tuple(bindings.bindings), graph.parameters,
                                tuple(sorted(module_ports.ports, key=lambda port: port.name)))
