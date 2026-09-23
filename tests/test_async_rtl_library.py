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


def test_four_phase_input_join_declares_the_vectorized_stage_contract() -> None:
    source = _controller_source("four_phase_input_join")

    assert "module four_phase_input_join" in source
    assert "parameter" in source and "N" in source
    for formal in ("input_req", "input_ack", "stage_release", "stage_active"):
        assert formal in source


def test_four_phase_output_fork_declares_the_vectorized_branch_contract() -> None:
    source = _controller_source("four_phase_output_fork")

    assert "module four_phase_output_fork" in source
    assert "parameter" in source and "M" in source
    assert "stage_active" in source
    assert "launch" in source


def test_four_phase_output_completion_declares_the_vectorized_release_contract() -> None:
    source = _controller_source("four_phase_output_completion")

    assert "module four_phase_output_completion" in source
    assert "parameter" in source and "M" in source
    assert "complete" in source
    assert "stage_release" in source


def test_bundled_data_storage_declares_the_current_m7a_capture_release_contract() -> None:
    source = _library_source("storage", "bundled_data_storage")

    assert "module bundled_data_storage" in source
    assert "parameter" in source and "WIDTH" in source
    for formal in ("data_in", "data_out", "capture", "stage_release"):
        assert formal in source


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


def test_en_receive_stage_declares_the_width_parameterized_three_channel_contract() -> None:
    source = _controller_source("en_receive_stage")

    assert "module en_receive_stage" in source
    assert "parameter" in source and "WIDTH" in source
    for formal in (
        "enable_req", "enable_ack", "enable_data",
        "external_req", "external_ack", "external_data",
        "body_req", "body_ack", "body_data",
    ):
        assert formal in source


def test_en_send_stage_declares_the_width_parameterized_three_channel_contract() -> None:
    source = _controller_source("en_send_stage")

    assert "module en_send_stage" in source
    assert "parameter" in source and "WIDTH" in source
    for formal in (
        "enable_req", "enable_ack", "enable_data",
        "body_req", "body_ack", "body_data",
        "external_req", "external_ack", "external_data",
    ):
        assert formal in source


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


@pytest.mark.parametrize("inputs", (1, 2))
def test_four_phase_input_join_waits_for_all_requests_and_releases_only_after_reset(
    tmp_path: Path, inputs: int,
) -> None:
    active_after_first_reset = "1'b0" if inputs == 1 else "1'b1"
    ack_after_first_reset = "'0" if inputs == 1 else f"{{{inputs}{{1'b1}}}}"
    _simulate(tmp_path, f"input_join_{inputs}", f"""
module tb;
  reg [{inputs - 1}:0] input_req = '0;
  reg stage_release = 0;
  wire [{inputs - 1}:0] input_ack;
  wire stage_active;
  four_phase_input_join #(.N({inputs})) dut (
    .input_req(input_req), .input_ack(input_ack),
    .stage_release(stage_release), .stage_active(stage_active)
  );
  initial begin
    #1; if (stage_active !== 1'b0 || input_ack !== '0) $fatal(1, "initial idle");
    input_req[{inputs - 1}] = 1'b1;
    #1; if (stage_active !== {"1'b1" if inputs == 1 else "1'b0"}) $fatal(1, "arrival must not be ordered");
    input_req[0] = 1'b1;
    #1; if (stage_active !== 1'b1 || input_ack !== {{{inputs}{{1'b1}}}}) $fatal(1, "all inputs activate together");
    stage_release = 1'b1;
    input_req[{inputs - 1}] = 1'b0;
    #1; if (stage_active !== {active_after_first_reset} || input_ack !== {ack_after_first_reset}) $fatal(1, "mixed reset handling");
    input_req[0] = 1'b0;
    #1; if (stage_active !== 1'b0 || input_ack !== '0) $fatal(1, "release after all reset");
    $finish;
  end
endmodule
""")


@pytest.mark.parametrize("outputs", (1, 2))
def test_four_phase_output_fork_launches_all_independent_branches(tmp_path: Path, outputs: int) -> None:
    _simulate(tmp_path, f"output_fork_{outputs}", f"""
module tb;
  reg stage_active = 0;
  wire [{outputs - 1}:0] launch;
  four_phase_output_fork #(.M({outputs})) dut (
    .stage_active(stage_active), .launch(launch)
  );
  initial begin
    #1; if (launch !== '0) $fatal(1, "idle launch");
    stage_active = 1'b1;
    #1; if (launch !== {{{outputs}{{1'b1}}}}) $fatal(1, "all branches launch independently");
    stage_active = 1'b0;
    #1; if (launch !== '0) $fatal(1, "launch resets with stage");
    $finish;
  end
endmodule
""")


