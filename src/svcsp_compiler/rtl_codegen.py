"""Phase 7B: deterministic structural SystemVerilog emission.

This module only renders the contracts and bindings established by Phase 7A.
It does not select templates, alter topology, infer widths, or implement any
of the referenced structural templates.
"""
from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
import hashlib
import re

from . import behavioral_ir as behavioral
from . import template_binding as binding


class RTLCodegenError(ValueError):
    """A bound structural graph cannot be rendered without an HDL decision."""


_IDENTIFIER = re.compile(r'^[A-Za-z_][A-Za-z0-9_$]*$')
_IDENTIFIER_PART = re.compile(r'[^A-Za-z0-9_$]')
_WORD = re.compile(r'\b[A-Za-z_][A-Za-z0-9_$]*\b')
_LITERAL = re.compile(r"^(?:[0-9][0-9A-Za-z_']*|'[01xXzZ])$")
_SV_KEYWORDS = frozenset({
    'always', 'assign', 'begin', 'case', 'end', 'endmodule', 'for', 'function',
    'if', 'input', 'logic', 'module', 'output', 'parameter', 'reg', 'wire',
})
_UNARY_OPERATORS = frozenset({'+', '-', '~', '!', '&', '~&', '|', '~|', '^', '~^', '^~'})
_BINARY_OPERATORS = frozenset({
    '+', '-', '*', '/', '%', '&', '|', '^', '~^', '^~', '<<', '>>', '<<<', '>>>',
    '&&', '||', '==', '!=', '===', '!==', '<', '<=', '>', '>=',
})


@dataclass
class _Names:
    """Allocate legal, deterministic names within one emitted module."""

    used: set[str]

    def allocate(self, prefix: str, identity: str, *, preserve: bool = False) -> str:
        if not isinstance(identity, str) or not identity:
            raise RTLCodegenError('invalid identifier mapping')
        base = _IDENTIFIER_PART.sub('_', identity)
        if not base or base[0].isdigit() or base in _SV_KEYWORDS:
            base = f'{prefix}_{base}'
        candidate = base if preserve else f'{prefix}_{base}'
        if candidate not in self.used:
            self.used.add(candidate)
            return candidate
        digest = hashlib.sha1(identity.encode('utf-8')).hexdigest()[:10]
        candidate = f'{candidate}_{digest}'
        if candidate in self.used:
            raise RTLCodegenError(f'cannot create collision-safe identifier for {identity!r}')
        self.used.add(candidate)
        return candidate


def _instances(graph: binding.BoundStructuralGraph) -> tuple[tuple[str, binding.StructuralTemplate,
                                                                 binding.TemplateContract], ...]:
    result: list[tuple[str, binding.StructuralTemplate, binding.TemplateContract]] = []
    for stage in graph.body_stages:
        result.append((stage.id, stage.controller_template, stage.contract))
    for wrapper in graph.wrappers:
        result.append((wrapper.id, wrapper.controller_template, wrapper.contract))
    for stage in graph.body_stages:
        if stage.storage:
            result.append((stage.storage.id, stage.storage.template, stage.storage.contract))
        if stage.matched_delay:
            result.append((stage.matched_delay.id, stage.matched_delay.template, stage.matched_delay.contract))
    return tuple(result)


