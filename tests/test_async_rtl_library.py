"""RTL-library contracts for the M7 async handshake controllers."""
from pathlib import Path
import shutil
import subprocess

import pytest


ROOT = Path(__file__).resolve().parents[1]
RTL_LIB = ROOT / "rtl_lib"
LIBRARY_SOURCES = tuple(sorted(RTL_LIB.glob("**/*.sv")))
IVERILOG = shutil.which("iverilog")
VVP = shutil.which("vvp")


def _controller_source(module: str) -> str:
    path = RTL_LIB / "controllers" / f"{module}.sv"
    assert path.is_file(), f"missing async RTL-library module: {path.relative_to(ROOT)}"
    return path.read_text()


def _library_source(directory: str, module: str) -> str:
    path = RTL_LIB / directory / f"{module}.sv"
    assert path.is_file(), f"missing async RTL-library module: {path.relative_to(ROOT)}"
    return path.read_text()


def test_bundled_data_matched_delay_has_only_control_formals() -> None:
    source = _library_source("delay", "bundled_data_matched_delay")

    assert "module bundled_data_matched_delay" in source
    assert "control_in" in source
    assert "control_out" in source
    assert "data_path" not in source


def test_ordinary_body_structural_library_components_are_declared() -> None:
    c_element = _controller_source("muller_c_element2")
    controller = _controller_source("four_phase_half_buffer_controller")
    request_join = _controller_source("four_phase_request_join")
    ack_fanout = _controller_source("four_phase_ack_fanout")
    request_fanout = _controller_source("four_phase_request_fanout")
    ack_join = _controller_source("four_phase_ack_join")
    latch_cell = _library_source("storage", "latch_cell")
    latch_bank = _library_source("storage", "bundled_data_latch_bank")

    assert "module muller_c_element2" in c_element
    assert "reset_n" in c_element
    assert "muller_c_element2 state_element" in controller
    assert "always" not in controller
    assert "reset_n" in controller
    for source, module in ((request_join, "four_phase_request_join"),
                           (ack_fanout, "four_phase_ack_fanout"),
                           (request_fanout, "four_phase_request_fanout"),
                           (ack_join, "four_phase_ack_join")):
        assert f"module {module}" in source
    assert "module latch_cell" in latch_cell
    assert "always" not in latch_cell
    assert "initial" not in latch_cell
    assert "module bundled_data_latch_bank" in latch_bank
    assert "latch_cell bit_latch" in latch_bank


