// Conditional receive micropipeline stage.  It consumes the enable token
// first, then always supplies one BODY token.  The disabled BODY value is the
// verification-model dummy payload of zero.
module en_receive_stage #(
    parameter integer WIDTH = 1
) (
    input  wire             enable_req,
    output reg              enable_ack,
    input  wire             enable_data,
    input  wire             external_req,
    output reg              external_ack,
    input  wire [WIDTH-1:0] external_data,
    output reg              body_req,
    input  wire             body_ack,
    output reg  [WIDTH-1:0] body_data
);
    localparam IDLE = 0, WAIT_EXTERNAL = 1, WAIT_BODY = 2;
    integer state;
    reg enabled;

    initial begin
        state = IDLE;
        enabled = 1'b0;
        enable_ack = 1'b0;
        external_ack = 1'b0;
        body_req = 1'b0;
        body_data = {WIDTH{1'b0}};
    end

    always @(*) begin
        case (state)
            IDLE: begin
                if (enable_req) begin
                    enabled = enable_data;
                    enable_ack = 1'b1;
                    if (enable_data) begin
                        state = WAIT_EXTERNAL;
                    end else begin
                        body_data = {WIDTH{1'b0}};
                        body_req = 1'b1;
                        state = WAIT_BODY;
                    end
                end
            end
            WAIT_EXTERNAL: begin
                if (!enable_req)
                    enable_ack = 1'b0;
                if (external_req) begin
                    body_data = external_data;
                    external_ack = 1'b1;
                    body_req = 1'b1;
                    state = WAIT_BODY;
                end
            end
            WAIT_BODY: begin
                if (!enable_req)
                    enable_ack = 1'b0;
                if (enabled && external_ack && !external_req)
                    external_ack = 1'b0;
                if (body_req && body_ack)
                    body_req = 1'b0;
                if (!enable_req && !enable_ack && !body_req && !body_ack &&
                    (!enabled || (!external_req && !external_ack))) begin
                    state = IDLE;
                    enabled = 1'b0;
                end
            end
            default: begin
                state = IDLE;
                enable_ack = 1'b0;
                external_ack = 1'b0;
                body_req = 1'b0;
            end
        endcase
    end
endmodule
