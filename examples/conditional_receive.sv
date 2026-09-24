module conditional_receive(input logic sel, input logic [7:0] fallback,
                           Channel #(8) L, R);
  logic [7:0] data;
  always begin
    if (sel)
      L.Receive(data);
    else
      data = fallback;
    R.Send(data);
  end
endmodule
