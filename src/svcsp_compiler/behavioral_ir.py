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
class Variable:
    """A lexical variable declaration, identified by declaration location."""

    name: str
    scope: tuple[str | int, ...]
    location: SourceLocation


@dataclass(frozen=True)
class Expression:
    """A symbolic behavioral expression, independent of parser syntax nodes."""

    form: str
    value: str | None = None
    operator: str | None = None
    operands: tuple['Expression', ...] = ()
    variable: Variable | None = None
    location: SourceLocation | None = field(default=None, compare=False)


@dataclass(frozen=True)
class ChannelEndpoint:
    """A declared channel plus any source-level array selection."""

    name: str
    selectors: tuple[Expression, ...] = ()
    location: SourceLocation | None = field(default=None, compare=False)


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
    location: SourceLocation | None = None


class BehavioralIRError(ValueError):
    """A frontend result could not be represented in Behavioral CSP IR."""


def _location(node: dict) -> SourceLocation | None:
    location = node.get('location')
    if not location:
        return None
    return SourceLocation(location['file'], location['line'], location['column'])


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


def _expression(node: dict, scope: dict[str, Variable]) -> Expression:
    node = _unwrap_expression(node)
    kind = node['kind']
    location = _location(node)
    if kind in {'IdentifierName', 'IdentifierSelectName'}:
        name = node['identifier']
        variable = scope.get(name)
        if variable is None:
            _fail(node, f'unbound variable {name}')
        selectors = node.get('selectors', [])
        if not selectors:
            return Expression('name', value=name, variable=variable, location=location)
        return Expression('select', value=name,
                          operands=tuple(_expression(selector, scope) for selector in selectors),
                          variable=variable,
                          location=location)
    if kind == 'IntegerVectorExpression':
        return Expression('literal', value=node['size'] + node['base'] + node['value'], location=location)
    if kind in {'IntegerLiteralExpression', 'RealLiteralExpression',
                'StringLiteralExpression', 'UnbasedUnsizedLiteralExpression'}:
        return Expression('literal', value=node.get('literal', node.get('text')), location=location)
    if kind == 'ElementSelect':
        return _expression(node['selector'], scope)
    if kind == 'BitSelect':
        return Expression('index', operands=(_expression(node['expr'], scope),), location=location)
    if kind == 'SimpleRangeSelect':
        return Expression('range', value=node['range'],
                          operands=(_expression(node['left'], scope), _expression(node['right'], scope)),
                          location=location)
    if kind == 'ConditionalExpression':
        return Expression('conditional', operands=(_expression(node['predicate'], scope),
                                                     _expression(node['left'], scope),
                                                     _expression(node['right'], scope)), location=location)
    if kind == 'ConcatenationExpression':
        return Expression('concatenate', operands=tuple(_expression(item, scope) for item in node['expressions']),
                          location=location)
    if kind == 'MultipleConcatenationExpression':
        return Expression('repeat', operands=(_expression(node['expression'], scope),
                                               _expression(node['concatenation'], scope)), location=location)
    if 'left' in node and 'right' in node:
        return Expression('binary', operator=node.get('operatorToken'),
                          operands=(_expression(node['left'], scope), _expression(node['right'], scope)),
                          location=location)
    if 'operand' in node:
        return Expression('unary', operator=node.get('operatorToken'),
                          operands=(_expression(node['operand'], scope),), location=location)
    _fail(node, f'cannot lower expression {kind}')


def _target(node: dict, scope: dict[str, Variable]) -> Variable | Expression:
    node = _unwrap_expression(node)
    if node['kind'] not in {'IdentifierName', 'IdentifierSelectName'}:
        _fail(node, 'expected a variable target')
    return _expression(node, scope) if node.get('selectors') else scope[node['identifier']]


def _declare(node: dict, scope: dict[str, Variable], declarations: dict[tuple[str, SourceLocation], Variable]) -> None:
    for declarator in node['declarators']:
        location = _location(declarator)
        assert location is not None
        scope[declarator['name']] = declarations[(declarator['name'], location)]


def _statement(node: dict, scope: dict[str, Variable],
               declarations: dict[tuple[str, SourceLocation], Variable]) -> Process:
    kind = node['kind']
    location = _location(node)
    if kind == 'SequentialBlockStatement':
        local = dict(scope)
        items = []
        for item in node.get('items', []):
            if item['kind'] == 'DataDeclaration':
                _declare(item, local, declarations)
            else:
                items.append(_statement(item, local, declarations))
        return Sequence(tuple(items), location)
    if kind == 'ParallelBlockStatement':
        local = dict(scope)
        branches = []
        for item in node.get('items', []):
            if item['kind'] == 'DataDeclaration':
                _declare(item, local, declarations)
            else:
                branches.append(_statement(item, local, declarations))
        return Parallel(tuple(branches), location)
    if kind == 'ConditionalStatement':
        else_clause = node.get('elseClause')
        return If(_expression(node['predicate'], scope), _statement(node['statement'], dict(scope), declarations),
                  _statement(else_clause['clause'], dict(scope), declarations) if else_clause else Skip(location), location)
    if kind == 'EmptyStatement':
        return Skip(location)
    if kind != 'ExpressionStatement':
        _fail(node, f'cannot lower statement {kind}')
    expression = node['expr']
    if expression['kind'] == 'AssignmentExpression':
        return Assign(_target(expression['left'], scope), _expression(expression['right'], scope), location)
    if expression['kind'] != 'InvocationExpression':
        _fail(expression, 'expected assignment or communication')
    callee = expression['left']
    method = callee['right']['identifier']
    receiver = callee['left']
    channel = ChannelEndpoint(receiver['identifier'],
                              tuple(_expression(selector, scope) for selector in receiver.get('selectors', [])),
                              _location(receiver))
    argument = expression['arguments']['parameters'][0]['expr']
    if method == 'Send':
        return Send(channel, _expression(argument, scope), location)
    if method == 'Receive':
        return Receive(channel, _target(argument, scope), location)
    _fail(expression, f'cannot lower channel method {method}')


def lower_behavioral(frontend: dict) -> BehavioralModule:
    """Lower a successful Phase 1 frontend report into Behavioral CSP IR."""
    required = {'module', 'always', 'channels', 'variables'}
    if not required <= frontend.keys():
        raise BehavioralIRError('expected a Phase 1 frontend result')
    variables = tuple(
        Variable(variable['name'], tuple(variable['scope']), _location(variable))
        for variable in frontend['variables']
    )
    declarations = {(variable.name, variable.location): variable for variable in variables}
    scope = {variable.name: variable for variable in variables if variable.scope == ('module',)}
    channels = tuple(ChannelEndpoint(channel['name'], location=_location(channel))
                     for channel in frontend['channels'])
    return BehavioralModule(
        name=frontend['module'],
        body=_statement(frontend['always']['statement'], scope, declarations),
        channels=channels,
        variables=variables,
        location=_location(frontend),
    )
