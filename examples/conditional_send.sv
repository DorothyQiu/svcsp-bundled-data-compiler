module conditional_send(input logic sel, Channel #(8) L, R);
  logic [7:0] data;
  always begin
    L.Receive(data);
    if (sel) begin
      R.Send(data);
    end
  end
endmodule
