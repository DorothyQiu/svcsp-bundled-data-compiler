module conditional_send(interface L, R);
  reg [7:0] data;
  always begin
    L.Receive(data);
    if (data != 0) begin
      R.Send(data);
    end else begin
      data = 0;
    end
  end
endmodule
