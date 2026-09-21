from dataclasses import asdict
from pathlib import Path
import shutil
import subprocess

import pytest

from svcsp_compiler import (
    DecomposedSVCSPError, emit_conditional_send_decomposition,
    lower_behavioral, normalize_communication, parse_text,
)


ROOT = Path(__file__).resolve().parents[1]
FIXTURE = ROOT / 'tests' / 'fixtures' / 'conditional_send_decomposition.sv'
RECEIVE_FIXTURE = ROOT / 'tests' / 'fixtures' / 'conditional_receive_decomposition.sv'
IVERILOG = shutil.which('iverilog')
VVP = shutil.which('vvp')


def normalized(source: str):
    return normalize_communication(lower_behavioral(parse_text(source, 'decomposed.sv')))


class _IcarusFlatteningError(ValueError):
    """The test-only Channel flattening subset cannot represent this source."""


def _flatten_channel_svcsp_for_icarus(source: str) -> str:
    """Desugar the narrow Channel subset used by the equivalence simulation.

    Icarus cannot pass SystemVerilog interfaces through module ports.  This
    test-only helper preserves the source operation ordering and lowers each
    Channel to payload/request/acknowledge signals plus local four-phase tasks.
    It intentionally accepts only the concrete-width, direct-call form emitted
    by this conditional-Send decomposition MVP.
    """
    import re

    module_pattern = re.compile(
        r'\bmodule\s+([A-Za-z_]\w*)\s*\((.*?)\)\s*;(.*?)\bendmodule', re.DOTALL,
    )
    channel_port_pattern = re.compile(r'\s*Channel\s+([A-Za-z_]\w*)\s*')
    channel_instance_pattern = re.compile(
        r'\bChannel\s*#\(\s*(\d+)\s*\)\s+([A-Za-z_]\w*)\s*\(\s*\)\s*;'
    )
    variable_pattern = re.compile(
        r'\blogic\s*(?:\[\s*(\d+)\s*:\s*(\d+)\s*\])?\s+([A-Za-z_]\w*)\s*;'
    )
    instance_pattern = re.compile(
        r'\b([A-Za-z_]\w*)\s+([A-Za-z_]\w*)\s*\((.*?)\)\s*;', re.DOTALL,
    )
    connection_pattern = re.compile(r'\.([A-Za-z_]\w*)\s*\(\s*([A-Za-z_]\w*)\s*\)')
    communication_pattern = re.compile(
        r'\b([A-Za-z_]\w*)\.(Send|Receive)\s*\(\s*([^()]+?)\s*\)\s*;'
    )

    class Module:
        def __init__(self, name, port_text, body):
            self.name = name
            self.ports = []
            for item in port_text.split(','):
                match = channel_port_pattern.fullmatch(item)
                if not match:
                    raise _IcarusFlatteningError(
                        f'{name}: only Channel port declarations are supported by the Icarus test flattener'
                    )
                self.ports.append(match.group(1))
            self.body = body
            self.variables = {}
            self.internal_channels = {}
            self.instances = []
            self.communications = []
            self.widths = {port: None for port in self.ports}
            self.roles = {port: None for port in self.ports}

    modules = []
    cursor = 0
    for match in module_pattern.finditer(source):
        if source[cursor:match.start()].strip():
            raise _IcarusFlatteningError('expected only complete module declarations')
        modules.append(Module(match.group(1), match.group(2), match.group(3)))
        cursor = match.end()
    if not modules or source[cursor:].strip():
        raise _IcarusFlatteningError('expected only complete module declarations')
    by_name = {module.name: module for module in modules}
    if len(by_name) != len(modules):
        raise _IcarusFlatteningError('duplicate module declaration')

    def declared_width(expression, variables):
        expression = expression.strip()
        if expression in variables:
            return variables[expression]
        if re.fullmatch(r"\d+'[bBoOdDhH][0-9a-fA-F_xXzZ]+", expression):
            return int(expression.split("'", 1)[0])
        select = re.fullmatch(r'([A-Za-z_]\w*)\s*\[\s*\d+\s*\]', expression)
        if select and select.group(1) in variables:
            return 1
        raise _IcarusFlatteningError(f'cannot prove concrete payload width for {expression!r}')

    for module in modules:
        for width, name in channel_instance_pattern.findall(module.body):
            if name in module.widths:
                raise _IcarusFlatteningError(f'{module.name}: duplicate Channel {name}')
            module.internal_channels[name] = int(width)
            module.widths[name] = int(width)
            module.roles[name] = None
        body_without_channels = channel_instance_pattern.sub('', module.body)
        for left, right, name in variable_pattern.findall(body_without_channels):
            if name in module.variables:
                raise _IcarusFlatteningError(f'{module.name}: duplicate logic variable {name}')
            module.variables[name] = abs(int(left) - int(right)) + 1 if left else 1
        for instance in instance_pattern.finditer(body_without_channels):
            child_name, instance_name, connection_text = instance.groups()
            if child_name not in by_name:
                continue
            connections = connection_pattern.findall(connection_text)
            if not connections or len(connections) != len(connection_text.split(',')):
                raise _IcarusFlatteningError(f'{module.name}: only named Channel connections are supported')
            if len({formal for formal, _ in connections}) != len(connections):
                raise _IcarusFlatteningError(f'{module.name}: duplicate child Channel connection')
            module.instances.append((child_name, instance_name, dict(connections), instance.group(0)))
        for communication in communication_pattern.finditer(body_without_channels):
            channel, method, argument = communication.groups()
            if channel not in module.widths:
                raise _IcarusFlatteningError(f'{module.name}: unknown Channel {channel}')
            role = 'send' if method == 'Send' else 'receive'
            width = declared_width(argument, module.variables)
            if module.widths[channel] is None:
                module.widths[channel] = width
            elif module.widths[channel] != width:
                raise _IcarusFlatteningError(f'{module.name}: incompatible width for Channel {channel}')
            if module.roles[channel] not in {None, role}:
                raise _IcarusFlatteningError(f'{module.name}: Channel {channel} has mixed Send/Receive roles')
            module.roles[channel] = role
            module.communications.append((channel, method, argument, communication.group(0)))

    # Child interface ports establish the parent external-port roles and the
    # widths of parent/internal actual channels.  Iterate to a fixed point so
    # the decomposed top learns its interface contract from BODY and wrapper.
    changed = True
    while changed:
        changed = False
        for module in modules:
            for child_name, _, connections, _ in module.instances:
                child = by_name[child_name]
                if set(connections) != set(child.ports):
                    raise _IcarusFlatteningError(
                        f'{module.name}: incomplete Channel connection for {child_name}'
                    )
                for formal, actual in connections.items():
                    if actual not in module.widths:
                        raise _IcarusFlatteningError(f'{module.name}: unknown Channel actual {actual}')
                    parent_width = module.widths[actual]
                    child_width = child.widths[formal]
                    if parent_width is None and child_width is not None:
                        module.widths[actual] = child_width
                        changed = True
                    elif parent_width is not None and child_width is not None and parent_width != child_width:
                        raise _IcarusFlatteningError(
                            f'{module.name}: incompatible width on Channel {actual}'
                        )
                    # An internal Channel joins a sender child to a receiver
                    # child, so it deliberately has no single module-facing
                    # direction.  Only an external parent port inherits a
                    # child's external role.
                    if actual in module.ports:
                        parent_role = module.roles[actual]
                        child_role = child.roles[formal]
                        if parent_role is None and child_role is not None:
                            module.roles[actual] = child_role
                            changed = True
                        elif parent_role is not None and child_role is not None and parent_role != child_role:
                            raise _IcarusFlatteningError(
                                f'{module.name}: incompatible role on Channel {actual}'
                            )

    for module in modules:
        for channel in module.ports:
            if module.widths[channel] is None or module.roles[channel] is None:
                raise _IcarusFlatteningError(
                    f'{module.name}: unresolved Channel width or direction for {channel}'
                )
        for channel, _, _, _ in module.communications:
            if module.widths[channel] is None or module.roles[channel] is None:
                raise _IcarusFlatteningError(
                    f'{module.name}: unresolved directly used Channel {channel}'
                )

    def channel_ports(name, width, role):
        vector = '' if width == 1 else f' [{width - 1}:0]'
        if role == 'receive':
            return [
                f'input logic{vector} {name}_payload',
                f'input logic {name}_request',
                f'output logic {name}_acknowledge',
            ]
        return [
            f'output logic{vector} {name}_payload',
            f'output logic {name}_request',
            f'input logic {name}_acknowledge',
        ]

    def task_lines(name, width, role):
        vector = '' if width == 1 else f' [{width - 1}:0]'
        if role == 'receive':
            return [
                f'  task automatic {name}_Receive(output logic{vector} value);',
                f'    wait ({name}_request === 1\'b1);',
                f'    value = {name}_payload;',
                f'    {name}_acknowledge = 1\'b1;',
                f'    wait ({name}_request === 1\'b0);',
                f'    {name}_acknowledge = 1\'b0;',
                '  endtask',
            ]
        return [
            f'  task automatic {name}_Send(input logic{vector} value);',
            f'    {name}_payload = value;',
            f'    {name}_request = 1\'b1;',
            f'    wait ({name}_acknowledge === 1\'b1);',
            f'    {name}_request = 1\'b0;',
            f'    wait ({name}_acknowledge === 1\'b0);',
            '  endtask',
        ]

    rendered = []
    for module in modules:
        flattened_ports = []
        for port in module.ports:
            flattened_ports.extend(channel_ports(port, module.widths[port], module.roles[port]))
        rendered.extend([f'module {module.name} (', '  ' + ',\n  '.join(flattened_ports), ');'])
        for name, width in module.internal_channels.items():
            vector = '' if width == 1 else f' [{width - 1}:0]'
            rendered.extend([
                f'  logic{vector} {name}_payload;',
                f'  logic {name}_request;',
                f'  logic {name}_acknowledge;',
            ])
        for match in variable_pattern.finditer(channel_instance_pattern.sub('', module.body)):
            rendered.append(f'  {match.group(0).strip()}')
        direct_channels = {channel for channel, _, _, _ in module.communications}
        for channel in direct_channels:
            rendered.extend(task_lines(channel, module.widths[channel], module.roles[channel]))
        output_initializers = []
        for channel in direct_channels:
            if module.roles[channel] == 'send':
                output_initializers.extend([f'{channel}_payload = \'0;', f'{channel}_request = 1\'b0;'])
            else:
                output_initializers.append(f'{channel}_acknowledge = 1\'b0;')
        if output_initializers:
            rendered.extend(['  initial begin', *(f'    {line}' for line in output_initializers), '  end'])

        body = channel_instance_pattern.sub('', module.body)
        for _, _, _, instance_text in module.instances:
            body = body.replace(instance_text, '')
        body = variable_pattern.sub('', body)
        for channel, method, argument, original_call in module.communications:
            body = body.replace(original_call, f'{channel}_{method}({argument});')
        if '.Send(' in body or '.Receive(' in body:
            raise _IcarusFlatteningError(f'{module.name}: unsupported Channel method syntax')
        if re.search(r'\b(Channel|interface|fork|join|case|for|while|repeat|task|function)\b', body):
            raise _IcarusFlatteningError(f'{module.name}: unsupported test-only flattening syntax')
        if body.count('always') > 1:
            raise _IcarusFlatteningError(f'{module.name}: multiple always blocks are unsupported')
        body = body.strip()
        if body:
            rendered.extend(f'  {line}' if line else '' for line in body.splitlines())

        for child_name, instance_name, connections, _ in module.instances:
            child = by_name[child_name]
            port_map = []
            for formal in child.ports:
                actual = connections[formal]
                port_map.extend([
                    f'.{formal}_payload({actual}_payload)',
                    f'.{formal}_request({actual}_request)',
                    f'.{formal}_acknowledge({actual}_acknowledge)',
                ])
            rendered.extend([
                f'  {child_name} {instance_name} (',
                '    ' + ',\n    '.join(port_map),
                '  );',
            ])
        rendered.append('endmodule')
        rendered.append('')
    return '\n'.join(rendered)


