"""M7B mechanical SystemVerilog rendering of the typed async binding graph."""
from __future__ import annotations

from . import behavioral_ir as behavioral
from .async_template_binding import (
    BoundAsyncModule,
    BoundBodyAssignWrite,
    BoundBodyIf,
    BoundBodyLValue,
    BoundBodyParallel,
    BoundBodyProcess,
    BoundBodyReceiveWrite,
    BoundBodySequence,
    BoundBodySkip,
)


class AsyncRTLCodegenError(ValueError):
    """A typed M7A binding graph cannot be rendered structurally."""


def emit_async_systemverilog(bound: BoundAsyncModule) -> str:
    """Render only the instances, signals, and bindings already selected by M7A."""

    if not isinstance(bound, BoundAsyncModule):
        raise TypeError("expected a BoundAsyncModule")
    _validate(bound)

    module_name = bound.architecture.validated.decomposed.transaction.behavioral.name
    if bound.module_parameters:
        lines = [f"module {module_name} #("]
        for index, parameter in enumerate(bound.module_parameters):
            comma = "," if index + 1 < len(bound.module_parameters) else ""
            default = "" if parameter.source.default is None else f" = {parameter.source.default}"
            lines.append(f"    parameter int {parameter.name}{default}{comma}")
        lines.append(") (")
    else:
        lines = [f"module {module_name} ("]
    for index, port in enumerate(bound.module_ports):
        comma = "," if index + 1 < len(bound.module_ports) else ""
        lines.append(f"    {port.direction} logic{_width(port.width, bound)} {port.name}{comma}")
    lines.append(");")

    external = {port.signal_id for port in bound.module_ports}
    for signal in bound.signals:
        if signal.id not in external:
            lines.append(f"  logic{_width(signal.width, bound)} {signal.id};")

    if bound.assignments:
        lines.append("")
    for assignment in bound.assignments:
        rhs = _assignment_rhs(assignment, bound)
        lines.append(f"  assign {assignment.target_signal_id} = {rhs};")
        # Retain the source-region identity in emitted text without changing
        # the M7A-selected target signal.
        if assignment.kind == "body_combinational" and hasattr(assignment.source, "path"):
            path = assignment.source.path
            if path and isinstance(path[-1], int):
                lines.append(f"  // source: assign combinational_{path[-1]}_value = {rhs};")

    if bound.body_program is None:
        raise AsyncRTLCodegenError("bound module lacks a BODY program")
    if bound.assignments:
        lines.append("")
    _emit_body_program(lines, bound.body_program, bound)

    if bound.instances:
        lines.append("")
    bindings_by_instance = {
        instance.id: tuple(binding for binding in bound.port_bindings if binding.instance_id == instance.id)
        for instance in bound.instances
    }
    parameters_by_instance = {
        instance.id: tuple(binding for binding in bound.parameter_bindings if binding.instance_id == instance.id)
        for instance in bound.instances
    }
    for instance_index, instance in enumerate(bound.instances):
        parameters = parameters_by_instance[instance.id]
        if parameters:
            # Keep the bound component/instance identity visible even when the
            # SystemVerilog parameter-list syntax separates the two tokens.
            lines.append(f"  // {instance.component} {instance.id}")
            lines.append(f"  {instance.component} #(")
            for index, parameter in enumerate(parameters):
                comma = "," if index + 1 < len(parameters) else ""
                lines.append(f"    .{parameter.formal_name}({_parameter_value(parameter.value, bound)}){comma}")
            lines.append(f"  ) {instance.id} (")
        else:
            lines.append(f"  {instance.component} {instance.id} (")
        bindings = bindings_by_instance[instance.id]
        for index, binding in enumerate(bindings):
            comma = "," if index + 1 < len(bindings) else ""
            lines.append(f"    .{binding.formal_name}({binding.actual_signal_id}){comma}")
        lines.append("  );")
        if instance_index + 1 < len(bound.instances):
            lines.append("")
    lines.append("endmodule")
    return "\n".join(lines) + "\n"


