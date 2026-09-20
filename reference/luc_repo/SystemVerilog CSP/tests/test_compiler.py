"""End-to-end compilation and transaction-level simulation of emitted Verilog."""

import json
from pathlib import Path
import random
import shutil
import subprocess
import sys
import tempfile
import unittest

from svcsp import parse_text
from svcsp.backend import emit_verilog, cell_models

ROOT = Path(__file__).resolve().parents[1]


def compile_source(source, **kwargs):
    from svcsp.lowering import lower
    return emit_verilog(lower(parse_text(source), **kwargs))


def simulate(netlist, bench):
    with tempfile.TemporaryDirectory() as directory:
        path = Path(directory)
        (path / "design.v").write_text(netlist)
        (path / "cells.v").write_text(cell_models())
        (path / "tb.v").write_text(bench)
        compiled = subprocess.run(["iverilog", "-g2005", "-Wall", "-s", "tb", "-o", str(path / "sim"),
                                   str(path / "design.v"), str(path / "cells.v"), str(path / "tb.v")],
                                  capture_output=True, text=True, timeout=30)
        if compiled.returncode:
            raise AssertionError(compiled.stderr)
        result = subprocess.run(["vvp", str(path / "sim")], capture_output=True, text=True, timeout=10)
        if result.returncode or "PASS" not in result.stdout or "ERROR" in result.stdout or "FAIL" in result.stdout:
            raise AssertionError(result.stdout + result.stderr)
        return result.stdout


def bench_header(channels, module="m_structural"):
    declarations = ["`timescale 1ns/1ps", "module tb;", "reg reset_n=0;", "integer completed=0;"]
    connections = [".reset_n(reset_n)"]
    for name, direction, width in channels:
        if direction == "input":
            declarations.extend([f"reg [{width-1}:0] {name}_data=0; reg {name}_req=0; wire {name}_ack;",
                f"task send_{name}; input [{width-1}:0] value; input integer pause; begin",
                f"{name}_data=value; #2; {name}_req=1; wait({name}_ack===1'b1);",
                f"#pause; {name}_req=0; wait({name}_ack===1'b0); #1; end endtask"])
        else:
            declarations.extend([f"wire [{width-1}:0] {name}_data; wire {name}_req; reg {name}_ack=0;",
                f"task expect_{name}; input [{width-1}:0] expected; input integer pause; integer j; begin",
                f"wait({name}_req===1'b1);",
                f"for(j=0;j<pause;j=j+1) begin #1; if ({name}_data !== expected || {name}_req !== 1'b1) begin",
                f'$display("FAIL {name} data or req under backpressure: expected %h actual %h",expected,{name}_data); $finish; end end',
                f"{name}_ack=1; wait({name}_req===1'b0); #3;",
                f'if ({name}_data !== expected) begin $display("FAIL {name} changed before ack returned low"); $finish; end',
                f"{name}_ack=0; #1; end endtask"])
        connections += [f".{name}_{suffix}({name}_{suffix})" for suffix in ("data", "req", "ack")]
    declarations.append(f"{module} dut(" + ",".join(connections) + ");")
    declarations.append('initial begin #100000; $display("FAIL timeout"); $finish; end')
    return "\n".join(declarations) + "\n"


def transaction_bench(channels, inputs, outputs, module="m_structural"):
    rng = random.Random(711)
    threads = []
    for name, values in inputs.items():
        calls = [f"#{rng.randrange(1, 10)}; send_{name}({value}, {rng.randrange(1, 8)});" for value in values]
        threads.append("begin\n" + "\n".join(calls) + "\nend")
    for name, values in outputs.items():
        calls = [f"#{rng.randrange(1, 10)}; expect_{name}({value}, {rng.randrange(2, 20)});" for value in values]
        threads.append("begin\n" + "\n".join(calls) + "\nend")
    return (bench_header(channels, module) + "initial begin #5; reset_n=1; fork\n" +
            "\n".join(threads) + '\njoin\n#20; $display("PASS"); $finish; end\nendmodule\n')


