module conditional_send_decomposition(Channel L, Channel R);
  logic [7:0] data;

  always begin
    L.Receive(data);
    if (data[0])
      R.Send(data);
  end
endmodule
