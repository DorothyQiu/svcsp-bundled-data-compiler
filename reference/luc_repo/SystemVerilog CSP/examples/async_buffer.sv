module async_buffer #(parameter int WIDTH = 8) (interface L, interface R);
    logic [WIDTH-1:0] data;
    always begin
        L.Receive(data);
        R.Send(data);
    end
endmodule
