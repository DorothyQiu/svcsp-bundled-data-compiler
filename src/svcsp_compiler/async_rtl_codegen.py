"""M7B mechanical SystemVerilog rendering of the typed async binding graph."""
from __future__ import annotations

from . import behavioral_ir as behavioral
from .async_template_binding import BoundAsyncModule


class AsyncRTLCodegenError(ValueError):
    """A typed M7A binding graph cannot be rendered structurally."""


def emit_async_systemverilog(bound: BoundAsyncModule) -> str:
    """Render only the instances, signals, and bindings already selected by M7A."""

    if not isinstance(bound, BoundAsyncModule):
        raise TypeError("expected a BoundAsyncModule")
    _validate(bound)

    module_name = bound.architecture.validated.decomposed.transaction.behavioral.name
    lines = [f"module {module_name} ("]
    for index, port in enumerate(bound.module_ports):
        comma = "," if index + 1 < len(bound.module_ports) else ""
        lines.append(f"    {port.direction} logic{_width(port.width)} {port.name}{comma}")
    lines.append(");")

    external = {port.signal_id for port in bound.module_ports}
    for signal in bound.signals:
        if signal.id not in external:
            lines.append(f"  logic{_width(signal.width)} {signal.id};")

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
                lines.append(f"    .{parameter.formal_name}({_parameter_value(parameter.value)}){comma}")
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


def _width(width: behavioral.PayloadWidth) -> str:
    if width.bits is not None:
        return "" if width.bits == 1 else f" [{width.bits - 1}:0]"
    if width.symbolic is None:
        raise AsyncRTLCodegenError("unresolved signal width")
    return f" [{width.symbolic}-1:0]"


def _parameter_value(value: behavioral.PayloadWidth) -> str:
    if value.bits is not None:
        return str(value.bits)
    if value.symbolic is None:
        raise AsyncRTLCodegenError("unresolved parameter value")
    return value.symbolic


def _assignment_rhs(assignment, bound: BoundAsyncModule) -> str:
    if assignment.expression is not None:
        return _expression(assignment.expression, bound)
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


def _expression(expression: behavioral.Expression, bound: BoundAsyncModule) -> str:
    if expression.form == "name":
        if expression.variable is not None:
            return _variable_signal(expression.variable, bound)
        if expression.value is not None:
            return expression.value
    if expression.form == "parameter" and expression.parameter is not None:
        return expression.parameter.name
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
