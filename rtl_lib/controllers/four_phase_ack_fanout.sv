// Structural return path from base_Lack to every participating BODY input.
module four_phase_ack_fanout #(
    parameter integer N = 2
) (
    input  wire         base_Lack,
    output wire [N-1:0] input_ack
);
    assign input_ack = {N{base_Lack}};
endmodule