def test_conditional_send_emits_body_wrapper_and_composed_top():
    module = normalized(FIXTURE.read_text())
    text = emit_conditional_send_decomposition(module)

    assert 'module conditional_send_decomposition_BODY (' in text
    assert 'module conditional_send_decomposition_X_SEND_0 (' in text
    assert 'module conditional_send_decomposition_DECOMPOSED (' in text
    assert 'Channel L,' in text
    assert 'Channel R' in text
    assert 'Channel #(8) body_channel_0();' in text
    assert 'Channel #(1) enable_channel_0();' in text
    assert 'Channel enable_channel_0' in text
    assert 'output logic enable_0' not in text
    assert 'input logic enable_0' not in text
    assert 'logic enable_0;' not in text
    assert '.enable_channel_0(enable_channel_0)' in text
    assert 'conditional_send_decomposition_BODY body (' in text
    assert 'conditional_send_decomposition_X_SEND_0 x_send (' in text


def test_body_send_is_unconditional_and_enable_is_body_produced():
    text = emit_conditional_send_decomposition(normalized(FIXTURE.read_text()))
    body = text.split('module conditional_send_decomposition_X_SEND_0', 1)[0]

    assert 'L.Receive(data);' in body
    assert 'enable_channel_0.Send(data[0]);' in body
    assert 'body_channel_0.Send(data);' in body
    assert 'R.Send(data);' not in body
    assert body.index('enable_channel_0.Send(data[0]);') < body.index('body_channel_0.Send(data);')


