`timescale 1ns/1ps
// FUNCTIONAL SIMULATION MODELS -- NOT A SYNTHESIS CELL LIBRARY.
//
// These clockless behavioral cells define the compiler's four-phase protocol.
// Their delays, procedural waits, and reset-cancellation forks are simulation
// constructs. A physical implementation needs characterized asynchronous cells,
// matched bundled-data delays, hazard analysis, and reset/timing verification.
// Replacing these models with gates without that work is not supported.
//
// Command protocol: go rises, done rises, go falls, done falls. A command
// initiator must retain go until done and wait for done to fall before reuse.
// Channel protocol: req rises, ack rises, req falls, ack falls. A sender holds
// its sampled data through ack falling. Send/receive done rises only after the
// complete external channel handshake, including return to zero.
//
// reset_n is active low; assertion interrupts all pending waits and delays.
// Reset the connected network together. Reset discards in-flight transactions;
// it does not promise delivery across reset. An unknown reset is treated as
// asserted. DELAY is a positive simulation delay in ns, not a timing estimate.

module csp_send #(
    parameter integer WIDTH = 8,
    parameter integer DELAY = 1
) (
    input wire reset_n,
    input wire go,
    output reg done,
    input wire [WIDTH-1:0] data_in,
    output reg [WIDTH-1:0] channel_data,
    output reg channel_req,
    input wire channel_ack
);
    initial begin
        if (WIDTH < 1 || DELAY < 1) begin
            $display("ERROR: csp_send requires WIDTH >= 1 and DELAY >= 1");
            $finish;
        end
        done = 1'b0;
        channel_data = {WIDTH{1'b0}};
        channel_req = 1'b0;
        forever begin
            wait (reset_n === 1'b1);
            begin : active
                fork
                    begin
                        forever begin
                            wait (go === 1'b1);
                            channel_data = data_in;
                            #DELAY;
                            wait (channel_ack === 1'b0);
                            channel_req = 1'b1;
                            wait (channel_ack === 1'b1);
                            #DELAY;
                            channel_req = 1'b0;
                            wait (channel_ack === 1'b0);
                            #DELAY;
                            done = 1'b1;
                            wait (go === 1'b0);
                            #DELAY;
                            done = 1'b0;
                        end
                    end
                    begin
                        wait (reset_n !== 1'b1);
                        disable active;
                    end
                join
            end
            done = 1'b0;
            channel_data = {WIDTH{1'b0}};
            channel_req = 1'b0;
        end
    end
endmodule

// Combine mutually exclusive sends to one external channel. Private sender
// requests fall after external ack rises; the mux then lowers external req.
// Snapshotting the selected payload keeps channel_data stable even as the
// private request vector returns to zero. This cell is NOT an arbiter.
module csp_send_mux #(
    parameter integer WIDTH = 8,
    parameter integer N = 2,
    parameter integer DELAY = 1
) (
    input wire reset_n,
    input wire [N-1:0] requests,
    input wire [N*WIDTH-1:0] data,
    output reg channel_req,
    output reg [WIDTH-1:0] channel_data,
    input wire channel_ack
);
    integer index;
    integer selected;
    integer count;
    // A second writer appearing after selection must also be caught.
    always @(requests or reset_n) begin
        if (reset_n === 1'b1) begin
            if ((^requests) === 1'bx) begin
                $display("ERROR: csp_send_mux requests contain X/Z");
                $finish;
            end
            if ((requests & (requests - 1'b1)) != {N{1'b0}}) begin
                $display("ERROR: csp_send_mux received concurrent sends");
                $finish;
            end
        end
    end
    initial begin
        if (WIDTH < 1 || N < 1 || DELAY < 1) begin
            $display("ERROR: csp_send_mux requires WIDTH, N, DELAY >= 1");
            $finish;
        end
        channel_req = 1'b0;
        channel_data = {WIDTH{1'b0}};
        selected = 0;
        count = 0;
        forever begin
            wait (reset_n === 1'b1);
            begin : active
                fork
                    begin
                        forever begin
                            wait ((|requests) === 1'b1);
                            count = 0;
                            for (index = 0; index < N; index = index + 1) begin
                                if (requests[index] === 1'b1) begin
                                    selected = index;
                                    count = count + 1;
                                end
                            end
                            if (count != 1 || (^requests) === 1'bx) begin
                                $display("ERROR: csp_send_mux requires one active sender");
                                $finish;
                            end
                            channel_data = data[selected*WIDTH +: WIDTH];
                            #DELAY;
                            wait (channel_ack === 1'b0);
                            channel_req = 1'b1;
                            wait (channel_ack === 1'b1);
                            wait ((|requests) === 1'b0);
                            #DELAY;
                            channel_req = 1'b0;
                            wait (channel_ack === 1'b0);
                        end
                    end
                    begin
                        wait (reset_n !== 1'b1);
                        disable active;
                    end
                join
            end
            channel_req = 1'b0;
            channel_data = {WIDTH{1'b0}};
            selected = 0;
            count = 0;
        end
    end
endmodule

module csp_receive #(
    parameter integer WIDTH = 8,
    parameter integer DELAY = 1
) (
    input wire reset_n,
    input wire go,
    output reg done,
    input wire [WIDTH-1:0] channel_data,
    input wire channel_req,
    output reg channel_ack,
    output reg [WIDTH-1:0] write_data,
    output reg write_enable
);
    initial begin
        if (WIDTH < 1 || DELAY < 1) begin
            $display("ERROR: csp_receive requires WIDTH >= 1 and DELAY >= 1");
            $finish;
        end
        done = 1'b0;
        channel_ack = 1'b0;
        write_data = {WIDTH{1'b0}};
        write_enable = 1'b0;
        forever begin
            wait (reset_n === 1'b1);
            begin : active
                fork
                    begin
                        forever begin
                            wait (go === 1'b1);
                            wait (channel_req === 1'b1);
                            write_data = channel_data;
                            #DELAY;
                            write_enable = 1'b1;
                            #(2*DELAY);
                            write_enable = 1'b0;
                            #DELAY;
                            channel_ack = 1'b1;
                            wait (channel_req === 1'b0);
                            #DELAY;
                            channel_ack = 1'b0;
                            #DELAY;
                            done = 1'b1;
                            wait (go === 1'b0);
                            #DELAY;
                            done = 1'b0;
                        end
                    end
                    begin
                        wait (reset_n !== 1'b1);
                        disable active;
                    end
                join
            end
            done = 1'b0;
            channel_ack = 1'b0;
            write_data = {WIDTH{1'b0}};
            write_enable = 1'b0;
        end
    end
endmodule

module csp_assign #(
    parameter integer WIDTH = 8,
    parameter integer DELAY = 1
) (
    input wire reset_n,
    input wire go,
    output reg done,
    input wire [WIDTH-1:0] data_in,
    output reg [WIDTH-1:0] write_data,
    output reg write_enable
);
    initial begin
        if (WIDTH < 1 || DELAY < 1) begin
            $display("ERROR: csp_assign requires WIDTH >= 1 and DELAY >= 1");
            $finish;
        end
        done = 1'b0;
        write_data = {WIDTH{1'b0}};
        write_enable = 1'b0;
        forever begin
            wait (reset_n === 1'b1);
            begin : active
                fork
                    begin
                        forever begin
                            wait (go === 1'b1);
                            write_data = data_in;
                            #DELAY;
                            write_enable = 1'b1;
                            #(2*DELAY);
                            write_enable = 1'b0;
                            #DELAY;
                            done = 1'b1;
                            wait (go === 1'b0);
                            #DELAY;
                            done = 1'b0;
                        end
                    end
                    begin
                        wait (reset_n !== 1'b1);
                        disable active;
                    end
                join
            end
            done = 1'b0;
            write_data = {WIDTH{1'b0}};
            write_enable = 1'b0;
        end
    end
endmodule

module csp_storage #(
    parameter integer WIDTH = 8,
    parameter [WIDTH-1:0] INITIAL = 0
) (
    input wire reset_n,
    input wire write_enable,
    input wire [WIDTH-1:0] write_data,
    output reg [WIDTH-1:0] value
);
    initial begin
        if (WIDTH < 1) begin
            $display("ERROR: csp_storage requires WIDTH >= 1");
            $finish;
        end
        value = INITIAL;
    end
    always @(posedge write_enable or negedge reset_n) begin
        if (reset_n !== 1'b1)
            value <= INITIAL;
        else begin
            // A shared variable's writer mux changes when enable rises.
            // Defer sampling until that zero-delay combinational mux settles.
            #0;
            if (reset_n === 1'b1)
                value <= write_data;
            else
                value <= INITIAL;
        end
    end
endmodule

module csp_join #(
    parameter integer N = 2
) (
    input wire reset_n,
    input wire go,
    input wire [N-1:0] branch_done,
    output reg done
);
    initial begin
        if (N < 1) begin
            $display("ERROR: csp_join requires N >= 1");
            $finish;
        end
        done = 1'b0;
        forever begin
            wait (reset_n === 1'b1);
            begin : active
                fork
                    begin
                        forever begin
                            wait (go === 1'b1);
                            wait ((&branch_done) === 1'b1);
                            done = 1'b1;
                            wait (go === 1'b0 && (|branch_done) === 1'b0);
                            done = 1'b0;
                        end
                    end
                    begin
                        wait (reset_n !== 1'b1);
                        disable active;
                    end
                join
            end
            done = 1'b0;
        end
    end
endmodule

module csp_branch #(
    parameter integer DELAY = 1
) (
    input wire reset_n,
    input wire go,
    output reg done,
    input wire condition,
    output reg then_go,
    input wire then_done,
    output reg else_go,
    input wire else_done
);
    reg selected;
    initial begin
        if (DELAY < 1) begin
            $display("ERROR: csp_branch requires DELAY >= 1");
            $finish;
        end
        done = 1'b0;
        then_go = 1'b0;
        else_go = 1'b0;
        selected = 1'b0;
        forever begin
            wait (reset_n === 1'b1);
            begin : active
                fork
                    begin
                        forever begin
                            wait (go === 1'b1);
                            selected = condition;
                            if (selected !== 1'b0 && selected !== 1'b1) begin
                                $display("ERROR: csp_branch condition contains X/Z");
                                $finish;
                            end
                            #DELAY;
                            if (selected) begin
                                then_go = 1'b1;
                                wait (then_done === 1'b1);
                                #DELAY;
                                then_go = 1'b0;
                                wait (then_done === 1'b0);
                            end else begin
                                else_go = 1'b1;
                                wait (else_done === 1'b1);
                                #DELAY;
                                else_go = 1'b0;
                                wait (else_done === 1'b0);
                            end
                            #DELAY;
                            done = 1'b1;
                            wait (go === 1'b0);
                            #DELAY;
                            done = 1'b0;
                        end
                    end
                    begin
                        wait (reset_n !== 1'b1);
                        disable active;
                    end
                join
            end
            done = 1'b0;
            then_go = 1'b0;
            else_go = 1'b0;
            selected = 1'b0;
        end
    end
endmodule

module csp_loop #(
    parameter integer DELAY = 1
) (
    input wire reset_n,
    input wire body_done,
    output reg body_go
);
    initial begin
        if (DELAY < 1) begin
            $display("ERROR: csp_loop requires DELAY >= 1");
            $finish;
        end
        body_go = 1'b0;
        forever begin
            wait (reset_n === 1'b1);
            begin : active
                fork
                    begin
                        forever begin
                            wait (body_done === 1'b0);
                            #DELAY;
                            body_go = 1'b1;
                            wait (body_done === 1'b1);
                            #DELAY;
                            body_go = 1'b0;
                            wait (body_done === 1'b0);
                        end
                    end
                    begin
                        wait (reset_n !== 1'b1);
                        disable active;
                    end
                join
            end
            body_go = 1'b0;
        end
    end
endmodule
