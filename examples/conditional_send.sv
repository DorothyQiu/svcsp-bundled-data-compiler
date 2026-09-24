module conditional_send(Channel #(8) L, R);
  logic [7:0] data;
  logic sel;

  always begin
    L.Receive(data);
    sel = data[0];
    if (sel) begin
      R.Send(data);
    end
  end
endmodule
