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
