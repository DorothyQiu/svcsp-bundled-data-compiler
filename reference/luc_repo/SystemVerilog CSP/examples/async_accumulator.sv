// Illustrates state, branch selection, and two successive sends on one port.
// An initializer supplies the value restored by the compiled reset_n input.
module async_accumulator #(parameter int WIDTH=8)(interface L, interface R);
    logic [WIDTH-1:0] x;
    logic [WIDTH-1:0] total=0;
    always begin
        L.Receive(x);
        if (x[0])
            total = total + x;
        else
            total = x;
        repeat (2) R.Send(total);
    end
endmodule