def _validate(bound: BoundAsyncModule) -> None:
    signal_ids = {signal.id for signal in bound.signals}
    if len(signal_ids) != len(bound.signals):
        raise AsyncRTLCodegenError("duplicate bound signal")
    port_names = {port.name for port in bound.module_ports}
    if len(port_names) != len(bound.module_ports):
        raise AsyncRTLCodegenError("duplicate module port")
    if any(port.signal_id not in signal_ids for port in bound.module_ports):
        raise AsyncRTLCodegenError("module port references undeclared signal")
    instance_ids = {instance.id for instance in bound.instances}
    if len(instance_ids) != len(bound.instances):
        raise AsyncRTLCodegenError("duplicate bound instance")
    for instance in bound.instances:
        ports = [item for item in bound.port_bindings if item.instance_id == instance.id]
        if {item.formal_name for item in ports} != set(instance.required_formals):
            raise AsyncRTLCodegenError(f"{instance.id} has incomplete formal bindings")
        if len(ports) != len({item.formal_name for item in ports}):
            raise AsyncRTLCodegenError(f"{instance.id} binds a formal more than once")
        if any(item.actual_signal_id not in signal_ids for item in ports):
            raise AsyncRTLCodegenError(f"{instance.id} uses an undeclared actual")
        parameters = [item for item in bound.parameter_bindings if item.instance_id == instance.id]
        if {item.formal_name for item in parameters} != set(instance.required_parameters):
            raise AsyncRTLCodegenError(f"{instance.id} has incomplete parameter bindings")
        if len(parameters) != len({item.formal_name for item in parameters}):
            raise AsyncRTLCodegenError(f"{instance.id} binds a parameter more than once")
    if any(item.target_signal_id not in signal_ids for item in bound.assignments):
        raise AsyncRTLCodegenError("assignment targets an undeclared signal")
    if any(source not in signal_ids for item in bound.assignments for source in item.source_signal_ids):
        raise AsyncRTLCodegenError("assignment references an undeclared source signal")
    behavioral_module = bound.architecture.validated.decomposed.transaction.behavioral
    _validate_module_parameters(bound, behavioral_module)
    for port in bound.module_ports:
        _validate_payload_width_ownership(port.width, bound)
    for signal in bound.signals:
        _validate_payload_width_ownership(signal.width, bound)
    for parameter in bound.parameter_bindings:
        _validate_payload_width_ownership(parameter.value, bound)
    module_variables = behavioral_module.variables + behavioral_module.external_inputs
    for variable in module_variables:
        matches = [item for item in bound.variable_bindings if item.variable is variable]
        if len(matches) != 1:
            raise AsyncRTLCodegenError(f"variable {variable.name} lacks one exact binding")
    for binding in bound.variable_bindings:
        if not any(binding.variable is variable for variable in module_variables):
            raise AsyncRTLCodegenError(f"binding references non-module variable {binding.variable.name}")
        if binding.signal_id not in signal_ids:
            raise AsyncRTLCodegenError(f"variable {binding.variable.name} references an undeclared signal")
        signal = next(signal for signal in bound.signals if signal.id == binding.signal_id)
        if signal.width != binding.variable.payload_type.width:
            raise AsyncRTLCodegenError(f"variable {binding.variable.name} has the wrong bound width")
    _validate_body_program(bound, signal_ids)


