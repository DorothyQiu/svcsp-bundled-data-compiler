from pathlib import Path
import re
import shutil
import subprocess

import pytest

from svcsp_compiler import LinearCompilationError, compile_linear_file


ROOT = Path(__file__).resolve().parents[1]
FIXTURE = ROOT / 'tests' / 'fixtures' / 'linear_receive_add_send.sv'
RTL_LIBRARY = tuple(sorted((ROOT / 'rtl_lib').glob('**/*.sv')))
IVERILOG = shutil.which('iverilog')
VVP = shutil.which('vvp')


def test_linear_fixture_compiles_through_the_complete_existing_pipeline(tmp_path):
    rtl = compile_linear_file(FIXTURE)
    generated = tmp_path / 'linear_receive_add_send.sv'
    generated.write_text(rtl)

    assert rtl.count('linear_controller ') == 1
    assert rtl.count('abstract_storage #(') == 1
    assert rtl.count('symbolic_matched_delay ') == 1
    assert '.WIDTH(8)' in rtl
    assert 'join_controller' not in rtl
    assert 'conditional_recv_wrapper' not in rtl
    assert 'conditional_send_wrapper' not in rtl

    if not (IVERILOG and VVP):
        pytest.skip('Icarus Verilog is not available')

    c_signal = re.search(r'logic \[7:0\] (var_c_[A-Za-z0-9_$]+);', rtl)
    assert c_signal, 'the fixture addend must remain an independently driven variable'
    testbench = tmp_path / 'tb.sv'
    testbench.write_text(f'''
`timescale 1ns/1ps
module tb;
  reg [7:0] a_payload = 8'd0;
  reg a_request = 1'b0;
  wire a_acknowledge;
  wire [7:0] b_payload;
  wire b_request;
  reg b_acknowledge = 1'b0;
  reg b_seen = 1'b0;

  linear_receive_add_send dut (
    .channel_A_receive_payload(a_payload),
    .channel_A_receive_request(a_request),
    .channel_A_receive_acknowledge(a_acknowledge),
    .channel_B_send_payload(b_payload),
    .channel_B_send_request(b_request),
    .channel_B_send_acknowledge(b_acknowledge)
  );

  // Sample at the request transition: bundled data must already be stable.
  always @(posedge b_request) begin
    if (b_payload !== 8'd8) $fatal(1, "B payload was not stable at request");
    b_seen = 1'b1;
  end

  initial begin
    dut.{c_signal.group(1)} = 8'd3;
    #5;

    // A input transaction: payload, request up / acknowledge up, request
    // down / acknowledge down.
    a_payload = 8'd5;
    a_request = 1'b1;
    @(posedge a_acknowledge);
    a_request = 1'b0;
    @(negedge a_acknowledge);

    // B output transaction: request carries 5 + 3, then completes the
    // reciprocal four-phase acknowledge cycle.
    wait (b_seen);
    b_acknowledge = 1'b1;
    @(negedge b_request);
    b_acknowledge = 1'b0;
    #1;
    $finish;
  end
endmodule
''')
    executable = tmp_path / 'simulation'
    compile_result = subprocess.run(
        [IVERILOG, '-g2012', '-s', 'tb', '-o', str(executable),
         *(str(source) for source in RTL_LIBRARY), str(generated), str(testbench)],
        cwd=ROOT, text=True, capture_output=True, check=False,
    )
    assert compile_result.returncode == 0, compile_result.stderr
    run_result = subprocess.run([VVP, str(executable)], cwd=ROOT, text=True, capture_output=True, check=False)
    assert run_result.returncode == 0, run_result.stdout + run_result.stderr


def test_linear_entry_point_rejects_parallel_source(tmp_path):
    source = tmp_path / 'parallel.sv'
    source.write_text('''module parallel(interface A, B); logic x, y; always fork
A.Receive(x); B.Receive(y); join endmodule''')
    with pytest.raises(LinearCompilationError, match='fork/join'):
        compile_linear_file(source)


@pytest.mark.parametrize('source_text', (
    '''module m(interface A, B, C); logic x, y; always begin
A.Receive(x); B.Receive(y); C.Send(x); end endmodule''',
    '''module m(interface A, B, C); logic x; always begin
A.Receive(x); B.Send(x); C.Send(x); end endmodule''',
    '''module m(interface A, B); logic c, x; always begin
A.Receive(x); if (c) B.Send(x); end endmodule''',
))
def test_linear_entry_point_rejects_non_mvp_boundary_topologies(tmp_path, source_text):
    source = tmp_path / 'unsupported.sv'
    source.write_text(source_text)
    with pytest.raises(LinearCompilationError):
        compile_linear_file(source)
