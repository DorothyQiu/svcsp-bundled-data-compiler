module receive_invert_send (
    input logic channel_A_receive_request,
    output logic channel_A_receive_acknowledge,
    input logic [7:0] channel_A_receive_payload,
    output logic channel_B_send_request,
    input logic channel_B_send_acknowledge,
    output logic [7:0] channel_B_send_payload
);
  logic [7:0] body_var_0_a;
  logic [7:0] body_var_1_y;
  logic input_join_control;
  logic [7:0] storage_0_data;
  logic stage_complete;
  logic input_join_req;
  logic input_join_ack;
  logic [7:0] storage_0_data_in;
  logic [7:0] storage_0_data_out;
  logic output_fork_launch;
  logic output_fork_complete;
  logic output_0_raw_launch;
  logic output_0_launch;
  logic output_0_complete;

  assign input_join_req = channel_A_receive_request;
  assign channel_A_receive_acknowledge = input_join_ack;
  assign body_var_0_a = channel_A_receive_payload;
  assign storage_0_data_in = body_var_1_y;
  assign body_var_1_y = (~body_var_0_a);
  // source: assign combinational_1_value = (~body_var_0_a);
  assign channel_B_send_request = output_0_launch;
  assign channel_B_send_payload = storage_0_data_out;
  assign output_0_complete = channel_B_send_acknowledge;
  assign output_0_raw_launch = output_fork_launch;
  assign output_fork_complete = output_0_complete;

  // four_phase_input_join input_join
  four_phase_input_join #(
    .N(1)
  ) input_join (
    .input_req(input_join_req),
    .input_ack(input_join_ack),
    .stage_release(stage_complete),
    .stage_active(input_join_control)
  );

  // bundled_data_storage storage_0
  bundled_data_storage #(
    .WIDTH(8)
  ) storage_0 (
    .data_in(storage_0_data_in),
    .data_out(storage_0_data_out),
    .capture(input_join_control),
    .stage_release(stage_complete)
  );

  // four_phase_output_fork output_fork
  four_phase_output_fork #(
    .M(1)
  ) output_fork (
    .stage_active(input_join_control),
    .launch(output_fork_launch)
  );

  // four_phase_output_completion output_completion
  four_phase_output_completion #(
    .M(1)
  ) output_completion (
    .complete(output_fork_complete),
    .stage_release(stage_complete)
  );

  bundled_data_matched_delay matched_delay_0 (
    .control_in(output_0_raw_launch),
    .control_out(output_0_launch)
  );
endmodule
