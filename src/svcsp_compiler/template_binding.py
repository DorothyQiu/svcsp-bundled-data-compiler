"""Phase 7A: bind Phase 6 choices to abstract structural template contracts.

This pass records template identities, interface contracts, and logical port
connections. It deliberately does not emit RTL or choose concrete controller,
storage, or delay implementations.
"""
from __future__ import annotations

from dataclasses import dataclass, replace
from enum import Enum

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
        TemplatePort('payload_in', 'upstream or external payload', PortDirection.INPUT,
                     PortSemanticKind.PAYLOAD),
        TemplatePort('payload_out', 'downstream or external payload', PortDirection.OUTPUT,
                     PortSemanticKind.PAYLOAD),
        TemplatePort('local_control', 'local stage control', PortDirection.OUTPUT,
                     PortSemanticKind.CONTROL, minimum=1, maximum=1),
        TemplatePort('storage_data_in', 'storage payload input', PortDirection.OUTPUT,
                     PortSemanticKind.PAYLOAD, maximum=1),
        TemplatePort('storage_data_out', 'storage payload output', PortDirection.INPUT,
                     PortSemanticKind.PAYLOAD, maximum=1),
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


@dataclass(frozen=True)
class BoundPortBinding:
    """One indexed formal-template port connected to a logical signal."""

    instance_id: str
    port_name: str
    signal_id: str
    index: int = 0


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
    def __init__(self) -> None:
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
        key = (instance_id, port_name)
        index = self._port_indexes.get(key, 0)
        self._port_indexes[key] = index + 1
        self.bindings.append(BoundPortBinding(instance_id, port_name, signal.id, index))


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


def _bind_data_dependencies(graph: microarchitecture.MicroarchitectureGraph, bindings: _Bindings) -> None:
    stages = {stage.id: stage for stage in graph.stages}
    for edge in graph.dependencies:
        if edge.kind is not dependency.DependencyKind.DATA or not edge.source_stage or not edge.target_stage:
            continue
        source = stages[edge.source_stage]
        variable = source.variable
        expression = None
        if source.body_operations:
            variable, expression = _operation_payload(source.body_operations[0].operation)
        signal = bindings.signal(BoundLogicalSignal(
            f'payload_{edge.source_node}_{edge.target_node}', PortSemanticKind.PAYLOAD,
            edge.source_stage, edge.target_stage, edge.kind, variable=variable, expression=expression,
            payload_type=_payload_type(variable, source.endpoint),
            location=source.location,
        ))
        bindings.bind(source.id, 'payload_out', signal)
        bindings.bind(edge.target_stage, 'payload_in', signal)


def _bind_unconditional_external_operations(graph: microarchitecture.MicroarchitectureGraph,
                                            bindings: _Bindings) -> None:
    for stage in graph.stages:
        for node in stage.body_operations:
            operation = node.operation
            if isinstance(operation, behavioral.Receive):
                request = bindings.signal(BoundLogicalSignal(
                    f'{stage.id}_external_req', PortSemanticKind.HANDSHAKE_REQUEST,
                    target_stage=stage.id, endpoint=operation.channel, location=operation.location,
                ))
                acknowledge = bindings.signal(BoundLogicalSignal(
                    f'{stage.id}_external_ack', PortSemanticKind.HANDSHAKE_ACKNOWLEDGE,
                    source_stage=stage.id, endpoint=operation.channel, location=operation.location,
                ))
                payload = bindings.signal(BoundLogicalSignal(
                    f'{stage.id}_external_payload', PortSemanticKind.PAYLOAD, target_stage=stage.id,
                    endpoint=operation.channel,
                    variable=operation.target if isinstance(operation.target, behavioral.Variable) else operation.target.variable,
                    payload_type=operation.channel.payload_type,
                    location=operation.location,
                ))
                bindings.bind(stage.id, 'upstream_req', request)
                bindings.bind(stage.id, 'upstream_ack', acknowledge)
                bindings.bind(stage.id, 'payload_in', payload)
            if isinstance(operation, behavioral.Send):
                variable, expression = _operation_payload(operation)
                request = bindings.signal(BoundLogicalSignal(
                    f'{stage.id}_external_req', PortSemanticKind.HANDSHAKE_REQUEST,
                    source_stage=stage.id, endpoint=operation.channel, location=operation.location,
                ))
                acknowledge = bindings.signal(BoundLogicalSignal(
                    f'{stage.id}_external_ack', PortSemanticKind.HANDSHAKE_ACKNOWLEDGE,
                    target_stage=stage.id, endpoint=operation.channel, location=operation.location,
                ))
                payload = bindings.signal(BoundLogicalSignal(
                    f'{stage.id}_external_payload', PortSemanticKind.PAYLOAD, source_stage=stage.id,
                    endpoint=operation.channel, variable=variable, expression=expression, location=operation.location,
                    payload_type=operation.channel.payload_type,
                ))
                bindings.bind(stage.id, 'downstream_req', request)
                bindings.bind(stage.id, 'downstream_ack', acknowledge)
                bindings.bind(stage.id, 'payload_out', payload)


