module fork_join_adder(interface A, B, R);
  logic [7:0] a, b, sum;
  always begin
    fork
      A.Receive(a);
      B.Receive(b);
    join
    sum = a + b;
    R.Send(sum);
  end
endmodule
