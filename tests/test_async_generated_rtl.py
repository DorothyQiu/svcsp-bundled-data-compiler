"""End-to-end M7C integration tests for generated async SystemVerilog."""
from pathlib import Path
import shutil
import subprocess

import pytest

from svcsp_compiler import compile_async_file
from svcsp_compiler.async_microarchitecture import lower_microarchitecture
from svcsp_compiler.async_rtl_codegen import emit_async_systemverilog
from svcsp_compiler.async_template_binding import bind_async_templates
from svcsp_compiler.behavioral_ir import (
    Assign,
    BehavioralModule,
    ChannelEndpoint,
    Expression,
    If,
    ONE_BIT,
    Parameter,
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
        self.external_inputs: dict[str, Variable] = {}

    def variable(self, name: str, payload_type: PayloadType = _BIT) -> Variable:
        return self.variables.setdefault(
            name, Variable(name, (), SourceLocation("generated_rtl.sv", 1, 1), payload_type),
        )

    def name(self, name: str, payload_type: PayloadType = _BIT) -> Expression:
        return Expression("name", value=name, variable=self.variable(name, payload_type))

    def external_name(self, name: str, payload_type: PayloadType = _BIT) -> Expression:
        if name in self.variables:
            raise ValueError(f"{name} is already a local variable")
        variable = self.external_inputs.setdefault(
            name, Variable(name, (), SourceLocation("generated_rtl.sv", 1, 1), payload_type),
        )
        return Expression("name", value=name, variable=variable)

    def literal(self, value: str) -> Expression:
        return Expression("literal", value=value)

    def receive(self, channel: str, target: str, payload_type: PayloadType = _BIT) -> Receive:
        return Receive(ChannelEndpoint(channel, payload_type=payload_type), self.variable(target, payload_type))

    def send(self, channel: str, value: Expression, payload_type: PayloadType = _BIT) -> Send:
        return Send(ChannelEndpoint(channel, payload_type=payload_type), value)

    def emit(self, body, *, parameters: tuple[Parameter, ...] = ()) -> str:
        behavioral = BehavioralModule(
            self.module_name, body, (), tuple(self.variables.values()),
            parameters=parameters,
            external_inputs=tuple(self.external_inputs.values()),
        )
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


def _compile_source(tmp_path: Path, name: str, source: str) -> str:
    path = tmp_path / f"{name}.sv"
    path.write_text(source)
    return compile_async_file(path)


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


def test_generated_parameter_guard_booleanizes_p_equals_two(tmp_path: Path) -> None:
    program = _Program("generated_parameter_guard_two")
    parameter = Parameter("P", program.module_name, SourceLocation("generated_rtl.sv", 1, 1), "2")
    guard = Expression("parameter", value="P", parameter=parameter)
    rtl = program.emit(Sequence((
        program.receive("A", "a", _BYTE),
        If(guard, program.send("B", program.name("a", _BYTE), _BYTE), Skip()),
    )), parameters=(parameter,))

    assert "assign enable_channel_0_value = !(!(P));" in rtl
    _simulate_generated(tmp_path, "parameter_guard_two", rtl, """
module tb;
  reg reset_n = 0, channel_A_receive_request = 0, channel_B_send_acknowledge = 0;
  reg [7:0] channel_A_receive_payload = 0;
  wire channel_A_receive_acknowledge, channel_B_send_request;
  wire [7:0] channel_B_send_payload;
  generated_parameter_guard_two dut (.*);
  initial begin #1; reset_n = 1'b1; end
  initial begin #150; $fatal(1, "P=2 guard deadlock"); end
  initial begin
    channel_A_receive_payload = 8'ha5;
    channel_A_receive_request = 1'b1;
    wait (channel_A_receive_acknowledge === 1'b1);
    channel_A_receive_request = 1'b0;
    wait (channel_B_send_request === 1'b1);
    if (channel_B_send_payload !== 8'ha5) $fatal(1, "P=2 payload was not forwarded");
    channel_B_send_acknowledge = 1'b1;
    wait (channel_B_send_request === 1'b0);
    channel_B_send_acknowledge = 1'b0;
    wait (channel_A_receive_acknowledge === 1'b0);
    $finish;
  end
endmodule
""")


def test_generated_multibit_external_guard_booleanizes_nonzero_and_zero(tmp_path: Path) -> None:
    program = _Program("generated_multibit_guard")
    select = program.external_name("sel", _BYTE)
    rtl = program.emit(Sequence((
        program.receive("A", "a", _BYTE),
        If(select, program.send("B", program.name("a", _BYTE), _BYTE), Skip()),
    )))

    assert "assign enable_channel_0_value = !(!(sel));" in rtl
    _simulate_generated(tmp_path, "multibit_guard", rtl, """
module tb;
  reg reset_n = 0, channel_A_receive_request = 0, channel_B_send_acknowledge = 0;
  reg [7:0] channel_A_receive_payload = 0, sel = 0;
  wire channel_A_receive_acknowledge, channel_B_send_request;
  wire [7:0] channel_B_send_payload;
  generated_multibit_guard dut (.*);
  initial begin #1; reset_n = 1'b1; end
  initial begin #250; $fatal(1, "multi-bit guard deadlock"); end
  task receive_a(input [7:0] value);
    begin
      channel_A_receive_payload = value;
      channel_A_receive_request = 1'b1;
      wait (channel_A_receive_acknowledge === 1'b1);
      channel_A_receive_request = 1'b0;
      wait (channel_A_receive_acknowledge === 1'b0);
    end
  endtask
  initial begin
    sel = 8'h00;
    receive_a(8'h11);
    #5; if (channel_B_send_request !== 1'b0) $fatal(1, "zero guard sent externally");
    sel = 8'h02;
    channel_A_receive_payload = 8'h5a;
    channel_A_receive_request = 1'b1;
    wait (channel_A_receive_acknowledge === 1'b1);
    channel_A_receive_request = 1'b0;
    wait (channel_B_send_request === 1'b1);
    if (channel_B_send_payload !== 8'h5a) $fatal(1, "nonzero guard did not send");
    channel_B_send_acknowledge = 1'b1;
    wait (channel_B_send_request === 1'b0);
    channel_B_send_acknowledge = 1'b0;
    wait (channel_A_receive_acknowledge === 1'b0);
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
      #3; if (channel_B_send_payload !== expected) $fatal(1, "active output payload changed");
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


def test_generated_2r1s_joins_opposite_input_arrival_orders_and_rearms(tmp_path: Path) -> None:
    program = _Program("generated_2r1s")
    left = program.name("a", _BYTE)
    right = program.name("b", _BYTE)
    rtl = program.emit(Sequence((
        program.receive("A", "a", _BYTE),
        program.receive("B", "b", _BYTE),
        program.send("C", Expression("binary", operator="+", operands=(left, right)), _BYTE),
    )))

    _simulate_generated(tmp_path, "generated_2r1s", rtl, """
module tb;
  reg reset_n = 0;
  reg channel_A_receive_request = 0, channel_B_receive_request = 0;
  reg [7:0] channel_A_receive_payload = 0, channel_B_receive_payload = 0;
  reg channel_C_send_acknowledge = 0;
  wire channel_A_receive_acknowledge, channel_B_receive_acknowledge, channel_C_send_request;
  wire [7:0] channel_C_send_payload;
  generated_2r1s dut (.*);
  initial begin #1; reset_n = 1'b1; end
  task complete_inputs;
    begin
      wait (channel_A_receive_acknowledge && channel_B_receive_acknowledge);
      channel_A_receive_request = 1'b0;
      channel_B_receive_request = 1'b0;
    end
  endtask
  task complete_output(input [7:0] expected);
    begin
      wait (channel_C_send_request === 1'b1);
      if (channel_C_send_payload !== expected) $fatal(1, "2R1S payload");
      channel_C_send_acknowledge = 1'b1;
      wait (channel_C_send_request === 1'b0);
      channel_C_send_acknowledge = 1'b0;
      wait (!channel_A_receive_acknowledge && !channel_B_receive_acknowledge);
    end
  endtask
  initial begin
    #200; $fatal(1, "2R1S deadlock");
  end
  initial begin
    channel_A_receive_payload = 8'h11;
    channel_A_receive_request = 1'b1;
    #4; if (channel_C_send_request !== 1'b0) $fatal(1, "2R1S launched before B");
    channel_B_receive_payload = 8'h22;
    channel_B_receive_request = 1'b1;
    complete_inputs();
    complete_output(8'h33);
    channel_B_receive_payload = 8'h03;
    channel_B_receive_request = 1'b1;
    #4; if (channel_C_send_request !== 1'b0) $fatal(1, "2R1S launched before A");
    channel_A_receive_payload = 8'h04;
    channel_A_receive_request = 1'b1;
    complete_inputs();
    complete_output(8'h07);
    #2; if ({channel_A_receive_acknowledge, channel_B_receive_acknowledge,
             channel_C_send_request} !== 3'b000) $fatal(1, "2R1S did not re-arm");
    $finish;
  end
endmodule
""")


def test_generated_1r2s_waits_for_both_output_acknowledgements_and_rearms(tmp_path: Path) -> None:
    program = _Program("generated_1r2s")
    value = program.name("a", _BYTE)
    rtl = program.emit(Sequence((
        program.receive("A", "a", _BYTE),
        program.send("B", value, _BYTE),
        program.send("C", Expression("binary", operator="+", operands=(value, program.literal("8'd1"))), _BYTE),
    )))

    _simulate_generated(tmp_path, "generated_1r2s", rtl, """
module tb;
  reg reset_n = 0, channel_A_receive_request = 0;
  reg [7:0] channel_A_receive_payload = 0;
  reg channel_B_send_acknowledge = 0, channel_C_send_acknowledge = 0;
  wire channel_A_receive_acknowledge, channel_B_send_request, channel_C_send_request;
  wire [7:0] channel_B_send_payload, channel_C_send_payload;
  generated_1r2s dut (.*);
  initial begin #1; reset_n = 1'b1; end
  task transaction(input [7:0] value, input reverse_ack);
    begin
      channel_A_receive_payload = value;
      channel_A_receive_request = 1'b1;
      wait (channel_A_receive_acknowledge === 1'b1);
      channel_A_receive_request = 1'b0;
      wait (channel_B_send_request && channel_C_send_request);
      if (channel_B_send_payload !== value || channel_C_send_payload !== value + 8'd1)
        $fatal(1, "1R2S payload");
      if (reverse_ack) begin
        channel_C_send_acknowledge = 1'b1;
        #2; if (!channel_B_send_request || !channel_C_send_request || !channel_A_receive_acknowledge)
          $fatal(1, "one C acknowledgement completed BODY");
        channel_B_send_acknowledge = 1'b1;
      end else begin
        channel_B_send_acknowledge = 1'b1;
        #2; if (!channel_B_send_request || !channel_C_send_request || !channel_A_receive_acknowledge)
          $fatal(1, "one B acknowledgement completed BODY");
        channel_C_send_acknowledge = 1'b1;
      end
      wait (!channel_B_send_request && !channel_C_send_request);
      channel_B_send_acknowledge = 1'b0;
      channel_C_send_acknowledge = 1'b0;
      wait (channel_A_receive_acknowledge === 1'b0);
    end
  endtask
  initial begin #200; $fatal(1, "1R2S deadlock"); end
  initial begin
    transaction(8'h21, 1'b0);
    transaction(8'h42, 1'b1);
    #2; if ({channel_A_receive_acknowledge, channel_B_send_request,
             channel_C_send_request} !== 3'b000) $fatal(1, "1R2S did not re-arm");
    $finish;
  end
endmodule
""")


def test_generated_2r2s_completes_join_fanout_and_ack_join(tmp_path: Path) -> None:
    program = _Program("generated_2r2s")
    rtl = program.emit(Sequence((
        program.receive("A", "a", _BYTE),
        program.receive("B", "b", _BYTE),
        program.send("C", program.name("a", _BYTE), _BYTE),
        program.send("D", program.name("b", _BYTE), _BYTE),
    )))

    _simulate_generated(tmp_path, "generated_2r2s", rtl, """
module tb;
  reg reset_n = 0;
  reg channel_A_receive_request = 0, channel_B_receive_request = 0;
  reg [7:0] channel_A_receive_payload = 0, channel_B_receive_payload = 0;
  reg channel_C_send_acknowledge = 0, channel_D_send_acknowledge = 0;
  wire channel_A_receive_acknowledge, channel_B_receive_acknowledge;
  wire channel_C_send_request, channel_D_send_request;
  wire [7:0] channel_C_send_payload, channel_D_send_payload;
  generated_2r2s dut (.*);
  initial begin #1; reset_n = 1'b1; end
  task transaction(input [7:0] a, input [7:0] b, input reverse);
    begin
      if (reverse) begin
        channel_B_receive_payload = b; channel_B_receive_request = 1'b1;
        #3; if (channel_C_send_request || channel_D_send_request) $fatal(1, "2R2S launched before A");
        channel_A_receive_payload = a; channel_A_receive_request = 1'b1;
      end else begin
        channel_A_receive_payload = a; channel_A_receive_request = 1'b1;
        #3; if (channel_C_send_request || channel_D_send_request) $fatal(1, "2R2S launched before B");
        channel_B_receive_payload = b; channel_B_receive_request = 1'b1;
      end
      wait (channel_A_receive_acknowledge && channel_B_receive_acknowledge);
      channel_A_receive_request = 1'b0; channel_B_receive_request = 1'b0;
      wait (channel_C_send_request && channel_D_send_request);
      if (channel_C_send_payload !== a || channel_D_send_payload !== b) $fatal(1, "2R2S payload");
      if (reverse) begin
        channel_D_send_acknowledge = 1'b1;
        #2; if (!channel_C_send_request || !channel_D_send_request ||
                !channel_A_receive_acknowledge || !channel_B_receive_acknowledge)
          $fatal(1, "2R2S completed after one ACK");
        channel_C_send_acknowledge = 1'b1;
      end else begin
        channel_C_send_acknowledge = 1'b1;
        #2; if (!channel_C_send_request || !channel_D_send_request ||
                !channel_A_receive_acknowledge || !channel_B_receive_acknowledge)
          $fatal(1, "2R2S completed after one ACK");
        channel_D_send_acknowledge = 1'b1;
      end
      wait (!channel_C_send_request && !channel_D_send_request);
      channel_C_send_acknowledge = 1'b0; channel_D_send_acknowledge = 1'b0;
      wait (!channel_A_receive_acknowledge && !channel_B_receive_acknowledge);
    end
  endtask
  initial begin #300; $fatal(1, "2R2S deadlock"); end
  initial begin
    transaction(8'h12, 8'h34, 1'b0);
    transaction(8'h56, 8'h78, 1'b1);
    $finish;
  end
endmodule
""")


def test_generated_conditional_input_output_selection_rearms_with_changed_enable(tmp_path: Path) -> None:
    program = _Program("generated_conditional_selection")
    select = program.external_name("sel")
    a = program.name("a", _BYTE)
    b = program.name("b", _BYTE)
    y = program.name("y", _BYTE)
    rtl = program.emit(Sequence((
        If(select, program.receive("A", "a", _BYTE), program.receive("B", "b", _BYTE)),
        Assign(program.variable("y", _BYTE), Expression("conditional", operands=(select, a, b))),
        If(select, program.send("C", y, _BYTE), program.send("D", y, _BYTE)),
    )))

    _simulate_generated(tmp_path, "generated_conditional_selection", rtl, """
module tb;
  reg reset_n = 0;
  reg sel = 0;
  reg channel_A_receive_request = 0, channel_B_receive_request = 0;
  reg [7:0] channel_A_receive_payload = 0, channel_B_receive_payload = 0;
  reg channel_C_send_acknowledge = 0, channel_D_send_acknowledge = 0;
  wire channel_A_receive_acknowledge, channel_B_receive_acknowledge;
  wire channel_C_send_request, channel_D_send_request;
  wire [7:0] channel_C_send_payload, channel_D_send_payload;
  generated_conditional_selection dut (.*);
  initial begin
    sel = 1'b1;
    #1; reset_n = 1'b1;
  end
  task transaction(input select, input [7:0] value, input next_select);
    begin
      if (select) begin
        channel_A_receive_payload = value; channel_A_receive_request = 1'b1;
        wait (channel_A_receive_acknowledge);
        if (channel_B_receive_acknowledge) $fatal(1, "unselected B receive touched");
        channel_A_receive_request = 1'b0;
        wait (!channel_A_receive_acknowledge);
        wait (channel_C_send_request);
        if (channel_C_send_payload !== value || channel_D_send_request)
          $fatal(1, "selected C transaction");
        sel = next_select;
        #1;
        channel_C_send_acknowledge = 1'b1;
        wait (!channel_C_send_request);
        channel_C_send_acknowledge = 1'b0;
      end else begin
        channel_B_receive_payload = value; channel_B_receive_request = 1'b1;
        wait (channel_B_receive_acknowledge);
        if (channel_A_receive_acknowledge) $fatal(1, "unselected A receive touched");
        channel_B_receive_request = 1'b0;
        wait (!channel_B_receive_acknowledge);
        wait (channel_D_send_request);
        if (channel_D_send_payload !== value || channel_C_send_request)
          $fatal(1, "selected D transaction");
        sel = next_select;
        #1;
        channel_D_send_acknowledge = 1'b1;
        wait (!channel_D_send_request);
        channel_D_send_acknowledge = 1'b0;
      end
      #3; if (channel_A_receive_acknowledge || channel_B_receive_acknowledge ||
              channel_C_send_request || channel_D_send_request)
        $fatal(1, "conditional selection did not return idle");
    end
  endtask
  initial begin #300; $fatal(1, "conditional selection deadlock"); end
  initial begin
    transaction(1'b1, 8'ha5, 1'b0);
    transaction(1'b0, 8'h3c, 1'b0);
    $finish;
  end
endmodule
""")


def test_generated_nested_conditionals_honor_effective_enable_conditions(tmp_path: Path) -> None:
    program = _Program("generated_nested_conditions")
    x = program.external_name("x")
    y = program.external_name("y")
    rtl = program.emit(Sequence((
        If(x,
           If(y, program.receive("A", "a"), program.receive("B", "b")),
           program.receive("C", "c")),
        If(x,
           If(y, program.send("D", program.literal("1'b1")), program.send("E", program.literal("1'b1"))),
           program.send("F", program.literal("1'b1"))),
    )))

    _simulate_generated(tmp_path, "generated_nested_conditions", rtl, """
module tb;
  reg reset_n = 0;
  reg x = 0, y = 0;
  reg channel_A_receive_request = 0, channel_B_receive_request = 0, channel_C_receive_request = 0;
  reg channel_A_receive_payload = 0, channel_B_receive_payload = 0, channel_C_receive_payload = 0;
  reg channel_D_send_acknowledge = 0, channel_E_send_acknowledge = 0, channel_F_send_acknowledge = 0;
  wire channel_A_receive_acknowledge, channel_B_receive_acknowledge, channel_C_receive_acknowledge;
  wire channel_D_send_request, channel_E_send_request, channel_F_send_request;
  wire channel_D_send_payload, channel_E_send_payload, channel_F_send_payload;
  generated_nested_conditions dut (.*);
  initial begin
    x = 1'b1;
    y = 1'b1;
    #1; reset_n = 1'b1;
  end
  task transaction(input [1:0] branch, input next_x, input next_y);
    begin
      case (branch)
        0: begin
          channel_A_receive_payload = 1'b1; channel_A_receive_request = 1'b1;
          wait (channel_A_receive_acknowledge);
          if (channel_B_receive_acknowledge || channel_C_receive_acknowledge)
            $fatal(1, "x && y touched an unselected receive");
          channel_A_receive_request = 1'b0; wait (!channel_A_receive_acknowledge);
          wait (channel_D_send_request);
          if (!channel_D_send_payload || channel_E_send_request || channel_F_send_request)
            $fatal(1, "x && y selected wrong send");
          x = next_x; y = next_y; #1;
          channel_D_send_acknowledge = 1'b1; wait (!channel_D_send_request);
          channel_D_send_acknowledge = 1'b0;
        end
        1: begin
          channel_B_receive_payload = 1'b1; channel_B_receive_request = 1'b1;
          wait (channel_B_receive_acknowledge);
          if (channel_A_receive_acknowledge || channel_C_receive_acknowledge)
            $fatal(1, "x && !y touched an unselected receive");
          channel_B_receive_request = 1'b0; wait (!channel_B_receive_acknowledge);
          wait (channel_E_send_request);
          if (!channel_E_send_payload || channel_D_send_request || channel_F_send_request)
            $fatal(1, "x && !y selected wrong send");
          x = next_x; y = next_y; #1;
          channel_E_send_acknowledge = 1'b1; wait (!channel_E_send_request);
          channel_E_send_acknowledge = 1'b0;
        end
        default: begin
          channel_C_receive_payload = 1'b1; channel_C_receive_request = 1'b1;
          wait (channel_C_receive_acknowledge);
          if (channel_A_receive_acknowledge || channel_B_receive_acknowledge)
            $fatal(1, "!x touched an unselected receive");
          channel_C_receive_request = 1'b0; wait (!channel_C_receive_acknowledge);
          wait (channel_F_send_request);
          if (!channel_F_send_payload || channel_D_send_request || channel_E_send_request)
            $fatal(1, "!x selected wrong send");
          x = next_x; y = next_y; #1;
          channel_F_send_acknowledge = 1'b1; wait (!channel_F_send_request);
          channel_F_send_acknowledge = 1'b0;
        end
      endcase
      #3; if (channel_A_receive_acknowledge || channel_B_receive_acknowledge ||
              channel_C_receive_acknowledge || channel_D_send_request ||
              channel_E_send_request || channel_F_send_request)
        $fatal(1, "nested conditional did not re-arm");
    end
  endtask
  initial begin #500; $fatal(1, "nested conditional deadlock"); end
  initial begin
    transaction(0, 1'b1, 1'b0);
    transaction(1, 1'b0, 1'b1);
    transaction(2, 1'b0, 1'b1);
    $finish;
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


@pytest.mark.parametrize(("name", "body", "first", "second"), (
    (
        "r9a_source_receive_assign_send",
        "A.Receive(a); y = ~a; B.Send(y);",
        "8'hff",
        "8'h00",
    ),
    (
        "r9a_source_receive_rewrite",
        "A.Receive(y); y = ~y; B.Send(y);",
        "8'hff",
        "8'h00",
    ),
    (
        "r9a_source_sequential_reassignment",
        "A.Receive(a); y = a; y = ~y; B.Send(y);",
        "8'hff",
        "8'h00",
    ),
))
def test_source_file_whole_variable_body_assignments_simulate_and_rearm(
    tmp_path: Path, name: str, body: str, first: str, second: str,
) -> None:
    rtl = _compile_source(tmp_path, name, f'''module {name}(Channel #(8) A, B);
logic [7:0] a, y;
always begin {body} end
endmodule
''')

    assert rtl.count("always_comb begin") == 1
    assert "logic [7:0] receive_value_0;" in rtl
    assert "assign body_var_" not in rtl
    assert "assign input_0_body_req" in rtl

    _simulate_generated(tmp_path, name, rtl, f'''
module tb;
  reg reset_n = 0, channel_A_receive_request = 0, channel_B_send_acknowledge = 0;
  reg [7:0] channel_A_receive_payload = 0;
  wire channel_A_receive_acknowledge, channel_B_send_request;
  wire [7:0] channel_B_send_payload;
  {name} dut (.*);
  initial begin #1; reset_n = 1'b1; end
  task transaction(input [7:0] value, input [7:0] expected);
    begin
      channel_A_receive_payload = value;
      channel_A_receive_request = 1'b1;
      wait (channel_A_receive_acknowledge === 1'b1);
      channel_A_receive_request = 1'b0;
      wait (channel_B_send_request === 1'b1);
      if (channel_B_send_payload !== expected) $fatal(1, "R9A BODY output mismatch");
      channel_B_send_acknowledge = 1'b1;
      wait (channel_B_send_request === 1'b0);
      channel_B_send_acknowledge = 1'b0;
      wait (channel_A_receive_acknowledge === 1'b0);
    end
  endtask
  initial begin #200; $fatal(1, "R9A BODY deadlock"); end
  initial begin
    transaction(8'h00, {first});
    transaction(8'hff, {second});
    #2; $finish;
  end
endmodule
''')


def test_source_file_if_else_and_nested_guarded_assignments_simulate(tmp_path: Path) -> None:
    name = "r9a_source_nested_assignments"
    rtl = _compile_source(tmp_path, name, f'''module {name}(
input logic sel, outer_guard, inner_guard, Channel #(8) A, B);
logic [7:0] a, y;
always begin
  A.Receive(a);
  if (sel)
    if (outer_guard) y = a;
    else y = ~a;
  else
    if (inner_guard) y = a;
    else y = ~a;
  B.Send(y);
end
endmodule
''')
    assert rtl.count("always_comb begin") == 1
    assert "assign body_var_" not in rtl

    _simulate_generated(tmp_path, name, rtl, f'''
module tb;
  reg reset_n = 0, sel = 0, outer_guard = 0, inner_guard = 0;
  reg channel_A_receive_request = 0, channel_B_send_acknowledge = 0;
  reg [7:0] channel_A_receive_payload = 0;
  wire channel_A_receive_acknowledge, channel_B_send_request;
  wire [7:0] channel_B_send_payload;
  {name} dut (.*);
  initial begin #1; reset_n = 1'b1; end
  task transaction(input [7:0] value, input outer_value, input inner_value,
                   input select_value, input [7:0] expected);
    begin
      outer_guard = outer_value; inner_guard = inner_value; sel = select_value;
      channel_A_receive_payload = value; channel_A_receive_request = 1'b1;
      wait (channel_A_receive_acknowledge === 1'b1);
      channel_A_receive_request = 1'b0;
      wait (channel_B_send_request === 1'b1);
      if (channel_B_send_payload !== expected) $fatal(1, "nested guarded assignment");
      channel_B_send_acknowledge = 1'b1;
      wait (channel_B_send_request === 1'b0);
      channel_B_send_acknowledge = 1'b0;
      wait (channel_A_receive_acknowledge === 1'b0);
    end
  endtask
  initial begin #300; $fatal(1, "nested guarded assignment deadlock"); end
  initial begin
    transaction(8'h12, 1'b1, 1'b0, 1'b1, 8'h12);
    transaction(8'h12, 1'b0, 1'b0, 1'b1, 8'hed);
    transaction(8'h12, 1'b0, 1'b1, 1'b0, 8'h12);
    transaction(8'h12, 1'b0, 1'b0, 1'b0, 8'hed);
    #2; $finish;
  end
endmodule
''')


def test_source_file_conditional_receive_fallback_simulates(tmp_path: Path) -> None:
    name = "r9a_source_conditional_receive_fallback"
    rtl = _compile_source(tmp_path, name, f'''module {name}(input logic sel, Channel #(1) A, B);
logic a, y;
always begin
  if (sel) A.Receive(a);
  if (sel) y = a; else y = sel;
  B.Send(y);
end
endmodule
''')
    assert "assign enable_channel_0_value = !(!(sel));" in rtl
    assert "receive_value_0" in rtl

    _simulate_generated(tmp_path, name, rtl, f'''
module tb;
  reg reset_n = 0, sel = 1, channel_A_receive_request = 0, channel_B_send_acknowledge = 0;
  reg channel_A_receive_payload = 0;
  wire channel_A_receive_acknowledge, channel_B_send_request;
  wire channel_B_send_payload;
  {name} dut (.*);
  initial begin #1; reset_n = 1'b1; end
  task complete_b(input expected);
    begin
      wait (channel_B_send_request === 1'b1);
      if (channel_B_send_payload !== expected)
        $fatal(1, "conditional Receive fallback payload expected=%b actual=%b", expected, channel_B_send_payload);
      channel_B_send_acknowledge = 1'b1;
      wait (channel_B_send_request === 1'b0);
      channel_B_send_acknowledge = 1'b0;
    end
  endtask
  initial begin #250; $fatal(1, "conditional Receive fallback deadlock"); end
  initial begin
    channel_A_receive_payload = 1'b1; channel_A_receive_request = 1'b1;
    wait (channel_A_receive_acknowledge === 1'b1);
    #1;
    channel_A_receive_request = 1'b0;
    wait (channel_A_receive_acknowledge === 1'b0);
    complete_b(1'b1);
    sel = 1'b0;
    #4; if (channel_A_receive_acknowledge !== 1'b0) $fatal(1, "disabled Receive touched A");
    complete_b(1'b0);
    #2; $finish;
  end
endmodule
''')


def test_source_file_parallel_body_writes_simulate_multi_output_completion(tmp_path: Path) -> None:
    name = "r9a_source_parallel_writes"
    rtl = _compile_source(tmp_path, name, f'''module {name}(Channel #(8) A, B, C, D);
logic [7:0] a, b, y, z;
always begin
  A.Receive(a); B.Receive(b);
  fork y = a; z = b; join
  C.Send(y); D.Send(z);
end
endmodule
''')
    assert rtl.count("always_comb begin") == 1

    _simulate_generated(tmp_path, name, rtl, f'''
module tb;
  reg reset_n = 0, channel_A_receive_request = 0, channel_B_receive_request = 0;
  reg [7:0] channel_A_receive_payload = 0, channel_B_receive_payload = 0;
  reg channel_C_send_acknowledge = 0, channel_D_send_acknowledge = 0;
  wire channel_A_receive_acknowledge, channel_B_receive_acknowledge;
  wire channel_C_send_request, channel_D_send_request;
  wire [7:0] channel_C_send_payload, channel_D_send_payload;
  {name} dut (.*);
  initial begin #1; reset_n = 1'b1; end
  initial begin #250; $fatal(1, "parallel BODY deadlock"); end
  initial begin
    channel_A_receive_payload = 8'h12; channel_B_receive_payload = 8'h34;
    channel_A_receive_request = 1'b1; channel_B_receive_request = 1'b1;
    wait (channel_A_receive_acknowledge && channel_B_receive_acknowledge);
    channel_A_receive_request = 1'b0; channel_B_receive_request = 1'b0;
    wait (channel_C_send_request && channel_D_send_request);
    if (channel_C_send_payload !== 8'h12 || channel_D_send_payload !== 8'h34)
      $fatal(1, "parallel BODY payload");
    channel_C_send_acknowledge = 1'b1;
    #2; if (!channel_D_send_request) $fatal(1, "one output ACK completed parallel BODY");
    channel_D_send_acknowledge = 1'b1;
    wait (!channel_C_send_request && !channel_D_send_request);
    channel_C_send_acknowledge = 1'b0; channel_D_send_acknowledge = 1'b0;
    wait (!channel_A_receive_acknowledge && !channel_B_receive_acknowledge);
    #2; $finish;
  end
endmodule
''')


@pytest.mark.parametrize(("name", "source", "declarations", "setup", "expected"), (
    (
        "r9a_source_external_rhs",
        "input logic [7:0] ext, ",
        "reg [7:0] ext = 0;",
        "ext = 8'hc3;",
        "8'hc3",
    ),
    (
        "r9a_source_symbolic_width",
        "",
        "",
        "",
        "8'h5a",
    ),
))
def test_source_file_external_rhs_and_symbolic_width_transactions(
    tmp_path: Path, name: str, source: str, declarations: str, setup: str, expected: str,
) -> None:
    parameters = "#(parameter int W = 8) " if "symbolic" in name else ""
    width = "W" if "symbolic" in name else "8"
    body = "y = ext;" if "external" in name else "y = x;"
    receive = "A.Receive(x);" if "symbolic" in name else "A.Receive(a);"
    packed_range = "[W-1:0]" if "symbolic" in name else "[7:0]"
    source_text = f'''module {name} {parameters}({source}Channel #({width}) A, B);
logic {packed_range} {'x, y' if 'symbolic' in name else 'a, y'};
always begin {receive} {body} B.Send(y); end
endmodule
'''
    rtl = _compile_source(tmp_path, name, source_text)
    assert ("parameter int W = 8" in rtl) if "symbolic" in name else ("input logic [7:0] ext" in rtl)

    _simulate_generated(tmp_path, name, rtl, f'''
module tb;
  reg reset_n = 0, channel_A_receive_request = 0, channel_B_send_acknowledge = 0;
  {declarations}
  reg [7:0] channel_A_receive_payload = 0;
  wire channel_A_receive_acknowledge, channel_B_send_request;
  wire [7:0] channel_B_send_payload;
  {name} dut (.*);
  initial begin #1; reset_n = 1'b1; end
  initial begin #150; $fatal(1, "external/symbolic transaction deadlock"); end
  initial begin
    {setup}
    channel_A_receive_payload = 8'h5a; channel_A_receive_request = 1'b1;
    wait (channel_A_receive_acknowledge === 1'b1); channel_A_receive_request = 1'b0;
    wait (channel_B_send_request === 1'b1);
    if (channel_B_send_payload !== {expected}) $fatal(1, "external/symbolic payload");
    channel_B_send_acknowledge = 1'b1; wait (channel_B_send_request === 1'b0);
    channel_B_send_acknowledge = 1'b0; wait (channel_A_receive_acknowledge === 1'b0);
    #2; $finish;
  end
endmodule
''')
