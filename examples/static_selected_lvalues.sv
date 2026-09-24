module static_selected_lvalues(Channel #(4) A, B);
  logic [7:0] value;

  always begin
    A.Receive(value[3:0]);
    value[7:4] = value[3:0];
    B.Send(value[7:4]);
  end
endmodule
