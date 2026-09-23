"""M7A structural binding for the async microarchitecture selected by M6."""

from __future__ import annotations

from dataclasses import dataclass, replace
import re

from . import behavioral_ir as behavioral
from .async_microarchitecture import (
    AckJoin,
    AsyncMicroarchitecture,
    EnableChannel,
    EnReceiveMatchedDelayRequirement,
    EnReceiveStorageSlot,
    EnReceiveStage,
    EnSendMatchedDelayRequirement,
    EnSendStorageSlot,
    EnSendStage,
    InputAckDirectConnection,
    InputAckFanout,
    InputPort,
    InputRequestDirectConnection,
    MatchedDelayRequirement,
    OutputAckDirectConnection,
    OutputPort,
    OutputRequestDirectConnection,
    RequestFanout,
    RequestJoin,
    StorageSlot,
)
from .communication_decomposition import BodyReceive
from .transaction import RegionOperation


class AsyncTemplateBindingError(ValueError):
    pass


@dataclass(frozen=True)
class BoundAsyncInstance:
    id: str
    template: str
    source: object

    inputs: tuple[InputPort, ...] = ()
    outputs: tuple[OutputPort, ...] = ()

    storage_slot: (
        StorageSlot
        | EnReceiveStorageSlot
        | EnSendStorageSlot
        | None
    ) = None
    output_port: InputPort | OutputPort | None = None

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
    endpoint: behavioral.ChannelEndpoint | None
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
class BoundAsyncModuleParameter:
    """An exact source parameter bound to the generated module interface."""

    source: behavioral.Parameter
    name: str


@dataclass(frozen=True)
class BoundAsyncAssignment:
    target_signal_id: str
    expression: behavioral.Expression | None
    source: object
    kind: str
    source_signal_ids: tuple[str, ...] = ()
    source_index: int | None = None


@dataclass(frozen=True)
class BoundAsyncVariableBinding:
    variable: behavioral.Variable
    signal_id: str


@dataclass(frozen=True)
class BoundBodyReceiveWrite:
    """One exact BODY Receive occurrence and its whole-Variable write."""

    source: BodyReceive
    input_port: InputPort
    target: behavioral.Variable


@dataclass(frozen=True)
class BoundBodyAssignWrite:
    """One exact source Assign occurrence and its whole-Variable write."""

    source: RegionOperation
    target: behavioral.Variable
    expression: behavioral.Expression


@dataclass(frozen=True)
class BoundBodySequence:
    items: tuple["BoundBodyProcess", ...]


@dataclass(frozen=True)
class BoundBodyIf:
    condition: behavioral.Expression
    then_branch: "BoundBodyProcess"
    else_branch: "BoundBodyProcess"


@dataclass(frozen=True)
class BoundBodyParallel:
    branches: tuple["BoundBodyProcess", ...]


@dataclass(frozen=True)
class BoundBodySkip:
    """A source Send or Skip, neither of which writes BODY variables."""

    source: behavioral.Send | behavioral.Skip


BoundBodyProcess = (
    BoundBodyReceiveWrite
    | BoundBodyAssignWrite
    | BoundBodySequence
    | BoundBodyIf
    | BoundBodyParallel
    | BoundBodySkip
)


@dataclass(frozen=True)
class BoundEnableChannel:
    source: EnableChannel
    enable_channel: EnableChannel
    availability: object
    producer_completion: bool

    request_signal_id: str
    acknowledge_signal_id: str
    data_signal_id: str
    value_signal_id: str

    launch_signal_id: str
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
    module_parameters: tuple[BoundAsyncModuleParameter, ...] = ()
    enable_channels: tuple[BoundEnableChannel, ...] = ()

    assignments: tuple[BoundAsyncAssignment, ...] = ()
    variable_bindings: tuple[BoundAsyncVariableBinding, ...] = ()
    body_program: BoundBodyProcess | None = None


_IDENTIFIER = re.compile(r"[^A-Za-z0-9_$]")


