module receive_add_send(input logic [7:0] c, Channel #(8) A, B);
  logic [7:0] a, b;

  always begin
    A.Receive(a);
    b = a + c;
    B.Send(b);
  end
endmodule
