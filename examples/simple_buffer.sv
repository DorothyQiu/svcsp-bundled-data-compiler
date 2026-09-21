module simple_buffer(interface L, R);
  logic [7:0] data;
  always begin
    L.Receive(data);
    R.Send(data);
  end
endmodule