def bind_async_templates(
    architecture: AsyncMicroarchitecture,
) -> BoundAsyncModule:
    """Bind exactly the M6-selected ordinary BODY topology and EN stages."""

    if not isinstance(architecture, AsyncMicroarchitecture):
        raise TypeError("expected an AsyncMicroarchitecture")

    instances: list[BoundAsyncInstance] = []
    signals: list[BoundAsyncSignal] = []
    ports: list[BoundAsyncModulePort] = []
    bindings: list[BoundAsyncPortBinding] = []
    parameters: list[BoundAsyncParameterBinding] = []
    assignments: list[BoundAsyncAssignment] = []
    behavioral_module = architecture.validated.decomposed.transaction.behavioral
    module_parameters: list[BoundAsyncModuleParameter] = []
    module_parameter_names: set[str] = set()

    for source in behavioral_module.parameters:
        if source.name in module_parameter_names:
            raise AsyncTemplateBindingError(f"duplicate module parameter {source.name}")
        module_parameter_names.add(source.name)
        module_parameters.append(BoundAsyncModuleParameter(source, source.name))

    def module_parameter(parameter: behavioral.Parameter) -> BoundAsyncModuleParameter:
        found = [item for item in module_parameters if item.source is parameter]
        if len(found) != 1:
            raise AsyncTemplateBindingError(
                f"missing or ambiguous module parameter binding for {parameter.name}"
            )
        return found[0]

    def validate_width(width: behavioral.PayloadWidth) -> None:
        if width.symbolic is not None and not width.parameters:
            raise AsyncTemplateBindingError("symbolic payload width lacks module parameter ownership")
        for parameter in width.parameters:
            module_parameter(parameter)

    def validate_expression(expression: behavioral.Expression) -> None:
        if expression.parameter is not None:
            module_parameter(expression.parameter)
        for operand in expression.operands:
            validate_expression(operand)

    def whole_variable_target(
        target: behavioral.Variable | behavioral.Expression,
    ) -> behavioral.Variable:
        if isinstance(target, behavioral.Variable):
            return target
        raise AsyncTemplateBindingError(
            "R9A does not support selected lvalue targets"
        )

    def validate_process(process: behavioral.Process) -> None:
        if isinstance(process, behavioral.Sequence):
            for item in process.items:
                validate_process(item)
            return
        if isinstance(process, behavioral.Parallel):
            for branch in process.branches:
                validate_process(branch)
            return
        if isinstance(process, behavioral.If):
            validate_expression(process.condition)
            validate_process(process.then_branch)
            validate_process(process.else_branch)
            return
        if isinstance(process, behavioral.Assign):
            if isinstance(process.target, behavioral.Expression):
                validate_expression(process.target)
            whole_variable_target(process.target)
            validate_expression(process.value)
            return
        if isinstance(process, behavioral.Receive):
            if isinstance(process.target, behavioral.Expression):
                validate_expression(process.target)
            whole_variable_target(process.target)
            for selector in process.channel.selectors:
                validate_expression(selector)
            return
        if isinstance(process, behavioral.Send):
            validate_expression(process.value)
            for selector in process.channel.selectors:
                validate_expression(selector)

    for variable in (*behavioral_module.variables, *behavioral_module.external_inputs):
        validate_width(variable.payload_type.width)
    for channel in behavioral_module.channels:
        if channel.payload_type is not None:
            validate_width(channel.payload_type.width)
    validate_process(behavioral_module.body)

    def ensure(
        name: str,
        kind: str,
        width: behavioral.PayloadWidth,
        endpoint: behavioral.ChannelEndpoint | None = None,
    ) -> str:
        validate_width(width)
        if name in module_parameter_names:
            raise AsyncTemplateBindingError(f"module parameter conflicts with generated signal {name}")
        existing = [item for item in signals if item.id == name]
        if existing:
            if len(existing) != 1 or existing[0] != BoundAsyncSignal(name, kind, width, endpoint):
                raise AsyncTemplateBindingError(f"conflicting bound signal {name}")
        else:
            signals.append(BoundAsyncSignal(name, kind, width, endpoint))
        return name

    def add_instance(
        name: str,
        template: str,
        source: object,
        **kwargs,
    ) -> BoundAsyncInstance:
        item = BoundAsyncInstance(name, template, source, **kwargs)
        instances.append(item)
        return item

    def bind(
        item: BoundAsyncInstance,
        formal: str,
        actual: str,
    ) -> None:
        bindings.append(
            BoundAsyncPortBinding(
                item.id,
                formal,
                actual,
            )
        )

    def parameter(
        item: BoundAsyncInstance,
        formal: str,
        value: behavioral.PayloadWidth,
    ) -> None:
        validate_width(value)
        parameters.append(
            BoundAsyncParameterBinding(
                item.id,
                formal,
                value,
            )
        )

    def external(
        endpoint: behavioral.ChannelEndpoint,
        flow: str,
        role: str,
        direction: str,
        width: behavioral.PayloadWidth,
    ) -> str:
        name = f"channel_{_endpoint_name(endpoint)}_{flow}_{role}"

        if name in module_parameter_names:
            raise AsyncTemplateBindingError(f"module parameter conflicts with public port {name}")
        if any(port.name == name for port in ports):
            raise AsyncTemplateBindingError(f"duplicate or conflicting public port {name}")

        ensure(
            name,
            "payload" if role == "payload" else role,
            width,
            endpoint,
        )

        ports.append(
            BoundAsyncModulePort(
                name,
                flow if role == "payload" else f"{flow}_{role}",
                role,
                direction,
                endpoint,
                width,
                name,
            )
        )

        return name

    def external_input(variable: behavioral.Variable) -> str:
        """Bind one declared external-input identity to its public RTL port."""

        name = variable.name
        if name in module_parameter_names:
            raise AsyncTemplateBindingError(f"module parameter conflicts with public port {name}")
        if any(port.name == name for port in ports):
            raise AsyncTemplateBindingError(f"duplicate or conflicting public port {name}")
        ensure(name, "payload", variable.payload_type.width)
        ports.append(
            BoundAsyncModulePort(
                name,
                "external_input",
                "payload",
                "input",
                None,
                variable.payload_type.width,
                name,
            )
        )
        return name

    if "reset_n" in module_parameter_names:
        raise AsyncTemplateBindingError("module parameter conflicts with public port reset_n")
    reset_n = ensure(
        "reset_n",
        "control",
        behavioral.ONE_BIT,
    )

    ports.append(
        BoundAsyncModulePort(
            "reset_n",
            "control",
            "reset",
            "input",
            None,
            behavioral.ONE_BIT,
            reset_n,
        )
    )

    variables: list[BoundAsyncVariableBinding] = []
    external_inputs = behavioral_module.external_inputs

    for variable in external_inputs:
        variables.append(BoundAsyncVariableBinding(variable, external_input(variable)))

    for index, variable in enumerate(
        behavioral_module.variables
    ):
        if any(variable is external for external in external_inputs):
            continue
        name = (
            f"body_var_{index}_"
            f"{_IDENTIFIER.sub('_', variable.name) or 'variable'}"
        )

        ensure(
            name,
            "payload",
            variable.payload_type.width,
        )

        variables.append(
            BoundAsyncVariableBinding(
                variable,
                name,
            )
        )

    def variable_signal(
        variable: behavioral.Variable,
    ) -> str:
        found = [
            item.signal_id
            for item in variables
            if item.variable is variable
        ]

        if len(found) != 1:
            raise AsyncTemplateBindingError(
                f"missing or ambiguous binding for variable {variable.name}"
            )

        return found[0]

    def expression_signals(
        expression: behavioral.Expression,
    ) -> tuple[str, ...]:
        result: list[str] = []

        def visit(
            item: behavioral.Expression,
        ) -> None:
            if item.variable is not None:
                name = variable_signal(item.variable)

                if name not in result:
                    result.append(name)

            for operand in item.operands:
                visit(operand)

        visit(expression)

        return tuple(result)

    # Preserve each exact M6 combinational RegionOperation and its original
    # behavioral Assign expression as an explicit M7 assignment.
    for source in architecture.combinational.operations:
        operation = source.operation

        if not isinstance(operation, behavioral.Assign):
            raise AsyncTemplateBindingError(
                "combinational region contains non-Assign operation"
            )

        target = _target_variable(operation.target)

        if target is None:
            raise AsyncTemplateBindingError(
                "Assign target has no Variable identity"
            )

        assignments.append(
            BoundAsyncAssignment(
                target_signal_id=variable_signal(target),
                expression=operation.value,
                source=source,
                kind="combinational",
                source_signal_ids=expression_signals(operation.value),
            )
        )

    receive_stages = {
        item.input_port: item
        for item in architecture.en_receive_stages
    }

    send_stages = {
        item.output_port: item
        for item in architecture.en_send_stages
    }

    def en_receive_resources(stage: EnReceiveStage) -> tuple[
            EnReceiveStorageSlot, EnReceiveMatchedDelayRequirement]:
        storage = [item for item in architecture.en_receive_storage if item.stage is stage]
        delays = [item for item in architecture.en_receive_matched_delays if item.stage is stage]
        if len(storage) != 1 or len(delays) != 1:
            raise AsyncTemplateBindingError("EN_RECV stage lacks exactly one M6 storage and delay resource")
        if storage[0].input_port is not stage.input_port or delays[0].input_port is not stage.input_port:
            raise AsyncTemplateBindingError("EN_RECV resource input-port identity mismatch")
        if delays[0].storage_slot is not storage[0]:
            raise AsyncTemplateBindingError("EN_RECV delay does not select its M6 storage resource")
        return storage[0], delays[0]

    def en_send_resources(stage: EnSendStage) -> tuple[
            EnSendStorageSlot, EnSendMatchedDelayRequirement]:
        storage = [item for item in architecture.en_send_storage if item.stage is stage]
        delays = [item for item in architecture.en_send_matched_delays if item.stage is stage]
        if len(storage) != 1 or len(delays) != 1:
            raise AsyncTemplateBindingError("EN_SEND stage lacks exactly one M6 storage and delay resource")
        if storage[0].output_port is not stage.output_port or delays[0].output_port is not stage.output_port:
            raise AsyncTemplateBindingError("EN_SEND resource output-port identity mismatch")
        if delays[0].storage_slot is not storage[0]:
            raise AsyncTemplateBindingError("EN_SEND delay does not select its M6 storage resource")
        return storage[0], delays[0]

    enable_senders = {
        channel: add_instance(
            f"enable_sender_{index}",
            "four_phase_enable_sender",
            channel.producer,
            enable_channel=channel,
        )
        for index, channel in enumerate(
            architecture.enable_channels
        )
    }

    input_body: dict[
        InputPort,
        tuple[str, str, str],
    ] = {}

    for index, port in enumerate(
        architecture.input_ports
    ):
        operation = port.body_receive.source.operation

        if not isinstance(
            operation,
            behavioral.Receive,
        ):
            raise AsyncTemplateBindingError(
                "input port lacks Receive"
            )

        payload = _receive_payload_type(operation)
        target = _target_variable(operation.target)

        if target is None:
            raise AsyncTemplateBindingError(
                "Receive target has no Variable identity"
            )

        req = ensure(
            f"input_{index}_body_req",
            "request",
            behavioral.ONE_BIT,
        )

        ack = ensure(
            f"input_{index}_body_ack",
            "acknowledge",
            behavioral.ONE_BIT,
        )

        data = variable_signal(target)

        input_body[port] = (
            req,
            ack,
            data,
        )

        ext_req = external(
            operation.channel,
            "receive",
            "request",
            "input",
            behavioral.ONE_BIT,
        )

        ext_ack = external(
            operation.channel,
            "receive",
            "acknowledge",
            "output",
            behavioral.ONE_BIT,
        )

        ext_data = external(
            operation.channel,
            "receive",
            "payload",
            "input",
            payload.width,
        )

        stage = receive_stages.get(port)

        if stage is None:
            assignments.extend(
                (
                    BoundAsyncAssignment(
                        req,
                        None,
                        port,
                        "signal_copy",
                        (ext_req,),
                    ),
                    BoundAsyncAssignment(
                        ext_ack,
                        None,
                        port,
                        "signal_copy",
                        (ack,),
                    ),
                    BoundAsyncAssignment(
                        data,
                        None,
                        port,
                        "signal_copy",
                        (ext_data,),
                    ),
                )
            )
        else:
            resource, delay = en_receive_resources(stage)
            raw_req = ensure(
                f"input_{index}_body_raw_req",
                "request",
                behavioral.ONE_BIT,
            )
            storage_data_in = ensure(
                f"{resource.id}_data_in",
                "payload",
                payload.width,
                operation.channel,
            )
            storage_enable_signal = ensure(
                f"{resource.id}_storage_enable",
                "control",
                behavioral.ONE_BIT,
            )
            item = add_instance(
                f"en_receive_controller_{index}",
                "en_receive_controller",
                stage,
                enable_channel=stage.enable_channel,
            )

            _bind_en_receive(
                item,
                stage,
                payload,
                ext_req,
                ext_ack,
                ext_data,
                raw_req,
                req,
                ack,
                storage_data_in,
                storage_enable_signal,
                bind,
                parameter,
            )

            storage = add_instance(
                resource.id,
                "bundled_data_latch_bank",
                resource,
                storage_slot=resource,
                output_port=resource.input_port,
            )
            parameter(storage, "WIDTH", payload.width)
            for formal, actual in (
                ("data_in", storage_data_in),
                ("storage_enable", storage_enable_signal),
                ("data_out", data),
            ):
                bind(storage, formal, actual)

            delay_item = add_instance(
                delay.id,
                "bundled_data_matched_delay",
                delay,
                storage_slot=resource,
                output_port=resource.input_port,
                value=delay.value,
            )
            bind(delay_item, "control_in", raw_req)
            bind(delay_item, "control_out", req)

    base_lreq = ensure(
        "base_Lreq",
        "request",
        behavioral.ONE_BIT,
    )

    base_lack = ensure(
        "base_Lack",
        "acknowledge",
        behavioral.ONE_BIT,
    )

    base_raw_rreq = ensure(
        "base_raw_Rreq",
        "request",
        behavioral.ONE_BIT,
    )

    base_rack = ensure(
        "base_Rack",
        "acknowledge",
        behavioral.ONE_BIT,
    )

    storage_enable = ensure(
        "storage_enable",
        "control",
        behavioral.ONE_BIT,
    )

    _bind_input_request(
        architecture.input_request,
        input_body,
        base_lreq,
        reset_n,
        add_instance,
        bind,
        parameter,
        assignments,
        ensure,
    )

    _bind_input_ack(
        architecture.input_ack,
        input_body,
        base_lack,
        add_instance,
        bind,
        parameter,
        assignments,
        ensure,
    )

    controller = add_instance(
        "base_controller",
        "four_phase_half_buffer_controller",
        architecture.base_controller,
    )

    for formal, actual in (
        ("reset_n", reset_n),
        ("base_Lreq", base_lreq),
        ("base_Rack", base_rack),
        ("base_Lack", base_lack),
        ("base_raw_Rreq", base_raw_rreq),
        ("storage_enable", storage_enable),
    ):
        bind(
            controller,
            formal,
            actual,
        )

    slots = {
        slot.retained_until: slot
        for slot in architecture.storage.slots
    }

    storage_data: dict[
        StorageSlot,
        str,
    ] = {}

    for index, slot in enumerate(
        architecture.storage.slots
    ):
        operation = slot.body_send.source.operation

        if not isinstance(
            operation,
            behavioral.Send,
        ):
            raise AsyncTemplateBindingError(
                "storage slot lacks Send"
            )

        payload = _send_payload_type(operation)

        data_in = ensure(
            f"storage_{index}_data_in",
            "payload",
            payload.width,
            operation.channel,
        )

        data_out = ensure(
            f"storage_{index}_data_out",
            "payload",
            payload.width,
            operation.channel,
        )

        storage_data[slot] = data_out

        item = add_instance(
            f"storage_{index}",
            "bundled_data_latch_bank",
            slot,
            storage_slot=slot,
        )

        parameter(
            item,
            "WIDTH",
            payload.width,
        )

        for formal, actual in (
            ("data_in", data_in),
            ("data_out", data_out),
            ("storage_enable", storage_enable),
        ):
            bind(
                item,
                formal,
                actual,
            )

        assignments.append(
            BoundAsyncAssignment(
                data_in,
                operation.value,
                slot.body_send,
                "body_send",
                expression_signals(operation.value),
            )
        )

    raw_requests: dict[
        OutputPort,
        str,
    ] = {}

    output_requests: dict[
        OutputPort,
        str,
    ] = {}

    output_acks: dict[
        OutputPort,
        str,
    ] = {}

    for index, port in enumerate(
        architecture.output_ports
    ):
        operation = port.body_send.source.operation

        if not isinstance(
            operation,
            behavioral.Send,
        ):
            raise AsyncTemplateBindingError(
                "output port lacks Send"
            )

        slot = slots.get(port)

        if slot is None:
            raise AsyncTemplateBindingError(
                "output port has no M6 storage slot"
            )

        payload = _send_payload_type(operation)

        raw_requests[port] = ensure(
            f"output_{index}_raw_req",
            "request",
            behavioral.ONE_BIT,
        )

        output_requests[port] = ensure(
            f"output_{index}_req",
            "request",
            behavioral.ONE_BIT,
        )

        output_acks[port] = ensure(
            f"output_{index}_ack",
            "acknowledge",
            behavioral.ONE_BIT,
        )

        ext_req = external(
            operation.channel,
            "send",
            "request",
            "output",
            behavioral.ONE_BIT,
        )

        ext_ack = external(
            operation.channel,
            "send",
            "acknowledge",
            "input",
            behavioral.ONE_BIT,
        )

        ext_data = external(
            operation.channel,
            "send",
            "payload",
            "output",
            payload.width,
        )

        stage = send_stages.get(port)

        if stage is None:
            assignments.extend(
                (
                    BoundAsyncAssignment(
                        ext_req,
                        None,
                        port,
                        "signal_copy",
                        (output_requests[port],),
                    ),
                    BoundAsyncAssignment(
                        output_acks[port],
                        None,
                        port,
                        "signal_copy",
                        (ext_ack,),
                    ),
                    BoundAsyncAssignment(
                        ext_data,
                        None,
                        port,
                        "signal_copy",
                        (storage_data[slot],),
                    ),
                )
            )
        else:
            resource, delay = en_send_resources(stage)
            external_raw_req = ensure(
                f"output_{index}_external_raw_req",
                "request",
                behavioral.ONE_BIT,
            )
            storage_data_in = ensure(
                f"{resource.id}_data_in",
                "payload",
                payload.width,
                operation.channel,
            )
            storage_enable_signal = ensure(
                f"{resource.id}_storage_enable",
                "control",
                behavioral.ONE_BIT,
            )
            item = add_instance(
                f"en_send_controller_{index}",
                "en_send_controller",
                stage,
                enable_channel=stage.enable_channel,
            )

            _bind_en_send(
                item,
                stage,
                payload,
                external_raw_req,
                ext_ack,
                output_requests[port],
                output_acks[port],
                storage_data[slot],
                storage_data_in,
                storage_enable_signal,
                bind,
                parameter,
            )

            storage = add_instance(
                resource.id,
                "bundled_data_latch_bank",
                resource,
                storage_slot=resource,
                output_port=resource.output_port,
            )
            parameter(storage, "WIDTH", payload.width)
            for formal, actual in (
                ("data_in", storage_data_in),
                ("storage_enable", storage_enable_signal),
                ("data_out", ext_data),
            ):
                bind(storage, formal, actual)

            delay_item = add_instance(
                delay.id,
                "bundled_data_matched_delay",
                delay,
                storage_slot=resource,
                output_port=resource.output_port,
                value=delay.value,
            )
            bind(delay_item, "control_in", external_raw_req)
            bind(delay_item, "control_out", ext_req)

    _bind_output_request(
        architecture.output_request,
        raw_requests,
        base_raw_rreq,
        add_instance,
        bind,
        parameter,
        assignments,
        ensure,
    )

    _bind_output_ack(
        architecture.output_ack,
        output_acks,
        base_rack,
        reset_n,
        add_instance,
        bind,
        parameter,
        assignments,
        ensure,
    )

    for requirement in architecture.matched_delays:
        item = add_instance(
            requirement.id,
            "bundled_data_matched_delay",
            requirement,
            storage_slot=requirement.storage_slot,
            output_port=requirement.output_port,
            value=requirement.value,
        )

        bind(
            item,
            "control_in",
            raw_requests[requirement.output_port],
        )

        bind(
            item,
            "control_out",
            output_requests[requirement.output_port],
        )

    enable_bindings: list[
        BoundEnableChannel
    ] = []

    for index, channel in enumerate(
        architecture.enable_channels
    ):
        value = ensure(
            f"enable_channel_{index}_value",
            "payload",
            behavioral.ONE_BIT,
        )

        req = ensure(
            f"enable_channel_{index}_req",
            "request",
            behavioral.ONE_BIT,
        )

        ack = ensure(
            f"enable_channel_{index}_ack",
            "acknowledge",
            behavioral.ONE_BIT,
        )

        data = ensure(
            f"enable_channel_{index}_data",
            "payload",
            behavioral.ONE_BIT,
        )

        if channel.availability.value == "pre_input":
            launch = ensure(
                f"enable_channel_{index}_pre_input_launch",
                "control",
                behavioral.ONE_BIT,
            )
        else:
            launch = storage_enable

        sender = enable_senders[channel]

        for formal, actual in (
            ("value", value),
            ("launch", launch),
            ("req", req),
            ("ack", ack),
            ("data", data),
        ):
            bind(
                sender,
                formal,
                actual,
            )

        assignments.append(
            BoundAsyncAssignment(
                value,
                channel.enable.condition,
                channel.enable,
                "enable_value",
                expression_signals(channel.enable.condition),
            )
        )

        if channel.availability.value == "pre_input":
            assignments.append(
                BoundAsyncAssignment(
                    launch,
                    None,
                    architecture.validated,
                    "transaction_entry_launch",
                    (base_rack,),
                )
            )

        enable_bindings.append(
            BoundEnableChannel(
                channel,
                channel,
                channel.availability,
                channel.producer.participates_in_transaction_completion,
                req,
                ack,
                data,
                value,
                launch,
                sender,
            )
        )

    updated = [
        replace(
            item,
            component=item.template,
            required_formals=tuple(
                binding.formal_name
                for binding in bindings
                if binding.instance_id == item.id
            ),
            required_parameters=tuple(
                parameter_binding.formal_name
                for parameter_binding in parameters
                if parameter_binding.instance_id == item.id
            ),
        )
        for item in instances
    ]

    def one_body_receive(
        operation: behavioral.Receive,
        path: tuple[int | str, ...],
    ) -> tuple[BodyReceive, InputPort]:
        matches = [
            (port.body_receive, port)
            for port in architecture.input_ports
            if port.body_receive.source.operation is operation
            and port.body_receive.source.path == path
        ]
        if len(matches) != 1:
            raise AsyncTemplateBindingError(
                "missing or ambiguous InputPort binding for BODY Receive"
            )
        return matches[0]

    def one_combinational_operation(
        operation: behavioral.Assign,
        path: tuple[int | str, ...],
    ) -> RegionOperation:
        matches = [
            source
            for source in architecture.combinational.operations
            if source.operation is operation and source.path == path
        ]
        if len(matches) != 1:
            raise AsyncTemplateBindingError(
                "missing or ambiguous combinational RegionOperation binding for Assign"
            )
        return matches[0]

    def bind_body_process(
        process: behavioral.Process,
        path: tuple[int | str, ...],
    ) -> BoundBodyProcess:
        if isinstance(process, behavioral.Sequence):
            return BoundBodySequence(tuple(
                bind_body_process(item, path + (index,))
                for index, item in enumerate(process.items)
            ))
        if isinstance(process, behavioral.Parallel):
            return BoundBodyParallel(tuple(
                bind_body_process(branch, path + ("parallel", index))
                for index, branch in enumerate(process.branches)
            ))
        if isinstance(process, behavioral.If):
            return BoundBodyIf(
                process.condition,
                bind_body_process(process.then_branch, path + ("then",)),
                bind_body_process(process.else_branch, path + ("else",)),
            )
        if isinstance(process, behavioral.Receive):
            source, input_port = one_body_receive(process, path)
            return BoundBodyReceiveWrite(
                source,
                input_port,
                whole_variable_target(process.target),
            )
        if isinstance(process, behavioral.Assign):
            return BoundBodyAssignWrite(
                one_combinational_operation(process, path),
                whole_variable_target(process.target),
                process.value,
            )
        if isinstance(process, (behavioral.Send, behavioral.Skip)):
            return BoundBodySkip(process)
        raise AsyncTemplateBindingError(
            f"unsupported BODY process {type(process).__name__}"
        )

    body_program = bind_body_process(behavioral_module.body, ())

    return BoundAsyncModule(
        architecture,
        tuple(updated),
        tuple(signals),
        tuple(ports),
        tuple(
            BoundAsyncConnection(
                binding.actual_signal_id,
                f"{binding.instance_id}.{binding.formal_name}",
                "formal",
            )
            for binding in bindings
        ),
        tuple(bindings),
        tuple(parameters),
        tuple(module_parameters),
        tuple(enable_bindings),
        tuple(assignments),
        tuple(variables),
        body_program,
    )