def test_wrapper_always_consumes_body_token_and_gates_only_external_send():
    text = emit_conditional_send_decomposition(normalized(FIXTURE.read_text()))
    wrapper = text.split('module conditional_send_decomposition_X_SEND_0', 1)[1].split(
        'module conditional_send_decomposition_DECOMPOSED', 1,
    )[0]

    assert 'enable_channel_0.Receive(enable_token);' in wrapper
    assert 'body_channel_0.Receive(body_payload);' in wrapper
    assert 'if (enable_token) external_channel.Send(body_payload);' in wrapper
    assert wrapper.index('enable_channel_0.Receive(enable_token);') < wrapper.index(
        'body_channel_0.Receive(body_payload);'
    ) < wrapper.index('if (enable_token) external_channel.Send(body_payload);')


def test_ordinary_external_receive_is_preserved_in_body_and_top_interface():
    text = emit_conditional_send_decomposition(normalized(FIXTURE.read_text()))
    assert 'module conditional_send_decomposition_BODY (\n  Channel L,' in text
    body = text.split('module conditional_send_decomposition_X_SEND_0', 1)[0]
    assert 'Channel R' not in body
    assert '.L(L)' in text
    assert '.external_channel(R)' in text


def test_conditional_receive_emits_body_wrapper_and_composed_top():
    text = emit_conditional_send_decomposition(normalized(RECEIVE_FIXTURE.read_text()))

    assert 'module conditional_receive_decomposition_BODY (' in text
    assert 'module conditional_receive_decomposition_X_RECV_0 (' in text
    assert 'module conditional_receive_decomposition_DECOMPOSED (' in text
    assert 'Channel #(8) body_channel_0();' in text
    assert 'Channel #(1) enable_channel_0();' in text
    assert 'conditional_receive_decomposition_X_RECV_0 x_recv (' in text


