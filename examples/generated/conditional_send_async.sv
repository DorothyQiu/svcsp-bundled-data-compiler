module conditional_send (
    input logic reset_n,
    input logic channel_L_receive_request,
    output logic channel_L_receive_acknowledge,
    input logic [7:0] channel_L_receive_payload,
    output logic channel_R_send_request,
    input logic channel_R_send_acknowledge,
    output logic [7:0] channel_R_send_payload
);
  logic [7:0] body_var_0_data;
  logic body_var_1_sel;
  logic input_0_body_req;
  logic input_0_body_ack;
  logic [7:0] receive_value_0;
  logic base_Lreq;
  logic base_Lack;
  logic base_raw_Rreq;
  logic base_Rack;
  logic storage_enable;
  logic [7:0] storage_0_data_in;
  logic [7:0] storage_0_data_out;
  logic output_0_raw_req;
  logic output_0_req;
  logic output_0_ack;
  logic output_0_external_raw_req;
  logic [7:0] en_send_storage_0_data_in;
  logic en_send_storage_0_storage_enable;
  logic enable_channel_0_value;
  logic enable_channel_0_req;
  logic enable_channel_0_ack;
  logic enable_channel_0_data;

  assign input_0_body_req = channel_L_receive_request;
  assign channel_L_receive_acknowledge = input_0_body_ack;
  assign receive_value_0 = channel_L_receive_payload;
  assign base_Lreq = input_0_body_req;
  assign input_0_body_ack = base_Lack;
  assign storage_0_data_in = body_var_0_data;
  assign output_0_raw_req = base_raw_Rreq;
  assign base_Rack = output_0_ack;
  assign enable_channel_0_value = !(!(body_var_1_sel));

  always_comb begin
    body_var_0_data = 'x;
    body_var_1_sel = 'x;
    body_var_0_data = receive_value_0;
    body_var_1_sel = body_var_0_data[0];
    if (body_var_1_sel) begin
    end else begin
    end
  end

  four_phase_enable_sender enable_sender_0 (
    .value(enable_channel_0_value),
    .launch(storage_enable),
    .req(enable_channel_0_req),
    .ack(enable_channel_0_ack),
    .data(enable_channel_0_data)
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

  // en_send_controller en_send_controller_0
  en_send_controller #(
    .WIDTH(8)
  ) en_send_controller_0 (
    .enable_req(enable_channel_0_req),
    .enable_ack(enable_channel_0_ack),
    .enable_data(enable_channel_0_data),
    .body_req(output_0_req),
    .body_ack(output_0_ack),
    .body_data(storage_0_data_out),
    .external_raw_req(output_0_external_raw_req),
    .external_ack(channel_R_send_acknowledge),
    .storage_data(en_send_storage_0_data_in),
    .storage_enable(en_send_storage_0_storage_enable)
  );

  // bundled_data_latch_bank en_send_storage_0
  bundled_data_latch_bank #(
    .WIDTH(8)
  ) en_send_storage_0 (
    .data_in(en_send_storage_0_data_in),
    .storage_enable(en_send_storage_0_storage_enable),
    .data_out(channel_R_send_payload)
  );

  bundled_data_matched_delay en_send_matched_delay_0 (
    .control_in(output_0_external_raw_req),
    .control_out(channel_R_send_request)
  );

  bundled_data_matched_delay matched_delay_0 (
    .control_in(output_0_raw_req),
    .control_out(output_0_req)
  );
endmodule