def _validate(graph: binding.BoundStructuralGraph) -> dict[str, binding.BoundLogicalSignal]:
    if not isinstance(graph, binding.BoundStructuralGraph):
        raise RTLCodegenError('expected a BoundStructuralGraph')
    if not isinstance(graph.module, str) or not graph.module:
        raise RTLCodegenError('invalid module identifier')

    instances = _instances(graph)
    instance_contracts: dict[str, binding.TemplateContract] = {}
    for instance_id, template, contract in instances:
        if instance_id in instance_contracts:
            raise RTLCodegenError(f'duplicate bound instance {instance_id}')
        if contract != binding.template_contract(template):
            raise RTLCodegenError(f'{instance_id} does not use the contract for {template.value}')
        instance_contracts[instance_id] = contract

    signals = {signal.id: signal for signal in graph.signals}
    if len(signals) != len(graph.signals) or any(not signal.id for signal in graph.signals):
        raise RTLCodegenError('bound graph has duplicate or invalid signal identifiers')
    parameter_set = set(graph.parameters)
    parameter_names: set[str] = set()
    for parameter in graph.parameters:
        if not parameter.name or parameter.name in parameter_names:
            raise RTLCodegenError('unresolved symbolic parameter ownership')
        parameter_names.add(parameter.name)

    for signal in graph.signals:
        if signal.semantic_kind is binding.PortSemanticKind.PAYLOAD:
            if signal.payload_type is None or signal.width is None:
                raise RTLCodegenError(f'payload signal {signal.id} has no bound width')
            if signal.width != signal.payload_type.width:
                raise RTLCodegenError(f'payload signal {signal.id} has inconsistent width metadata')
        elif signal.width != behavioral.ONE_BIT:
            raise RTLCodegenError(f'non-payload signal {signal.id} is not explicitly one bit')
        width = signal.width
        if width and width.symbolic is not None:
            if not width.parameters or any(parameter not in parameter_set for parameter in width.parameters):
                raise RTLCodegenError(f'payload signal {signal.id} has unresolved symbolic parameter ownership')
        if len(signal.drivers) > 1:
            raise RTLCodegenError(f'signal {signal.id} has multiple bound drivers')
        if (signal.expression is not None or signal.enable is not None) != bool(signal.drivers and
                signal.drivers[0].kind is binding.SignalDriverKind.EXPRESSION):
            raise RTLCodegenError(f'signal {signal.id} has inconsistent expression-driver ownership')

    module_port_signals: set[str] = set()
    module_port_names: set[str] = set()
    for port in graph.module_ports:
        signal = signals.get(port.signal_id)
        if (not _IDENTIFIER.fullmatch(port.name) or port.name in module_port_names or
                port.signal_id in module_port_signals or signal is None or not signal.is_module_port):
            raise RTLCodegenError('invalid module-port binding')
        if signal.semantic_kind is not port.semantic_kind or signal.endpoint != port.endpoint:
            raise RTLCodegenError(f'module port {port.name} has inconsistent signal metadata')
        if port.semantic_kind is binding.PortSemanticKind.PAYLOAD:
            if port.payload_type is None or port.width is None or port.payload_type != signal.payload_type or port.width != signal.width:
                raise RTLCodegenError(f'module port {port.name} has inconsistent payload width')
        elif port.width != behavioral.ONE_BIT or signal.width != behavioral.ONE_BIT:
            raise RTLCodegenError(f'module port {port.name} is not one bit')
        module_port_names.add(port.name)
        module_port_signals.add(port.signal_id)
    if any(signal.is_module_port and signal.id not in module_port_signals for signal in graph.signals):
        raise RTLCodegenError('module-port signal is missing its module-port contract')

    counts: dict[tuple[str, str], int] = defaultdict(int)
    occupied: set[tuple[str, str, int]] = set()
    for port_binding in graph.port_bindings:
        contract = instance_contracts.get(port_binding.instance_id)
        if contract is None:
            raise RTLCodegenError(f'port binding refers to unknown instance {port_binding.instance_id}')
        port = contract.port(port_binding.port_name)
        signal = signals.get(port_binding.signal_id)
        if port is None or signal is None:
            raise RTLCodegenError('port binding refers to an unknown port or signal')
        if port_binding.index < 0:
            raise RTLCodegenError('port binding has a negative index')
        if port.semantic_kind is not signal.semantic_kind:
            raise RTLCodegenError(f'{port_binding.instance_id}.{port_binding.port_name} has incompatible signal kind')
        if not port_binding.formal_name or port_binding.formal_name != port.resolved_formal_name(port_binding.index):
            raise RTLCodegenError(f'{port_binding.instance_id}.{port_binding.port_name} has no resolved formal name')
        if port.direction is binding.PortDirection.OUTPUT:
            expected = binding.BoundSignalDriver(binding.SignalDriverKind.TEMPLATE_OUTPUT,
                                                 f'{port_binding.instance_id}.{port_binding.formal_name}')
            if signal.drivers != (expected,):
                raise RTLCodegenError(f'{port_binding.instance_id}.{port_binding.formal_name} lacks unique output ownership')
        key = (port_binding.instance_id, port_binding.port_name, port_binding.index)
        if key in occupied:
            raise RTLCodegenError(f'duplicate binding for {port_binding.instance_id}.{port_binding.port_name}[{port_binding.index}]')
        occupied.add(key)
        counts[(port_binding.instance_id, port_binding.port_name)] += 1
    for instance_id, contract in instance_contracts.items():
        for port in contract.ports:
            count = counts[(instance_id, port.name)]
            if count < port.minimum or (port.maximum is not None and count > port.maximum):
                raise RTLCodegenError(f'missing or excess required binding for {instance_id}.{port.name}')
    used_signal_ids = {item.signal_id for item in graph.port_bindings}
    if any(port.signal_id not in used_signal_ids for port in graph.module_ports):
        raise RTLCodegenError('module port has no external-channel binding')
    return signals