def test_body_receive_is_unconditional_and_enable_is_body_produced():
    text = emit_conditional_send_decomposition(normalized(RECEIVE_FIXTURE.read_text()))
    body = text.split('module conditional_receive_decomposition_X_RECV_0', 1)[0]

    assert 'Control.Receive(enable);' in body
    assert 'enable_channel_0.Send(enable);' in body
    assert 'body_channel_0.Receive(data);' in body
    assert 'L.Receive(data);' not in body
    assert body.index('enable_channel_0.Send(enable);') < body.index('body_channel_0.Receive(data);')


def test_receive_wrapper_receives_enable_then_forwards_real_or_dummy_token():
    text = emit_conditional_send_decomposition(normalized(RECEIVE_FIXTURE.read_text()))
    wrapper = text.split('module conditional_receive_decomposition_X_RECV_0', 1)[1].split(
        'module conditional_receive_decomposition_DECOMPOSED', 1,
    )[0]

    assert 'enable_channel_0.Receive(enable_token);' in wrapper
    assert 'if (enable_token) begin\n      external_channel.Receive(body_payload);' in wrapper
    assert "end else begin\n      body_payload = '0;" in wrapper
    assert 'body_channel_0.Send(body_payload);' in wrapper
    assert 'external_channel.Receive(body_payload);' not in wrapper.split('end else begin', 1)[1]
    assert wrapper.index('enable_channel_0.Receive(enable_token);') < wrapper.index(
        'external_channel.Receive(body_payload);'
    ) < wrapper.index('body_channel_0.Send(body_payload);')