def test_ordinary_body_controller_reset_four_phase_behavior_and_latch_bank(tmp_path: Path) -> None:
    _simulate(tmp_path, "ordinary_body_structural_components", """
module tb;
  reg reset_n = 0;
  reg [1:0] input_req = 2'b00;
  reg [1:0] output_ack = 2'b00;
  reg base_Lreq = 1, base_Rack = 0, base_Lack = 0, base_raw_Rreq = 0;
  reg [3:0] data_in = 4'b0000;
  reg storage_enable = 0;
  wire joined_Lreq, joined_Rack, controller_Lack, controller_raw_Rreq, controller_storage_enable;
  wire [1:0] input_ack, output_raw_Rreq;
  wire [3:0] data_out;

  four_phase_request_join #(.N(2)) request_join (
    .reset_n(reset_n), .input_req(input_req), .base_Lreq(joined_Lreq)
  );
  four_phase_ack_join #(.M(2)) ack_join (
    .reset_n(reset_n), .output_ack(output_ack), .base_Rack(joined_Rack)
  );
  four_phase_ack_fanout #(.N(2)) ack_fanout (
    .base_Lack(base_Lack), .input_ack(input_ack)
  );
  four_phase_request_fanout #(.M(2)) request_fanout (
    .base_raw_Rreq(base_raw_Rreq), .output_raw_Rreq(output_raw_Rreq)
  );
  four_phase_half_buffer_controller controller (
    .reset_n(reset_n), .base_Lreq(base_Lreq), .base_Rack(base_Rack),
    .base_Lack(controller_Lack), .base_raw_Rreq(controller_raw_Rreq),
    .storage_enable(controller_storage_enable)
  );
  bundled_data_latch_bank #(.WIDTH(4)) latch_bank (
    .data_in(data_in), .storage_enable(storage_enable), .data_out(data_out)
  );

  initial begin
    #1; if ({controller_Lack, controller_raw_Rreq, controller_storage_enable} !== 3'b000)
      $fatal(1, "controller reset");
    base_Lreq = 1'b0; reset_n = 1'b1;
    #1; if ({controller_Lack, controller_raw_Rreq, controller_storage_enable} !== 3'b000)
      $fatal(1, "released reset idle");
    #1; if (joined_Lreq !== 1'b0 || joined_Rack !== 1'b0) $fatal(1, "initial joins");
    input_req = 2'b01;
    #1; if (joined_Lreq !== 1'b0) $fatal(1, "request join changed on mixed input");
    input_req = 2'b11;
    #1; if (joined_Lreq !== 1'b1) $fatal(1, "request join missed all-high input");
    input_req = 2'b01;
    #1; if (joined_Lreq !== 1'b1) $fatal(1, "request join did not retain high");
    input_req = 2'b00;
    #1; if (joined_Lreq !== 1'b0) $fatal(1, "request join missed all-low input");
    output_ack = 2'b10;
    #1; if (joined_Rack !== 1'b0) $fatal(1, "ack join changed on mixed input");
    output_ack = 2'b11;
    #1; if (joined_Rack !== 1'b1) $fatal(1, "ack join missed all-high input");
    output_ack = 2'b10;
    #1; if (joined_Rack !== 1'b1) $fatal(1, "ack join did not retain high");
    output_ack = 2'b00;
    #1; if (joined_Rack !== 1'b0) $fatal(1, "ack join missed all-low input");
    base_Lack = 1'b1; base_raw_Rreq = 1'b1;
    #1; if (input_ack !== 2'b11 || output_raw_Rreq !== 2'b11) $fatal(1, "fanout wiring");
    base_Lreq = 1'b1;
    #1; if ({controller_Lack, controller_raw_Rreq, controller_storage_enable} !== 3'b111)
      $fatal(1, "controller launch");
    base_Rack = 1'b1;
    #1; if ({controller_Lack, controller_raw_Rreq, controller_storage_enable} !== 3'b111)
      $fatal(1, "controller hold");
    base_Lreq = 1'b0;
    #1; if ({controller_Lack, controller_raw_Rreq, controller_storage_enable} !== 3'b000)
      $fatal(1, "controller reset");
    storage_enable = 1'b1; data_in = 4'ha;
    #1; if (data_out !== 4'ha) $fatal(1, "latch bank capture");
    storage_enable = 1'b0; data_in = 4'h5;
    #1; if (data_out !== 4'ha) $fatal(1, "latch bank retention");
    $finish;
  end
endmodule
""")


