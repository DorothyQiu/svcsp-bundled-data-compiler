"""M7A structural binding for the async microarchitecture selected by M6."""
from __future__ import annotations

from dataclasses import dataclass, replace
import re

from . import behavioral_ir as behavioral
from .async_microarchitecture import (
    AsyncMicroarchitecture,
    EnableChannel,
    EnReceiveStage,
    EnSendStage,
    InputJoin,
    InputPort,
    MatchedDelayRequirement,
    OutputFork,
    OutputPort,
    StorageSlot,
)
from .communication_decomposition import EnReceive, EnSend


class AsyncTemplateBindingError(ValueError):
    """An M6 architecture lacks information required for structural binding."""


@dataclass(frozen=True)
class BoundAsyncInstance:
    """An instance selected by M6, with its source identity retained."""

    id: str
    template: str
    source: object
    inputs: tuple[InputPort, ...] = ()
    outputs: tuple[OutputPort, ...] = ()
    serializes_inputs: bool | None = None
    serializes_outputs: bool | None = None
    storage_slot: StorageSlot | None = None
    input_join: InputJoin | None = None
    output_port: OutputPort | None = None
    value: None = None
    component: str = ""
    required_formals: tuple[str, ...] = ()
    required_parameters: tuple[str, ...] = ()
    enable_channel: EnableChannel | None = None


@dataclass(frozen=True)
class BoundAsyncSignal:
    id: str
    kind: str
    width: behavioral.PayloadWidth
    endpoint: behavioral.ChannelEndpoint | None = None


@dataclass(frozen=True)
class BoundAsyncModulePort:
    name: str
    flow: str
    role: str
    direction: str
    endpoint: behavioral.ChannelEndpoint
    width: behavioral.PayloadWidth
    signal_id: str


@dataclass(frozen=True)
class BoundAsyncConnection:
    source: str
    target: str
    kind: str


@dataclass(frozen=True)
class BoundAsyncPortBinding:
    instance_id: str
    formal_name: str
    actual_signal_id: str


@dataclass(frozen=True)
class BoundAsyncParameterBinding:
    instance_id: str
    formal_name: str
    value: behavioral.PayloadWidth


@dataclass(frozen=True)
class BoundEnableChannel:
    """The complete bound four-phase transport for one M6 EnableChannel."""

    source: EnableChannel
    enable_channel: EnableChannel
    availability: object
    producer_completion: bool
    request_signal_id: str
    acknowledge_signal_id: str
    data_signal_id: str
    body_sender: BoundAsyncInstance


@dataclass(frozen=True)
class BoundAsyncModule:
    architecture: AsyncMicroarchitecture
    instances: tuple[BoundAsyncInstance, ...]
    signals: tuple[BoundAsyncSignal, ...]
    module_ports: tuple[BoundAsyncModulePort, ...]
    connections: tuple[BoundAsyncConnection, ...]
    port_bindings: tuple[BoundAsyncPortBinding, ...] = ()
    parameter_bindings: tuple[BoundAsyncParameterBinding, ...] = ()
    enable_channels: tuple[BoundEnableChannel, ...] = ()


_IDENTIFIER = re.compile(r"[^A-Za-z0-9_$]")


