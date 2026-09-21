from pathlib import Path
import shutil
import subprocess

import pytest

from svcsp_compiler import (
    analyze_dependencies, bind_templates, emit_systemverilog, lower_behavioral,
    normalize_communication, parse_text, select_microarchitecture, synthesize_pipeline,
)


ROOT = Path(__file__).resolve().parents[1]
RTL_LIB = ROOT / 'rtl_lib'
LIBRARY_SOURCES = tuple(sorted(RTL_LIB.glob('**/*.sv')))
IVERILOG = shutil.which('iverilog')
VVP = shutil.which('vvp')


def test_mvp_library_exposes_only_the_linear_storage_delay_bindings():
    controller = (RTL_LIB / 'controllers' / 'four_phase_linear_controller.sv').read_text()
    storage = (RTL_LIB / 'storage' / 'transparent_latch.sv').read_text()
    delay = (RTL_LIB / 'delay' / 'matched_delay.sv').read_text()
    assert 'module four_phase_linear_controller' in controller
    assert 'module linear_controller' in controller
    assert 'module transparent_latch' in storage
    assert 'module abstract_storage' in storage
    assert 'module matched_delay' in delay
    assert 'module symbolic_matched_delay' in delay
    assert 'four_phase_linear_controller controller' in controller
    assert 'transparent_latch #(.WIDTH(WIDTH)) storage' in storage
    assert 'matched_delay #(.DELAY(DELAY)) delay_element' in delay
    assert 'join_controller' not in controller
    assert 'conditional_send_wrapper' not in controller


def generated_linear_rtl():
    source = '''module m(interface C); logic x, y; always
C.Send(x + y); endmodule'''
    behavioral = lower_behavioral(parse_text(source, 'library_generated.sv'))
    graph = bind_templates(select_microarchitecture(
        synthesize_pipeline(analyze_dependencies(normalize_communication(behavioral)))
    ))
    return emit_systemverilog(graph)


def generated_multi_bit_linear_rtl():
    source = '''module m(Channel #(8) C); logic [7:0] x, y; always
C.Send(x + y); endmodule'''
    behavioral = lower_behavioral(parse_text(source, 'library_generated_multi_bit.sv'))
    graph = bind_templates(select_microarchitecture(
        synthesize_pipeline(analyze_dependencies(normalize_communication(behavioral)))
    ))
    return emit_systemverilog(graph)


def test_generated_linear_rtl_uses_the_three_mvp_template_bindings():
    rtl = generated_linear_rtl()
    assert 'linear_controller ' in rtl
    assert 'abstract_storage ' in rtl
    assert 'symbolic_matched_delay ' in rtl


def test_linear_adapter_routes_raw_request_only_through_delay_return():
    controller = (RTL_LIB / 'controllers' / 'four_phase_linear_controller.sv').read_text()
    assert '.raw_rreq(raw_rreq)' in controller
    assert 'assign local_control = raw_rreq;' in controller
    assert 'assign downstream_req_0 = delayed_control;' in controller
    assert '.raw_rreq(downstream_req_0)' not in controller


def _simulate(tmp_path, name, testbench, extra_sources=()):
    if not (IVERILOG and VVP):
        pytest.skip('Icarus Verilog is not available')
    testbench_path = tmp_path / f'{name}.sv'
    executable = tmp_path / name
    testbench_path.write_text(testbench)
    result = subprocess.run(
        [IVERILOG, '-g2012', '-s', 'tb', '-o', str(executable),
         *(str(source) for source in LIBRARY_SOURCES), *(str(source) for source in extra_sources), str(testbench_path)],
        cwd=ROOT, text=True, capture_output=True, check=False,
    )
    assert result.returncode == 0, result.stderr
    result = subprocess.run([VVP, str(executable)], cwd=ROOT, text=True, capture_output=True, check=False)
    assert result.returncode == 0, result.stdout + result.stderr


def test_four_phase_controller_c_element_behavior(tmp_path):
    _simulate(tmp_path, 'controller', '''
module tb;
  reg lreq = 0, rack = 0;
  wire lack, latch_en, raw_rreq;
  four_phase_linear_controller dut (.lreq(lreq), .rack(rack), .lack(lack), .latch_en(latch_en), .raw_rreq(raw_rreq));
  initial begin
    #1; if ({lack, latch_en, raw_rreq} !== 3'b000) $fatal(1, "initial state");
    lreq = 1; rack = 0; #1; if ({lack, latch_en, raw_rreq} !== 3'b111) $fatal(1, "set state");
    rack = 1; #1; if ({lack, latch_en, raw_rreq} !== 3'b111) $fatal(1, "hold high");
    lreq = 0; #1; if ({lack, latch_en, raw_rreq} !== 3'b000) $fatal(1, "clear state");
    rack = 0; #1; if ({lack, latch_en, raw_rreq} !== 3'b000) $fatal(1, "hold low");
    $finish;
  end
endmodule
''')


