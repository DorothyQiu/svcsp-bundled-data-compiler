// Conditional send micropipeline stage.  It captures and completes the BODY
// transfer before any enabled external transfer has completed, making this a
// half-buffer between BODY and the external channel.
module en_send_stage #(
    parameter integer WIDTH = 1
) (
    input  wire             enable_req,
    output reg              enable_ack,
    input  wire             enable_data,
    input  wire             body_req,
    output reg              body_ack,
    input  wire [WIDTH-1:0] body_data,
    output reg              external_req,
    input  wire             external_ack,
    output reg  [WIDTH-1:0] external_data
);
    localparam IDLE = 0, WAIT_BODY = 1, ACTIVE = 2;
    integer state;
    reg enabled;

    initial begin
        state = IDLE;
        enabled = 1'b0;
        enable_ack = 1'b0;
        body_ack = 1'b0;
        external_req = 1'b0;
        external_data = {WIDTH{1'b0}};
    end

    always @(*) begin
        case (state)
            IDLE: begin
                if (enable_req) begin
                    enabled = enable_data;
                    enable_ack = 1'b1;
                    if (body_req) begin
                        external_data = body_data;
                        body_ack = 1'b1;
                        if (enable_data)
                            external_req = 1'b1;
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
                    external_data = body_data;
                    body_ack = 1'b1;
                    if (enabled)
                        external_req = 1'b1;
                    state = ACTIVE;
                end
            end
            ACTIVE: begin
                if (!enable_req)
                    enable_ack = 1'b0;
                if (body_ack && !body_req)
                    body_ack = 1'b0;
                if (enabled && external_req && external_ack)
                    external_req = 1'b0;
                if (!enable_req && !enable_ack && !body_req && !body_ack &&
                    (!enabled || (!external_req && !external_ack))) begin
                    state = IDLE;
                    enabled = 1'b0;
                end
            end
            default: begin
                state = IDLE;
                enable_ack = 1'b0;
                body_ack = 1'b0;
                external_req = 1'b0;
            end
        endcase
    end
endmodule