def bind_async_templates(architecture: AsyncMicroarchitecture) -> BoundAsyncModule:
    """Mechanically bind the complete topology already selected by M6."""

    if not isinstance(architecture, AsyncMicroarchitecture):
        raise TypeError("expected an AsyncMicroarchitecture")

    instances: list[BoundAsyncInstance] = []
    signals: list[BoundAsyncSignal] = []
    ports: list[BoundAsyncModulePort] = []
    connections: list[BoundAsyncConnection] = []

    def signal(identifier: str, kind: str, width: behavioral.PayloadWidth,
               endpoint: behavioral.ChannelEndpoint | None = None) -> str:
        signals.append(BoundAsyncSignal(identifier, kind, width, endpoint))
        return identifier

    def external(endpoint: behavioral.ChannelEndpoint, flow: str, role: str,
                 direction: str, width: behavioral.PayloadWidth) -> str:
        prefix = _endpoint_name(endpoint)
        identifier = f"channel_{prefix}_{flow}_{role}"
        signal(identifier, "payload" if role == "payload" else role, width, endpoint)
        # ``flow`` denotes the payload direction at the module boundary.  The
        # request/acknowledge pins remain separate ports but are not payload
        # flows themselves.
        port_flow = flow if role == "payload" else f"{flow}_{role}"
        ports.append(BoundAsyncModulePort(identifier, port_flow, role, direction, endpoint, width, identifier))
        return identifier

    join = architecture.input_join
    instances.append(BoundAsyncInstance(
        "input_join", "four_phase_input_join", join, inputs=join.inputs,
        serializes_inputs=join.serializes_inputs,
    ))
    join_control = signal("input_join_control", "control", behavioral.ONE_BIT)
    receive_stage_by_port = {stage.input_port: stage for stage in architecture.en_receive_stages}
    send_stage_by_port = {stage.output_port: stage for stage in architecture.en_send_stages}
    for index, channel in enumerate(architecture.enable_channels):
        instances.append(BoundAsyncInstance(
            f"enable_sender_{index}", "four_phase_enable_sender", channel.producer,
            enable_channel=channel,
        ))

    receive_data: dict[InputPort, str] = {}
    for index, input_port in enumerate(join.inputs):
        operation = input_port.body_receive.source.operation
        if not isinstance(operation, behavioral.Receive):
            raise AsyncTemplateBindingError("input port lacks a Receive source")
        endpoint = operation.channel
        payload = _receive_payload_type(operation)
        request = external(endpoint, "receive", "request", "input", behavioral.ONE_BIT)
        acknowledge = external(endpoint, "receive", "acknowledge", "output", behavioral.ONE_BIT)
        data = external(endpoint, "receive", "payload", "input", payload.width)
        instance_id = f"input_{index}"
        stage = receive_stage_by_port.get(input_port)
        if stage is None:
            instances.append(BoundAsyncInstance(instance_id, "four_phase_receive_port", input_port))
        else:
            instance_id = f"en_receive_stage_{index}"
            instances.append(BoundAsyncInstance(instance_id, "en_receive_stage", stage,
                                                enable_channel=stage.enable_channel))
        body_data = signal(f"{instance_id}_body_data", "payload", payload.width, endpoint)
        receive_data[input_port] = body_data
        adapter_id = instance_id
        connections.extend((
            BoundAsyncConnection(request, f"{adapter_id}.request", "request"),
            BoundAsyncConnection(f"{adapter_id}.acknowledge", acknowledge, "acknowledge"),
            BoundAsyncConnection(data, f"{adapter_id}.external_data", "payload"),
            BoundAsyncConnection(f"{adapter_id}.body_data", body_data, "payload"),
            BoundAsyncConnection(body_data, "input_join.input", "payload"),
            BoundAsyncConnection(join_control, f"{instance_id}.control", "control"),
        ))

    storage_data: dict[StorageSlot, str] = {}
    for index, slot in enumerate(architecture.storage.slots):
        operation = slot.body_send.source.operation
        if not isinstance(operation, behavioral.Send):
            raise AsyncTemplateBindingError("storage slot lacks a Send source")
        payload = _send_payload_type(operation)
        instance_id = f"storage_{index}"
        instances.append(BoundAsyncInstance(
            instance_id, "bundled_data_storage", slot, storage_slot=slot,
        ))
        data = signal(f"{instance_id}_data", "payload", payload.width, operation.channel)
        storage_data[slot] = data
        connections.append(BoundAsyncConnection(join_control, f"{instance_id}.capture", "control"))

    fork = architecture.output_fork
    instances.append(BoundAsyncInstance(
        "output_fork", "four_phase_output_fork", fork, outputs=fork.outputs,
        serializes_outputs=fork.serializes_outputs,
    ))
    instances.append(BoundAsyncInstance("output_completion", "four_phase_output_completion", fork))

    slot_for_output = {slot.retained_until: slot for slot in architecture.storage.slots}
    for index, output in enumerate(fork.outputs):
        operation = output.body_send.source.operation
        if not isinstance(operation, behavioral.Send):
            raise AsyncTemplateBindingError("output port lacks a Send source")
        endpoint = operation.channel
        payload = _send_payload_type(operation)
        slot = slot_for_output.get(output)
        if slot is None:
            raise AsyncTemplateBindingError("output port has no M6 storage slot")
        request = external(endpoint, "send", "request", "output", behavioral.ONE_BIT)
        acknowledge = external(endpoint, "send", "acknowledge", "input", behavioral.ONE_BIT)
        data = external(endpoint, "send", "payload", "output", payload.width)
        instance_id = f"output_{index}"
        stage = send_stage_by_port.get(output)
        if stage is None:
            instances.append(BoundAsyncInstance(instance_id, "four_phase_send_port", output))
        else:
            instance_id = f"en_send_stage_{index}"
            instances.append(BoundAsyncInstance(instance_id, "en_send_stage", stage,
                                                enable_channel=stage.enable_channel))
        adapter_id = instance_id
        connections.extend((
            BoundAsyncConnection(storage_data[slot], f"{adapter_id}.body_data", "payload"),
            BoundAsyncConnection(f"output_fork.request_{index}", f"{adapter_id}.body_request", "request"),
            BoundAsyncConnection(f"{adapter_id}.request", request, "request"),
            BoundAsyncConnection(acknowledge, f"{adapter_id}.acknowledge", "acknowledge"),
            BoundAsyncConnection(f"{adapter_id}.external_data", data, "payload"),
            BoundAsyncConnection(f"{adapter_id}.complete", f"output_completion.input_{index}", "completion"),
        ))

    output_indices = {output: index for index, output in enumerate(fork.outputs)}
    storage_indices = {slot: index for index, slot in enumerate(architecture.storage.slots)}
    for requirement in architecture.matched_delays:
        _bind_delay(requirement, instances, connections, output_indices, storage_indices)

    instances, signals, connections, port_bindings, parameter_bindings, enable_channel_bindings = _typed_bindings(
        architecture, instances, signals, ports,
    )
    return BoundAsyncModule(architecture, tuple(instances), tuple(signals), tuple(ports), tuple(connections),
                            tuple(port_bindings), tuple(parameter_bindings), tuple(enable_channel_bindings))


