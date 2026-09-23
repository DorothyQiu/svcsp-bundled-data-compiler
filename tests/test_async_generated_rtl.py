"""End-to-end M7C integration tests for generated async SystemVerilog."""
from pathlib import Path
import shutil
import subprocess

import pytest

from svcsp_compiler.async_microarchitecture import lower_microarchitecture
from svcsp_compiler.async_rtl_codegen import emit_async_systemverilog
from svcsp_compiler.async_template_binding import bind_async_templates
from svcsp_compiler.behavioral_ir import (
    BehavioralModule,
    ChannelEndpoint,
    Expression,
    If,
    ONE_BIT,
    PayloadType,
    PayloadWidth,
    Receive,
    Send,
    Sequence,
    Skip,
    SourceLocation,
    Variable,
)
from svcsp_compiler.communication_decomposition import decompose_transaction
from svcsp_compiler.semantic_analysis import analyze_semantics
from svcsp_compiler.transaction import extract_transaction


ROOT = Path(__file__).resolve().parents[1]
RTL_LIBRARY = tuple(sorted((ROOT / "rtl_lib").glob("**/*.sv")))
IVERILOG = shutil.which("iverilog")
VVP = shutil.which("vvp")

_BIT = PayloadType("logic", ONE_BIT)
_BYTE = PayloadType("logic", PayloadWidth(bits=8))


class _Program:
    def __init__(self, module_name: str) -> None:
        self.module_name = module_name
        self.variables: dict[str, Variable] = {}

    def variable(self, name: str, payload_type: PayloadType = _BIT) -> Variable:
        return self.variables.setdefault(
            name, Variable(name, (), SourceLocation("generated_rtl.sv", 1, 1), payload_type),
        )

    def name(self, name: str, payload_type: PayloadType = _BIT) -> Expression:
        return Expression("name", value=name, variable=self.variable(name, payload_type))

    def literal(self, value: str) -> Expression:
        return Expression("literal", value=value)

    def receive(self, channel: str, target: str, payload_type: PayloadType = _BIT) -> Receive:
        return Receive(ChannelEndpoint(channel, payload_type=payload_type), self.variable(target, payload_type))

    def send(self, channel: str, value: Expression, payload_type: PayloadType = _BIT) -> Send:
        return Send(ChannelEndpoint(channel, payload_type=payload_type), value)

    def emit(self, body) -> str:
        behavioral = BehavioralModule(self.module_name, body, (), tuple(self.variables.values()))
        transaction = extract_transaction(behavioral)
        decomposed = decompose_transaction(transaction)
        architecture = lower_microarchitecture(analyze_semantics(decomposed))
        return emit_async_systemverilog(bind_async_templates(architecture))


def _simulate_generated(tmp_path: Path, name: str, generated_rtl: str, testbench: str) -> None:
    if not (IVERILOG and VVP):
        pytest.skip("Icarus Verilog is not available")
    generated_path = tmp_path / f"{name}_generated.sv"
    testbench_path = tmp_path / f"{name}_tb.sv"
    executable = tmp_path / name
    generated_path.write_text(generated_rtl)
    testbench_path.write_text(testbench)
    compile_result = subprocess.run(
        [IVERILOG, "-g2012", "-s", "tb", "-o", str(executable),
         *(str(source) for source in RTL_LIBRARY), str(generated_path), str(testbench_path)],
        cwd=ROOT, text=True, capture_output=True, check=False,
    )
    assert compile_result.returncode == 0, compile_result.stderr
    run_result = subprocess.run(
        [VVP, str(executable)], cwd=ROOT, text=True, capture_output=True, timeout=10,
        check=False,
    )
    assert run_result.returncode == 0, run_result.stdout + run_result.stderr


def test_generated_constant_1r1s_completes_one_four_phase_transaction(tmp_path: Path) -> None:
    program = _Program("generated_constant_1r1s")
    rtl = program.emit(Sequence((
        program.receive("A", "a"),
        program.send("B", program.literal("1'b1")),
    )))

    _simulate_generated(tmp_path, "constant_1r1s", rtl, """
module tb;
  reg reset_n = 0;
  reg channel_A_receive_request = 0, channel_A_receive_payload = 0;
  reg channel_B_send_acknowledge = 0;
  wire channel_A_receive_acknowledge, channel_B_send_request, channel_B_send_payload;
  generated_constant_1r1s dut (.*);
  initial begin #1; reset_n = 1'b1; end
  initial begin
    #100; $fatal(1, "deadlock");
  end
  initial begin
    channel_A_receive_request = 1'b1;
    wait (channel_A_receive_acknowledge === 1'b1);
    channel_A_receive_request = 1'b0;
    wait (channel_A_receive_acknowledge === 1'b0);
  end
  initial begin
    wait (channel_B_send_request === 1'b1);
    if (channel_B_send_payload !== 1'b1) $fatal(1, "constant output");
    channel_B_send_acknowledge = 1'b1;
    wait (channel_B_send_request === 1'b0);
    channel_B_send_acknowledge = 1'b0;
    #2; if ({channel_A_receive_acknowledge, channel_B_send_request} !== 2'b00)
      $fatal(1, "transaction did not return idle");
    $finish;
  end
endmodule
""")