def test_ordinary_body_three_way_request_and_ack_joins_are_monotonic_c_element_reductions(
    tmp_path: Path,
) -> None:
    _simulate(tmp_path, "ordinary_body_three_way_joins", """
module tb;
  reg reset_n = 0;
  reg [2:0] input_req = 3'b000;
  reg [2:0] output_ack = 3'b000;
  wire base_Lreq, base_Rack;

  four_phase_request_join #(.N(3)) request_join (
    .reset_n(reset_n), .input_req(input_req), .base_Lreq(base_Lreq)
  );
  four_phase_ack_join #(.M(3)) ack_join (
    .reset_n(reset_n), .output_ack(output_ack), .base_Rack(base_Rack)
  );

  initial begin
    #1; if ({base_Lreq, base_Rack} !== 2'b00) $fatal(1, "join reset");
    reset_n = 1'b1;
    input_req[2] = 1'b1;
    #1; if (base_Lreq !== 1'b0) $fatal(1, "request first arrival");
    input_req[0] = 1'b1;
    #1; if (base_Lreq !== 1'b0) $fatal(1, "request second arrival");
    input_req[1] = 1'b1;
    #1; if (base_Lreq !== 1'b1) $fatal(1, "request all high");
    input_req[0] = 1'b0;
    #1; if (base_Lreq !== 1'b1) $fatal(1, "request first return holds");
    input_req[2] = 1'b0;
    #1; if (base_Lreq !== 1'b1) $fatal(1, "request second return holds");
    input_req[1] = 1'b0;
    #1; if (base_Lreq !== 1'b0) $fatal(1, "request all low");
    output_ack[1] = 1'b1;
    #1; if (base_Rack !== 1'b0) $fatal(1, "ack first arrival");
    output_ack[2] = 1'b1;
    #1; if (base_Rack !== 1'b0) $fatal(1, "ack second arrival");
    output_ack[0] = 1'b1;
    #1; if (base_Rack !== 1'b1) $fatal(1, "ack all high");
    output_ack[2] = 1'b0;
    #1; if (base_Rack !== 1'b1) $fatal(1, "ack first return holds");
    output_ack[0] = 1'b0;
    #1; if (base_Rack !== 1'b1) $fatal(1, "ack second return holds");
    output_ack[1] = 1'b0;
    #1; if (base_Rack !== 1'b0) $fatal(1, "ack all low");
    $finish;
  end
endmodule
""")


def test_four_phase_enable_sender_declares_the_channel_sender_contract() -> None:
    source = _controller_source("four_phase_enable_sender")

    assert "module four_phase_enable_sender" in source
    for formal in ("value", "launch", "req", "ack", "data"):
        assert formal in source


def test_en_receive_controller_declares_control_storage_and_raw_request_contract() -> None:
    source = _controller_source("en_receive_controller")

    assert "module en_receive_controller" in source
    assert "parameter" in source and "WIDTH" in source
    for formal in (
        "enable_req", "enable_ack", "enable_data",
        "external_req", "external_ack", "external_data",
        "body_raw_req", "body_ack", "storage_data", "storage_enable",
    ):
        assert formal in source
    assert "output reg  [WIDTH-1:0] body_data" not in source


def test_en_send_controller_declares_control_storage_and_raw_request_contract() -> None:
    source = _controller_source("en_send_controller")

    assert "module en_send_controller" in source
    assert "parameter" in source and "WIDTH" in source
    for formal in (
        "enable_req", "enable_ack", "enable_data",
        "body_req", "body_ack", "body_data",
        "external_raw_req", "external_ack", "storage_data", "storage_enable",
    ):
        assert formal in source
    assert "output reg  [WIDTH-1:0] external_data" not in source


def _simulate(tmp_path: Path, name: str, testbench: str) -> None:
    if not (IVERILOG and VVP):
        pytest.skip("Icarus Verilog is not available")
    testbench_path = tmp_path / f"{name}.sv"
    executable = tmp_path / name
    testbench_path.write_text(testbench)
    compile_result = subprocess.run(
        [IVERILOG, "-g2012", "-s", "tb", "-o", str(executable),
         *(str(source) for source in LIBRARY_SOURCES), str(testbench_path)],
        cwd=ROOT, text=True, capture_output=True, check=False,
    )
    assert compile_result.returncode == 0, compile_result.stderr
    run_result = subprocess.run(
        [VVP, str(executable)], cwd=ROOT, text=True, capture_output=True, check=False,
    )
    assert run_result.returncode == 0, run_result.stdout + run_result.stderr


