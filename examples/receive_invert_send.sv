module receive_invert_send(Channel #(8) A, B);
  logic [7:0] a, y;

  always begin
    A.Receive(a);
    y = ~a;
    B.Send(y);
  end
endmodule