def _bind_input_request(
    selected,
    body,
    base,
    reset,
    add,
    bind,
    parameter,
    assignments,
    ensure,
):
    if isinstance(
        selected,
        RequestJoin,
    ):
        item = add(
            "request_join",
            "four_phase_request_join",
            selected,
            inputs=selected.inputs,
        )

        vector = ensure(
            "input_request_vector",
            "request",
            behavioral.PayloadWidth(
                bits=len(selected.inputs)
            ),
        )

        assignments.append(
            BoundAsyncAssignment(
                vector,
                None,
                selected,
                "pack_control_vector",
                tuple(
                    body[port][0]
                    for port in selected.inputs
                ),
            )
        )

        for formal, actual in (
            ("reset_n", reset),
            ("input_req", vector),
            ("base_Lreq", base),
        ):
            bind(
                item,
                formal,
                actual,
            )

        parameter(
            item,
            "N",
            behavioral.PayloadWidth(
                bits=len(selected.inputs)
            ),
        )

        return

    if isinstance(
        selected,
        InputRequestDirectConnection,
    ):
        assignments.append(
            BoundAsyncAssignment(
                base,
                None,
                selected,
                "signal_copy",
                (
                    body[selected.input][0],
                ),
            )
        )

        return

    raise AsyncTemplateBindingError(
        "unsupported M6 input request topology"
    )