def test_transparent_latch_transparency_and_retention(tmp_path):
    _simulate(tmp_path, 'latch', '''
module tb;
  reg [3:0] data_in = 4'h0;
  reg en = 0;
  wire [3:0] data_out;
  transparent_latch #(.WIDTH(4)) dut (.data_in(data_in), .en(en), .data_out(data_out));
  initial begin
    en = 1; data_in = 4'ha; #1; if (data_out !== 4'ha) $fatal(1, "transparent");
    data_in = 4'h3; #1; if (data_out !== 4'h3) $fatal(1, "tracks input");
    en = 0; data_in = 4'hf; #1; if (data_out !== 4'h3) $fatal(1, "holds data");
    $finish;
  end
endmodule
''')


def test_matched_delay_preserves_transitions_after_configured_delay(tmp_path):
    _simulate(tmp_path, 'delay', '''
`timescale 1ns/1ps
module tb;
  reg in = 0;
  wire out;
  matched_delay #(.DELAY(3)) dut (.in(in), .out(out));
  initial begin
    // Let the initial low input traverse the delayed continuous assignment.
    #4 if (out !== 1'b0) $fatal(1, "initial low value did not settle");
    in = 1;
    #2 if (out !== 1'b0) $fatal(1, "rising transition arrived too early");
    #2 if (out !== 1'b1) $fatal(1, "rising transition missing");
    in = 0;
    #2 if (out !== 1'b1) $fatal(1, "falling transition arrived too early");
    #2 if (out !== 1'b0) $fatal(1, "falling transition missing");
    $finish;
  end
endmodule
''')


def test_linear_request_is_delayed_and_data_is_stable_before_it(tmp_path):
    _simulate(tmp_path, 'linear_delayed_request', '''
`timescale 1ns/1ps
module tb;
  reg lreq = 0, rack = 0;
  reg [7:0] data_in = 8'h00;
  wire lack, raw_request, delayed_request, downstream_request, latch_en;
  wire [7:0] data_out;
  linear_controller control (
    .upstream_req_0(lreq), .upstream_ack_0(lack),
    .downstream_req_0(downstream_request), .downstream_ack_0(rack),
    .local_control(raw_request), .storage_control(1'b0),
    .delayed_control(delayed_request)
  );
  symbolic_matched_delay #(.DELAY(3)) delay (
    .control_in(raw_request), .control_out(delayed_request)
  );
  transparent_latch #(.WIDTH(8)) storage (
    .data_in(data_in), .en(raw_request), .data_out(data_out)
  );
  initial begin
    // Let the delay model establish the initial low request first.
    #4;
    if (downstream_request !== 1'b0) $fatal(1, "initial delayed request");
    data_in = 8'ha5;
    lreq = 1;
    #2;
    if (raw_request !== 1'b1) $fatal(1, "raw request missing");
    if (downstream_request !== 1'b0) $fatal(1, "raw request bypassed delay");
    if (data_out !== 8'ha5) $fatal(1, "data not stable before request");
    #2;
    if (downstream_request !== 1'b1) $fatal(1, "delayed rising request missing");
    lreq = 0; rack = 1;
    #2;
    if (raw_request !== 1'b0) $fatal(1, "raw falling request missing");
    if (downstream_request !== 1'b1) $fatal(1, "falling request bypassed delay");
    #2;
    if (downstream_request !== 1'b0) $fatal(1, "delayed falling request missing");
    $finish;
  end
endmodule
''')


def test_generated_linear_design_compiles_with_mvp_library(tmp_path):
    generated = tmp_path / 'generated_linear.sv'
    generated.write_text(generated_linear_rtl())
    _simulate(tmp_path, 'generated', 'module tb; m dut (); endmodule\n', (generated,))


def test_generated_multi_bit_linear_design_compiles_with_sized_storage(tmp_path):
    generated = tmp_path / 'generated_multi_bit_linear.sv'
    generated.write_text(generated_multi_bit_linear_rtl())
    assert '.WIDTH(8)' in generated.read_text()
    _simulate(tmp_path, 'generated_multi_bit', 'module tb; m dut (); endmodule\n', (generated,))
