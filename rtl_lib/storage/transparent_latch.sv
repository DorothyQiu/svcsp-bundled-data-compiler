// MVP transparent bundled-data storage element.
module transparent_latch #(
    parameter integer WIDTH = 1
) (
    input  wire [WIDTH-1:0] data_in,
    input  wire             en,
    output reg  [WIDTH-1:0] data_out
);
    initial data_out = {WIDTH{1'b0}};

    always @ (data_in or en) begin
        if (en)
            data_out <= data_in;
    end
endmodule

// Phase 7A's abstract-storage template identity bound to the MVP transparent
// latch. Phase 7A binds WIDTH explicitly for every generated storage instance.
module abstract_storage #(
    parameter integer WIDTH = 1
) (
    input  wire [WIDTH-1:0] data_in,
    output wire [WIDTH-1:0] data_out,
    input  wire             control_in,
    output wire             control_out
);
    transparent_latch #(.WIDTH(WIDTH)) storage (
        .data_in(data_in),
        .en(control_in),
        .data_out(data_out)
    );

    assign control_out = control_in;
endmodule
