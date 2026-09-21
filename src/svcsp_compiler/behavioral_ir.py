"""Phase 2: syntax-independent Behavioral CSP IR and frontend lowering.

This phase preserves source-level process behavior.  It deliberately makes no
normalization or implementation decisions.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import TypeAlias


@dataclass(frozen=True)
class SourceLocation:
    file: str
    line: int
    column: int


@dataclass(frozen=True)
class Parameter:
    """A module-owned symbolic parameter declaration used by payload widths."""

    name: str
    module: str
    location: SourceLocation
    default: str | None = None


@dataclass(frozen=True)
class PayloadWidth:
    """A concrete or unevaluated symbolic packed payload width."""

    bits: int | None = None
    symbolic: str | None = None
    parameters: tuple[Parameter, ...] = ()

    def __post_init__(self) -> None:
        if (self.bits is None) == (self.symbolic is None):
            raise ValueError('payload width requires exactly one concrete or symbolic representation')
        if self.bits is not None and self.bits <= 0:
            raise ValueError('concrete payload width must be positive')
        if self.symbolic is not None and not self.parameters:
            raise ValueError('symbolic payload width requires declared parameter ownership')


@dataclass(frozen=True)
class PayloadType:
    """A parser-independent scalar or packed data declaration type."""

    base: str
    width: PayloadWidth
    packed_range: tuple[str, str] | None = None


ONE_BIT = PayloadWidth(bits=1)


@dataclass(frozen=True)
class Variable:
    """A lexical variable declaration, identified by declaration location."""

    name: str
    scope: tuple[str | int, ...]
    location: SourceLocation
    payload_type: PayloadType


@dataclass(frozen=True)
class Expression:
    """A symbolic behavioral expression, independent of parser syntax nodes."""

    form: str
    value: str | None = None
    operator: str | None = None
    operands: tuple['Expression', ...] = ()
    variable: Variable | None = None
    parameter: Parameter | None = None
    location: SourceLocation | None = field(default=None, compare=False)


@dataclass(frozen=True)
class ChannelEndpoint:
    """A declared channel plus any source-level array selection."""

    name: str
    selectors: tuple[Expression, ...] = ()
    location: SourceLocation | None = field(default=None, compare=False)
    payload_type: PayloadType | None = None


@dataclass(frozen=True)
class Sequence:
    items: tuple['Process', ...]
    location: SourceLocation | None = None


@dataclass(frozen=True)
class Parallel:
    branches: tuple['Process', ...]
    location: SourceLocation | None = None


@dataclass(frozen=True)
class If:
    condition: Expression
    then_branch: 'Process'
    else_branch: 'Process'
    location: SourceLocation | None = None


@dataclass(frozen=True)
class Send:
    channel: ChannelEndpoint
    value: Expression
    location: SourceLocation | None = None


@dataclass(frozen=True)
class Receive:
    channel: ChannelEndpoint
    target: Variable | Expression
    location: SourceLocation | None = None


@dataclass(frozen=True)
class Assign:
    target: Variable | Expression
    value: Expression
    location: SourceLocation | None = None


@dataclass(frozen=True)
class Skip:
    location: SourceLocation | None = None


Process: TypeAlias = Sequence | Parallel | If | Send | Receive | Assign | Skip


@dataclass(frozen=True)
class BehavioralModule:
    name: str
    body: Process
    channels: tuple[ChannelEndpoint, ...]
    variables: tuple[Variable, ...]
    parameters: tuple[Parameter, ...] = ()
    location: SourceLocation | None = None


class BehavioralIRError(ValueError):
    """A frontend result could not be represented in Behavioral CSP IR."""


def _location(node: dict) -> SourceLocation | None:
    location = node.get('location')
    if not location:
        return None
    return SourceLocation(location['file'], location['line'], location['column'])


def _payload_type(data: dict | None) -> PayloadType | None:
    if data is None:
        return None
    width = data.get('width', {})
    bits, symbolic = width.get('bits'), width.get('symbolic')
    if bits is None and symbolic is None:
        return None
    packed_range = data.get('packed_range')
    parameters = tuple(Parameter(parameter['name'], parameter['module'], _location(parameter), parameter.get('default'))
                       for parameter in width.get('parameters', []))
    return PayloadType(data['base'], PayloadWidth(bits, symbolic, parameters),
                       (packed_range['left'], packed_range['right']) if packed_range else None)


def _fail(node: dict, message: str) -> None:
    location = _location(node)
    if location:
        raise BehavioralIRError(f'{location.file}:{location.line}:{location.column}: {message}')
    raise BehavioralIRError(message)


def _unwrap_expression(node: dict) -> dict:
    while node['kind'] in {'SimplePropertyExpr', 'SimpleSequenceExpr', 'ParenthesizedExpression'}:
        node = node['expr'] if 'expr' in node else node['expression']
    if node['kind'] == 'ConditionalPredicate':
        conditions = node['conditions']
        if len(conditions) != 1:
            _fail(node, 'expected one condition')
        node = conditions[0]['expr']
    return node


def _expression(node: dict, scope: dict[str, Variable],
                parameters: dict[str, Parameter] | None = None) -> Expression:
    node = _unwrap_expression(node)
    kind = node['kind']
    location = _location(node)
    if kind in {'IdentifierName', 'IdentifierSelectName'}:
        name = node['identifier']
        variable = scope.get(name)
        if variable is None:
            parameter = (parameters or {}).get(name)
            if parameter is None:
                _fail(node, f'unbound variable {name}')
            return Expression('parameter', value=name, parameter=parameter, location=location)
        selectors = node.get('selectors', [])
        if not selectors:
            return Expression('name', value=name, variable=variable, location=location)
        return Expression('select', value=name,
                          operands=tuple(_expression(selector, scope, parameters) for selector in selectors),
                          variable=variable,
                          location=location)
    if kind == 'IntegerVectorExpression':
        return Expression('literal', value=node['size'] + node['base'] + node['value'], location=location)
    if kind in {'IntegerLiteralExpression', 'RealLiteralExpression',
                'StringLiteralExpression', 'UnbasedUnsizedLiteralExpression'}:
        return Expression('literal', value=node.get('literal', node.get('text')), location=location)
    if kind == 'ElementSelect':
        return _expression(node['selector'], scope, parameters)
    if kind == 'BitSelect':
        return Expression('index', operands=(_expression(node['expr'], scope, parameters),), location=location)
    if kind == 'SimpleRangeSelect':
        return Expression('range', value=node['range'],
                          operands=(_expression(node['left'], scope, parameters), _expression(node['right'], scope, parameters)),
                          location=location)
    if kind == 'ConditionalExpression':
        return Expression('conditional', operands=(_expression(node['predicate'], scope, parameters),
                                                     _expression(node['left'], scope, parameters),
                                                     _expression(node['right'], scope, parameters)), location=location)
    if kind == 'ConcatenationExpression':
        return Expression('concatenate', operands=tuple(_expression(item, scope, parameters) for item in node['expressions']),
                          location=location)
    if kind == 'MultipleConcatenationExpression':
        return Expression('repeat', operands=(_expression(node['expression'], scope, parameters),
                                               _expression(node['concatenation'], scope, parameters)), location=location)
    if 'left' in node and 'right' in node:
        return Expression('binary', operator=node.get('operatorToken'),
                          operands=(_expression(node['left'], scope, parameters), _expression(node['right'], scope, parameters)),
                          location=location)
    if 'operand' in node:
        return Expression('unary', operator=node.get('operatorToken'),
                          operands=(_expression(node['operand'], scope, parameters),), location=location)
    _fail(node, f'cannot lower expression {kind}')


def _target(node: dict, scope: dict[str, Variable],
            parameters: dict[str, Parameter] | None = None) -> Variable | Expression:
    node = _unwrap_expression(node)
    if node['kind'] not in {'IdentifierName', 'IdentifierSelectName'}:
        _fail(node, 'expected a variable target')
    return _expression(node, scope, parameters) if node.get('selectors') else scope[node['identifier']]


def _target_payload_type(target: Variable | Expression) -> PayloadType | None:
    return target.payload_type if isinstance(target, Variable) else expression_payload_type(target)


def _concrete_literal(expression: Expression) -> int | None:
    if expression.form != 'literal' or expression.value is None:
        return None
    try:
        return int(expression.value)
    except ValueError:
        return None


def _symbolic_selector(expression: Expression) -> tuple[str, tuple[Parameter, ...]] | None:
    if expression.form == 'parameter' and expression.parameter is not None:
        return expression.parameter.name, (expression.parameter,)
    if expression.form == 'literal' and expression.value is not None:
        return expression.value, ()
    if expression.form in {'unary', 'binary'}:
        rendered = []
        parameters: list[Parameter] = []
        for operand in expression.operands:
            result = _symbolic_selector(operand)
            if result is None:
                return None
            rendered.append(result[0])
            parameters.extend(result[1])
        if expression.form == 'unary' and len(rendered) == 1:
            return f'({expression.operator}{rendered[0]})', tuple(dict.fromkeys(parameters))
        if expression.form == 'binary' and len(rendered) == 2:
            return f'({rendered[0]} {expression.operator} {rendered[1]})', tuple(dict.fromkeys(parameters))
    return None


def _selected_type(base: PayloadType, selectors: tuple[Expression, ...]) -> PayloadType | None:
    result = base
    for selector in selectors:
        if selector.form == 'index':
            result = PayloadType(result.base, ONE_BIT)
            continue
        if selector.form != 'range' or len(selector.operands) != 2:
            return None
        left, right = (_concrete_literal(selector.operands[0]), _concrete_literal(selector.operands[1]))
        if left is not None and right is not None:
            result = PayloadType(result.base, PayloadWidth(bits=abs(left - right) + 1))
            continue
        symbolic_left, symbolic_right = (_symbolic_selector(selector.operands[0]),
                                         _symbolic_selector(selector.operands[1]))
        if symbolic_left is None or symbolic_right is None:
            return None
        left_text, left_parameters = symbolic_left
        right_text, right_parameters = symbolic_right
        compact_left, compact_right = left_text.replace(' ', '').strip('()'), right_text.replace(' ', '')
        if compact_right == '0' and compact_left.endswith('-1'):
            symbolic = compact_left[:-2]
            owned = left_parameters
        else:
            symbolic = f'{left_text}:{right_text}'
            owned = tuple(dict.fromkeys(left_parameters + right_parameters))
        if not owned:
            return None
        result = PayloadType(result.base, PayloadWidth(symbolic=symbolic, parameters=owned))
    return result


def expression_payload_type(expression: Expression) -> PayloadType | None:
    """Infer only expression widths proven by the supported symbolic subset."""
    if expression.form == 'name' and expression.variable is not None:
        return expression.variable.payload_type
    if expression.form == 'select' and expression.variable is not None:
        return _selected_type(expression.variable.payload_type, expression.operands)
    if expression.form == 'unary' and len(expression.operands) == 1:
        operand = expression_payload_type(expression.operands[0])
        if expression.operator in {'!', '&', '~&', '|', '~|', '^', '~^', '^~'}:
            return PayloadType(operand.base if operand else 'logic', ONE_BIT)
        return operand if expression.operator in {'+', '-', '~'} else None
    if expression.form == 'binary' and len(expression.operands) == 2:
        left, right = (expression_payload_type(expression.operands[0]),
                       expression_payload_type(expression.operands[1]))
        if expression.operator in {'&&', '||', '==', '!=', '===', '!==', '<', '<=', '>', '>='}:
            return PayloadType((left or right).base if left or right else 'logic', ONE_BIT)
        if left is not None and right is not None and payload_types_compatible(left, right):
            return left
        return None
    if expression.form == 'conditional' and len(expression.operands) == 3:
        left, right = (expression_payload_type(expression.operands[1]),
                       expression_payload_type(expression.operands[2]))
        return left if left is not None and right is not None and payload_types_compatible(left, right) else None
    return None


def payload_types_compatible(left: PayloadType, right: PayloadType) -> bool:
    left_width, right_width = left.width, right.width
    if left_width.bits is not None and right_width.bits is not None:
        return left_width.bits == right_width.bits
    if left_width.symbolic is not None and right_width.symbolic is not None:
        return (left_width.symbolic == right_width.symbolic and
                left_width.parameters == right_width.parameters)
    return False


def _require_compatible(node: dict, context: str, left: PayloadType | None,
                        right: PayloadType | None, *, require_right: bool = False) -> None:
    if require_right and left is not None and right is None:
        _fail(node, f'cannot prove payload width for {context}')
    if left is not None and right is not None and not payload_types_compatible(left, right):
        _fail(node, f'incompatible payload widths for {context}')


def _declare(node: dict, scope: dict[str, Variable], declarations: dict[tuple[str, SourceLocation], Variable]) -> None:
    for declarator in node['declarators']:
        location = _location(declarator)
        assert location is not None
        scope[declarator['name']] = declarations[(declarator['name'], location)]


def _statement(node: dict, scope: dict[str, Variable],
               declarations: dict[tuple[str, SourceLocation], Variable],
               channel_payloads: dict[str, PayloadType | None],
               parameters: dict[str, Parameter]) -> Process:
    kind = node['kind']
    location = _location(node)
    if kind == 'SequentialBlockStatement':
        local = dict(scope)
        items = []
        for item in node.get('items', []):
            if item['kind'] == 'DataDeclaration':
                _declare(item, local, declarations)
            else:
                items.append(_statement(item, local, declarations, channel_payloads, parameters))
        return Sequence(tuple(items), location)
    if kind == 'ParallelBlockStatement':
        local = dict(scope)
        branches = []
        for item in node.get('items', []):
            if item['kind'] == 'DataDeclaration':
                _declare(item, local, declarations)
            else:
                branches.append(_statement(item, local, declarations, channel_payloads, parameters))
        return Parallel(tuple(branches), location)
    if kind == 'ConditionalStatement':
        else_clause = node.get('elseClause')
        return If(_expression(node['predicate'], scope, parameters), _statement(node['statement'], dict(scope), declarations, channel_payloads, parameters),
                  _statement(else_clause['clause'], dict(scope), declarations, channel_payloads, parameters) if else_clause else Skip(location), location)
    if kind == 'EmptyStatement':
        return Skip(location)
    if kind != 'ExpressionStatement':
        _fail(node, f'cannot lower statement {kind}')
    expression = node['expr']
    if expression['kind'] == 'AssignmentExpression':
        target, value = _target(expression['left'], scope, parameters), _expression(expression['right'], scope, parameters)
        # The target supplies the destination type for the operation, never
        # the width of an otherwise unproven RHS.  Accepting an unknown RHS
        # here would defer an implicit resize to emitted SystemVerilog.
        _require_compatible(expression, 'assignment', _target_payload_type(target), expression_payload_type(value),
                            require_right=True)
        return Assign(target, value, location)
    if expression['kind'] != 'InvocationExpression':
        _fail(expression, 'expected assignment or communication')
    callee = expression['left']
    method = callee['right']['identifier']
    receiver = callee['left']
    argument = expression['arguments']['parameters'][0]['expr']
    if method == 'Send':
        value = _expression(argument, scope, parameters)
        declared_type = channel_payloads.get(receiver['identifier'])
        value_type = expression_payload_type(value)
        _require_compatible(expression, 'Send', declared_type, value_type, require_right=True)
        channel = ChannelEndpoint(receiver['identifier'],
                                  tuple(_expression(selector, scope, parameters) for selector in receiver.get('selectors', [])),
                                  _location(receiver), declared_type or value_type)
        return Send(channel, value, location)
    if method == 'Receive':
        target = _target(argument, scope, parameters)
        declared_type = channel_payloads.get(receiver['identifier'])
        target_type = _target_payload_type(target)
        _require_compatible(expression, 'Receive', declared_type, target_type)
        channel = ChannelEndpoint(receiver['identifier'],
                                  tuple(_expression(selector, scope, parameters) for selector in receiver.get('selectors', [])),
                                  _location(receiver), declared_type or target_type)
        return Receive(channel, target, location)
    _fail(expression, f'cannot lower channel method {method}')


def lower_behavioral(frontend: dict) -> BehavioralModule:
    """Lower a successful Phase 1 frontend report into Behavioral CSP IR."""
    required = {'module', 'always', 'channels', 'variables'}
    if not required <= frontend.keys():
        raise BehavioralIRError('expected a Phase 1 frontend result')
    variables = tuple(
        Variable(variable['name'], tuple(variable['scope']), _location(variable), _payload_type(variable.get('payload_type')))
        for variable in frontend['variables']
    )
    declarations = {(variable.name, variable.location): variable for variable in variables}
    scope = {variable.name: variable for variable in variables if variable.scope == ('module',)}
    channels = tuple(ChannelEndpoint(channel['name'], location=_location(channel),
                                     payload_type=_payload_type(channel.get('payload_type')))
                     for channel in frontend['channels'])
    channel_payloads = {channel.name: channel.payload_type for channel in channels}
    parameters = tuple(Parameter(parameter['name'], parameter['module'], _location(parameter), parameter.get('default'))
                       for parameter in frontend.get('parameters', []))
    parameter_map = {parameter.name: parameter for parameter in parameters}
    return BehavioralModule(
        name=frontend['module'],
        body=_statement(frontend['always']['statement'], scope, declarations, channel_payloads, parameter_map),
        channels=channels,
        variables=variables,
        parameters=parameters,
        location=_location(frontend),
    )
