"""Emit a narrow Phase 3 conditional-communication decomposition as SVCSP text.

This is a source-level decomposition emitter, not an RTL emitter and not a new
IR.  It consumes ``NormalizedModule`` directly and intentionally supports one
conditional Send or Receive occurrence only.
"""
from __future__ import annotations

import re

from . import behavioral_ir as behavioral
from . import communication_normalization as normalization


class DecomposedSVCSPError(ValueError):
    """A normalized module is outside the decomposed-SVCSP emitter MVP."""


_IDENTIFIER = re.compile(r'^[A-Za-z_][A-Za-z0-9_$]*$')


def _identifier(value: str, context: str) -> str:
    if not _IDENTIFIER.fullmatch(value):
        raise DecomposedSVCSPError(f'unsupported {context} identifier {value!r}')
    return value


def _width(payload_type: behavioral.PayloadType | None) -> str:
    if payload_type is None:
        raise DecomposedSVCSPError('cannot establish conditional communication payload width')
    width = payload_type.width
    if width.bits is not None:
        return str(width.bits)
    if width.symbolic is not None and width.parameters:
        return width.symbolic
    raise DecomposedSVCSPError('conditional communication payload width is unresolved')


def _type(payload_type: behavioral.PayloadType) -> str:
    if payload_type.width.bits == 1:
        return payload_type.base
    if payload_type.packed_range is not None:
        return f'{payload_type.base} [{payload_type.packed_range[0]}:{payload_type.packed_range[1]}]'
    return f'{payload_type.base} [{_width(payload_type)}-1:0]'


def _expression(expression: behavioral.Expression) -> str:
    if expression.form == 'name' and expression.variable is not None:
        return _identifier(expression.variable.name, 'variable')
    if expression.form == 'parameter' and expression.parameter is not None:
        return _identifier(expression.parameter.name, 'parameter')
    if expression.form == 'literal' and expression.value is not None:
        return expression.value
    if expression.form == 'select' and expression.variable is not None:
        result = _identifier(expression.variable.name, 'variable')
        for selector in expression.operands:
            if selector.form == 'index' and len(selector.operands) == 1:
                result += f'[{_expression(selector.operands[0])}]'
            elif selector.form == 'range' and len(selector.operands) == 2:
                result += f'[{_expression(selector.operands[0])}:{_expression(selector.operands[1])}]'
            else:
                raise DecomposedSVCSPError('unsupported packed selection in expression')
        return result
    if expression.form == 'unary' and expression.operator and len(expression.operands) == 1:
        return f'({expression.operator}{_expression(expression.operands[0])})'
    if expression.form == 'binary' and expression.operator and len(expression.operands) == 2:
        return f'({_expression(expression.operands[0])} {expression.operator} {_expression(expression.operands[1])})'
    if expression.form == 'conditional' and len(expression.operands) == 3:
        return f'({_expression(expression.operands[0])} ? {_expression(expression.operands[1])} : {_expression(expression.operands[2])})'
    if expression.form == 'concatenate':
        return '{' + ', '.join(_expression(item) for item in expression.operands) + '}'
    if expression.form == 'repeat' and len(expression.operands) == 2:
        return '{' + _expression(expression.operands[0]) + '{' + _expression(expression.operands[1]) + '}}'
    raise DecomposedSVCSPError(f'unsupported expression form {expression.form!r}')


def _target(target: behavioral.Variable | behavioral.Expression) -> str:
    if isinstance(target, behavioral.Variable):
        return _identifier(target.name, 'variable')
    return _expression(target)


def _contains_site(process: normalization.NormalizedProcess,
                   site: normalization.CommunicationSite) -> bool:
    if process is site:
        return True
    if isinstance(process, behavioral.Sequence):
        return any(_contains_site(item, site) for item in process.items)
    if isinstance(process, behavioral.If):
        return _contains_site(process.then_branch, site) or _contains_site(process.else_branch, site)
    if isinstance(process, behavioral.Parallel):
        return any(_contains_site(item, site) for item in process.branches)
    return False