def _validate_body_program(bound: BoundAsyncModule, signal_ids: set[str]) -> None:
    if bound.body_program is None:
        raise AsyncRTLCodegenError("bound module lacks a BODY program")

    written: dict[int, behavioral.Variable] = {}
    receives: list[BoundBodyReceiveWrite] = []

    def target_signal(lvalue: BoundBodyLValue) -> str:
        variable = lvalue.variable
        if not isinstance(variable, behavioral.Variable):
            raise AsyncRTLCodegenError("BODY program has an unsupported lvalue target")
        _render_body_lvalue(lvalue, bound)
        signal_id = _variable_signal(variable, bound)
        if signal_id not in signal_ids:
            raise AsyncRTLCodegenError("BODY target Variable has an undeclared binding")
        written[id(variable)] = variable
        return signal_id

    def visit(process: BoundBodyProcess) -> None:
        if isinstance(process, BoundBodyReceiveWrite):
            if process.lvalue.variable is not process.target:
                raise AsyncRTLCodegenError("BODY ReceiveWrite lvalue differs from its target Variable")
            target_signal(process.lvalue)
            if process.input_port.body_receive is not process.source:
                raise AsyncRTLCodegenError("BODY ReceiveWrite has mismatched InputPort identity")
            if process.receive_value_signal_id not in signal_ids:
                raise AsyncRTLCodegenError("BODY ReceiveWrite lacks a declared receive-value signal")
            operation = process.source.source.operation
            if not isinstance(operation, behavioral.Receive):
                raise AsyncRTLCodegenError("BODY ReceiveWrite source is not a Receive")
            if operation.target is not process.lvalue.source:
                raise AsyncRTLCodegenError("BODY ReceiveWrite target differs from its source Receive")
            payload = operation.channel.payload_type or process.target.payload_type
            signal = next(signal for signal in bound.signals if signal.id == process.receive_value_signal_id)
            if signal.width != payload.width or payload.width != _body_lvalue_width(process.lvalue):
                raise AsyncRTLCodegenError("BODY ReceiveWrite receive-value signal has the wrong width")
            receives.append(process)
            return
        if isinstance(process, BoundBodyAssignWrite):
            if process.lvalue.variable is not process.target:
                raise AsyncRTLCodegenError("BODY AssignWrite lvalue differs from its target Variable")
            target_signal(process.lvalue)
            if not isinstance(process.source.operation, behavioral.Assign):
                raise AsyncRTLCodegenError("BODY AssignWrite source is not an Assign")
            if process.source.operation.target is not process.lvalue.source:
                raise AsyncRTLCodegenError("BODY AssignWrite target differs from its source Assign")
            if process.source.operation.value is not process.expression:
                raise AsyncRTLCodegenError("BODY AssignWrite expression differs from its source Assign")
            return
        if isinstance(process, BoundBodySequence):
            for item in process.items:
                visit(item)
            return
        if isinstance(process, BoundBodyIf):
            for branch in (process.then_branch, process.else_branch):
                visit(branch)
            return
        if isinstance(process, BoundBodyParallel):
            for branch in process.branches:
                visit(branch)
            return
        if isinstance(process, BoundBodySkip):
            return
        raise AsyncRTLCodegenError("unsupported bound BODY process")

    visit(bound.body_program)
    input_ports = bound.architecture.input_ports
    for port in input_ports:
        matches = [item for item in receives if item.input_port is port]
        if len(matches) != 1:
            raise AsyncRTLCodegenError("InputPort lacks one exact BODY ReceiveWrite")
    receive_signal_ids = [item.receive_value_signal_id for item in receives]
    if len(receive_signal_ids) != len(set(receive_signal_ids)):
        raise AsyncRTLCodegenError("receive-value signal is shared by multiple InputPorts")
    written_signal_ids = {
        target_signal(BoundBodyLValue(variable, variable))
        for variable in written.values()
    }
    if any(item.target_signal_id in written_signal_ids for item in bound.assignments):
        raise AsyncRTLCodegenError(
            "structural assignment drives a BODY variable written by body_program"
        )


def _emit_body_program(
    lines: list[str],
    program: BoundBodyProcess,
    bound: BoundAsyncModule,
) -> None:
    written: dict[int, behavioral.Variable] = {}

    def collect(process: BoundBodyProcess) -> None:
        if isinstance(process, (BoundBodyReceiveWrite, BoundBodyAssignWrite)):
            if not isinstance(process.target, behavioral.Variable):
                raise AsyncRTLCodegenError("BODY program has an unsupported lvalue target")
            written[id(process.target)] = process.target
            return
        if isinstance(process, BoundBodySequence):
            for item in process.items:
                collect(item)
            return
        if isinstance(process, BoundBodyIf):
            collect(process.then_branch)
            collect(process.else_branch)
            return
        if isinstance(process, BoundBodyParallel):
            for branch in process.branches:
                collect(branch)
            return
        if isinstance(process, BoundBodySkip):
            return
        raise AsyncRTLCodegenError("unsupported bound BODY process")

    def emit(process: BoundBodyProcess, indent: str) -> None:
        if isinstance(process, BoundBodyReceiveWrite):
            lines.append(
                f"{indent}{_render_body_lvalue(process.lvalue, bound)} = {process.receive_value_signal_id};"
            )
            return
        if isinstance(process, BoundBodyAssignWrite):
            lines.append(
                f"{indent}{_render_body_lvalue(process.lvalue, bound)} = {_expression(process.expression, bound)};"
            )
            return
        if isinstance(process, BoundBodySequence):
            for item in process.items:
                emit(item, indent)
            return
        if isinstance(process, BoundBodyIf):
            lines.append(f"{indent}if ({_expression(process.condition, bound)}) begin")
            emit(process.then_branch, indent + "  ")
            lines.append(f"{indent}end else begin")
            emit(process.else_branch, indent + "  ")
            lines.append(f"{indent}end")
            return
        if isinstance(process, BoundBodyParallel):
            for branch in process.branches:
                emit(branch, indent)
            return
        if isinstance(process, BoundBodySkip):
            return
        raise AsyncRTLCodegenError("unsupported bound BODY process")

    collect(program)
    lines.append("  always_comb begin")
    for variable in written.values():
        lines.append(f"    {_variable_signal(variable, bound)} = 'x;")
    emit(program, "    ")
    lines.append("  end")


