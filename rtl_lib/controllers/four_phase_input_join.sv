// Four-phase join for the independent BODY input handshakes of one stage.
module four_phase_input_join #(
  parameter integer N = 1
) (
  input  logic [N-1:0] input_req,
  output logic [N-1:0] input_ack,
  input  logic         stage_release,
  output logic         stage_active
);
  initial stage_active = 1'b0;

  // This is the generalized C-element state relation.  Mixed request/reset
  // states intentionally retain the active state until the four-phase cycle
  // is complete and the downstream stage releases it.
  always @(*) begin
    if (&input_req && !stage_release)
      stage_active = 1'b1;
    else if (~|input_req && stage_release)
      stage_active = 1'b0;
  end

  assign input_ack = {N{stage_active}};
endmodule
