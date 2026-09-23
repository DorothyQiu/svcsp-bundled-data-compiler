// Behavioral EN_SEND control.  Payload retention is supplied by the bound
// structural latch bank; this controller exposes only its capture data/control
// and the raw outgoing external request.
module en_send_controller #(
    parameter integer WIDTH = 1
) (
    input  wire             enable_req,
    output reg              enable_ack,
    input  wire             enable_data,
    input  wire             body_req,
    output reg              body_ack,
    input  wire [WIDTH-1:0] body_data,
    output reg              external_raw_req,
    input  wire             external_ack,
    output wire [WIDTH-1:0] storage_data,
    output wire             storage_enable
);
    localparam IDLE = 0, WAIT_BODY = 1, ACTIVE = 2;
    integer state;
    reg enabled;

    // BODY holds its payload while body_req is asserted.  The latch closes
    // when body_ack returns low after the BODY request has returned to zero.
    assign storage_data = body_data;
    assign storage_enable = body_ack;

    initial begin
        state = IDLE;
        enabled = 1'b0;
        enable_ack = 1'b0;
        body_ack = 1'b0;
        external_raw_req = 1'b0;
    end

    always @(*) begin
        case (state)
            IDLE: begin
                if (enable_req) begin
                    enabled = enable_data;
                    enable_ack = 1'b1;
                    if (body_req) begin
                        body_ack = 1'b1;
                        if (enable_data)
                            external_raw_req = 1'b1;
                        state = ACTIVE;
                    end else begin
                        state = WAIT_BODY;
                    end
                end
            end
            WAIT_BODY: begin
                if (!enable_req)
                    enable_ack = 1'b0;
                if (body_req) begin
                    body_ack = 1'b1;
                    if (enabled)
                        external_raw_req = 1'b1;
                    state = ACTIVE;
                end
            end
            ACTIVE: begin
                if (!enable_req)
                    enable_ack = 1'b0;
                if (body_ack && !body_req)
                    body_ack = 1'b0;
                if (enabled && external_raw_req && external_ack)
                    external_raw_req = 1'b0;
                if (!enable_req && !enable_ack && !body_req && !body_ack &&
                    (!enabled || (!external_raw_req && !external_ack))) begin
                    state = IDLE;
                    enabled = 1'b0;
                end
            end
            default: begin
                state = IDLE;
                enable_ack = 1'b0;
                body_ack = 1'b0;
                external_raw_req = 1'b0;
            end
        endcase
    end
endmodule
