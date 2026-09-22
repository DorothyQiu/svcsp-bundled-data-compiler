"""Frontend source syntax and module-local SVCSP semantic extraction.

No elaboration, width evaluation, behavioral IR, or architectural lowering.
All results are JSON-compatible dictionaries; syntax retains pyslang kind names.
"""
from __future__ import annotations

import json
import re
from pathlib import Path

from pyslang import DiagnosticEngine, SourceManager
from pyslang.syntax import SyntaxTree


class FrontendError(ValueError):
    """Invalid source or a construct outside the supported frontend subset."""


# The frontend accepts only ordinary, side-effect-free data expressions. Keep this
# explicit: pyslang represents some special names (for example ``$random`` and
# ``this``) as leaf nodes, so a blacklist of calls and assignments is not
# sufficient to reject them.
_SUPPORTED_EXPRESSION_KINDS = frozenset({
    'IdentifierName', 'IdentifierSelectName',
    'IntegerLiteralExpression', 'IntegerVectorExpression',
    'RealLiteralExpression', 'StringLiteralExpression',
    'UnbasedUnsizedLiteralExpression',
    'ParenthesizedExpression', 'ConditionalExpression',
    'UnaryPlusExpression', 'UnaryMinusExpression', 'UnaryLogicalNotExpression',
    'UnaryBitwiseAndExpression', 'UnaryBitwiseNandExpression',
    'UnaryBitwiseNorExpression', 'UnaryBitwiseNotExpression',
    'UnaryBitwiseOrExpression', 'UnaryBitwiseXnorExpression',
    'UnaryBitwiseXorExpression',
    'AddExpression', 'SubtractExpression', 'MultiplyExpression',
    'DivideExpression', 'ModExpression', 'PowerExpression',
    'BinaryAndExpression', 'BinaryOrExpression', 'BinaryXnorExpression',
    'BinaryXorExpression', 'LogicalAndExpression', 'LogicalOrExpression',
    'LogicalEquivalenceExpression', 'LogicalImplicationExpression',
    'LogicalShiftLeftExpression', 'LogicalShiftRightExpression',
    'ArithmeticShiftLeftExpression', 'ArithmeticShiftRightExpression',
    'EqualityExpression', 'InequalityExpression', 'CaseEqualityExpression',
    'CaseInequalityExpression', 'WildcardEqualityExpression',
    'WildcardInequalityExpression', 'LessThanExpression',
    'LessThanEqualExpression', 'GreaterThanExpression',
    'GreaterThanEqualExpression', 'ConcatenationExpression',
    'MultipleConcatenationExpression',
    'ConditionalPredicate', 'ConditionalPattern',
    'ElementSelect', 'BitSelect', 'SimpleRangeSelect',
    'RangeDimensionSpecifier', 'VariableDimension',
})

def _syntax(tree):
    if any(d.isError() for d in tree.diagnostics):
        raise FrontendError(DiagnosticEngine.reportAll(tree.sourceManager, tree.diagnostics))
    manager = tree.sourceManager

    def convert(data, native):
        if isinstance(data, list):
            return [convert(d, n) for d, n in zip(data, native, strict=True)
                    if d.get('kind') != 'Comma']
        if 'text' in data:
            return str(native.valueText) if data['kind'] == 'Identifier' else data['text']
        loc = manager.getFullyExpandedLoc(native.sourceRange.start)
        return {'kind': data['kind'],
                'location': {'file': str(manager.getFileName(loc)),
                             'line': manager.getLineNumber(loc),
                             'column': manager.getColumnNumber(loc)},
                **{k: convert(v, getattr(native, k)) for k, v in data.items() if k != 'kind'}}

    return convert(json.loads(tree.root.to_json()), tree.root)


def walk(node):
    """Walk source syntax dictionaries in source order."""
    if isinstance(node, dict) and 'kind' in node:
        yield node
        for value in node.values():
            yield from walk(value)
    elif isinstance(node, list):
        for value in node:
            yield from walk(value)


def _fail(node, message):
    loc = node['location']
    raise FrontendError(f"{loc['file']}:{loc['line']}:{loc['column']}: {message}")