def _typed_bindings(architecture: AsyncMicroarchitecture, instances: list[BoundAsyncInstance],
                    signals: list[BoundAsyncSignal], ports: list[BoundAsyncModulePort]) -> tuple[
                        list[BoundAsyncInstance], list[BoundAsyncSignal], list[BoundAsyncConnection],
                        list[BoundAsyncPortBinding], list[BoundAsyncParameterBinding],
                        list[BoundEnableChannel]]:
    """Turn the M6 graph into a closed set of named template actuals."""

    signal_by_id = {signal.id: signal for signal in signals}

    def ensure(identifier: str, kind: str, width: behavioral.PayloadWidth,
               endpoint: behavioral.ChannelEndpoint | None = None) -> str:
        if identifier not in signal_by_id:
            signal_by_id[identifier] = BoundAsyncSignal(identifier, kind, width, endpoint)
        return identifier

    def external(endpoint: behavioral.ChannelEndpoint, flow: str, role: str) -> str:
        return next(port.signal_id for port in ports
                    if port.endpoint == endpoint and port.role == role and
                    (port.flow == flow if role == "payload" else port.flow == f"{flow}_{role}"))

    by_source = {instance.source: instance for instance in instances}
    formals: dict[str, tuple[str, ...]] = {}
    parameters: dict[str, tuple[str, ...]] = {}
    bindings: list[BoundAsyncPortBinding] = []
    parameter_bindings: list[BoundAsyncParameterBinding] = []

    def bind(instance: BoundAsyncInstance, formal: str, actual: str) -> None:
        if actual not in signal_by_id:
            raise AsyncTemplateBindingError(f"{instance.id}.{formal} has undeclared actual {actual}")
        bindings.append(BoundAsyncPortBinding(instance.id, formal, actual))

    enable_channel_bindings: list[BoundEnableChannel] = []
    enable_signals: dict[EnableChannel, tuple[str, str, str]] = {}
    for index, channel in enumerate(architecture.enable_channels):
        request = ensure(f"enable_channel_{index}_req", "request", behavioral.ONE_BIT)
        acknowledge = ensure(f"enable_channel_{index}_ack", "acknowledge", behavioral.ONE_BIT)
        data = ensure(f"enable_channel_{index}_data", "payload", behavioral.ONE_BIT)
        sender = by_source[channel.producer]
        formals[sender.id] = ("req", "ack", "data")
        bind(sender, "req", request)
        bind(sender, "ack", acknowledge)
        bind(sender, "data", data)
        enable_signals[channel] = request, acknowledge, data
        enable_channel_bindings.append(BoundEnableChannel(
            channel, channel, channel.availability,
            channel.producer.participates_in_transaction_completion,
            request, acknowledge, data, sender,
        ))

    join = architecture.input_join
    join_instance = by_source[join]
    join_control = ensure("input_join_control", "control", behavioral.ONE_BIT)
    stage_complete = ensure("stage_complete", "control", behavioral.ONE_BIT)
    join_formals: list[str] = []
    input_body: dict[InputPort, tuple[str, str, str]] = {}
    for index, input_port in enumerate(join.inputs):
        operation = input_port.body_receive.source.operation
        assert isinstance(operation, behavioral.Receive)
        payload = _receive_payload_type(operation)
        body = (
            ensure(f"input_{index}_body_req", "request", behavioral.ONE_BIT),
            ensure(f"input_{index}_body_ack", "acknowledge", behavioral.ONE_BIT),
            ensure(f"input_{index}_body_data", "payload", payload.width, operation.channel),
        )
        input_body[input_port] = body
        req, ack, data = body
        if input_port.en_receive is None:
            port_instance = by_source[input_port]
            port_formals = ("external_req", "external_ack", "external_data", "body_req", "body_ack", "body_data", "control")
            formals[port_instance.id] = port_formals
            for formal, actual in (
                ("external_req", external(operation.channel, "receive", "request")),
                ("external_ack", external(operation.channel, "receive", "acknowledge")),
                ("external_data", external(operation.channel, "receive", "payload")),
                ("body_req", req), ("body_ack", ack), ("body_data", data), ("control", join_control),
            ):
                bind(port_instance, formal, actual)
        else:
            stage = next(item for item in architecture.en_receive_stages if item.input_port is input_port)
            stage_instance = by_source[stage]
            enable_req, enable_ack, enable_data = enable_signals[stage.enable_channel]
            formals[stage_instance.id] = (
                "enable_req", "enable_ack", "enable_data", "external_req", "external_ack", "external_data",
                "body_req", "body_ack", "body_data",
            )
            for formal, actual in (
                ("enable_req", enable_req), ("enable_ack", enable_ack), ("enable_data", enable_data),
                ("external_req", external(operation.channel, "receive", "request")),
                ("external_ack", external(operation.channel, "receive", "acknowledge")),
                ("external_data", external(operation.channel, "receive", "payload")),
                ("body_req", req), ("body_ack", ack), ("body_data", data),
            ):
                bind(stage_instance, formal, actual)
        join_formals.extend((f"input_req_{index}", f"input_ack_{index}"))
        bind(join_instance, f"input_req_{index}", req)
        bind(join_instance, f"input_ack_{index}", ack)
    join_formals.extend(("stage_release", "control"))
    formals[join_instance.id] = tuple(join_formals)
    bind(join_instance, "stage_release", stage_complete)
    bind(join_instance, "control", join_control)

    storage_by_output = {slot.retained_until: slot for slot in architecture.storage.slots}
    for index, slot in enumerate(architecture.storage.slots):
        operation = slot.body_send.source.operation
        assert isinstance(operation, behavioral.Send)
        payload = _send_payload_type(operation)
        storage = by_source[slot]
        data = ensure(f"storage_{index}_data", "payload", payload.width, operation.channel)
        formals[storage.id] = ("data_in", "data_out", "capture", "release")
        parameters[storage.id] = ("WIDTH",)
        for formal, actual in (("data_in", data), ("data_out", data), ("capture", join_control),
                               ("release", stage_complete)):
            bind(storage, formal, actual)
        parameter_bindings.append(BoundAsyncParameterBinding(storage.id, "WIDTH", payload.width))

    fork = architecture.output_fork
    fork_instance = next(instance for instance in instances if instance.template == "four_phase_output_fork")
    completion_instance = next(instance for instance in instances if instance.template == "four_phase_output_completion")
    fork_formals: list[str] = []
    completion_formals: list[str] = []
    output_launch: dict[OutputPort, str] = {}
    output_complete: dict[OutputPort, str] = {}
    for index, output in enumerate(fork.outputs):
        operation = output.body_send.source.operation
        assert isinstance(operation, behavioral.Send)
        payload = _send_payload_type(operation)
        slot = storage_by_output[output]
        launch = output_launch[output] = ensure(f"output_{index}_launch", "request", behavioral.ONE_BIT)
        complete = output_complete[output] = ensure(f"output_{index}_complete", "completion", behavioral.ONE_BIT)
        if output.en_send is None:
            branch = by_source[output]
            formals[branch.id] = ("external_req", "external_ack", "payload", "launch", "complete")
            for formal, actual in (
                ("external_req", external(operation.channel, "send", "request")),
                ("external_ack", external(operation.channel, "send", "acknowledge")),
                ("payload", f"storage_{architecture.storage.slots.index(slot)}_data"),
                ("launch", launch), ("complete", complete),
            ):
                bind(branch, formal, actual)
        else:
            stage = next(item for item in architecture.en_send_stages if item.output_port is output)
            stage_instance = by_source[stage]
            body_req = ensure(f"output_{index}_body_req", "request", behavioral.ONE_BIT)
            body_ack = ensure(f"output_{index}_body_ack", "acknowledge", behavioral.ONE_BIT)
            enable_req, enable_ack, enable_data = enable_signals[stage.enable_channel]
            formals[stage_instance.id] = (
                "enable_req", "enable_ack", "enable_data", "body_req", "body_ack", "body_data",
                "external_req", "external_ack", "external_data",
            )
            for formal, actual in (
                ("enable_req", enable_req), ("enable_ack", enable_ack), ("enable_data", enable_data),
                ("body_req", body_req), ("body_ack", body_ack),
                ("body_data", f"storage_{architecture.storage.slots.index(slot)}_data"),
                ("external_req", external(operation.channel, "send", "request")),
                ("external_ack", external(operation.channel, "send", "acknowledge")),
                ("external_data", external(operation.channel, "send", "payload")),
            ):
                bind(stage_instance, formal, actual)
        fork_formals.extend((f"launch_{index}", f"complete_{index}"))
        completion_formals.append(f"complete_{index}")
        bind(fork_instance, f"launch_{index}", launch)
        bind(fork_instance, f"complete_{index}", complete)
        bind(completion_instance, f"complete_{index}", complete)
    fork_formals.append("stage_complete")
    completion_formals.append("stage_complete")
    formals[fork_instance.id] = tuple(fork_formals)
    formals[completion_instance.id] = tuple(completion_formals)
    bind(fork_instance, "stage_complete", stage_complete)
    bind(completion_instance, "stage_complete", stage_complete)

    for requirement in architecture.matched_delays:
        delay = by_source[requirement]
        formals[delay.id] = ("control_in", "control_out", "data_path")
        bind(delay, "control_in", join_control)
        bind(delay, "control_out", output_launch[requirement.output_port])
        slot_index = architecture.storage.slots.index(requirement.storage_slot)
        bind(delay, "data_path", f"storage_{slot_index}_data")

    updated = [replace(instance, component=instance.template,
                       required_formals=formals.get(instance.id, ()),
                       required_parameters=parameters.get(instance.id, ())) for instance in instances]
    for instance in updated:
        owned = [binding for binding in bindings if binding.instance_id == instance.id]
        if {binding.formal_name for binding in owned} != set(instance.required_formals) or len(owned) != len(instance.required_formals):
            raise AsyncTemplateBindingError(f"{instance.id} has incomplete formal bindings")
    connections = [BoundAsyncConnection(item.actual_signal_id, f"{item.instance_id}.{item.formal_name}", "formal")
                   for item in bindings]
    return updated, list(signal_by_id.values()), connections, bindings, parameter_bindings, enable_channel_bindings