def test_generated_byte_passthrough_rearms_for_two_transactions(tmp_path: Path) -> None:
    program = _Program("generated_byte_passthrough")
    rtl = program.emit(Sequence((
        program.receive("A", "a", _BYTE),
        program.send("B", program.name("a", _BYTE), _BYTE),
    )))

    _simulate_generated(tmp_path, "byte_passthrough", rtl, """
module tb;
  reg reset_n = 0;
  reg channel_A_receive_request = 0;
  reg [7:0] channel_A_receive_payload = 0;
  reg channel_B_send_acknowledge = 0;
  wire channel_A_receive_acknowledge, channel_B_send_request;
  wire [7:0] channel_B_send_payload;
  generated_byte_passthrough dut (.*);
  initial begin #1; reset_n = 1'b1; end
  task receive_a(input [7:0] value);
    begin
      channel_A_receive_payload = value;
      channel_A_receive_request = 1'b1;
      wait (channel_A_receive_acknowledge === 1'b1);
      channel_A_receive_request = 1'b0;
      wait (channel_A_receive_acknowledge === 1'b0);
    end
  endtask
  task complete_b(input [7:0] expected);
    begin
      wait (channel_B_send_request === 1'b1);
      if (channel_B_send_payload !== expected) $fatal(1, "payload mismatch");
      channel_B_send_acknowledge = 1'b1;
      wait (channel_B_send_request === 1'b0);
      channel_B_send_acknowledge = 1'b0;
    end
  endtask
  initial begin
    #100; $fatal(1, "deadlock");
  end
  initial begin
    fork
      begin receive_a(8'ha5); receive_a(8'h3c); end
      begin complete_b(8'ha5); complete_b(8'h3c); #2; $finish; end
    join
  end
endmodule
""")


def test_generated_post_input_conditional_send_preserves_disabled_body_completion(tmp_path: Path) -> None:
    program = _Program("generated_conditional_send")
    value = program.name("a")
    rtl = program.emit(Sequence((
        program.receive("A", "a"),
        If(value, program.send("B", value), Skip()),
    )))

    _simulate_generated(tmp_path, "conditional_send", rtl, """
module tb;
  reg reset_n = 0;
  reg channel_A_receive_request = 0, channel_A_receive_payload = 0;
  reg channel_B_send_acknowledge = 0;
  wire channel_A_receive_acknowledge, channel_B_send_request, channel_B_send_payload;
  generated_conditional_send dut (.*);
  initial begin #1; reset_n = 1'b1; end
  task receive_a(input value);
    begin
      channel_A_receive_payload = value;
      channel_A_receive_request = 1'b1;
      wait (channel_A_receive_acknowledge === 1'b1);
      channel_A_receive_request = 1'b0;
      wait (channel_A_receive_acknowledge === 1'b0);
    end
  endtask
  initial begin
    #150; $fatal(1, "deadlock");
  end
  initial begin
    receive_a(1'b1);
    #2;
    receive_a(1'b0);
    #10; if (channel_B_send_request !== 1'b0) $fatal(1, "disabled external send");
    $finish;
  end
  initial begin
    wait (channel_B_send_request === 1'b1);
    if (channel_B_send_payload !== 1'b1) $fatal(1, "enabled payload");
    channel_B_send_acknowledge = 1'b1;
    wait (channel_B_send_request === 1'b0);
    channel_B_send_acknowledge = 1'b0;
  end
endmodule
""")


@pytest.mark.parametrize("condition, external_transaction", (("1'b0", False), ("1'b1", True)))
def test_generated_pre_input_conditional_receive_uses_enabled_or_dummy_body_token(
    tmp_path: Path, condition: str, external_transaction: bool,
) -> None:
    program = _Program(f"generated_conditional_receive_{condition[-1]}")
    rtl = program.emit(Sequence((
        If(program.literal(condition), program.receive("A", "a"), Skip()),
        program.send("B", program.literal("1'b1")),
    )))
    module_name = program.module_name

    external_driver = """
    channel_A_receive_payload = 1'b1;
    channel_A_receive_request = 1'b1;
    wait (channel_A_receive_acknowledge === 1'b1);
    channel_A_receive_request = 1'b0;
    wait (channel_A_receive_acknowledge === 1'b0);
""" if external_transaction else """
    #10; if ({channel_A_receive_request, channel_A_receive_acknowledge} !== 2'b00)
      $fatal(1, "disabled external receive was touched");
"""

    _simulate_generated(tmp_path, f"conditional_receive_{condition[-1]}", rtl, f"""
module tb;
  reg reset_n = 0;
  reg channel_A_receive_request = 0, channel_A_receive_payload = 0;
  reg channel_B_send_acknowledge = 0;
  wire channel_A_receive_acknowledge, channel_B_send_request, channel_B_send_payload;
  {module_name} dut (.*);
  initial begin #1; reset_n = 1'b1; end
  initial begin
    #150; $fatal(1, "deadlock");
  end
  initial begin
{external_driver}
  end
  initial begin
    wait (channel_B_send_request === 1'b1);
    if (channel_B_send_payload !== 1'b1) $fatal(1, "BODY constant output");
    channel_B_send_acknowledge = 1'b1;
    wait (channel_B_send_request === 1'b0);
    channel_B_send_acknowledge = 1'b0;
    #2; $finish;
  end
endmodule
""")
