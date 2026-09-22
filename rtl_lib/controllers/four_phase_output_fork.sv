// Stateless four-phase launch fanout for independent BODY output handshakes.
module four_phase_output_fork #(
  parameter integer M = 1
) (
  input  logic         stage_active,
  output logic [M-1:0] launch
);
  assign launch = {M{stage_active}};
endmodule