def test_multiple_conditional_communications_fail_closed():
    module = normalized('''module m(Channel #(8) A, Channel #(8) B); logic c; logic [7:0] x; always begin
if (c) A.Receive(x); if (c) B.Receive(x); end endmodule''')
    with pytest.raises(DecomposedSVCSPError, match='exactly one conditional communication'):
        emit_conditional_send_decomposition(module)


def test_emitter_does_not_mutate_normalized_module():
    module = normalized(FIXTURE.read_text())
    snapshot = asdict(module)
    emit_conditional_send_decomposition(module)
    assert asdict(module) == snapshot


def test_icarus_channel_flattener_expands_production_channel_source_and_fails_closed():
    original = FIXTURE.read_text()
    decomposed = emit_conditional_send_decomposition(normalized(original))

    original_flat = _flatten_channel_svcsp_for_icarus(original)
    decomposed_flat = _flatten_channel_svcsp_for_icarus(decomposed)
    assert 'interface Channel' not in original_flat
    assert 'input logic [7:0] L_payload' in original_flat
    assert 'task automatic L_Receive' in original_flat
    assert 'task automatic R_Send' in original_flat
    assert 'Channel' not in decomposed_flat
    assert 'logic [7:0] body_channel_0_payload;' in decomposed_flat
    assert 'logic enable_channel_0_request;' in decomposed_flat
    assert '.enable_channel_0_request(enable_channel_0_request)' in decomposed_flat

    with pytest.raises(_IcarusFlatteningError, match='unsupported test-only flattening syntax'):
        _flatten_channel_svcsp_for_icarus(
            'module unsupported(Channel A); always fork A.Send(1\'b1); join endmodule'
        )