def _replace_parameter_words(text: str, parameters: dict[str, str]) -> str:
    unknown = [word for word in _WORD.findall(text) if word not in parameters]
    if unknown:
        raise RTLCodegenError(f'unresolved symbolic parameter {unknown[0]}')
    return _WORD.sub(lambda match: parameters[match.group(0)], text)


def _width_declaration(signal: binding.BoundLogicalSignal, parameters: dict[str, str]) -> str:
    width = signal.width
    if width is None:
        raise RTLCodegenError(f'signal {signal.id} has no width')
    if width.bits is not None:
        return '' if width.bits == 1 else f' [{width.bits - 1}:0]'
    if width.symbolic is None or not width.parameters:
        raise RTLCodegenError(f'signal {signal.id} has unresolved symbolic width')
    if signal.payload_type and signal.payload_type.packed_range:
        left, right = signal.payload_type.packed_range
        return f' [{_replace_parameter_words(left, parameters)}:{_replace_parameter_words(right, parameters)}]'
    return f' [{_replace_parameter_words(width.symbolic, parameters)}-1:0]'


def _expression(expression: behavioral.Expression, variables: dict[behavioral.Variable, str],
                parameters: dict[behavioral.Parameter, str]) -> str:
    if expression.form == 'name' and expression.variable is not None:
        try:
            return variables[expression.variable]
        except KeyError as error:
            raise RTLCodegenError(f'unmapped variable {expression.variable.name}') from error
    if expression.form == 'parameter' and expression.parameter is not None:
        try:
            return parameters[expression.parameter]
        except KeyError as error:
            raise RTLCodegenError(f'unresolved parameter {expression.parameter.name}') from error
    if expression.form == 'literal' and expression.value and _LITERAL.fullmatch(expression.value):
        return expression.value
    if expression.form == 'select' and expression.variable is not None:
        base = _expression(behavioral.Expression('name', variable=expression.variable), variables, parameters)
        selections: list[str] = []
        for selector in expression.operands:
            if selector.form == 'index' and len(selector.operands) == 1:
                selections.append(f'[{_expression(selector.operands[0], variables, parameters)}]')
            elif selector.form == 'range' and len(selector.operands) == 2:
                selections.append(f'[{_expression(selector.operands[0], variables, parameters)}:'
                                  f'{_expression(selector.operands[1], variables, parameters)}]')
            else:
                raise RTLCodegenError('unsupported packed selection expression')
        return base + ''.join(selections)
    if expression.form == 'unary' and expression.operator in _UNARY_OPERATORS and len(expression.operands) == 1:
        return f'({expression.operator}{_expression(expression.operands[0], variables, parameters)})'
    if expression.form == 'binary' and expression.operator in _BINARY_OPERATORS and len(expression.operands) == 2:
        return (f'({_expression(expression.operands[0], variables, parameters)} {expression.operator} '
                f'{_expression(expression.operands[1], variables, parameters)})')
    if expression.form == 'conditional' and len(expression.operands) == 3:
        return (f'({_expression(expression.operands[0], variables, parameters)} ? '
                f'{_expression(expression.operands[1], variables, parameters)} : '
                f'{_expression(expression.operands[2], variables, parameters)})')
    if expression.form == 'concatenate' and expression.operands:
        return '{' + ', '.join(_expression(item, variables, parameters) for item in expression.operands) + '}'
    if expression.form == 'repeat' and len(expression.operands) == 2:
        return '{' + _expression(expression.operands[0], variables, parameters) + ' {' + \
               _expression(expression.operands[1], variables, parameters) + '}}'
    raise RTLCodegenError(f'unsupported expression form {expression.form}')


def _variables(graph: binding.BoundStructuralGraph) -> tuple[behavioral.Variable, ...]:
    found: list[behavioral.Variable] = []

    def add_expression(expression: behavioral.Expression | None) -> None:
        if expression is None:
            return
        if expression.variable is not None:
            found.append(expression.variable)
        for operand in expression.operands:
            add_expression(operand)

    for signal in graph.signals:
        if signal.variable is not None:
            found.append(signal.variable)
        add_expression(signal.expression)
        if signal.enable is not None:
            add_expression(signal.enable.condition)
    return tuple(dict.fromkeys(found))


