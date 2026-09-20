"""Protocol tests of the reference cells; these do not establish physical timing."""

from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]
IVERILOG = shutil.which("iverilog")
VVP = shutil.which("vvp")


@unittest.skipUnless(IVERILOG and VVP, "Icarus Verilog is required for cell simulation")
class CellSimulationTests(unittest.TestCase):
    def simulate(self, bench):
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "tb.v"
            executable = Path(directory) / "sim"
            source.write_text(
                "`timescale 1ns/1ps\nmodule tb;\n"
                'initial begin #2000; $fatal(1, "TIMEOUT"); end\n'
                + bench + "\nendmodule\n"
            )
            compiled = subprocess.run(
                [IVERILOG, "-g2012", "-s", "tb", "-o", str(executable),
                 str(ROOT / "svcsp/rtl/csp_cells.v"), str(source)],
                capture_output=True, text=True, timeout=20,
            )
            self.assertEqual(compiled.returncode, 0, compiled.stdout + compiled.stderr)
            result = subprocess.run([VVP, str(executable)], capture_output=True,
                                    text=True, timeout=20)
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            self.assertIn("PASS", result.stdout)
            self.assertNotIn("ERROR", result.stdout)

    def test_transfer_capture_return_to_zero_and_reset_at_each_phase(self):
        self.simulate(r'''
reg reset_n = 0, go = 0;
reg [7:0] data_in = 0;
wire send_done, receive_done, req, ack, we;
wire [7:0] channel_data, wd, value;
// Intentionally put the enable-controlled writer mux before storage.
wire [7:0] mux_data = we ? wd : 8'b0;
csp_send #(8) tx(reset_n, go, send_done, data_in, channel_data, req, ack);
csp_receive #(8) rx(reset_n, go, receive_done, channel_data, req, ack, wd, we);
csp_storage #(8, 8'h12) storage(reset_n, we, mux_data, value);
integer phase;
task transfer(input [7:0] expected);
begin
    data_in = expected;
    go = 1;
    wait(req === 1);
    #0.2; data_in = ~expected;
    wait(ack === 1);
    if (value !== expected) $fatal(1, "ack precedes storage capture");
    wait(send_done === 1 && receive_done === 1);
    if (req || ack || we) $fatal(1, "done precedes channel return to zero");
    if (value !== expected || channel_data !== expected)
        $fatal(1, "sender resampled data or storage mux raced");
    #2;
    if (!send_done || !receive_done) $fatal(1, "done not retained");
    go = 0;
    wait(send_done === 0 && receive_done === 0);
    #1;
end
endtask
initial begin
    #2; reset_n = 1;
    transfer(8'h5a);
    transfer(8'h00);
    transfer(8'hff);
    for (phase = 0; phase < 4; phase = phase + 1) begin
        data_in = 8'h34;
        go = 1;
        case (phase)
            0: wait(req === 1);
            1: wait(we === 1);
            2: wait(ack === 1);
            3: wait(send_done === 1 && receive_done === 1);
        endcase
        #0.1; reset_n = 0;
        #0.1;
        if (send_done || receive_done || req || ack || we || value !== 8'h12)
            $fatal(1, "reset did not immediately abort active transaction");
        go = 0;
        #2; reset_n = 1;
        #1; transfer(8'ha5 + phase);
    end
    $display("PASS"); $finish;
end
''')

    def test_sequential_send_mux_holds_payload_and_survives_reset(self):
        self.simulate(r'''
reg reset_n = 0, go_a = 0, go_b = 0, ack = 0;
wire done_a, done_b, req_a, req_b, req;
wire [7:0] data_a, data_b, data;
csp_send #(8) a(reset_n, go_a, done_a, 8'h35, data_a, req_a, ack);
csp_send #(8) b(reset_n, go_b, done_b, 8'hc7, data_b, req_b, ack);
csp_send_mux #(8,2) mux(reset_n, {req_b,req_a}, {data_b,data_a}, req, data, ack);
task transfer(input integer selected, input [7:0] expected);
begin
    if (selected == 0) go_a = 1; else go_b = 1;
    wait(req === 1);
    if (data !== expected) $fatal(1, "mux selected wrong payload");
    #3; ack = 1;
    wait(req === 0);
    #3;
    if (data !== expected) $fatal(1, "mux data changed during return to zero");
    if (done_a || done_b) $fatal(1, "send completed before ack fell");
    ack = 0;
    if (selected == 0) begin
        wait(done_a === 1); go_a = 0; wait(done_a === 0);
    end else begin
        wait(done_b === 1); go_b = 0; wait(done_b === 0);
    end
    #1;
end
endtask
initial begin
    #2; reset_n = 1;
    transfer(0, 8'h35);
    transfer(1, 8'hc7);
    transfer(0, 8'h35);
    go_b = 1;
    wait(req === 1);
    #0.1; reset_n = 0;
    #0.1;
    if(req || req_a || req_b || done_a || done_b)
        $fatal(1, "mux reset failed");
    go_b = 0;
    #2; reset_n = 1;
    transfer(1, 8'hc7);
    $display("PASS"); $finish;
end
''')

    def test_branch_samples_condition_and_resets_selected_child(self):
        self.simulate(r'''
reg reset_n = 0, go = 0, condition = 0;
wire done, then_go, else_go, then_done, else_done, then_we, else_we;
wire [7:0] then_data, else_data, value;
csp_branch branch(reset_n, go, done, condition, then_go, then_done, else_go, else_done);
csp_assign #(8) yes(reset_n, then_go, then_done, 8'h53, then_data, then_we);
csp_assign #(8) no(reset_n, else_go, else_done, 8'ha7, else_data, else_we);
wire we = then_we | else_we;
wire [7:0] data = ({8{then_we}} & then_data) | ({8{else_we}} & else_data);
csp_storage #(8) storage(reset_n, we, data, value);
task choose(input bit choice, input [7:0] expected);
begin
    condition = choice; go = 1;
    wait(then_go || else_go);
    condition = ~choice;
    wait(done === 1);
    if (value !== expected) $fatal(1, "branch resampled condition");
    if (then_go || else_go || then_done || else_done)
        $fatal(1, "branch did not finish selected child reset");
    #2;
    if (!done) $fatal(1, "branch did not retain done");
    go = 0; wait(done === 0); #1;
end
endtask
initial begin
    #2; reset_n = 1;
    choose(1, 8'h53);
    choose(0, 8'ha7);
    condition = 1; go = 1;
    wait(then_we === 1);
    #0.1; reset_n = 0;
    #0.1;
    if (done || then_go || else_go || then_done || else_done || value !== 0)
        $fatal(1, "branch reset failed");
    go = 0;
    #2; reset_n = 1;
    choose(0, 8'ha7);
    $display("PASS"); $finish;
end
''')

    def test_loop_and_join_wait_for_slow_branch_and_return_to_zero(self):
        self.simulate(r'''
reg reset_n = 0;
wire go, done, fast_done, slow_done, fast_we, slow_we;
wire [7:0] fast_data, slow_data, fast_value, slow_value;
csp_loop loop_control(reset_n, done, go);
csp_join #(2) join_control(reset_n, go, {slow_done,fast_done}, done);
csp_assign #(8,1) fast(reset_n, go, fast_done, fast_value+8'd1, fast_data, fast_we);
csp_assign #(8,3) slow(reset_n, go, slow_done, slow_value+8'd1, slow_data, slow_we);
csp_storage #(8) fast_store(reset_n, fast_we, fast_data, fast_value);
csp_storage #(8) slow_store(reset_n, slow_we, slow_data, slow_value);
integer complete = 0;
always @(posedge done) begin
    if (!fast_done || !slow_done || fast_we || slow_we)
        $fatal(1, "join completed before all branches");
    if (fast_value !== slow_value) $fatal(1, "loop branch executed extra times");
    complete = complete + 1;
end
always @(negedge done) begin
    if (reset_n && (go || fast_done || slow_done))
        $fatal(1, "join reset before branches returned to zero");
end
initial begin
    #2; reset_n = 1;
    wait(complete == 3);
    wait(go === 0);
    wait(go === 1);
    wait(fast_we === 1);
    #0.1; reset_n = 0;
    #0.1;
    if (go || done || fast_done || slow_done || fast_we || slow_we)
        $fatal(1, "loop did not abort on reset");
    #2; reset_n = 1;
    wait(complete == 6);
    reset_n = 0;
    $display("PASS"); $finish;
end
''')


if __name__ == "__main__":
    unittest.main()