def test_conditional_send_decomposition_is_behaviorally_equivalent(tmp_path):
    """Run original and emitted decomposition through the same flattened CSP model."""
    original = FIXTURE.read_text()
    decomposed = emit_conditional_send_decomposition(normalized(original))
    original_path = tmp_path / 'original_flat.sv'
    decomposed_path = tmp_path / 'decomposed_flat.sv'
    testbench_path = tmp_path / 'tb.sv'
    executable = tmp_path / 'simulation'
    # Both inputs pass through the same test-only flattening helper.  The
    # production emitter itself remains Channel-based SVCSP.
    original_path.write_text(_flatten_channel_svcsp_for_icarus(original))
    decomposed_path.write_text(_flatten_channel_svcsp_for_icarus(decomposed))
    if not (IVERILOG and VVP):
        pytest.skip('Icarus Verilog is not available')
    testbench_path.write_text(r'''
`timescale 1ns/1ps
module tb;
  logic [7:0] l_original_payload = '0;
  logic l_original_request = 1'b0;
  wire l_original_acknowledge;
  wire [7:0] r_original_payload;
  wire r_original_request;
  logic r_original_acknowledge = 1'b0;

  logic [7:0] l_decomposed_payload = '0;
  logic l_decomposed_request = 1'b0;
  wire l_decomposed_acknowledge;
  wire [7:0] r_decomposed_payload;
  wire r_decomposed_request;
  logic r_decomposed_acknowledge = 1'b0;

  logic [7:0] original_trace [0:1];
  logic [7:0] decomposed_trace [0:1];
  integer original_count = 0;
  integer decomposed_count = 0;

  conditional_send_decomposition original (
    .L_payload(l_original_payload),
    .L_request(l_original_request),
    .L_acknowledge(l_original_acknowledge),
    .R_payload(r_original_payload),
    .R_request(r_original_request),
    .R_acknowledge(r_original_acknowledge)
  );
  conditional_send_decomposition_DECOMPOSED decomposed (
    .L_payload(l_decomposed_payload),
    .L_request(l_decomposed_request),
    .L_acknowledge(l_decomposed_acknowledge),
    .R_payload(r_decomposed_payload),
    .R_request(r_decomposed_request),
    .R_acknowledge(r_decomposed_acknowledge)
  );

  task automatic send_original(input logic [7:0] value);
    l_original_payload = value;
    l_original_request = 1'b1;
    wait (l_original_acknowledge === 1'b1);
    l_original_request = 1'b0;
    wait (l_original_acknowledge === 1'b0);
  endtask

  task automatic send_decomposed(input logic [7:0] value);
    l_decomposed_payload = value;
    l_decomposed_request = 1'b1;
    wait (l_decomposed_acknowledge === 1'b1);
    l_decomposed_request = 1'b0;
    wait (l_decomposed_acknowledge === 1'b0);
  endtask

  task automatic drive_same_input(input logic [7:0] value);
    fork
      send_original(value);
      send_decomposed(value);
    join
  endtask

  task automatic receive_original(output logic [7:0] value);
    wait (r_original_request === 1'b1);
    value = r_original_payload;
    r_original_acknowledge = 1'b1;
    wait (r_original_request === 1'b0);
    r_original_acknowledge = 1'b0;
  endtask

  task automatic receive_decomposed(output logic [7:0] value);
    wait (r_decomposed_request === 1'b1);
    value = r_decomposed_payload;
    r_decomposed_acknowledge = 1'b1;
    wait (r_decomposed_request === 1'b0);
    r_decomposed_acknowledge = 1'b0;
  endtask

  task automatic receive_same_output(input logic [7:0] expected);
    logic [7:0] original_value;
    logic [7:0] decomposed_value;
    fork
      receive_original(original_value);
      receive_decomposed(decomposed_value);
    join
    if (original_value !== expected)
      $fatal(1, "original output %0d, expected %0d", original_value, expected);
    if (decomposed_value !== expected)
      $fatal(1, "decomposed output %0d, expected %0d", decomposed_value, expected);
    original_trace[original_count] = original_value;
    decomposed_trace[decomposed_count] = decomposed_value;
    original_count = original_count + 1;
    decomposed_count = decomposed_count + 1;
  endtask

  task automatic require_no_output_after_disabled_iteration;
    #1;
    if (r_original_request !== 1'b0)
      $fatal(1, "original emitted an output for a disabled iteration");
    if (r_decomposed_request !== 1'b0)
      $fatal(1, "decomposed emitted an output for a disabled iteration");
  endtask

  initial begin
    // The data[0] enable pattern is 1, 0, 1, 0.
    drive_same_input(8'd1);
    receive_same_output(8'd1);
    drive_same_input(8'd2);
    require_no_output_after_disabled_iteration();
    drive_same_input(8'd3);
    receive_same_output(8'd3);
    drive_same_input(8'd4);
    require_no_output_after_disabled_iteration();

    if (original_count != 2 || decomposed_count != 2)
      $fatal(1, "incorrect output-token count");
    if (original_trace[0] !== decomposed_trace[0] ||
        original_trace[1] !== decomposed_trace[1])
      $fatal(1, "external output traces differ");
    $display("PASS conditional Send equivalence: original=%0d,%0d decomposed=%0d,%0d",
             original_trace[0], original_trace[1],
             decomposed_trace[0], decomposed_trace[1]);
    $finish;
  end

  initial begin
    #100;
    $fatal(1, "conditional Send equivalence test timed out");
  end
endmodule
''')

    compile_result = subprocess.run(
        [IVERILOG, '-g2012', '-s', 'tb', '-o', str(executable),
         str(original_path), str(decomposed_path), str(testbench_path)],
        cwd=ROOT, text=True, capture_output=True, check=False,
    )
    assert compile_result.returncode == 0, compile_result.stderr
    run_result = subprocess.run(
        [VVP, str(executable)], cwd=ROOT, text=True, capture_output=True, check=False,
    )
    assert run_result.returncode == 0, run_result.stdout + run_result.stderr
    assert 'PASS conditional Send equivalence: original=1,3 decomposed=1,3' in run_result.stdout