def _dimension_expression(expr, parameters=None):
    """Render a supported declaration-bound expression without evaluating it."""
    kind = expr['kind']
    if kind not in _SUPPORTED_EXPRESSION_KINDS:
        _fail(expr, f'unsupported data expression: {kind}')
    if kind in {'IntegerLiteralExpression', 'IntegerVectorExpression'}:
        return expr.get('literal', expr.get('size', '') + expr.get('base', '') + expr.get('value', ''))
    if kind in {'IdentifierName', 'IdentifierSelectName'} and not expr.get('selectors'):
        if parameters is not None and expr['identifier'] not in parameters:
            _fail(expr, f'undeclared symbolic width parameter: {expr["identifier"]}')
        return expr['identifier']
    if 'left' in expr and 'right' in expr:
        return f'({_dimension_expression(expr["left"], parameters)} {expr.get("operatorToken", "?")} ' \
               f'{_dimension_expression(expr["right"], parameters)})'
    if 'operand' in expr:
        return f'({expr.get("operatorToken", "?")}{_dimension_expression(expr["operand"], parameters)})'
    _fail(expr, f'unsupported data expression: {kind}')


def _integer(text):
    return int(text) if re.fullmatch(r'-?[0-9]+', text) else None


def _width_parameters(expr, parameters):
    names = []
    for node in walk(expr):
        if node['kind'] in {'IdentifierName', 'IdentifierSelectName'}:
            name = node['identifier']
            if name not in parameters:
                _fail(node, f'undeclared symbolic width parameter: {name}')
            if name not in names:
                names.append(name)
    return [parameters[name] for name in names]


def _symbolic_range_width(left, right):
    """Recognize the common [W-1:0] spelling as the width identity W."""
    compact_left, compact_right = left.replace(' ', '').strip('()'), right.replace(' ', '')
    match = re.fullmatch(r'([A-Za-z_$][A-Za-z0-9_$]*)-1', compact_left)
    if compact_right == '0' and match:
        return match.group(1)
    return f'{left}:{right}'


def _payload_type(dtype, parameters):
    """Extract supported packed data metadata as JSON-compatible symbolic data."""
    dimensions = dtype.get('dimensions', [])
    if not dimensions:
        return {'base': dtype['keyword'], 'width': {'bits': 1, 'symbolic': None}, 'packed_range': None}
    if len(dimensions) != 1:
        _fail(dtype, 'only one packed dimension is supported for payload widths')
    specifier = dimensions[0].get('specifier', {})
    selector = specifier.get('selector', {})
    if selector.get('kind') != 'SimpleRangeSelect':
        _fail(dimensions[0], 'expected a packed range dimension')
    left = _dimension_expression(selector['left'], parameters)
    right = _dimension_expression(selector['right'], parameters)
    left_value, right_value = _integer(left), _integer(right)
    bits = abs(left_value - right_value) + 1 if left_value is not None and right_value is not None else None
    return {
        'base': dtype['keyword'],
        'width': {'bits': bits, 'symbolic': None if bits is not None else _symbolic_range_width(left, right),
                  'parameters': [] if bits is not None else
                  _width_parameters(selector['left'], parameters) +
                  [parameter for parameter in _width_parameters(selector['right'], parameters)
                   if parameter['name'] not in {item['name'] for item in _width_parameters(selector['left'], parameters)}]},
        'packed_range': {'left': left, 'right': right},
    }


def _channel_payload_type(dtype, parameter_values):
    """Extract an optional single symbolic payload-width parameter from Channel #(W)."""
    if dtype.get('kind') != 'NamedType':
        return None
    name = dtype.get('name', {})
    parameter_list = name.get('parameters') if name.get('kind') == 'ClassName' else None
    if not parameter_list:
        return None
    values = parameter_list.get('parameters', [])
    if len(values) != 1 or values[0].get('kind') != 'OrderedParamAssignment':
        _fail(parameter_list, 'channel payload type requires one positional width parameter')
    symbolic = _dimension_expression(values[0]['expr'], parameter_values)
    bits = _integer(symbolic)
    return {'base': name['identifier'], 'width': {'bits': bits, 'symbolic': None if bits is not None else symbolic,
                                                   'parameters': [] if bits is not None else
                                                   _width_parameters(values[0]['expr'], parameter_values)},
            'packed_range': None}


