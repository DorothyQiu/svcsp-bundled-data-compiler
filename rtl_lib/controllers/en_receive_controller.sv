// Behavioral EN_RECV control.  Payload retention is supplied by the bound
// structural latch bank; this controller exposes only its capture data/control
// and the raw outgoing BODY request.
module en_receive_controller #(
    parameter integer WIDTH = 1
) (
    input  wire             enable_req,
    output reg              enable_ack,
    input  wire             enable_data,
    input  wire             external_req,
    output reg              external_ack,
    input  wire [WIDTH-1:0] external_data,
    output reg              body_raw_req,
    input  wire             body_ack,
    output wire [WIDTH-1:0] storage_data,
    output wire             storage_enable
);
    localparam IDLE = 0, WAIT_EXTERNAL = 1, WAIT_BODY = 2;
    integer state;
    reg enabled;

    // The external sender holds data while its request is asserted.  The
    // disabled path deliberately captures the documented dummy payload.
    assign storage_data = enabled ? external_data : {WIDTH{1'b0}};
    assign storage_enable = body_raw_req &&
                            (!enabled || (external_req && external_ack));

    initial begin
        state = IDLE;
        enabled = 1'b0;
        enable_ack = 1'b0;
        external_ack = 1'b0;
        body_raw_req = 1'b0;
    end

    always @(*) begin
        case (state)
            IDLE: begin
                if (enable_req) begin
                    enabled = enable_data;
                    enable_ack = 1'b1;
                    if (enable_data) begin
                        if (external_req) begin
                            external_ack = 1'b1;
                            body_raw_req = 1'b1;
                            state = WAIT_BODY;
                        end else begin
                            state = WAIT_EXTERNAL;
                        end
                    end else begin
                        body_raw_req = 1'b1;
                        state = WAIT_BODY;
                    end
                end
            end
            WAIT_EXTERNAL: begin
                if (!enable_req)
                    enable_ack = 1'b0;
                if (external_req) begin
                    external_ack = 1'b1;
                    body_raw_req = 1'b1;
                    state = WAIT_BODY;
                end
            end
            WAIT_BODY: begin
                if (!enable_req)
                    enable_ack = 1'b0;
                if (enabled && external_ack && !external_req)
                    external_ack = 1'b0;
                if (body_raw_req && body_ack)
                    body_raw_req = 1'b0;
                if (!enable_req && !enable_ack && !body_raw_req && !body_ack &&
                    (!enabled || (!external_req && !external_ack))) begin
                    state = IDLE;
                    enabled = 1'b0;
                end
            end
            default: begin
                state = IDLE;
                enable_ack = 1'b0;
                external_ack = 1'b0;
                body_raw_req = 1'b0;
            end
        endcase
    end
endmodule