def test_conditional_receive_decomposition_is_behaviorally_equivalent(tmp_path):
    """Compare real four-phase handshakes for enabled and disabled receives."""
    original = RECEIVE_FIXTURE.read_text()
    decomposed = emit_conditional_send_decomposition(normalized(original))
    original_path = tmp_path / 'original_flat.sv'
    decomposed_path = tmp_path / 'decomposed_flat.sv'
    testbench_path = tmp_path / 'tb.sv'
    executable = tmp_path / 'simulation'
    original_path.write_text(_flatten_channel_svcsp_for_icarus(original))
    decomposed_path.write_text(_flatten_channel_svcsp_for_icarus(decomposed))
    if not (IVERILOG and VVP):
        pytest.skip('Icarus Verilog is not available')
    testbench_path.write_text(r'''
`timescale 1ns/1ps
module tb;
  logic control_original_payload = 1'b0;
  logic control_original_request = 1'b0;
  wire control_original_acknowledge;
  logic [7:0] l_original_payload = '0;
  logic l_original_request = 1'b0;
  wire l_original_acknowledge;
  wire [7:0] r_original_payload;
  wire r_original_request;
  logic r_original_acknowledge = 1'b0;

  logic control_decomposed_payload = 1'b0;
  logic control_decomposed_request = 1'b0;
  wire control_decomposed_acknowledge;
  logic [7:0] l_decomposed_payload = '0;
  logic l_decomposed_request = 1'b0;
  wire l_decomposed_acknowledge;
  wire [7:0] r_decomposed_payload;
  wire r_decomposed_request;
  logic r_decomposed_acknowledge = 1'b0;

  logic [7:0] original_trace [0:3];
  logic [7:0] decomposed_trace [0:3];
  integer original_count = 0;
  integer decomposed_count = 0;
  integer original_l_transactions = 0;
  integer decomposed_l_transactions = 0;

  conditional_receive_decomposition original (
    .Control_payload(control_original_payload),
    .Control_request(control_original_request),
    .Control_acknowledge(control_original_acknowledge),
    .L_payload(l_original_payload),
    .L_request(l_original_request),
    .L_acknowledge(l_original_acknowledge),
    .R_payload(r_original_payload),
    .R_request(r_original_request),
    .R_acknowledge(r_original_acknowledge)
  );
  conditional_receive_decomposition_DECOMPOSED decomposed (
    .Control_payload(control_decomposed_payload),
    .Control_request(control_decomposed_request),
    .Control_acknowledge(control_decomposed_acknowledge),
    .L_payload(l_decomposed_payload),
    .L_request(l_decomposed_request),
    .L_acknowledge(l_decomposed_acknowledge),
    .R_payload(r_decomposed_payload),
    .R_request(r_decomposed_request),
    .R_acknowledge(r_decomposed_acknowledge)
  );

  task automatic send_control_original(input logic value);
    control_original_payload = value;
    control_original_request = 1'b1;
    wait (control_original_acknowledge === 1'b1);
    control_original_request = 1'b0;
    wait (control_original_acknowledge === 1'b0);
  endtask

  task automatic send_control_decomposed(input logic value);
    control_decomposed_payload = value;
    control_decomposed_request = 1'b1;
    wait (control_decomposed_acknowledge === 1'b1);
    control_decomposed_request = 1'b0;
    wait (control_decomposed_acknowledge === 1'b0);
  endtask

  task automatic drive_enable(input logic value);
    fork
      send_control_original(value);
      send_control_decomposed(value);
    join
  endtask

  task automatic send_l_original(input logic [7:0] value);
    l_original_payload = value;
    l_original_request = 1'b1;
    wait (l_original_acknowledge === 1'b1);
    l_original_request = 1'b0;
    wait (l_original_acknowledge === 1'b0);
    original_l_transactions = original_l_transactions + 1;
  endtask

  task automatic send_l_decomposed(input logic [7:0] value);
    l_decomposed_payload = value;
    l_decomposed_request = 1'b1;
    wait (l_decomposed_acknowledge === 1'b1);
    l_decomposed_request = 1'b0;
    wait (l_decomposed_acknowledge === 1'b0);
    decomposed_l_transactions = decomposed_l_transactions + 1;
  endtask

  task automatic drive_enabled_input(input logic [7:0] value);
    fork
      send_l_original(value);
      send_l_decomposed(value);
    join
  endtask

  task automatic receive_original(output logic [7:0] value);
    wait (r_original_request === 1'b1);
    value = r_original_payload;
    r_original_acknowledge = 1'b1;
    wait (r_original_request === 1'b0);
    r_original_acknowledge = 1'b0;
  endtask

  task automatic receive_decomposed(output logic [7:0] value);
    wait (r_decomposed_request === 1'b1);
    value = r_decomposed_payload;
    r_decomposed_acknowledge = 1'b1;
    wait (r_decomposed_request === 1'b0);
    r_decomposed_acknowledge = 1'b0;
  endtask

  task automatic receive_same_output(input logic [7:0] expected);
    logic [7:0] original_value;
    logic [7:0] decomposed_value;
    fork
      receive_original(original_value);
      receive_decomposed(decomposed_value);
    join
    if (original_value !== expected || decomposed_value !== expected)
      $fatal(1, "unexpected output original=%0d decomposed=%0d expected=%0d",
             original_value, decomposed_value, expected);
    original_trace[original_count] = original_value;
    decomposed_trace[decomposed_count] = decomposed_value;
    original_count = original_count + 1;
    decomposed_count = decomposed_count + 1;
  endtask

  task automatic require_disabled_l_is_unacknowledged;
    #1;
    if (l_original_request !== 1'b0 || l_original_acknowledge !== 1'b0)
      $fatal(1, "original consumed or acknowledged disabled L");
    if (l_decomposed_request !== 1'b0 || l_decomposed_acknowledge !== 1'b0)
      $fatal(1, "decomposed consumed or acknowledged disabled L");
  endtask

  initial begin
    // Enable pattern 1, 0, 1, 0.  L tokens are driven only for enabled iterations.
    drive_enable(1'b1);
    drive_enabled_input(8'd11);
    receive_same_output(8'd11);

    drive_enable(1'b0);
    require_disabled_l_is_unacknowledged();
    receive_same_output(8'd0);

    drive_enable(1'b1);
    drive_enabled_input(8'd33);
    receive_same_output(8'd33);

    drive_enable(1'b0);
    require_disabled_l_is_unacknowledged();
    receive_same_output(8'd0);

    if (original_l_transactions != 2 || decomposed_l_transactions != 2)
      $fatal(1, "enabled iterations did not consume exactly one L token each");
    if (original_count != 4 || decomposed_count != 4)
      $fatal(1, "BODY did not progress through every iteration");
    if (original_trace[0] !== decomposed_trace[0] ||
        original_trace[1] !== decomposed_trace[1] ||
        original_trace[2] !== decomposed_trace[2] ||
        original_trace[3] !== decomposed_trace[3])
      $fatal(1, "external output traces differ");
    $display("PASS conditional Receive equivalence: original=%0d,%0d,%0d,%0d decomposed=%0d,%0d,%0d,%0d disabled_L_ack=0",
             original_trace[0], original_trace[1], original_trace[2], original_trace[3],
             decomposed_trace[0], decomposed_trace[1], decomposed_trace[2], decomposed_trace[3]);
    $finish;
  end

  initial begin
    #100;
    $fatal(1, "conditional Receive equivalence test timed out");
  end
endmodule
''')

    compile_result = subprocess.run(
        [IVERILOG, '-g2012', '-s', 'tb', '-o', str(executable),
         str(original_path), str(decomposed_path), str(testbench_path)],
        cwd=ROOT, text=True, capture_output=True, check=False,
    )
    assert compile_result.returncode == 0, compile_result.stderr
    run_result = subprocess.run(
        [VVP, str(executable)], cwd=ROOT, text=True, capture_output=True, check=False,
    )
    assert run_result.returncode == 0, run_result.stdout + run_result.stderr
    assert ('PASS conditional Receive equivalence: original=11,0,33,0 '
            'decomposed=11,0,33,0 disabled_L_ack=0') in run_result.stdout