@unittest.skipUnless(shutil.which("iverilog") and shutil.which("vvp"), "Icarus Verilog required for simulation")
class CompilerSimulationTests(unittest.TestCase):
    def test_buffer_backpressure_and_repeated_transactions(self):
        source = (ROOT / "examples/async_buffer.sv").read_text()
        values = [0, 1, 255, 128] + [random.Random(i).randrange(256) for i in range(25)]
        simulate(compile_source(source), transaction_bench(
            [("L", "input", 8), ("R", "output", 8)], {"L": values}, {"R": values}, "async_buffer_structural"))

    def test_parallel_adder_skew_carry_and_parameter_override(self):
        source = (ROOT / "examples/async_adder.sv").read_text()
        a, b = [0, 15, 15, 1, 8, 3], [15, 1, 15, 2, 8, 7]
        simulate(compile_source(source, parameters={"WIDTH": 4}), transaction_bench(
            [("A", "input", 4), ("B", "input", 4), ("SUM", "output", 5)],
            {"A": a, "B": b}, {"SUM": [x + y for x, y in zip(a, b)]}, "async_adder_structural"))

    def test_state_condition_and_repeated_send(self):
        source = '''module m(interface L, R); logic [7:0] x, acc=0;
always begin L.Receive(x); if(x[0]) acc=acc+x; else acc=x^8'ha5;
repeat(2) R.Send(acc); end endmodule'''
        values = [3, 5, 2, 255, 0, 1, 128, 127]
        expected, acc = [], 0
        for value in values:
            acc = ((acc + value) if value & 1 else (value ^ 0xa5)) & 255
            expected.extend([acc, acc])
        simulate(compile_source(source), transaction_bench(
            [("L", "input", 8), ("R", "output", 8)], {"L": values}, {"R": expected}))

    def test_multiple_writes_and_receives_on_one_channel(self):
        source = '''module m(interface L, R); logic [7:0] x,y;
always begin L.Receive(x); L.Receive(y); x=x+y; x=x+8'd3; R.Send(x); end endmodule'''
        values = [1, 2, 255, 2, 120, 130, 0, 0]
        expected = [(values[i] + values[i+1] + 3) & 255 for i in range(0, len(values), 2)]
        simulate(compile_source(source), transaction_bench(
            [("L", "input", 8), ("R", "output", 8)], {"L": values}, {"R": expected}))

    def test_parallel_fanout(self):
        source = '''module m(interface L, R, S); logic [7:0] x;
always begin L.Receive(x); fork R.Send(x); S.Send(x); join end endmodule'''
        values = [0, 255, 17, 33, 63]
        simulate(compile_source(source), transaction_bench(
            [("L", "input", 8), ("R", "output", 8), ("S", "output", 8)],
            {"L": values}, {"R": values, "S": values}))

    def test_reset_interrupts_stalled_send_and_restarts(self):
        source = "module m(interface L,R); logic [7:0] x; always begin L.Receive(x); R.Send(x); end endmodule"
        bench = bench_header([("L", "input", 8), ("R", "output", 8)]) + '''
initial begin
 #5; reset_n=1; send_L(55,2); wait(R_req===1'b1); #4; reset_n=0;
 #3; if(R_req!==0 || L_ack!==0) begin $display("FAIL reset"); $finish; end
 #3; reset_n=1;
 fork send_L(99,3); expect_R(99,10); join
 #10; $display("PASS"); $finish;
end
endmodule
'''
        simulate(compile_source(source), bench)


class CompilerCliTests(unittest.TestCase):
    def test_cli_ir_and_compile(self):
        for command in ("ir", "compile"):
            result = subprocess.run([sys.executable, "-m", "svcsp", command, "examples/async_adder.sv"],
                                    cwd=ROOT, capture_output=True, text=True)
            self.assertEqual(result.returncode, 0, result.stderr)
            if command == "ir":
                self.assertEqual(json.loads(result.stdout)["ir"]["name"], "async_adder")
            else:
                self.assertIn("module async_adder_structural", result.stdout)
                self.assertNotIn("always", result.stdout)

    def test_failed_compilation_does_not_overwrite_output(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory)
            source, output = path / "bad.sv", path / "out.v"
            source.write_text("module m(interface L); always #10 L.Send(1); endmodule\n")
            output.write_text("keep me")
            result = subprocess.run([sys.executable, "-m", "svcsp", "compile", str(source), "-o", str(output)],
                                    cwd=ROOT, capture_output=True, text=True)
            self.assertEqual(result.returncode, 1)
            self.assertNotIn("Traceback", result.stderr)
            self.assertEqual(output.read_text(), "keep me")


if __name__ == "__main__":
    unittest.main()
