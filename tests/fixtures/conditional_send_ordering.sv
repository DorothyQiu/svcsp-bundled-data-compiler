module conditional_send_ordering(Channel L, Channel R, Channel S);
  logic [7:0] x;

  always begin
    L.Receive(x);
    if (x[0])
      R.Send(x);
    S.Send(x);
  end
endmodule
