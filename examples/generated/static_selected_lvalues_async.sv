module static_selected_lvalues (
    input logic reset_n,
    input logic channel_A_receive_request,
    output logic channel_A_receive_acknowledge,
    input logic [3:0] channel_A_receive_payload,
    output logic channel_B_send_request,
    input logic channel_B_send_acknowledge,
    output logic [3:0] channel_B_send_payload
);
  logic [7:0] body_var_0_value;
  logic input_0_body_req;
  logic input_0_body_ack;
  logic [3:0] receive_value_0;
  logic base_Lreq;
  logic base_Lack;
  logic base_raw_Rreq;
  logic base_Rack;
  logic storage_enable;
  logic [3:0] storage_0_data_in;
  logic [3:0] storage_0_data_out;
  logic output_0_raw_req;
  logic output_0_req;
  logic output_0_ack;

  assign input_0_body_req = channel_A_receive_request;
  assign channel_A_receive_acknowledge = input_0_body_ack;
  assign receive_value_0 = channel_A_receive_payload;
  assign base_Lreq = input_0_body_req;
  assign input_0_body_ack = base_Lack;
  assign storage_0_data_in = body_var_0_value[7:4];
  assign channel_B_send_request = output_0_req;
  assign output_0_ack = channel_B_send_acknowledge;
  assign channel_B_send_payload = storage_0_data_out;
  assign output_0_raw_req = base_raw_Rreq;
  assign base_Rack = output_0_ack;

  always_comb begin
    body_var_0_value = 'x;
    body_var_0_value[3:0] = receive_value_0;
    body_var_0_value[7:4] = body_var_0_value[3:0];
  end

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
    .WIDTH(4)
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