def _bind_input_ack(
    selected,
    body,
    base,
    add,
    bind,
    parameter,
    assignments,
    ensure,
):
    if isinstance(
        selected,
        InputAckFanout,
    ):
        item = add(
            "input_ack_fanout",
            "four_phase_ack_fanout",
            selected,
            inputs=selected.inputs,
        )

        vector = ensure(
            "input_ack_vector",
            "acknowledge",
            behavioral.PayloadWidth(
                bits=len(selected.inputs)
            ),
        )

        bind(
            item,
            "base_Lack",
            base,
        )

        bind(
            item,
            "input_ack",
            vector,
        )

        parameter(
            item,
            "N",
            behavioral.PayloadWidth(
                bits=len(selected.inputs)
            ),
        )

        for index, port in enumerate(
            selected.inputs
        ):
            assignments.append(
                BoundAsyncAssignment(
                    body[port][1],
                    None,
                    selected,
                    "unpack_control_vector",
                    (vector,),
                    index,
                )
            )

        return

    if isinstance(
        selected,
        InputAckDirectConnection,
    ):
        assignments.append(
            BoundAsyncAssignment(
                body[selected.input][1],
                None,
                selected,
                "signal_copy",
                (base,),
            )
        )

        return

    raise AsyncTemplateBindingError(
        "unsupported M6 input acknowledgement topology"
    )