def _extract(tree, channel_types):
    root = _syntax(tree)
    members = root.get('members', []) if root['kind'] == 'CompilationUnit' else [root]
    if len(members) != 1 or members[0]['kind'] != 'ModuleDeclaration':
        _fail(root, 'expected exactly one module')
    module = members[0]
    header = module['header']
    if header.get('imports'):
        _fail(header, 'header imports are not supported')
    parameters = {}
    parameter_list = header.get('parameters')
    if parameter_list:
        for declaration in parameter_list.get('declarations', []):
            if (declaration.get('kind') != 'ParameterDeclaration' or declaration.get('keyword') != 'parameter'
                    or declaration.get('type', {}).get('kind') != 'IntType'):
                _fail(declaration, 'only parameter int declarations are supported for symbolic widths')
            for declarator in declaration.get('declarators', []):
                name = declarator['name']
                if name in parameters:
                    _fail(declarator, f'duplicate parameter declaration: {name}')
                initializer = declarator.get('initializer')
                parameters[name] = {
                    'name': name,
                    'module': header['name'],
                    'default': _dimension_expression(initializer['expr']) if initializer else None,
                    'location': declarator['location'],
                }
    channels, variables, operations = [], [], []
    symbols = {}
    types = set(channel_types)

    def declare(name, category, node, scope):
        if name in scope:
            _fail(node, f'duplicate declaration: {name}')
        scope[name] = category

    ports = header.get('ports')
    inherited = None
    if ports:
        if ports['kind'] != 'AnsiPortList':
            _fail(ports, 'only ANSI channel/interface ports are supported')
        for port in ports.get('ports', []):
            h, decl = port.get('header', {}), port.get('declarator')
            if not decl:
                _fail(port, 'unsupported port declaration')
            dtype = h.get('dataType', {})
            implicit = (h.get('kind') == 'VariablePortHeader'
                        and dtype.get('kind') == 'ImplicitType'
                        and set(dtype) <= {'kind', 'location'}
                        and not h.get('direction') and not h.get('varKeyword'))
            if not implicit:
                inherited = None
                if h.get('kind') == 'InterfacePortHeader':
                    typename = h['nameOrKeyword']
                    if typename == 'interface' or typename in types:
                        inherited = (typename, h.get('modport', {}).get('member'))
                elif dtype.get('kind') == 'NamedType' and not h.get('direction'):
                    typename = dtype['name'].get('identifier')
                    if typename in types:
                        inherited = (typename, None)
            if inherited is None:
                _fail(port, 'expected a channel/interface port (configure channel_types for named types)')
            if decl.get('initializer'):
                _fail(decl, 'channel port initializers are not supported')
            name = decl['name']
            declare(name, 'channel', decl, symbols)
            channels.append({'name': name, 'type': inherited[0], 'modport': inherited[1],
                             'dimensions': decl.get('dimensions', []), 'direction': 'unknown',
                             'payload_type': _channel_payload_type(dtype, parameters),
                             'location': decl['location'], 'operations': []})
    endpoints = {c['name']: c for c in channels}

    def expression(expr, scope):
        for node in walk(expr):
            kind = node['kind']
            if kind not in _SUPPORTED_EXPRESSION_KINDS:
                _fail(node, f'unsupported data expression: {kind}')
            if kind in {'IdentifierName', 'IdentifierSelectName'}:
                name = node['identifier']
                if scope.get(name) != 'variable' and name not in parameters:
                    _fail(node, f'expected a declared local variable: {name}')

    def target(expr, scope):
        while expr['kind'] == 'ParenthesizedExpression':
            expr = expr['expression']
        if expr['kind'] not in {'IdentifierName', 'IdentifierSelectName'}:
            _fail(expr, 'expected a local variable receive/assignment target')
        expression(expr, scope)

    def variable(declaration, scope, path):
        dtype = declaration['type']
        if dtype['kind'] not in {'LogicType', 'RegType', 'BitType'} or declaration.get('modifiers'):
            _fail(declaration, 'only unqualified logic/reg/bit variables are supported')
        payload_type = _payload_type(dtype, parameters)
        for decl in declaration['declarators']:
            name = decl['name']
            declare(name, 'variable', decl, scope)
            for dimension in decl.get('dimensions', []):
                expression(dimension, scope)
            initializer = decl.get('initializer')
            if initializer:
                expression(initializer['expr'], scope)
            variables.append({'name': name, 'type': dtype, 'payload_type': payload_type, 'scope': list(path),
                              'dimensions': decl.get('dimensions', []),
                              'initializer': initializer, 'location': decl['location']})

    def statement(node, scope, path):
        kind = node['kind']
        if kind in {'SequentialBlockStatement', 'ParallelBlockStatement'}:
            if kind == 'ParallelBlockStatement' and node['end'] != 'join':
                _fail(node, 'only fork/join is supported')
            local = dict(scope)
            declared = set()
            if node.get('blockName'):
                local[node['blockName']['name']] = 'block'
            for index, item in enumerate(node.get('items', [])):
                if item['kind'] == 'DataDeclaration':
                    # Inner declarations may shadow outer names, but not each other.
                    for decl in item['declarators']:
                        if decl['name'] in declared:
                            _fail(decl, f"duplicate declaration: {decl['name']}")
                        declared.add(decl['name'])
                        local.pop(decl['name'], None)
                    variable(item, local, path)
                else:
                    statement(item, local, (*path, 'items', index))
        elif kind == 'ConditionalStatement':
            if node.get('uniqueOrPriority'):
                _fail(node, 'qualified if statements are not supported')
            predicate = node['predicate']
            conditions = predicate['conditions']
            if len(conditions) != 1 or conditions[0].get('matchesClause'):
                _fail(node, 'only expression conditions are supported')
            expression(predicate, scope)
            statement(node['statement'], scope, (*path, 'statement'))
            if node.get('elseClause'):
                statement(node['elseClause']['clause'], scope, (*path, 'elseClause', 'clause'))
        elif kind == 'EmptyStatement':
            return
        elif kind == 'ExpressionStatement':
            expr = node['expr']
            if expr['kind'] == 'AssignmentExpression':
                if expr.get('timingControl'):
                    _fail(expr, 'timed assignments are not supported')
                target(expr['left'], scope)
                expression(expr['right'], scope)
                return
            if expr['kind'] != 'InvocationExpression':
                _fail(expr, 'expected blocking assignment or Send/Receive call')
            callee = expr['left']
            receiver, method = callee.get('left', {}), callee.get('right', {})
            name = receiver.get('identifier')
            method_name = method.get('identifier')
            if (callee['kind'] != 'ScopedName' or callee.get('separator') != '.'
                    or receiver.get('kind') not in {'IdentifierName', 'IdentifierSelectName'}
                    or method.get('kind') != 'IdentifierName'
                    or method_name not in {'Send', 'Receive'} or scope.get(name) != 'channel'):
                _fail(expr, 'expected Send/Receive on a declared channel port')
            for selector in receiver.get('selectors', []):
                expression(selector, scope)
            args = expr.get('arguments', {}).get('parameters', [])
            if len(args) != 1 or args[0]['kind'] != 'OrderedArgument' or 'expr' not in args[0]:
                _fail(expr, 'Send/Receive requires one positional data argument')
            arg = args[0]['expr']
            while arg['kind'] in {'SimplePropertyExpr', 'SimpleSequenceExpr'}:
                arg = arg['expr']
            (target if method_name == 'Receive' else expression)(arg, scope)
            operation = {'method': method_name, 'channel': name, 'receiver': receiver,
                         'argument': arg, 'location': expr['location'], 'syntax_path': list(path)}
            operations.append(operation)
            endpoints[name]['operations'].append(operation)
        else:
            _fail(node, f'unsupported frontend statement: {kind}')

    processes = []
    for member in module.get('members', []):
        if member['kind'] == 'DataDeclaration':
            variable(member, symbols, ('module',))
        elif member['kind'] == 'AlwaysBlock':
            processes.append(member)
        else:
            _fail(member, f"unsupported frontend module member: {member['kind']}")
    for channel in channels:
        for dimension in channel['dimensions']:
            expression(dimension, symbols)
    if len(processes) != 1:
        _fail(module, 'expected exactly one top-level always block')
    process = processes[0]
    statement(process['statement'], symbols, ('always', 'statement'))
    for channel in channels:
        roles = {'input' if op['method'] == 'Receive' else 'output' for op in channel['operations']}
        channel['direction'] = 'bidirectional' if len(roles) == 2 else next(iter(roles), 'unknown')
    return {'module': header['name'], 'location': module['location'], 'parameters': list(parameters.values()),
            'variables': variables,
            'channels': channels, 'operations': operations, 'always': process, 'syntax': module,
            'warnings': DiagnosticEngine.reportAll(tree.sourceManager, tree.diagnostics)}


def parse_text(source: str, filename: str = 'source.sv', *, channel_types=('Channel',)) -> dict:
    """Extract one module; named channel types are explicit, generic interfaces implicit."""
    return _extract(SyntaxTree.fromText(source, name=filename), channel_types)


def parse_file(path: str | Path, *, include_dirs=(), channel_types=('Channel',)) -> dict:
    """Parse a file with pyslang preprocessing and optional user include directories."""
    path = Path(path).resolve()
    if not path.is_file():
        raise FileNotFoundError(path)
    manager = SourceManager()
    for directory in include_dirs:
        manager.addUserDirectories(str(Path(directory).resolve()))
    return _extract(SyntaxTree.fromFiles([str(path)], manager), channel_types)