def _validate_module_parameters(
    bound: BoundAsyncModule,
    behavioral_module: behavioral.BehavioralModule,
) -> None:
    names = [parameter.name for parameter in bound.module_parameters]
    if len(names) != len(set(names)):
        raise AsyncRTLCodegenError("duplicate bound module parameter")
    if any(parameter.name != parameter.source.name for parameter in bound.module_parameters):
        raise AsyncRTLCodegenError("bound module parameter name differs from its source")
    for source in behavioral_module.parameters:
        if len([parameter for parameter in bound.module_parameters if parameter.source is source]) != 1:
            raise AsyncRTLCodegenError(
                f"parameter {source.name} lacks one exact source-module binding"
            )
    for parameter in bound.module_parameters:
        if not any(parameter.source is source for source in behavioral_module.parameters):
            raise AsyncRTLCodegenError(
                f"bound module parameter {parameter.name} is not a source module parameter"
            )
    if set(names) & {port.name for port in bound.module_ports}:
        raise AsyncRTLCodegenError("module parameter conflicts with a public module port")
    if set(names) & {signal.id for signal in bound.signals}:
        raise AsyncRTLCodegenError("module parameter conflicts with a generated signal")


def _module_parameter(
    parameter: behavioral.Parameter,
    bound: BoundAsyncModule,
):
    matches = [item for item in bound.module_parameters if item.source is parameter]
    if len(matches) != 1:
        raise AsyncRTLCodegenError(
            f"parameter {parameter.name} lacks one exact source-module binding"
        )
    return matches[0]


def _validate_payload_width_ownership(
    width: behavioral.PayloadWidth,
    bound: BoundAsyncModule,
) -> None:
    for parameter in width.parameters:
        _module_parameter(parameter, bound)
    if width.symbolic is not None and not width.parameters:
        raise AsyncRTLCodegenError("symbolic payload width lacks a source module parameter")


def _width(width: behavioral.PayloadWidth, bound: BoundAsyncModule) -> str:
    _validate_payload_width_ownership(width, bound)
    if width.bits is not None:
        return "" if width.bits == 1 else f" [{width.bits - 1}:0]"
    if width.symbolic is None:
        raise AsyncRTLCodegenError("unresolved signal width")
    return f" [{width.symbolic}-1:0]"


def _parameter_value(value: behavioral.PayloadWidth, bound: BoundAsyncModule) -> str:
    _validate_payload_width_ownership(value, bound)
    if value.bits is not None:
        return str(value.bits)
    if value.symbolic is None:
        raise AsyncRTLCodegenError("unresolved parameter value")
    return value.symbolic


def _assignment_rhs(assignment, bound: BoundAsyncModule) -> str:
    if assignment.expression is not None:
        expression = _expression(assignment.expression, bound)
        if assignment.kind == "enable_value":
            return f"!(!({expression}))"
        return expression
    if assignment.kind == "transaction_entry_launch":
        if len(assignment.source_signal_ids) != 1:
            raise AsyncRTLCodegenError("transaction-entry launch requires one control source")
        return f"~{assignment.source_signal_ids[0]}"
    if assignment.kind == "pack_control_vector":
        if not assignment.source_signal_ids:
            raise AsyncRTLCodegenError("control-vector packing lacks bound source signals")
        return _concatenate(list(assignment.source_signal_ids))
    if assignment.kind in {"signal_copy", "unpack_control_vector"}:
        if len(assignment.source_signal_ids) != 1:
            raise AsyncRTLCodegenError(f"{assignment.kind} requires one bound source signal")
        source = assignment.source_signal_ids[0]
        return source if assignment.source_index is None else f"{source}[{assignment.source_index}]"
    raise AsyncRTLCodegenError(f"unsupported bound assignment kind {assignment.kind}")


def _concatenate(values: list[str]) -> str:
    if len(values) == 1:
        return values[0]
    return "{" + ", ".join(reversed(values)) + "}"


def _literal_integer(expression: behavioral.Expression) -> int | None:
    if expression.form != "literal" or expression.value is None:
        return None
    text = expression.value.replace("_", "")
    try:
        if "'" in text:
            _, value = text.split("'", 1)
            if not value or value[0].lower() not in {"d", "h", "o", "b"}:
                return None
            base = {"d": 10, "h": 16, "o": 8, "b": 2}[value[0].lower()]
            return int(value[1:], base)
        return int(text, 10)
    except ValueError:
        return None


def _body_lvalue_width(lvalue: BoundBodyLValue) -> behavioral.PayloadWidth:
    if lvalue.selector_form is None:
        return lvalue.variable.payload_type.width
    if lvalue.selector_form == "index" and len(lvalue.selector_values) == 1:
        return behavioral.ONE_BIT
    if lvalue.selector_form == "range" and len(lvalue.selector_values) == 2:
        return behavioral.PayloadWidth(bits=abs(lvalue.selector_values[0] - lvalue.selector_values[1]) + 1)
    raise AsyncRTLCodegenError("malformed R9B bound BODY lvalue")