def _bind_output_request(
    selected,
    raw,
    base,
    add,
    bind,
    parameter,
    assignments,
    ensure,
):
    if isinstance(
        selected,
        RequestFanout,
    ):
        item = add(
            "output_request_fanout",
            "four_phase_request_fanout",
            selected,
            outputs=selected.outputs,
        )

        vector = ensure(
            "output_raw_request_vector",
            "request",
            behavioral.PayloadWidth(
                bits=len(selected.outputs)
            ),
        )

        bind(
            item,
            "base_raw_Rreq",
            base,
        )

        bind(
            item,
            "output_raw_Rreq",
            vector,
        )

        parameter(
            item,
            "M",
            behavioral.PayloadWidth(
                bits=len(selected.outputs)
            ),
        )

        for index, port in enumerate(
            selected.outputs
        ):
            assignments.append(
                BoundAsyncAssignment(
                    raw[port],
                    None,
                    selected,
                    "unpack_control_vector",
                    (vector,),
                    index,
                )
            )

        return

    if isinstance(
        selected,
        OutputRequestDirectConnection,
    ):
        assignments.append(
            BoundAsyncAssignment(
                raw[selected.output],
                None,
                selected,
                "signal_copy",
                (base,),
            )
        )

        return

    raise AsyncTemplateBindingError(
        "unsupported M6 output request topology"
    )


