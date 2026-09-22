"""Verification-only SVCSP rendering for an M4 decomposed transaction.

This renderer is intentionally not part of compiler lowering. The concrete
zero used for a disabled receive is a simulation choice for this view, not an
M4 payload value or hardware-architecture decision.
"""
from __future__ import annotations

import re

from . import behavioral_ir as behavioral
from .communication_decomposition import DecomposedTransaction
from .transaction import SourcePath


class DecomposedSVCSPError(ValueError):
    """The narrow decomposed-SVCSP verification view cannot render its input."""


_IDENTIFIER = re.compile(r'^[A-Za-z_][A-Za-z0-9_$]*$')


def emit_conditional_send_decomposition(transaction: DecomposedTransaction) -> str:
    """Render a simulation-only SVCSP view of ``transaction``.

    External operations remain conditional in this verification view. A
    disabled conditional receive receives ``'0`` only to concretize its
    abstract ``InvalidPayload`` for simulation.
    """

    if not isinstance(transaction, DecomposedTransaction):
        raise DecomposedSVCSPError('expected a DecomposedTransaction')

    module = transaction.transaction.behavioral
    invalid_receives = {
        body_receive.source.path
        for body_receive in transaction.body_receives
        if body_receive.disabled_payload is not None
    }
    invalid_targets = [
        body_receive.source.operation.target
        for body_receive in transaction.body_receives
        if body_receive.disabled_payload is not None
    ]
    if len(invalid_targets) > 1:
        raise DecomposedSVCSPError('verification view supports one invalid receive payload')

    ports = ',\n  '.join(f'Channel {_identifier(channel.name, "channel")}' for channel in module.channels)
    lines = [f'module {_identifier(module.name, "module")}_DECOMPOSED (', f'  {ports}', ');']
    for variable in module.variables:
        lines.append(f'  {_type(variable.payload_type)} {_identifier(variable.name, "variable")};')
    if invalid_targets:
        lines.append(f'  {_type(_target_type(invalid_targets[0]))} body_payload;')
    lines.append('  always begin')
    lines.extend(_render_process(
        module.body, (), invalid_receives,
        _target(invalid_targets[0]) if invalid_targets else None, 2,
    ))
    lines.extend(['  end', 'endmodule', ''])
    return '\n'.join(lines)


def _render_process(process: behavioral.Process, path: SourcePath,
                    invalid_receives: set[SourcePath], invalid_target: str | None,
                    indent: int) -> list[str]:
    prefix = '  ' * indent
    if isinstance(process, behavioral.Skip):
        return []
    if isinstance(process, behavioral.Receive):
        return [f'{prefix}{_endpoint(process.channel)}.Receive({_target(process.target)});']
    if isinstance(process, behavioral.Send):
        return [f'{prefix}{_endpoint(process.channel)}.Send({_expression(process.value)});']
    if isinstance(process, behavioral.Assign):
        return [f'{prefix}{_target(process.target)} = {_expression(process.value)};']
    if isinstance(process, behavioral.Sequence):
        lines: list[str] = []
        for index, item in enumerate(process.items):
            lines.extend(_render_process(item, path + (index,), invalid_receives, invalid_target, indent))
        return lines
    if isinstance(process, behavioral.Parallel):
        lines = [f'{prefix}fork']
        for index, branch in enumerate(process.branches):
            lines.extend(_render_process(
                branch, path + ('parallel', index), invalid_receives, invalid_target, indent + 1,
            ))
        lines.append(f'{prefix}join')
        return lines
    if isinstance(process, behavioral.If):
        return _render_if(process, path, invalid_receives, invalid_target, indent)
    raise DecomposedSVCSPError(f'unsupported behavioral process {type(process).__name__}')


def _render_if(process: behavioral.If, path: SourcePath,
               invalid_receives: set[SourcePath], invalid_target: str | None,
               indent: int) -> list[str]:
    """Render a direct conditional receive with a simulation-only invalid token."""

    then_path, else_path = path + ('then',), path + ('else',)
    then_invalid = isinstance(process.then_branch, behavioral.Receive) and then_path in invalid_receives
    else_invalid = isinstance(process.else_branch, behavioral.Receive) and else_path in invalid_receives
    prefix = '  ' * indent
    lines = [f'{prefix}if ({_expression(process.condition)}) begin']
    if else_invalid:
        lines.extend(_invalid_assignment(invalid_target, indent + 1))
    else:
        lines.extend(_render_process(process.then_branch, then_path, invalid_receives, invalid_target, indent + 1))
    lines.append(f'{prefix}end else begin')
    if then_invalid:
        lines.extend(_invalid_assignment(invalid_target, indent + 1))
    else:
        lines.extend(_render_process(process.else_branch, else_path, invalid_receives, invalid_target, indent + 1))
    lines.append(f'{prefix}end')
    return lines


def _invalid_assignment(target: str | None, indent: int) -> list[str]:
    if target is None:
        raise DecomposedSVCSPError('missing invalid receive target')
    prefix = '  ' * indent
    return [f"{prefix}body_payload = '0;", f'{prefix}{target} = body_payload;']


def _identifier(value: str, context: str) -> str:
    if not _IDENTIFIER.fullmatch(value):
        raise DecomposedSVCSPError(f'unsupported {context} identifier {value!r}')
    return value


def _type(payload_type: behavioral.PayloadType) -> str:
    if payload_type.width.bits == 1:
        return payload_type.base
    if payload_type.packed_range is not None:
        return f'{payload_type.base} [{payload_type.packed_range[0]}:{payload_type.packed_range[1]}]'
    return f'{payload_type.base} [{payload_type.width.bits}-1:0]'


def _target_type(target: behavioral.Variable | behavioral.Expression) -> behavioral.PayloadType:
    if isinstance(target, behavioral.Variable):
        return target.payload_type
    if target.variable is not None:
        return target.variable.payload_type
    raise DecomposedSVCSPError('cannot establish invalid receive payload type')


def _endpoint(endpoint: behavioral.ChannelEndpoint) -> str:
    rendered = _identifier(endpoint.name, 'channel')
    for selector in endpoint.selectors:
        if selector.form == 'index' and len(selector.operands) == 1:
            rendered += f'[{_expression(selector.operands[0])}]'
        else:
            raise DecomposedSVCSPError('unsupported channel selection')
    return rendered


def _target(target: behavioral.Variable | behavioral.Expression) -> str:
    return _identifier(target.name, 'variable') if isinstance(target, behavioral.Variable) else _expression(target)


def _expression(expression: behavioral.Expression) -> str:
    if expression.form == 'name' and expression.variable is not None:
        return _identifier(expression.variable.name, 'variable')
    if expression.form == 'parameter' and expression.parameter is not None:
        return _identifier(expression.parameter.name, 'parameter')
    if expression.form == 'literal' and expression.value is not None:
        return expression.value
    if expression.form == 'select' and expression.variable is not None:
        selections = []
        for item in expression.operands:
            if item.form != 'index' or len(item.operands) != 1:
                raise DecomposedSVCSPError('unsupported packed selection')
            selections.append(f'[{_expression(item.operands[0])}]')
        return _identifier(expression.variable.name, 'variable') + ''.join(selections)
    if expression.form == 'unary' and expression.operator and len(expression.operands) == 1:
        return f'({expression.operator}{_expression(expression.operands[0])})'
    if expression.form == 'binary' and expression.operator and len(expression.operands) == 2:
        return f'({_expression(expression.operands[0])} {expression.operator} {_expression(expression.operands[1])})'
    raise DecomposedSVCSPError(f'unsupported expression form {expression.form!r}')