def _bind_wrapper_ports(graph: microarchitecture.MicroarchitectureGraph, bindings: _Bindings) -> None:
    operations = {item.id: item.operation for item in graph.metadata if item.kind is dependency.NodeKind.WRAPPER}
    for wrapper in graph.wrappers:
        operation = operations.get(wrapper.id)
        if not isinstance(operation, (normalization.NormalizedReceive, normalization.NormalizedSend)):
            raise TemplateBindingError(f'wrapper {wrapper.id} has no normalized communication operation')
        variable, expression = _operation_payload(operation)
        enable = bindings.signal(BoundLogicalSignal(
            f'{wrapper.id}_enable', PortSemanticKind.ENABLE, enable=wrapper.enable, location=wrapper.location,
        ))
        bindings.bind(wrapper.id, 'enable', enable)
        external_req = bindings.signal(BoundLogicalSignal(
            f'{wrapper.id}_external_req', PortSemanticKind.HANDSHAKE_REQUEST,
            endpoint=wrapper.endpoint, location=wrapper.location,
        ))
        external_ack = bindings.signal(BoundLogicalSignal(
            f'{wrapper.id}_external_ack', PortSemanticKind.HANDSHAKE_ACKNOWLEDGE,
            endpoint=wrapper.endpoint, location=wrapper.location,
        ))
        external_payload = bindings.signal(BoundLogicalSignal(
            f'{wrapper.id}_external_payload', PortSemanticKind.PAYLOAD, endpoint=wrapper.endpoint,
            variable=variable, expression=expression, location=wrapper.location,
            payload_type=wrapper.endpoint.payload_type,
        ))
        body_req = bindings.signal(BoundLogicalSignal(
            f'{wrapper.id}_body_req', PortSemanticKind.HANDSHAKE_REQUEST,
            source_stage=wrapper.attached_to if isinstance(operation, normalization.NormalizedSend) else None,
            target_stage=wrapper.attached_to if isinstance(operation, normalization.NormalizedReceive) else None,
            endpoint=wrapper.endpoint, location=wrapper.location,
        ))
        body_ack = bindings.signal(BoundLogicalSignal(
            f'{wrapper.id}_body_ack', PortSemanticKind.HANDSHAKE_ACKNOWLEDGE,
            source_stage=wrapper.attached_to if isinstance(operation, normalization.NormalizedReceive) else None,
            target_stage=wrapper.attached_to if isinstance(operation, normalization.NormalizedSend) else None,
            endpoint=wrapper.endpoint, location=wrapper.location,
        ))
        body_payload = bindings.signal(BoundLogicalSignal(
            f'{wrapper.id}_body_payload', PortSemanticKind.PAYLOAD,
            source_stage=wrapper.attached_to if isinstance(operation, normalization.NormalizedSend) else None,
            target_stage=wrapper.attached_to if isinstance(operation, normalization.NormalizedReceive) else None,
            endpoint=wrapper.endpoint, variable=variable, expression=expression, location=wrapper.location,
            payload_type=wrapper.endpoint.payload_type,
        ))
        if isinstance(operation, normalization.NormalizedReceive):
            for port, signal in (('external_req', external_req), ('external_ack', external_ack),
                                 ('external_data', external_payload), ('body_req', body_req),
                                 ('body_ack', body_ack), ('body_data', body_payload)):
                bindings.bind(wrapper.id, port, signal)
            bindings.bind(wrapper.attached_to, 'upstream_req', body_req)
            bindings.bind(wrapper.attached_to, 'upstream_ack', body_ack)
            bindings.bind(wrapper.attached_to, 'payload_in', body_payload)
        else:
            for port, signal in (('body_req', body_req), ('body_ack', body_ack), ('body_data', body_payload),
                                 ('external_req', external_req), ('external_ack', external_ack),
                                 ('external_data', external_payload)):
                bindings.bind(wrapper.id, port, signal)
            bindings.bind(wrapper.attached_to, 'downstream_req', body_req)
            bindings.bind(wrapper.attached_to, 'downstream_ack', body_ack)
            bindings.bind(wrapper.attached_to, 'payload_out', body_payload)


def _bind_storage_and_delays(stages: tuple[BoundBodyStage, ...], wrappers: tuple[BoundWrapper, ...],
                             bindings: _Bindings) -> None:
    wrapper_endpoints = {wrapper.attached_to: wrapper.endpoint for wrapper in wrappers}
    for stage in stages:
        control = next(signal for signal in bindings.signals if signal.id == f'{stage.id}_local_control')
        if stage.storage:
            payload_type = _payload_type(stage.variable, stage.endpoint or wrapper_endpoints.get(stage.id))
            data_in = bindings.signal(BoundLogicalSignal(
                f'{stage.storage.id}_data_in', PortSemanticKind.PAYLOAD, source_stage=stage.id,
                variable=stage.variable, payload_type=payload_type, location=stage.location,
            ))
            data_out = bindings.signal(BoundLogicalSignal(
                f'{stage.storage.id}_data_out', PortSemanticKind.PAYLOAD, target_stage=stage.id,
                variable=stage.variable, payload_type=payload_type, location=stage.location,
            ))
            control_out = bindings.signal(BoundLogicalSignal(
                f'{stage.storage.id}_control_out', PortSemanticKind.CONTROL, target_stage=stage.id,
                location=stage.location,
            ))
            bindings.bind(stage.id, 'storage_data_in', data_in)
            bindings.bind(stage.id, 'storage_data_out', data_out)
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
    bindings = _Bindings()
    handshakes = _handshake_signals(graph, bindings)
    _bind_stage_topology(graph, bindings, handshakes)
    _bind_data_dependencies(graph, bindings)
    _bind_unconditional_external_operations(graph, bindings)
    _bind_wrapper_ports(graph, bindings)
    _bind_storage_and_delays(body_stages, wrappers, bindings)
    _validate_bindings(body_stages, wrappers, bindings)
    dependencies = tuple(BoundStructuralDependency(
        edge.source_node, edge.target_node, edge.kind, edge.source_stage, edge.target_stage,
    ) for edge in graph.dependencies)
    return BoundStructuralGraph(graph.module, TEMPLATE_CONTRACTS, body_stages, wrappers, dependencies,
                                graph.metadata, tuple(bindings.signals), tuple(bindings.bindings))