def _bind_output_ack(
    selected,
    acks,
    base,
    reset,
    add,
    bind,
    parameter,
    assignments,
    ensure,
):
    if isinstance(
        selected,
        AckJoin,
    ):
        item = add(
            "output_ack_join",
            "four_phase_ack_join",
            selected,
            outputs=selected.outputs,
        )

        vector = ensure(
            "output_ack_vector",
            "acknowledge",
            behavioral.PayloadWidth(
                bits=len(selected.outputs)
            ),
        )

        assignments.append(
            BoundAsyncAssignment(
                vector,
                None,
                selected,
                "pack_control_vector",
                tuple(
                    acks[port]
                    for port in selected.outputs
                ),
            )
        )

        for formal, actual in (
            ("reset_n", reset),
            ("output_ack", vector),
            ("base_Rack", base),
        ):
            bind(
                item,
                formal,
                actual,
            )

        parameter(
            item,
            "M",
            behavioral.PayloadWidth(
                bits=len(selected.outputs)
            ),
        )

        return

    if isinstance(
        selected,
        OutputAckDirectConnection,
    ):
        assignments.append(
            BoundAsyncAssignment(
                base,
                None,
                selected,
                "signal_copy",
                (
                    acks[selected.output],
                ),
            )
        )

        return

    raise AsyncTemplateBindingError(
        "unsupported M6 output acknowledgement topology"
    )


