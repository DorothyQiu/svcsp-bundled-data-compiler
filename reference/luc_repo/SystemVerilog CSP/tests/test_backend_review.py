"""Additional independent review cases for emitted datapaths and control."""

import shutil
import unittest

from test_compiler import compile_source, simulate, transaction_bench


@unittest.skipUnless(shutil.which("iverilog") and shutil.which("vvp"), "Icarus Verilog required for simulation")
class BackendReviewSimulationTests(unittest.TestCase):
    def test_assignment_and_send_preserve_wider_expression_context(self):
        source = '''module m(interface L, R, S);
logic [7:0] x;
logic [15:0] y;
always begin
    L.Receive(x);
    y=x+8'd1;
    R.Send(y);
    S.Send(x+8'd1);
end
endmodule'''
        values = [0, 1, 127, 254, 255]
        expected = [x + 1 for x in values]
        simulate(compile_source(source, channel_widths={"S": 16}), transaction_bench(
            [("L", "input", 8), ("R", "output", 16), ("S", "output", 16)],
            {"L": values}, {"R": expected, "S": expected}))

    def test_alternative_senders_on_one_channel_and_empty_branch(self):
        source = '''module m(interface L, R);
logic [7:0] x;
always begin
    L.Receive(x);
    if(x[0]) begin
        if(x[1]) R.Send(x); else R.Send(x^8'ha5);
    end else begin
        if(x[1]) ; else ;
        R.Send(x+8'd1);
    end
end
endmodule'''
        values = [0, 1, 2, 3, 255, 254, 129, 128]
        expected = [(x if x & 2 else x ^ 0xa5) if x & 1 else (x + 1) & 255 for x in values]
        simulate(compile_source(source), transaction_bench(
            [("L", "input", 8), ("R", "output", 8)], {"L": values}, {"R": expected}))

    def test_signed_parameter_remains_signed_in_comparison_and_extension(self):
        source = '''module m #(parameter int NEG=-1)(interface L, R);
logic [7:0] x;
logic [15:0] y;
always begin
    L.Receive(x);
    if(NEG<0) y={8'd0,x}+NEG; else y=16'h1234;
    R.Send(y);
end
endmodule'''
        values = [0, 1, 127, 255]
        simulate(compile_source(source), transaction_bench(
            [("L", "input", 8), ("R", "output", 16)],
            {"L": values}, {"R": [(x - 1) & 65535 for x in values]}))


if __name__ == "__main__":
    unittest.main()