def test_bundled_data_matched_delay_delays_control_transitions_only(tmp_path: Path) -> None:
    _simulate(tmp_path, "bundled_data_matched_delay", """
`timescale 1ns/1ps
module tb;
  reg control_in = 0;
  wire control_out;
  bundled_data_matched_delay #(.DELAY(3)) dut (
    .control_in(control_in), .control_out(control_out)
  );
  initial begin
    #4; if (control_out !== 1'b0) $fatal(1, "initial low control");
    control_in = 1'b1;
    #2; if (control_out !== 1'b0) $fatal(1, "rising control arrived too early");
    #2; if (control_out !== 1'b1) $fatal(1, "rising control did not propagate");
    control_in = 1'b0;
    #2; if (control_out !== 1'b1) $fatal(1, "falling control arrived too early");
    #2; if (control_out !== 1'b0) $fatal(1, "falling control did not propagate");
    $finish;
  end
endmodule
""")


def test_four_phase_enable_sender_captures_one_stable_token_per_launch(tmp_path: Path) -> None:
    _simulate(tmp_path, "enable_sender", """
module tb;
  reg value = 0, launch = 0, ack = 0;
  wire req, data;
  four_phase_enable_sender dut (.value(value), .launch(launch), .req(req), .ack(ack), .data(data));
  initial begin
    #1; if ({req, data} !== 2'b00) $fatal(1, "initial idle");
    value = 1'b1; launch = 1'b1;
    #1; if ({req, data} !== 2'b11) $fatal(1, "launch captures first value");
    value = 1'b0;
    #1; if ({req, data} !== 2'b11) $fatal(1, "active payload changed");
    ack = 1'b1;
    #1; if (req !== 1'b1) $fatal(1, "request dropped before launch reset");
    launch = 1'b0;
    #1; if (req !== 1'b0) $fatal(1, "request did not reset after acknowledge");
    ack = 1'b0;
    #1; if (req !== 1'b0) $fatal(1, "request restarted before acknowledge reset");
    value = 1'b0; launch = 1'b1;
    #1; if ({req, data} !== 2'b10) $fatal(1, "second launch did not capture new value");
    ack = 1'b1; launch = 1'b0;
    #1; if (req !== 1'b0) $fatal(1, "second request did not reset");
    ack = 1'b0;
    #1; $finish;
  end
endmodule
""")


@pytest.mark.parametrize("width, first, second", (
    (1, "1'b1", "1'b0"),
    (8, "8'ha5", "8'h3c"),
))
def test_en_receive_controller_uses_structural_storage_and_delayed_body_request(
    tmp_path: Path, width: int, first: str, second: str,
) -> None:
    _simulate(tmp_path, f"en_receive_{width}", f"""
module tb;
  reg enable_req = 0, enable_data = 0;
  reg external_req = 0, body_ack = 0;
  reg [{width - 1}:0] external_data = '0;
  wire enable_ack, external_ack, body_raw_req, body_req, storage_enable;
  wire [{width - 1}:0] storage_data, body_data;
  en_receive_controller #(.WIDTH({width})) controller (
    .enable_req(enable_req), .enable_ack(enable_ack), .enable_data(enable_data),
    .external_req(external_req), .external_ack(external_ack), .external_data(external_data),
    .body_raw_req(body_raw_req), .body_ack(body_ack),
    .storage_data(storage_data), .storage_enable(storage_enable)
  );
  bundled_data_latch_bank #(.WIDTH({width})) storage (
    .data_in(storage_data), .storage_enable(storage_enable), .data_out(body_data)
  );
  bundled_data_matched_delay #(.DELAY(0)) delay (
    .control_in(body_raw_req), .control_out(body_req)
  );
  initial begin
    #1; if ({{enable_ack, external_ack, body_req}} !== 3'b000) $fatal(1, "initial idle");
    enable_data = 1'b1; enable_req = 1'b1;
    #1; if (enable_ack !== 1'b1 || external_ack !== 1'b0 || body_req !== 1'b0) $fatal(1, "enable not consumed first");
    enable_req = 1'b0;
    #1; if (enable_ack !== 1'b0) $fatal(1, "enable reset");
    external_data = {first}; external_req = 1'b1;
    #1; if (external_ack !== 1'b1 || body_req !== 1'b1 || body_data !== {first}) $fatal(1, "enabled receive missing");
    external_req = 1'b0;
    #1; if (external_ack !== 1'b0) $fatal(1, "external reset");
    external_data = {second};
    #1; if (body_data !== {first}) $fatal(1, "structural BODY storage did not retain payload");
    body_ack = 1'b1;
    #1; if (body_req !== 1'b0) $fatal(1, "body request reset");
    body_ack = 1'b0;
    #1;
    enable_data = 1'b0; enable_req = 1'b1;
    #1; if (enable_ack !== 1'b1 || external_ack !== 1'b0 || body_req !== 1'b1 || body_data !== '0) $fatal(1, "disabled receive token");
    enable_req = 1'b0;
    #1; if (enable_ack !== 1'b0) $fatal(1, "disabled enable reset");
    body_ack = 1'b1;
    #1; if (body_req !== 1'b0 || external_ack !== 1'b0) $fatal(1, "disabled body completion");
    body_ack = 1'b0;
    #1; $finish;
  end
endmodule
""")


