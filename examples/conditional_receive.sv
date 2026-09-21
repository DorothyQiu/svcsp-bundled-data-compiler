module conditional_receive(interface Control, L, R);
  bit enable;
  logic [7:0] data;
  always begin
    Control.Receive(enable);
    if (enable)
      L.Receive(data);
    else
      data = 0;
    R.Send(data);
  end
endmodule