def _enable_index(
    channel: EnableChannel,
) -> str:
    return channel.id.rsplit("_", 1)[-1]


def _bind_en_receive(
    item,
    stage: EnReceiveStage,
    payload,
    ext_req,
    ext_ack,
    ext_data,
    raw_req,
    req,
    ack,
    storage_data,
    storage_enable,
    bind,
    parameter,
):
    index = _enable_index(
        stage.enable_channel
    )

    for formal, actual in (
        (
            "enable_req",
            f"enable_channel_{index}_req",
        ),
        (
            "enable_ack",
            f"enable_channel_{index}_ack",
        ),
        (
            "enable_data",
            f"enable_channel_{index}_data",
        ),
        ("external_req", ext_req),
        ("external_ack", ext_ack),
        ("external_data", ext_data),
        ("body_raw_req", raw_req),
        ("body_ack", ack),
        ("storage_data", storage_data),
        ("storage_enable", storage_enable),
    ):
        bind(
            item,
            formal,
            actual,
        )

    parameter(
        item,
        "WIDTH",
        payload.width,
    )


def _bind_en_send(
    item,
    stage: EnSendStage,
    payload,
    ext_req,
    ext_ack,
    req,
    ack,
    data,
    storage_data,
    storage_enable,
    bind,
    parameter,
):
    index = _enable_index(
        stage.enable_channel
    )

    for formal, actual in (
        (
            "enable_req",
            f"enable_channel_{index}_req",
        ),
        (
            "enable_ack",
            f"enable_channel_{index}_ack",
        ),
        (
            "enable_data",
            f"enable_channel_{index}_data",
        ),
        ("body_req", req),
        ("body_ack", ack),
        ("body_data", data),
        ("external_raw_req", ext_req),
        ("external_ack", ext_ack),
        ("storage_data", storage_data),
        ("storage_enable", storage_enable),
    ):
        bind(
            item,
            formal,
            actual,
        )

    parameter(
        item,
        "WIDTH",
        payload.width,
    )


