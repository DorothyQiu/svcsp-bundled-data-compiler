"""Opt-in VCD generation for representative public source examples.

Run this file explicitly with ``pytest -q tests/test_example_waveforms.py -s``
to refresh the waveforms under ``examples/waveforms``.
"""
from pathlib import Path
import os
import shutil
import subprocess
import sys

import pytest

from svcsp_compiler import compile_async_file


ROOT = Path(__file__).resolve().parents[1]
EXAMPLES = ROOT / "examples"
WAVEFORMS = EXAMPLES / "waveforms"
RTL_LIBRARY = tuple(sorted((ROOT / "rtl_lib").glob("**/*.sv")))
IVERILOG = shutil.which("iverilog")
VVP = shutil.which("vvp")

_EXPLICIT = any("test_example_waveforms.py" in argument for argument in sys.argv)
pytestmark = pytest.mark.skipif(
    not (_EXPLICIT or os.environ.get("SVCSP_GENERATE_WAVEFORMS") == "1"),
    reason="waveform generation is opt-in; run this test file explicitly",
)


def _simulate_example(tmp_path: Path, stem: str, testbench: str) -> Path:
    if not (IVERILOG and VVP):
        pytest.skip("Icarus Verilog (iverilog and vvp) is not available")

    WAVEFORMS.mkdir(exist_ok=True)
    waveform = WAVEFORMS / f"{stem}.vcd"
    waveform.unlink(missing_ok=True)
    generated = tmp_path / f"{stem}_async.sv"
    testbench_path = tmp_path / f"{stem}_tb.sv"
    executable = tmp_path / stem
    generated.write_text(compile_async_file(EXAMPLES / f"{stem}.sv"))
    testbench_path.write_text(testbench.replace("__VCD__", str(waveform)))

    compile_result = subprocess.run(
        [IVERILOG, "-g2012", "-s", "tb", "-o", str(executable),
         *(str(source) for source in RTL_LIBRARY), str(generated), str(testbench_path)],
        cwd=ROOT, text=True, capture_output=True, check=False,
    )
    assert compile_result.returncode == 0, compile_result.stderr
    run_result = subprocess.run(
        [VVP, str(executable)], cwd=ROOT, text=True, capture_output=True,
        timeout=10, check=False,
    )
    assert run_result.returncode == 0, run_result.stdout + run_result.stderr
    assert waveform.is_file() and waveform.stat().st_size > 0
    return waveform


def test_receive_invert_send_generates_two_transaction_waveform(tmp_path: Path) -> None:
    waveform = _simulate_example(tmp_path, "receive_invert_send", r'''
module tb;
  reg reset_n = 0, channel_A_receive_request = 0, channel_B_send_acknowledge = 0;
  reg [7:0] channel_A_receive_payload = 0;
  wire channel_A_receive_acknowledge, channel_B_send_request;
  wire [7:0] channel_B_send_payload;
  receive_invert_send dut (.*);

  initial begin
    $dumpfile("__VCD__");
    $dumpvars(0, tb);
    $dumpvars(1, dut);
  end
  initial begin #1; reset_n = 1'b1; end
  task transaction(input [7:0] value, input [7:0] expected);
    begin
      channel_A_receive_payload = value;
      channel_A_receive_request = 1'b1;
      wait (channel_A_receive_acknowledge === 1'b1);
      channel_A_receive_request = 1'b0;
      wait (channel_B_send_request === 1'b1);
      if (channel_B_send_payload !== expected) $fatal(1, "inverted payload");
      channel_B_send_acknowledge = 1'b1;
      wait (channel_B_send_request === 1'b0);
      channel_B_send_acknowledge = 1'b0;
      wait (channel_A_receive_acknowledge === 1'b0);
    end
  endtask
  initial begin #200; $fatal(1, "receive_invert_send deadlock"); end
  initial begin
    transaction(8'h3c, 8'hc3);
    transaction(8'ha5, 8'h5a);
    #2; $finish;
  end
endmodule
''')
    assert waveform == WAVEFORMS / "receive_invert_send.vcd"


def test_conditional_send_generates_enabled_and_disabled_waveform(tmp_path: Path) -> None:
    waveform = _simulate_example(tmp_path, "conditional_send", r'''
module tb;
  reg reset_n = 0, channel_L_receive_request = 0, channel_R_send_acknowledge = 0;
  reg [7:0] channel_L_receive_payload = 0;
  wire channel_L_receive_acknowledge, channel_R_send_request;
  wire [7:0] channel_R_send_payload;
  conditional_send dut (.*);

  initial begin
    $dumpfile("__VCD__");
    $dumpvars(0, tb);
    $dumpvars(1, dut);
  end
  initial begin #1; reset_n = 1'b1; end
  task receive_l(input [7:0] value, input enabled);
    begin
      channel_L_receive_payload = value;
      channel_L_receive_request = 1'b1;
      wait (channel_L_receive_acknowledge === 1'b1);
      channel_L_receive_request = 1'b0;
      if (enabled) begin
        wait (channel_R_send_request === 1'b1);
        if (channel_R_send_payload !== value) $fatal(1, "conditional Send payload");
        channel_R_send_acknowledge = 1'b1;
        wait (channel_R_send_request === 1'b0);
        channel_R_send_acknowledge = 1'b0;
      end
      wait (channel_L_receive_acknowledge === 1'b0);
      if (!enabled) begin
        #4; if (channel_R_send_request !== 1'b0)
          $fatal(1, "disabled conditional Send requested R");
      end
    end
  endtask
  initial begin #250; $fatal(1, "conditional_send deadlock"); end
  initial begin
    receive_l(8'h35, 1'b1);
    receive_l(8'h34, 1'b0);
    #2; $finish;
  end
endmodule
''')
    assert waveform == WAVEFORMS / "conditional_send.vcd"