def _instance_name(names: _Names, instance_id: str, template: binding.StructuralTemplate) -> str:
    prefix = {
        binding.StructuralTemplate.LINEAR_CONTROLLER: 'stage',
        binding.StructuralTemplate.JOIN_CONTROLLER: 'join',
        binding.StructuralTemplate.CONDITIONAL_RECV_WRAPPER: 'wrapper_recv',
        binding.StructuralTemplate.CONDITIONAL_SEND_WRAPPER: 'wrapper_send',
        binding.StructuralTemplate.ABSTRACT_STORAGE: 'storage',
        binding.StructuralTemplate.SYMBOLIC_MATCHED_DELAY: 'delay',
    }[template]
    return names.allocate(prefix, instance_id)


def emit_systemverilog(graph: binding.BoundStructuralGraph) -> str:
    """Render one deterministic structural SystemVerilog module from Phase 7A.

    All ports, signals, widths, storage, delays, and template choices must
    already be present in ``graph``.  The returned text references the selected
    abstract template module names; it does not define their implementations.
    """
    signals = _validate(graph)
    names = _Names(set())
    module_name = names.allocate('module', graph.module, preserve=True)
    parameter_names: dict[behavioral.Parameter, str] = {}
    parameter_by_source_name: dict[str, str] = {}
    for parameter in graph.parameters:
        if parameter.name in parameter_by_source_name:
            raise RTLCodegenError(f'cannot map duplicate parameter {parameter.name}')
        mapped = names.allocate('param', parameter.name, preserve=True)
        parameter_names[parameter] = mapped
        parameter_by_source_name[parameter.name] = mapped
    module_port_by_signal = {port.signal_id: port for port in graph.module_ports}
    signal_names = {
        signal.id: (module_port_by_signal[signal.id].name if signal.id in module_port_by_signal
                    else names.allocate('sig', signal.id))
        for signal in graph.signals
    }
    variable_names = {
        variable: names.allocate('var', f'{variable.name}_{variable.scope}_{variable.location.file}_'
                                 f'{variable.location.line}_{variable.location.column}')
        for variable in _variables(graph)
    }
    instance_names = {
        instance_id: _instance_name(names, instance_id, template)
        for instance_id, template, _ in _instances(graph)
    }

    lines: list[str] = []
    module_prefix = f'module {module_name}'
    if graph.parameters:
        declarations = []
        for parameter in graph.parameters:
            if parameter.default is None:
                raise RTLCodegenError(f'parameter {parameter.name} has no preserved default')
            default = _replace_parameter_words(parameter.default, parameter_by_source_name)
            declarations.append(f'    parameter int {parameter_names[parameter]} = {default}')
        module_prefix += ' #(\n' + ',\n'.join(declarations) + '\n)'
    if graph.module_ports:
        lines.extend([module_prefix + ' (', ',\n'.join(f'  {port.name}' for port in graph.module_ports), ');'])
    else:
        lines.append(module_prefix + ';')

    for port in graph.module_ports:
        signal = signals[port.signal_id]
        lines.append(f'  {port.direction.value} logic{_width_declaration(signal, parameter_by_source_name)} {port.name};')

    for variable, name in variable_names.items():
        width = variable.payload_type.width
        temporary = binding.BoundLogicalSignal(f'variable_{name}', binding.PortSemanticKind.PAYLOAD,
                                                payload_type=variable.payload_type, width=width)
        lines.append(f'  logic{_width_declaration(temporary, parameter_by_source_name)} {name};')
    for signal in graph.signals:
        if signal.is_module_port:
            continue
        lines.append(f'  logic{_width_declaration(signal, parameter_by_source_name)} {signal_names[signal.id]};')

    for signal in graph.signals:
        if signal.drivers and signal.drivers[0].kind is binding.SignalDriverKind.EXPRESSION and signal.enable is not None:
            value = _expression(signal.enable.condition, variable_names, parameter_names)
        elif signal.drivers and signal.drivers[0].kind is binding.SignalDriverKind.EXPRESSION and signal.expression is not None:
            value = _expression(signal.expression, variable_names, parameter_names)
        else:
            continue
        lines.append(f'  assign {signal_names[signal.id]} = {value};')

    grouped: dict[tuple[str, str], list[binding.BoundPortBinding]] = defaultdict(list)
    for port_binding in graph.port_bindings:
        grouped[(port_binding.instance_id, port_binding.port_name)].append(port_binding)
    for instance_id, template, contract in _instances(graph):
        connections: list[str] = []
        for port in contract.ports:
            for port_binding in sorted(grouped[(instance_id, port.name)], key=lambda item: item.index):
                connections.append(f'    .{port_binding.formal_name}({signal_names[port_binding.signal_id]})')
        lines.append(f'  {template.value} {instance_names[instance_id]} (')
        lines.append(',\n'.join(connections))
        lines.append('  );')
    lines.append('endmodule')
    return '\n'.join(lines) + '\n'