def _endpoint_name(
    endpoint,
):
    selectors = "_".join(
        _selector_name(item)
        for item in endpoint.selectors
    )

    return (
        _IDENTIFIER.sub(
            "_",
            "_".join(
                item
                for item in (
                    endpoint.name,
                    selectors,
                )
                if item
            ),
        )
        or "channel"
    )


def _selector_name(
    expression,
):
    if (
        expression.form == "index"
        and expression.operands
    ):
        return _selector_name(
            expression.operands[0]
        )

    if expression.value is not None:
        return expression.value

    return (
        expression.variable.name
        if expression.variable is not None
        else expression.form
    )


def _target_variable(
    target,
):
    return (
        target
        if isinstance(
            target,
            behavioral.Variable,
        )
        else target.variable
    )


def _receive_payload_type(
    operation,
):
    target = _target_variable(
        operation.target
    )

    payload = (
        operation.channel.payload_type
        or (
            target.payload_type
            if target
            else None
        )
    )

    if payload is None:
        raise AsyncTemplateBindingError(
            "Receive has no payload width"
        )

    return payload


def _send_payload_type(
    operation,
):
    payload = (
        operation.channel.payload_type
        or behavioral.expression_payload_type(
            operation.value
        )
    )

    if payload is None:
        raise AsyncTemplateBindingError(
            "Send has no payload width"
        )

    return payload