def _ordinary_channels(process: normalization.NormalizedProcess,
                       result: set[behavioral.ChannelEndpoint]) -> None:
    if isinstance(process, (behavioral.Send, behavioral.Receive)):
        result.add(process.channel)
    elif isinstance(process, behavioral.Sequence):
        for item in process.items:
            _ordinary_channels(item, result)
    elif isinstance(process, behavioral.If):
        _ordinary_channels(process.then_branch, result)
        _ordinary_channels(process.else_branch, result)
    elif isinstance(process, behavioral.Parallel):
        for item in process.branches:
            _ordinary_channels(item, result)


def _same_external_endpoint(left: behavioral.ChannelEndpoint,
                            right: behavioral.ChannelEndpoint) -> bool:
    """Compare declaration identity without requiring equal resolved payload context."""
    return left.name == right.name and left.selectors == right.selectors


def _is_empty(process: normalization.NormalizedProcess) -> bool:
    return isinstance(process, behavioral.Skip) or (
        isinstance(process, behavioral.Sequence) and not process.items
    )


def _direct_site_branch(process: normalization.NormalizedProcess,
                        site: normalization.CommunicationSite) -> bool:
    return process is site or (
        isinstance(process, behavioral.Sequence) and len(process.items) == 1 and process.items[0] is site
    )


def _render_regular(process: normalization.NormalizedProcess, indent: int) -> list[str]:
    prefix = '  ' * indent
    if isinstance(process, normalization.CommunicationSite):
        raise DecomposedSVCSPError('conditional Send site must be a direct If branch in this MVP')
    if isinstance(process, behavioral.Skip):
        return []
    if isinstance(process, behavioral.Send):
        return [f'{prefix}{_identifier(process.channel.name, "channel")}.Send({_expression(process.value)});']
    if isinstance(process, behavioral.Receive):
        return [f'{prefix}{_identifier(process.channel.name, "channel")}.Receive({_target(process.target)});']
    if isinstance(process, behavioral.Assign):
        return [f'{prefix}{_target(process.target)} = {_expression(process.value)};']
    if isinstance(process, behavioral.Sequence):
        lines: list[str] = []
        for item in process.items:
            lines.extend(_render_regular(item, indent))
        return lines
    if isinstance(process, behavioral.If):
        lines = [f'{prefix}if ({_expression(process.condition)}) begin']
        lines.extend(_render_regular(process.then_branch, indent + 1))
        lines.append(f'{prefix}end else begin')
        lines.extend(_render_regular(process.else_branch, indent + 1))
        lines.append(f'{prefix}end')
        return lines
    raise DecomposedSVCSPError(f'unsupported ordinary BODY process {type(process).__name__}')


def _render_body_process(process: normalization.NormalizedProcess,
                         site: normalization.CommunicationSite,
                         communication: normalization.BodyCommunication,
                         enable_channel: str,
                         indent: int) -> list[str]:
    """Render one supported direct-site If with an unconditional BODY operation."""
    prefix = '  ' * indent
    if isinstance(process, behavioral.Sequence):
        lines: list[str] = []
        for item in process.items:
            lines.extend(_render_body_process(item, site, communication, enable_channel, indent))
        return lines
    if not _contains_site(process, site):
        return _render_regular(process, indent)
    if not isinstance(process, behavioral.If):
        raise DecomposedSVCSPError('conditional communication site must be directly contained by an If in this MVP')

    then_site = _direct_site_branch(process.then_branch, site)
    else_site = _direct_site_branch(process.else_branch, site)
    if then_site == else_site:
        raise DecomposedSVCSPError('conditional communication site must occur in exactly one direct If branch')

    lines = [f'{prefix}{enable_channel}.Send({_expression(communication.enable.condition)});']
    residual = process.else_branch if then_site else process.then_branch
    residual_condition = f'!({_expression(process.condition)})' if then_site else _expression(process.condition)
    if not _is_empty(residual):
        lines.append(f'{prefix}if ({residual_condition}) begin')
        lines.extend(_render_regular(residual, indent + 1))
        lines.append(f'{prefix}end')
    body_channel = _identifier(communication.channel.id, 'body channel')
    if isinstance(communication, normalization.BodySend):
        lines.append(f'{prefix}{body_channel}.Send({_expression(communication.value)});')
    elif isinstance(communication, normalization.BodyReceive):
        lines.append(f'{prefix}{body_channel}.Receive({_target(communication.target)});')
    else:
        raise DecomposedSVCSPError('unsupported BODY communication')
    return lines


