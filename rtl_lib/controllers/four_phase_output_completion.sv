// Generalized C-element collecting independent BODY output completions.
module four_phase_output_completion #(
  parameter integer M = 1
) (
  input  logic [M-1:0] complete,
  output logic         release
);
  initial release = 1'b0;

  // A partial four-phase reset retains release.  It may fall only after all
  // completed branches have returned low.
  always @(*) begin
    if (&complete)
      release = 1'b1;
    else if (~|complete)
      release = 1'b0;
  end
endmodule
