module fork_join_adder (
    input logic reset_n,
    input logic channel_A_receive_request,
    output logic channel_A_receive_acknowledge,
    input logic [7:0] channel_A_receive_payload,
    input logic channel_B_receive_request,
    output logic channel_B_receive_acknowledge,
    input logic [7:0] channel_B_receive_payload,
    output logic channel_R_send_request,
    input logic channel_R_send_acknowledge,
    output logic [7:0] channel_R_send_payload
);
  logic [7:0] body_var_0_a;
  logic [7:0] body_var_1_b;
  logic [7:0] body_var_2_sum;
  logic input_0_body_req;
  logic input_0_body_ack;
  logic [7:0] receive_value_0;
  logic input_1_body_req;
  logic input_1_body_ack;
  logic [7:0] receive_value_1;
  logic base_Lreq;
  logic base_Lack;
  logic base_raw_Rreq;
  logic base_Rack;
  logic storage_enable;
  logic [1:0] input_request_vector;
  logic [1:0] input_ack_vector;
  logic [7:0] storage_0_data_in;
  logic [7:0] storage_0_data_out;
  logic output_0_raw_req;
  logic output_0_req;
  logic output_0_ack;

  assign input_0_body_req = channel_A_receive_request;
  assign channel_A_receive_acknowledge = input_0_body_ack;
  assign receive_value_0 = channel_A_receive_payload;
  assign input_1_body_req = channel_B_receive_request;
  assign channel_B_receive_acknowledge = input_1_body_ack;
  assign receive_value_1 = channel_B_receive_payload;
  assign input_request_vector = {input_1_body_req, input_0_body_req};
  assign input_0_body_ack = input_ack_vector[0];
  assign input_1_body_ack = input_ack_vector[1];
  assign storage_0_data_in = body_var_2_sum;
  assign channel_R_send_request = output_0_req;
  assign output_0_ack = channel_R_send_acknowledge;
  assign channel_R_send_payload = storage_0_data_out;
  assign output_0_raw_req = base_raw_Rreq;
  assign base_Rack = output_0_ack;

  always_comb begin
    body_var_0_a = 'x;
    body_var_1_b = 'x;
    body_var_2_sum = 'x;
    body_var_0_a = receive_value_0;
    body_var_1_b = receive_value_1;
    body_var_2_sum = (body_var_0_a + body_var_1_b);
  end

  // four_phase_request_join request_join
  four_phase_request_join #(
    .N(2)
  ) request_join (
    .reset_n(reset_n),
    .input_req(input_request_vector),
    .base_Lreq(base_Lreq)
  );

  // four_phase_ack_fanout input_ack_fanout
  four_phase_ack_fanout #(
    .N(2)
  ) input_ack_fanout (
    .base_Lack(base_Lack),
    .input_ack(input_ack_vector)
  );

  four_phase_half_buffer_controller base_controller (
    .reset_n(reset_n),
    .base_Lreq(base_Lreq),
    .base_Rack(base_Rack),
    .base_Lack(base_Lack),
    .base_raw_Rreq(base_raw_Rreq),
    .storage_enable(storage_enable)
  );

  // bundled_data_latch_bank storage_0
  bundled_data_latch_bank #(
    .WIDTH(8)
  ) storage_0 (
    .data_in(storage_0_data_in),
    .data_out(storage_0_data_out),
    .storage_enable(storage_enable)
  );

  bundled_data_matched_delay matched_delay_0 (
    .control_in(output_0_raw_req),
    .control_out(output_0_req)
  );
endmodule
