module receive_add_send(Channel #(8) A, B);
  logic [7:0] a, b, c;

  always begin
    A.Receive(a);
    b = a + c;
    B.Send(b);
  end
endmodule