def _module_parameters(parameters: tuple[behavioral.Parameter, ...]) -> str:
    if not parameters:
        return ''
    values = []
    for parameter in parameters:
        if parameter.default is None:
            raise DecomposedSVCSPError(f'parameter {parameter.name} has no preserved default')
        values.append(f'parameter int {_identifier(parameter.name, "parameter")} = {parameter.default}')
    return ' #(\n  ' + ',\n  '.join(values) + '\n)'


def _module_header(name: str, ports: list[str], parameters: tuple[behavioral.Parameter, ...]) -> list[str]:
    return [f'module {name}{_module_parameters(parameters)} (', '  ' + ',\n  '.join(ports), ');']


def emit_conditional_send_decomposition(module: normalization.NormalizedModule) -> str:
    """Emit one conditional Send/Receive BODY/wrapper/top composition.

    This MVP rejects multiple wrappers, selected endpoints, nested/non-direct
    conditional sites, parallel behavior, and shared ordinary use of the
    conditional external endpoint.
    """
    if not isinstance(module, normalization.NormalizedModule):
        raise DecomposedSVCSPError('expected a NormalizedModule')
    if len(module.wrappers) != 1 or len(module.body_communications) != 1:
        raise DecomposedSVCSPError('decomposed-SVCSP emitter requires exactly one conditional communication occurrence')
    wrapper = module.wrappers[0]
    communication = module.body_communications[0]
    if not isinstance(wrapper, (normalization.NormalizedSend, normalization.NormalizedReceive)):
        raise DecomposedSVCSPError('unsupported conditional wrapper')
    if ((isinstance(wrapper, normalization.NormalizedSend) and not isinstance(communication, normalization.BodySend)) or
            (isinstance(wrapper, normalization.NormalizedReceive) and not isinstance(communication, normalization.BodyReceive))):
        raise DecomposedSVCSPError('conditional wrapper/body communication mismatch')
    if (wrapper.site is not communication.site or wrapper.body_channel is not communication.channel or
            wrapper.enable is not communication.enable):
        raise DecomposedSVCSPError('conditional communication identities are inconsistent')
    if isinstance(communication, normalization.BodySend) and communication.payload_valid_when is not wrapper.enable:
        raise DecomposedSVCSPError('conditional Send identities are inconsistent')
    if isinstance(communication, normalization.BodyReceive) and (
            communication.data_valid_when is not wrapper.enable or
            communication.disabled_token is not wrapper.disabled_token):
        raise DecomposedSVCSPError('conditional Receive identities are inconsistent')
    if isinstance(wrapper, normalization.NormalizedReceive) and (
            not wrapper.consumes_external_when_enabled or
            not wrapper.forwards_real_data_when_enabled or
            wrapper.acknowledges_external_when_disabled or
            not wrapper.provides_body_token_when_disabled):
        raise DecomposedSVCSPError('conditional Receive semantics are unsupported by the decomposed-SVCSP emitter')
    if wrapper.endpoint.selectors:
        raise DecomposedSVCSPError('selected conditional endpoints are unsupported by the decomposed-SVCSP emitter')
    if isinstance(module.body, behavioral.Parallel):
        raise DecomposedSVCSPError('parallel behavior is unsupported by the decomposed-SVCSP emitter')

    ordinary: set[behavioral.ChannelEndpoint] = set()
    _ordinary_channels(module.body, ordinary)
    if any(_same_external_endpoint(wrapper.endpoint, endpoint) for endpoint in ordinary):
        raise DecomposedSVCSPError('conditional endpoint also has ordinary BODY communication')
    if any(channel.selectors for channel in ordinary):
        raise DecomposedSVCSPError('selected ordinary endpoints are unsupported by the decomposed-SVCSP emitter')

    declared_wrapper_endpoints = [channel for channel in module.channels
                                  if _same_external_endpoint(channel, wrapper.endpoint)]
    if len(declared_wrapper_endpoints) != 1:
        raise DecomposedSVCSPError('conditional endpoint is not a module external interface')
    variable_names = [_identifier(variable.name, 'variable') for variable in module.variables]
    if len(variable_names) != len(set(variable_names)) or any(variable.scope != ('module',) for variable in module.variables):
        raise DecomposedSVCSPError('shadowed or block-local variables are unsupported by the decomposed-SVCSP emitter')

    body_name = f'{_identifier(module.name, "module")}_BODY'
    wrapper_kind = 'SEND' if isinstance(wrapper, normalization.NormalizedSend) else 'RECV'
    wrapper_instance = 'x_send' if isinstance(wrapper, normalization.NormalizedSend) else 'x_recv'
    wrapper_name = f'{_identifier(module.name, "module")}_X_{wrapper_kind}_0'
    top_name = f'{_identifier(module.name, "module")}_DECOMPOSED'
    internal_name = _identifier(communication.channel.id, 'body channel')
    enable_channel_name = _identifier(f'enable_channel_{wrapper.enable.occurrence}', 'enable channel')
    external_name = _identifier(wrapper.endpoint.name, 'channel')
    body_external = [channel.name for channel in module.channels
                     if not _same_external_endpoint(channel, wrapper.endpoint)]

    body_ports = [f'Channel {_identifier(name, "channel")}' for name in body_external]
    body_ports.extend([f'Channel {internal_name}', f'Channel {enable_channel_name}'])
    wrapper_ports = [f'Channel external_channel', f'Channel {internal_name}', f'Channel {enable_channel_name}']
    top_ports = [f'Channel {_identifier(channel.name, "channel")}' for channel in module.channels]

    lines: list[str] = []
    lines.extend(_module_header(body_name, body_ports, module.parameters))
    for variable in module.variables:
        lines.append(f'  {_type(variable.payload_type)} {_identifier(variable.name, "variable")};')
    lines.append('  always begin')
    lines.extend(_render_body_process(module.body, wrapper.site, communication, enable_channel_name, 2))
    lines.append('  end')
    lines.append('endmodule')
    lines.append('')

    payload_type = wrapper.endpoint.payload_type
    if payload_type is None:
        raise DecomposedSVCSPError('cannot establish wrapper payload type')
    lines.extend(_module_header(wrapper_name, wrapper_ports, module.parameters))
    lines.append(f'  {_type(payload_type)} body_payload;')
    lines.append('  logic enable_token;')
    lines.append('  always begin')
    lines.append(f'    {enable_channel_name}.Receive(enable_token);')
    if isinstance(wrapper, normalization.NormalizedSend):
        lines.append(f'    {internal_name}.Receive(body_payload);')
        lines.append('    if (enable_token) external_channel.Send(body_payload);')
    else:
        lines.append('    if (enable_token) begin')
        lines.append('      external_channel.Receive(body_payload);')
        lines.append('    end else begin')
        lines.append("      body_payload = '0;")
        lines.append('    end')
        lines.append(f'    {internal_name}.Send(body_payload);')
    lines.append('  end')
    lines.append('endmodule')
    lines.append('')

    lines.extend(_module_header(top_name, top_ports, module.parameters))
    lines.append(f'  Channel #({_width(payload_type)}) {internal_name}();')
    lines.append(f'  Channel #(1) {enable_channel_name}();')
    body_connections = [f'.{_identifier(name, "channel")}({_identifier(name, "channel")})' for name in body_external]
    body_connections.extend([f'.{internal_name}({internal_name})',
                             f'.{enable_channel_name}({enable_channel_name})'])
    lines.append(f'  {body_name} body (')
    lines.append('    ' + ',\n    '.join(body_connections))
    lines.append('  );')
    lines.append(f'  {wrapper_name} {wrapper_instance} (')
    lines.append(f'    .external_channel({external_name}),')
    lines.append(f'    .{internal_name}({internal_name}),')
    lines.append(f'    .{enable_channel_name}({enable_channel_name})')
    lines.append('  );')
    lines.append('endmodule')
    return '\n'.join(lines) + '\n'