def _bind_delay(requirement: MatchedDelayRequirement, instances: list[BoundAsyncInstance],
                connections: list[BoundAsyncConnection], output_indices: dict[OutputPort, int],
                storage_indices: dict[StorageSlot, int]) -> None:
    index = requirement.id.rsplit("_", 1)[-1]
    instance_id = f"matched_delay_{index}"
    instances.append(BoundAsyncInstance(
        instance_id, "bundled_data_matched_delay", requirement,
        storage_slot=requirement.storage_slot,
        input_join=requirement.input_join,
        output_port=requirement.output_port,
        value=requirement.value,
    ))
    connections.extend((
        BoundAsyncConnection("input_join_control", f"{instance_id}.control_in", "control"),
        BoundAsyncConnection(f"{instance_id}.control_out",
                             f"output_fork.request_{output_indices[requirement.output_port]}",
                             "matched_delay"),
        BoundAsyncConnection(f"storage_{storage_indices[requirement.storage_slot]}_data",
                             f"{instance_id}.data_path", "payload"),
    ))


def _endpoint_name(endpoint: behavioral.ChannelEndpoint) -> str:
    selectors = "_".join(_selector_name(selector) for selector in endpoint.selectors)
    raw = "_".join(part for part in (endpoint.name, selectors) if part)
    return _IDENTIFIER.sub("_", raw) or "channel"


def _selector_name(expression: behavioral.Expression) -> str:
    if expression.form == "index" and expression.operands:
        return _selector_name(expression.operands[0])
    if expression.value is not None:
        return expression.value
    if expression.variable is not None:
        return expression.variable.name
    return expression.form


def _receive_payload_type(operation: behavioral.Receive) -> behavioral.PayloadType:
    target = operation.target if isinstance(operation.target, behavioral.Variable) else operation.target.variable
    payload = operation.channel.payload_type or (target.payload_type if target else None)
    if payload is None:
        raise AsyncTemplateBindingError("Receive has no payload width")
    return payload


def _send_payload_type(operation: behavioral.Send) -> behavioral.PayloadType:
    payload = operation.channel.payload_type or behavioral.expression_payload_type(operation.value)
    if payload is None:
        raise AsyncTemplateBindingError("Send has no payload width")
    return payload