@pytest.mark.parametrize("outputs", (1, 2))
def test_four_phase_output_completion_holds_release_until_all_branches_reset(
    tmp_path: Path, outputs: int,
) -> None:
    release_after_first_reset = "1'b0" if outputs == 1 else "1'b1"
    _simulate(tmp_path, f"output_completion_{outputs}", f"""
module tb;
  reg [{outputs - 1}:0] complete = '0;
  wire stage_release;
  four_phase_output_completion #(.M({outputs})) dut (
    .complete(complete), .stage_release(stage_release)
  );
  initial begin
    #1; if (stage_release !== 1'b0) $fatal(1, "initial release");
    complete[{outputs - 1}] = 1'b1;
    #1; if (stage_release !== {"1'b1" if outputs == 1 else "1'b0"}) $fatal(1, "wait for all completions");
    complete[0] = 1'b1;
    #1; if (stage_release !== 1'b1) $fatal(1, "all completed");
    complete[{outputs - 1}] = 1'b0;
    #1; if (stage_release !== {release_after_first_reset}) $fatal(1, "release reset handling");
    complete[0] = 1'b0;
    #1; if (stage_release !== 1'b0) $fatal(1, "release clears after all reset");
    $finish;
  end
endmodule
""")


@pytest.mark.parametrize("width, first, second", (
    (1, "1'b1", "1'b0"),
    (8, "8'ha5", "8'h3c"),
))
def test_bundled_data_storage_transparency_and_release_retention(
    tmp_path: Path, width: int, first: str, second: str,
) -> None:
    _simulate(tmp_path, f"bundled_storage_{width}", f"""
module tb;
  reg [{width - 1}:0] data_in = '0;
  reg capture = 0;
  reg stage_release = 0;
  wire [{width - 1}:0] data_out;
  bundled_data_storage #(.WIDTH({width})) dut (
    .data_in(data_in), .data_out(data_out), .capture(capture), .stage_release(stage_release)
  );
  initial begin
    capture = 1'b1; data_in = {first};
    #1; if (data_out !== {first}) $fatal(1, "capture is transparent");
    stage_release = 1'b1; data_in = {second};
    #1; if (data_out !== {first}) $fatal(1, "release retains data");
    stage_release = 1'b0; capture = 1'b0; data_in = {second};
    #1; if (data_out !== {first}) $fatal(1, "closed storage remains retained");
    capture = 1'b1;
    #1; if (data_out !== {second}) $fatal(1, "capture reopens after release");
    $finish;
  end
endmodule
""")


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
def test_en_receive_stage_handles_enabled_and_disabled_body_tokens(
    tmp_path: Path, width: int, first: str, second: str,
) -> None:
    _simulate(tmp_path, f"en_receive_{width}", f"""
module tb;
  reg enable_req = 0, enable_data = 0;
  reg external_req = 0, body_ack = 0;
  reg [{width - 1}:0] external_data = '0;
  wire enable_ack, external_ack, body_req;
  wire [{width - 1}:0] body_data;
  en_receive_stage #(.WIDTH({width})) dut (
    .enable_req(enable_req), .enable_ack(enable_ack), .enable_data(enable_data),
    .external_req(external_req), .external_ack(external_ack), .external_data(external_data),
    .body_req(body_req), .body_ack(body_ack), .body_data(body_data)
  );
  initial begin
    #1; if ({{enable_ack, external_ack, body_req}} !== 3'b000) $fatal(1, "initial idle");
    enable_data = 1'b1; enable_req = 1'b1;
    #1; if (enable_ack !== 1'b1 || external_ack !== 1'b0 || body_req !== 1'b0) $fatal(1, "enable not consumed first");
    enable_req = 1'b0;
    #1; if (enable_ack !== 1'b0) $fatal(1, "enable reset");
    external_data = {first}; external_req = 1'b1;
    #1; if (external_ack !== 1'b1 || body_req !== 1'b1 || body_data !== {first}) $fatal(1, "enabled receive missing");
    external_data = {second};
    #1; if (body_data !== {first}) $fatal(1, "body payload was not retained");
    external_req = 1'b0;
    #1; if (external_ack !== 1'b0) $fatal(1, "external reset");
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
def test_en_send_stage_consumes_body_before_conditional_external_send(
    tmp_path: Path, width: int, first: str, second: str,
) -> None:
    _simulate(tmp_path, f"en_send_{width}", f"""
module tb;
  reg enable_req = 0, enable_data = 0;
  reg body_req = 0, external_ack = 0;
  reg [{width - 1}:0] body_data = '0;
  wire enable_ack, body_ack, external_req;
  wire [{width - 1}:0] external_data;
  en_send_stage #(.WIDTH({width})) dut (
    .enable_req(enable_req), .enable_ack(enable_ack), .enable_data(enable_data),
    .body_req(body_req), .body_ack(body_ack), .body_data(body_data),
    .external_req(external_req), .external_ack(external_ack), .external_data(external_data)
  );
  initial begin
    body_data = {first}; body_req = 1'b1;
    #1; if (body_ack !== 1'b0 || external_req !== 1'b0) $fatal(1, "body accepted before enable");
    enable_data = 1'b1; enable_req = 1'b1;
    #1; if (enable_ack !== 1'b1 || body_ack !== 1'b1 || external_req !== 1'b1 || external_data !== {first}) $fatal(1, "enabled send missing");
    body_data = {second};
    #1; if (external_data !== {first}) $fatal(1, "external payload was not retained");
    body_req = 1'b0;
    #1; if (body_ack !== 1'b0) $fatal(1, "body acknowledgement reset");
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
