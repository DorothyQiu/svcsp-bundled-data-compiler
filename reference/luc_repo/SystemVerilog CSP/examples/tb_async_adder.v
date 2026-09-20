`timescale 1ns/1ps
module tb;
    reg reset_n=0;
    reg [7:0] A_data=0, B_data=0;
    reg A_req=0, B_req=0, SUM_ack=0;
    wire A_ack, B_ack, SUM_req;
    wire [8:0] SUM_data;

    async_adder_structural dut(
        .reset_n(reset_n),
        .A_data(A_data), .A_req(A_req), .A_ack(A_ack),
        .B_data(B_data), .B_req(B_req), .B_ack(B_ack),
        .SUM_data(SUM_data), .SUM_req(SUM_req), .SUM_ack(SUM_ack)
    );

    task send_A;
        input [7:0] value;
        begin
            A_data=value; #2; A_req=1;
            wait(A_ack===1'b1); #3; A_req=0;
            wait(A_ack===1'b0);
        end
    endtask
    task send_B;
        input [7:0] value;
        begin
            B_data=value; #2; B_req=1;
            wait(B_ack===1'b1); #5; B_req=0;
            wait(B_ack===1'b0);
        end
    endtask
    task expect_sum;
        input [8:0] value;
        integer cycle;
        begin
            wait(SUM_req===1'b1);
            // Exercise output backpressure while requiring stable data.
            for(cycle=0; cycle<13; cycle=cycle+1) begin
                #1;
                if(SUM_req!==1'b1 || SUM_data!==value) begin
                    $display("FAIL: expected %d, got %d", value, SUM_data);
                    $finish;
                end
            end
            SUM_ack=1; wait(SUM_req===1'b0); #3; SUM_ack=0;
            $display("received sum %d", value);
        end
    endtask

    initial begin
        #5; reset_n=1;
        fork
            begin send_A(9); send_A(255); end
            begin #19; send_B(13); #7; send_B(255); end
            begin expect_sum(22); expect_sum(510); end
        join
        #10; $display("PASS: structural asynchronous adder"); $finish;
    end
    initial begin
        #10000; $display("FAIL: timeout"); $finish;
    end
endmodule