@pytest.mark.parametrize("width, first, second", (
    (1, "1'b1", "1'b0"),
    (8, "8'ha5", "8'h3c"),
))
def test_en_send_controller_uses_structural_storage_and_delayed_external_request(
    tmp_path: Path, width: int, first: str, second: str,
) -> None:
    _simulate(tmp_path, f"en_send_{width}", f"""
module tb;
  reg enable_req = 0, enable_data = 0;
  reg body_req = 0, external_ack = 0;
  reg [{width - 1}:0] body_data = '0;
  wire enable_ack, body_ack, external_raw_req, external_req, storage_enable;
  wire [{width - 1}:0] storage_data, external_data;
  en_send_controller #(.WIDTH({width})) controller (
    .enable_req(enable_req), .enable_ack(enable_ack), .enable_data(enable_data),
    .body_req(body_req), .body_ack(body_ack), .body_data(body_data),
    .external_raw_req(external_raw_req), .external_ack(external_ack),
    .storage_data(storage_data), .storage_enable(storage_enable)
  );
  bundled_data_latch_bank #(.WIDTH({width})) storage (
    .data_in(storage_data), .storage_enable(storage_enable), .data_out(external_data)
  );
  bundled_data_matched_delay #(.DELAY(0)) delay (
    .control_in(external_raw_req), .control_out(external_req)
  );
  initial begin
    body_data = {first}; body_req = 1'b1;
    #1; if (body_ack !== 1'b0 || external_req !== 1'b0) $fatal(1, "body accepted before enable");
    enable_data = 1'b1; enable_req = 1'b1;
    #1; if (enable_ack !== 1'b1 || body_ack !== 1'b1 || external_req !== 1'b1 || external_data !== {first}) $fatal(1, "enabled send missing");
    body_req = 1'b0;
    #1; if (body_ack !== 1'b0) $fatal(1, "body acknowledgement reset");
    body_data = {second};
    #1; if (external_data !== {first}) $fatal(1, "structural external storage did not retain payload");
    enable_req = 1'b0;
    #1; if (enable_ack !== 1'b0) $fatal(1, "enable acknowledgement reset");
    external_ack = 1'b1;
    #1; if (external_req !== 1'b0) $fatal(1, "external request reset");
    external_ack = 1'b0;
    #1;
    body_data = {second}; body_req = 1'b1;
    #1; if (body_ack !== 1'b0) $fatal(1, "second body accepted before disabled enable");
    enable_data = 1'b0; enable_req = 1'b1;
    #1; if (enable_ack !== 1'b1 || body_ack !== 1'b1 || external_req !== 1'b0) $fatal(1, "disabled send behavior");
    body_req = 1'b0;
    #1; if (body_ack !== 1'b0) $fatal(1, "disabled body acknowledgement reset");
    enable_req = 1'b0;
    #1; if (enable_ack !== 1'b0 || external_req !== 1'b0) $fatal(1, "disabled transaction reset");
    $finish;
  end
endmodule
""")