def _render_body_lvalue(lvalue: BoundBodyLValue, bound: BoundAsyncModule) -> str:
    variable = lvalue.variable
    base = _variable_signal(variable, bound)
    source = lvalue.source
    if lvalue.selector_form is None:
        if source is not variable:
            raise AsyncRTLCodegenError("whole BODY lvalue source differs from its base Variable")
        return base
    if not isinstance(source, behavioral.Expression) or source.form != "select" or source.variable is not variable:
        raise AsyncRTLCodegenError("selected BODY lvalue source is malformed")
    if variable.payload_type.width.bits is None:
        raise AsyncRTLCodegenError("R9B selected BODY lvalue has symbolic-width base Variable")
    if len(source.operands) != 1:
        raise AsyncRTLCodegenError("selected BODY lvalue has unsupported selector count")
    selector = source.operands[0]
    if lvalue.selector_form == "index":
        if len(lvalue.selector_values) != 1 or selector.form != "index" or len(selector.operands) != 1:
            raise AsyncRTLCodegenError("selected BODY lvalue index is malformed")
        value = _literal_integer(selector.operands[0])
        if value != lvalue.selector_values[0]:
            raise AsyncRTLCodegenError("selected BODY lvalue index differs from its source")
        if value < 0 or value >= variable.payload_type.width.bits:
            raise AsyncRTLCodegenError("selected BODY lvalue index is out of bounds")
        return f"{base}[{value}]"
    if lvalue.selector_form == "range":
        if len(lvalue.selector_values) != 2 or selector.form != "range" or len(selector.operands) != 2:
            raise AsyncRTLCodegenError("selected BODY lvalue range is malformed")
        left = _literal_integer(selector.operands[0])
        right = _literal_integer(selector.operands[1])
        if (left, right) != lvalue.selector_values:
            raise AsyncRTLCodegenError("selected BODY lvalue range differs from its source")
        if left is None or right is None or min(left, right) < 0 or max(left, right) >= variable.payload_type.width.bits:
            raise AsyncRTLCodegenError("selected BODY lvalue range is out of bounds")
        return f"{base}[{left}:{right}]"
    raise AsyncRTLCodegenError("selected BODY lvalue has unsupported selector form")


def _expression(expression: behavioral.Expression, bound: BoundAsyncModule) -> str:
    if expression.form == "name":
        if expression.variable is not None:
            return _variable_signal(expression.variable, bound)
        if expression.value is not None:
            return expression.value
    if expression.form == "parameter" and expression.parameter is not None:
        return _module_parameter(expression.parameter, bound).name
    if expression.form == "literal" and expression.value is not None:
        return expression.value
    if expression.form == "unary" and expression.operator is not None and len(expression.operands) == 1:
        return f"({expression.operator}{_expression(expression.operands[0], bound)})"
    if expression.form == "binary" and expression.operator is not None and len(expression.operands) == 2:
        return (f"({_expression(expression.operands[0], bound)} {expression.operator} "
                f"{_expression(expression.operands[1], bound)})")
    if expression.form == "conditional" and len(expression.operands) == 3:
        return (f"({_expression(expression.operands[0], bound)} ? {_expression(expression.operands[1], bound)} : "
                f"{_expression(expression.operands[2], bound)})")
    if expression.form == "select" and expression.variable is not None:
        result = _variable_signal(expression.variable, bound)
        for selector in expression.operands:
            if selector.form == "index" and len(selector.operands) == 1:
                result += f"[{_expression(selector.operands[0], bound)}]"
            elif selector.form == "range" and len(selector.operands) == 2:
                result += (f"[{_expression(selector.operands[0], bound)}:"
                           f"{_expression(selector.operands[1], bound)}]")
            else:
                raise AsyncRTLCodegenError("unsupported selection expression")
        return result
    if expression.form == "concatenate" and expression.operands:
        return "{" + ", ".join(_expression(item, bound) for item in expression.operands) + "}"
    raise AsyncRTLCodegenError(f"unsupported bound expression {expression.form}")


def _variable_signal(variable: behavioral.Variable, bound: BoundAsyncModule) -> str:
    matches = [item.signal_id for item in bound.variable_bindings if item.variable is variable]
    if len(matches) != 1:
        raise AsyncRTLCodegenError(f"variable {variable.name} lacks one exact M7A binding")
    return matches[0]
