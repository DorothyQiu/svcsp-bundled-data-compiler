"""Binding, sizing, definite assignment and fail-closed subset regression tests."""
import json
from pathlib import Path
import unittest

from svcsp.lowering import CompileError, lower
from svcsp.parser import parse_files, parse_text

ROOT = Path(__file__).resolve().parents[1]


def compile_body(body, declarations="logic [7:0] a;", ports="interface A, B", **kwargs):
    source = f"module m({ports}); {declarations} always begin {body} end endmodule"
    return lower(parse_text(source, "test.sv"), **kwargs)


class LoweringTests(unittest.TestCase):
    def test_escaped_keyword_cannot_be_emitted_as_an_ordinary_identifier(self):
        with self.assertRaisesRegex(CompileError, "ordinary Verilog name"):
            lower(parse_text(r"module m(interface L); logic [7:0] \wire ; always L.Receive(\wire ); endmodule"))

    def test_existing_adder_binds_parallel_input_and_widened_sum(self):
        ir = lower(parse_files([ROOT / "examples/async_adder.sv"]))
        self.assertEqual(ir["channels"], {"A": {"direction": "input", "width": 8},
                                           "B": {"direction": "input", "width": 8},
                                           "SUM": {"direction": "output", "width": 9}})
        self.assertEqual(ir["body"]["body"][0]["kind"], "parallel")
        self.assertEqual(ir["body"]["body"][1]["expr"]["reads"], ["a", "b"])
        json.dumps(ir)

    def test_parameter_override_and_localparam_range(self):
        ir = lower(parse_text('''module m #(parameter int W=8)(interface A, B);
        localparam int LAST=W-1;
        logic [LAST:0] a;
        always begin A.Receive(a); B.Send(a); end endmodule'''), parameters={"W": 12})
        self.assertEqual(ir["parameters"], {"W": 12, "LAST": 11})
        self.assertEqual(ir["variables"]["a"]["width"], 12)

    def test_constant_context_matches_sv_not_python_arithmetic(self):
        ir = compile_body("A.Send(a);", "logic [8:0] a=8'hff + 8'd1;", "interface A")
        self.assertEqual(ir["variables"]["a"]["initial"], 256)
        ir = compile_body("A.Send(a);", "logic [7:0] a=8'hff + 8'd1;", "interface A")
        self.assertEqual(ir["variables"]["a"]["initial"], 0)

    def test_bit_default_zero_and_logic_requires_assignment(self):
        ir = compile_body("A.Send(a);", "bit [7:0] a;", "interface A")
        self.assertEqual(ir["variables"]["a"]["initial"], 0)
        with self.assertRaisesRegex(CompileError, "before definite initialization"):
            compile_body("A.Send(a);", ports="interface A")

    def test_parameter_signedness_and_width_preserved(self):
        ir = lower(parse_text("module m #(parameter int P=-1)(interface A); always A.Send(P); endmodule"))
        self.assertEqual(ir["parameters"]["P"], -1)
        self.assertEqual(ir["body"]["expr"]["text"], "(-32'sd1)")
        self.assertEqual(ir["channels"]["A"]["width"], 32)

    def test_expression_sizing_and_parentheses(self):
        ir = compile_body("A.Receive(a); B.Send((a+1)*2);", channel_widths={"B": 9})
        send = ir["body"]["body"][1]
        self.assertEqual(send["expr"]["text"], "((a + 1) * 2)")
        self.assertEqual(send["expr"]["width"], 32)
        self.assertEqual(ir["channels"]["B"]["width"], 9)

    def test_select_replication_concatenation_and_ternary(self):
        ir = compile_body("A.Receive(a); B.Send(a[7] ? {2{a[3:0]}} : {4'b0, a[7:4]});")
        self.assertEqual(ir["channels"]["B"]["width"], 8)
        self.assertIn("{2{a[3:0]}}", ir["body"]["body"][1]["expr"]["text"])

    def test_unary_reductions_are_one_bit(self):
        for operator in ("&", "~&", "|", "~|", "^", "~^", "^~", "!"):
            with self.subTest(operator=operator):
                ir = compile_body(f"A.Receive(a); B.Send({operator}a);")
                self.assertEqual(ir["channels"]["B"]["width"], 1)

    def test_parallel_definite_initialization_union(self):
        ir = compile_body("fork A.Receive(a); B.Receive(b); join C.Send(a+b);",
                          "logic [7:0] a,b;", "interface A, B, C")
        self.assertEqual(ir["channels"]["C"]["width"], 8)

    def test_parallel_allows_shared_reads(self):
        ir = compile_body("fork A.Send(a); B.Send(a); join", "logic [7:0] a=5;")
        self.assertEqual(ir["body"]["body"][0]["kind"], "parallel")

    def test_repeat_unrolls_and_assigns(self):
        ir = compile_body("repeat(3) A.Receive(a); B.Send(a);")
        self.assertEqual(len(ir["body"]["body"][0]["body"]), 3)

    def test_zero_repeat_does_not_initialize(self):
        with self.assertRaisesRegex(CompileError, "before definite initialization"):
            compile_body("repeat(0) A.Receive(a); B.Send(a);")

    def test_if_definite_assignment_intersects_branches(self):
        ir = compile_body("A.Receive(a); if(a) b=8'd1; else b=8'd2; B.Send(b);", "logic [7:0] a,b;")
        self.assertEqual(ir["body"]["body"][1]["kind"], "if")
        with self.assertRaisesRegex(CompileError, "before definite initialization"):
            compile_body("A.Receive(a); if(a) b=8'd1; B.Send(b);", "logic [7:0] a,b;")

    def test_parameter_and_channel_override_validation(self):
        source = "module m #(parameter W=8)(interface A); always A.Send(W); endmodule"
        for options in ({"parameters": {"BAD": 2}}, {"parameters": {"W": True}},
                        {"channel_widths": {"BAD": 8}}, {"channel_widths": {"A": 0}},
                        {"channel_widths": {"A": True}}):
            with self.subTest(options=options), self.assertRaises(CompileError):
                lower(parse_text(source), **options)
        with self.assertRaisesRegex(CompileError, "cannot be overridden"):
            lower(parse_text("module m(interface A); localparam W=8; always A.Send(W); endmodule"), parameters={"W": 9})

    def test_explicit_width_resolves_inconsistent_send_inference(self):
        body = "A.Send(8'd1); A.Send(16'd2);"
        with self.assertRaisesRegex(CompileError, "inconsistent inferred widths"):
            compile_body(body, ports="interface A")
        self.assertEqual(compile_body(body, ports="interface A", channel_widths={"A": 12})["channels"]["A"]["width"], 12)

    def test_input_override_must_match_destination(self):
        with self.assertRaisesRegex(CompileError, "receive destination width"):
            compile_body("A.Receive(a); B.Send(a);", channel_widths={"A": 16})

    def test_channel_type_named_and_inherited(self):
        ir = compile_body("A.Receive(a); B.Send(a);", ports="Channel A, B")
        self.assertEqual(set(ir["channels"]), {"A", "B"})

    def test_source_location_in_diagnostic(self):
        with self.assertRaisesRegex(CompileError, r"fault.sv:3:\d+:.*TimingControlStatement"):
            lower(parse_text("module m(interface A);\n logic a;\n always #1 A.Receive(a);\n endmodule", "fault.sv"))

    def test_top_selection_and_duplicate_module(self):
        source = "module m(interface A); always A.Send(1); endmodule\nmodule n(interface B); always B.Send(2); endmodule"
        with self.assertRaisesRegex(CompileError, "select a top"):
            lower(parse_text(source))
        self.assertEqual(lower(parse_text(source), top="n")["name"], "n")
        with self.assertRaisesRegex(CompileError, "unknown top"):
            lower(parse_text(source), top="absent")
        with self.assertRaisesRegex(CompileError, "duplicate module"):
            lower(parse_text(source.replace("module n", "module m")), top="m")

    def test_races_and_shared_channels_rejected(self):
        cases = [("fork A.Receive(a); B.Receive(a); join", "logic [7:0] a;"),
                 ("fork A.Send(a); a=8'd2; join B.Send(a);", "logic [7:0] a=0;"),
                 ("fork A.Send(a); A.Send(a); join B.Send(a);", "logic [7:0] a=0;")]
        for body, declarations in cases:
            with self.subTest(body=body), self.assertRaisesRegex(CompileError, "parallel branch conflict"):
                compile_body(body, declarations)

    def test_missing_communication_on_any_path_rejected(self):
        for body in ("a=8'd1;", "if(a) A.Send(a);", "repeat(0) A.Send(a);"):
            with self.subTest(body=body), self.assertRaisesRegex(CompileError, "every always iteration path"):
                compile_body(body, "logic [7:0] a=0;", "interface A")

    def test_unknown_values_and_unbased_literals_rejected(self):
        for literal in ("8'hxx", "8'bzz", "'0", "'1", "1/0", "'hff"):
            with self.subTest(literal=literal), self.assertRaises(CompileError):
                compile_body(f"A.Send({literal});", ports="interface A")

    def test_unsupported_statements_rejected(self):
        cases = ["#1 A.Send(a);", "@(a) A.Send(a);", "a<=8'd1; A.Send(a);",
                 "a++; A.Send(a);", "a+=1; A.Send(a);", "wait(a) A.Send(a);",
                 "while(a) A.Send(a);", "forever A.Send(a);", "for(int i=0;i<2;i++) A.Send(a);",
                 "fork A.Send(a); join_any", "fork A.Send(a); join_none",
                 "case(a) 0: A.Send(a); default: A.Send(a); endcase",
                 "begin logic t; A.Send(a); end", "begin : label1 A.Send(a); end",
                 "$display(a); A.Send(a);", "A.SplitSend(a);", "A.Peek(a);", "A.Probe();",
                 "A.Send(a, a);", "A.Send(.value(a));", "A.Receive(a[0]);", "a[0]=1'b0; A.Send(a);",
                 "repeat(257) A.Send(a);", "repeat(-1) A.Send(a);", "repeat(a) A.Send(a);"]
        for body in cases:
            with self.subTest(body=body), self.assertRaises(CompileError):
                compile_body(body, "logic [7:0] a=0;", "interface A")

    def test_unsupported_module_members_and_types_rejected(self):
        declarations = ["logic signed [7:0] a;", "integer a;", "logic [0:7] a;", "logic [7:1] a;",
                        "logic [1:0][3:0] a;", "logic a[8];", "wire a;", "const logic a=0;",
                        "logic a; initial a=0;", "logic a; always a=0;", "logic a; assign a=0;",
                        "logic a; foo child();", "logic a; task t; endtask", "logic a; function f; endfunction",
                        "(* keep=1 *) logic a;"]
        for declaration in declarations:
            with self.subTest(declaration=declaration), self.assertRaises(CompileError):
                compile_body("A.Receive(a);", declaration, "interface A")

    def test_unsupported_expressions_rejected(self):
        expressions = ["a[8]", "a[0:3]", "a[a]", "a[0+:4]", "int'(a)", "$unsigned(a)",
                       "a===0", "a**2", "a inside {0,1}", "'{a}", "a.member", "missing"]
        for expr in expressions:
            with self.subTest(expr=expr), self.assertRaises(CompileError):
                compile_body(f"A.Send({expr});", "logic [7:0] a=0;", "interface A")

    def test_reserved_duplicate_and_flattened_names_rejected(self):
        declarations = ["logic reset_n;", "logic __csp_user;", "logic [7:0] a,a;", "logic A_data;", "logic A;"]
        for decl in declarations:
            with self.subTest(decl=decl), self.assertRaises(CompileError):
                compile_body("A.Send(1);", decl, "interface A")

    def test_bidirectional_and_unused_endpoints_rejected(self):
        with self.assertRaisesRegex(CompileError, "bidirectional"):
            compile_body("A.Receive(a); A.Send(a);", ports="interface A")
        with self.assertRaisesRegex(CompileError, "unused channel"):
            compile_body("A.Send(1);")

    def test_only_channel_ansi_ports_supported(self):
        sources = ["module m(input logic A); always A.Send(1); endmodule",
                   "module m(interface .sender A); always A.Send(1); endmodule",
                   "module m(Channel.sender A); always A.Send(1); endmodule",
                   "module m(interface A[2]); always A.Send(1); endmodule",
                   "module m(A); input A; always A.Send(1); endmodule"]
        for source in sources:
            with self.subTest(source=source), self.assertRaises(CompileError):
                lower(parse_text(source))


if __name__ == "__main__":
    unittest.main()
