// Behavioral asynchronous CSP: two independent blocking receives, followed
// by one blocking send. No global clock is assumed.
module async_adder #(
    parameter int WIDTH = 8
) (
    interface A,
    interface B,
    interface SUM
);
    logic [WIDTH-1:0] a, b;
    logic [WIDTH:0] sum;

    always begin
        fork
            A.Receive(a);
            B.Receive(b);
        join
        sum = {1'b0, a} + {1'b0, b};
        SUM.Send(sum);
    end
endmodule
