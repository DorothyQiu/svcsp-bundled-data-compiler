// One half-buffered four-phase sender for a concrete EnableChannel token.
// Behavioral state is intentional: this is a controller model, not a
// gate-level C-element implementation.
module four_phase_enable_sender (
    input  wire value,
    input  wire launch,
    output reg  req,
    input  wire ack,
    output reg  data
);
    localparam IDLE = 0, ACTIVE = 1, REARM = 2;
    integer state;

    initial begin
        state = IDLE;
        req = 1'b0;
        data = 1'b0;
    end

    always @(*) begin
        case (state)
            IDLE: begin
                if (launch && !ack) begin
                    data = value;
                    req = 1'b1;
                    state = ACTIVE;
                end
            end
            ACTIVE: begin
                if (ack && !launch) begin
                    req = 1'b0;
                    state = REARM;
                end
            end
            REARM: begin
                if (!ack && !launch)
                    state = IDLE;
            end
            default: begin
                state = IDLE;
                req = 1'b0;
            end
        endcase
    end
endmodule
