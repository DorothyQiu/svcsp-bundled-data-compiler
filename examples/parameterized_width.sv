module parameterized_width #(parameter int W = 8) (Channel #(W) A, B);
  logic [W-1:0] value;

  always begin
    A.Receive(value);
    B.Send(value);
  end
endmodule
