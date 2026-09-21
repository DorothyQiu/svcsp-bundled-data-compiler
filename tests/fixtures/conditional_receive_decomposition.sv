module conditional_receive_decomposition(Channel Control, Channel L, Channel R);
  logic enable;
  logic [7:0] data;

  always begin
    Control.Receive(enable);
    if (enable)
      L.Receive(data);
    else
      data = data ^ data;
    R.Send(data);
  end
endmodule
