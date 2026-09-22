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
    assert "release" in source


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
    #1; if (stage_active !== 1'b1 || input_ack !== {{{inputs}{{1'b1}}}}) $fatal(1, "mixed reset holds active");
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
    _simulate(tmp_path, f"output_completion_{outputs}", f"""
module tb;
  reg [{outputs - 1}:0] complete = '0;
  wire release;
  four_phase_output_completion #(.M({outputs})) dut (
    .complete(complete), .release(release)
  );
  initial begin
    #1; if (release !== 1'b0) $fatal(1, "initial release");
    complete[{outputs - 1}] = 1'b1;
    #1; if (release !== {"1'b1" if outputs == 1 else "1'b0"}) $fatal(1, "wait for all completions");
    complete[0] = 1'b1;
    #1; if (release !== 1'b1) $fatal(1, "all completed");
    complete[{outputs - 1}] = 1'b0;
    #1; if (release !== 1'b1) $fatal(1, "release holds during mixed reset");
    complete[0] = 1'b0;
    #1; if (release !== 1'b0) $fatal(1, "release clears after all reset");
    $finish;
  end
endmodule
""")
