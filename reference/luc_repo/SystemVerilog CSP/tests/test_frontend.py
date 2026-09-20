import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

from svcsp import ParseError, analyze_channels, parse_files, parse_text
from svcsp.ast import walk

ROOT = Path(__file__).resolve().parents[1]


def report(source, types=("Channel",)):
    return analyze_channels(parse_text(source), types)["modules"][0]


def directions(result):
    return {item["name"]: item["direction"] for item in result["channels"]}


class AstTests(unittest.TestCase):
    def test_adder_preserves_concurrency_and_dataflow(self):
        ast = parse_files([ROOT / "examples/async_adder.sv"])
        nodes = list(walk(ast))
        parallel = next(n for n in nodes if n.kind == "Parallel")
        self.assertEqual(parallel.get("join"), "join")
        self.assertEqual(len(parallel.get("body")), 2)
        self.assertEqual(len([n for n in nodes if n.kind == "Call"]), 3)
        self.assertIn("AddExpression", [n.kind for n in nodes])
        # Output is a regular portable data structure, not native parser handles.
        json.dumps(ast.to_dict())

    def test_precedence(self):
        ast = parse_text("module m; int x; initial x=(1+2)*3; endmodule")
        product = next(n for n in walk(ast) if n.kind == "MultiplyExpression")
        self.assertEqual(product.get("left").kind, "AddExpression")

    def test_join_modes_are_distinct(self):
        for join in ["join", "join_any", "join_none"]:
            ast = parse_text(f"module m; initial fork ; ; {join} endmodule")
            node = next(n for n in walk(ast) if n.kind == "Parallel")
            self.assertEqual(node.get("join"), join)

    def test_error_has_source_location(self):
        with self.assertRaises(ParseError) as caught:
            parse_text("module m(interface L);\n initial L.Receive(x)\nendmodule", "broken.sv")
        self.assertIn("broken.sv:2", str(caught.exception))

    def test_include_macro_and_conditional_preprocessing(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory)
            include = path / "include"
            include.mkdir()
            (include / "defs.svh").write_text('`define RECEIVE(ch, x) ch.Receive(x)\n')
            (path / "design.sv").write_text('''`include "defs.svh"
module m(interface L, interface R);
logic x;
always begin
`RECEIVE(L, x);
`ifdef UNUSED
R.Receive(x);
`else
R.Send(x);
`endif
end
endmodule
''')
            ast = parse_files([path / "design.sv"], [include])
            self.assertEqual(directions(analyze_channels(ast)["modules"][0]),
                             {"L": "input", "R": "output"})

    def test_missing_include_fails(self):
        with self.assertRaises(ParseError):
            parse_text('`include "does_not_exist.svh"\nmodule m; endmodule')

    def test_multiple_files(self):
        ast = parse_files([ROOT / "examples/async_adder.sv", ROOT / "examples/async_buffer.sv"])
        self.assertEqual([m["module"] for m in analyze_channels(ast)["modules"]],
                         ["async_adder", "async_buffer"])

    def test_parameter_width_initializers_and_named_arguments_survive(self):
        ast = parse_text('''module m #(parameter WIDTH=8)(interface R);
logic [WIDTH-1:0] data = '0;
initial R.Send(.value(data[0 +: 4]));
endmodule''')
        nodes = list(walk(ast))
        parameter = next(n for n in nodes if n.kind == "ParameterDeclaration")
        self.assertEqual(parameter.get("declarators")[0].get("name"), "WIDTH")
        variable = next(n for n in nodes if n.kind == "DataDeclaration")
        self.assertEqual(variable.get("declarators")[0].get("initializer").get("literal"), "'0")
        self.assertIn("SubtractExpression", {n.kind for n in nodes})
        argument = next(n for n in nodes if n.kind == "NamedArgument")
        self.assertEqual(argument.get("name"), "value")
        selection = next(n for n in nodes if n.kind == "AscendingRangeSelect")
        self.assertEqual(selection.get("range"), "+:")

    def test_escaped_identifiers_keep_semantic_spelling(self):
        ast = parse_text("module \\module.name (interface \\input.port ); initial \\input.port .Receive(x); endmodule")
        module = next(n for n in walk(ast) if n.kind == "Module")
        self.assertEqual(module.get("name"), "module.name")
        self.assertEqual(directions(analyze_channels(ast)["modules"][0]), {"input.port": "input"})

    def test_implicit_task_invocations_are_calls_but_member_values_are_not(self):
        ast = parse_text("module m(interface L); int x; initial begin helper; L.Send; $finish; x=L.status; end endmodule")
        calls = [n for n in walk(ast) if n.kind == "Call"]
        self.assertEqual(len(calls), 3)
        self.assertEqual([call.get("arguments") for call in calls], [[], [], []])
        self.assertEqual(calls[0].get("callee").get("name"), "helper")
        self.assertEqual(calls[1].get("callee").kind, "Member")
        self.assertEqual(calls[2].get("callee").kind, "SystemName")


class ChannelTests(unittest.TestCase):
    def test_buffer_and_locations(self):
        result = report("module m(interface L, interface R);\nlogic x;\nalways begin\nL.Receive(x);\nR.Send(x);\nend\nendmodule")
        self.assertEqual(directions(result), {"L": "input", "R": "output"})
        self.assertTrue(result["analysis_complete"])
        self.assertEqual(result["channels"][0]["uses"][0]["location"]["line"], 4)

    def test_declared_channels_only_and_no_comment_string_matches(self):
        result = report('''module m(interface L, input logic clk);
// L.Send(x);
initial begin $display("L.Receive(x)"); object.Send(x); end
endmodule''')
        self.assertEqual(directions(result), {"L": "unknown"})
        self.assertIn("unresolved_receiver", [d["code"] for d in result["diagnostics"]])

    def test_unused_and_probe_do_not_invent_direction(self):
        result = report("module m(interface L, R); initial wait(L.Probe()); endmodule")
        self.assertEqual(directions(result), {"L": "unknown", "R": "unknown"})
        self.assertEqual(result["channels"][0]["uses"][0]["role"], "observe")

    def test_splits_peek_and_bidirectional(self):
        result = report('''module m(interface L, R, C);
initial begin L.SplitReceive(x,1); R.SplitSend(x,1); C.Peek(x); C.Send(x); end
endmodule''')
        self.assertEqual(directions(result), {"L": "input", "R": "output", "C": "bidirectional"})

    def test_typed_ports_inheritance_modports_and_arrays(self):
        result = report('''module m(Channel A, B, Channel.rx C[2], input logic clk, other);
initial begin A.Receive(x); B.Send(x); C[1].Receive(x); end endmodule''')
        self.assertEqual(directions(result), {"A": "input", "B": "output", "C": "input"})
        self.assertEqual(result["channels"][2]["modport"], "rx")
        self.assertTrue(result["channels"][2]["dimensions"])

    def test_array_direction_aggregates_elements(self):
        result = report("module m(interface C[2]); initial begin C[0].Receive(x); C[1].Send(x); end endmodule")
        self.assertEqual(directions(result), {"C": "bidirectional"})

    def test_internal_endpoint_scope(self):
        result = report("module m; Channel #(.WIDTH(8)) mid(), unused(); initial mid.Send(x); endmodule")
        self.assertEqual(directions(result), {"mid": "output", "unused": "unknown"})
        self.assertEqual(result["channels"][0]["scope"], "internal")

    def test_case_loops_conditionals_and_delay(self):
        result = report('''module m(interface A, B, R); int x;
always begin
forever begin
case(x)
0: A.Receive(x);
default: begin repeat(2) B.Receive(x); end
endcase
if (x) #2 R.Send(x); else while (x) R.Send(x-1);
end
end endmodule''')
        self.assertEqual(directions(result), {"A": "input", "B": "input", "R": "output"})

    def test_shadowed_names_are_not_module_endpoints(self):
        result = report("module m(interface L); initial begin int L; L.Send(x); end endmodule")
        self.assertEqual(directions(result), {"L": "unknown"})
        self.assertFalse(result["analysis_complete"])

    def test_loop_variable_shadowing(self):
        for statement in ["for(int L=0; L<2; L++) L.Send(x);", "foreach(a[L]) L.Send(x);"]:
            result = report(f"module m(interface L); initial {statement} endmodule")
            self.assertEqual(directions(result), {"L": "unknown"})
            self.assertFalse(result["analysis_complete"])

    def test_nonvariable_block_declarations_shadow_endpoints(self):
        declarations = [
            "typedef int L;",
            "localparam L=1;",
            "typedef enum {L, OTHER} state_t;",
            "enum {L, OTHER} state;",
            "import p::L;",
        ]
        for declaration in declarations:
            with self.subTest(declaration=declaration):
                result = report(f"module m(interface L); initial begin {declaration} L.Send(x); end endmodule")
                self.assertEqual(directions(result), {"L": "unknown"})
                self.assertIn("unresolved_receiver", [d["code"] for d in result["diagnostics"]])

    def test_unbound_block_names_cannot_supply_channel_evidence(self):
        cases = [
            ("begin : L L.Send(x); end", "unresolved_receiver"),
            ("begin begin : L end L.Send(x); end", "unresolved_receiver"),
            ("begin import p::*; L.Send(x); end", "wildcard_import_not_resolved"),
            ("begin enum {L[2]} state; L0.Send(x); end", "enum_names_not_elaborated"),
        ]
        for statement, diagnostic in cases:
            with self.subTest(statement=statement):
                result = report(f"module m(interface L, L0); initial {statement} endmodule")
                self.assertEqual(directions(result), {"L": "unknown", "L0": "unknown"})
                self.assertIn(diagnostic, [d["code"] for d in result["diagnostics"]])

    def test_shadowing_does_not_escape_its_block(self):
        result = report('''module m(interface L); initial begin
begin typedef int L; L.Send(x); end
L.Receive(x);
end endmodule''')
        self.assertEqual(directions(result), {"L": "input"})
        self.assertEqual(len(result["channels"][0]["uses"]), 1)

    def test_task_body_not_misattributed(self):
        result = report("module m(interface L); task t; L.Send(x); endtask initial t(); endmodule")
        self.assertEqual(directions(result), {"L": "unknown"})
        self.assertFalse(result["analysis_complete"])

    def test_child_connection_is_explicitly_unresolved(self):
        result = report("module m(interface L); child u(L); endmodule")
        self.assertFalse(result["analysis_complete"])
        self.assertEqual(result["diagnostics"][0]["code"], "hierarchy_not_resolved")

    def test_custom_interface_type(self):
        source = "module m(Link L); initial L.Send(x); endmodule"
        self.assertEqual(directions(report(source)), {})
        self.assertEqual(directions(report(source, ("Link",))), {"L": "output"})

    def test_generate_and_non_ansi_are_flagged(self):
        for source in ["module m(interface L); if(1) begin initial L.Send(x); end endmodule",
                       "module m(L); input L; endmodule"]:
            self.assertFalse(report(source)["analysis_complete"])

    def test_case_sensitive_method_names(self):
        result = report("module m(interface L); initial L.send(x); endmodule")
        self.assertEqual(directions(result), {"L": "unknown"})
        self.assertFalse(result["analysis_complete"])

    def test_implicit_task_invocations_are_analyzed(self):
        result = report("module m(interface L); initial begin L.Send; helper; $finish; end endmodule")
        self.assertEqual(directions(result), {"L": "output"})
        self.assertEqual([d["code"] for d in result["diagnostics"]], ["indirect_call"])


class CliTests(unittest.TestCase):
    def test_cli_both_commands(self):
        for command in ["ast", "channels"]:
            result = subprocess.run([sys.executable, "-m", "svcsp", command,
                                     "examples/async_adder.sv", "--strict"],
                                    cwd=ROOT, capture_output=True, text=True)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(json.loads(result.stdout)["schema_version"], 1)

    def test_cli_missing_file(self):
        result = subprocess.run([sys.executable, "-m", "svcsp", "ast", "missing.sv"],
                                cwd=ROOT, capture_output=True, text=True)
        self.assertEqual(result.returncode, 1)
        self.assertEqual(result.stdout, "")

    def test_strict_cli_reports_incomplete_analysis(self):
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "hierarchy.sv"
            source.write_text("module m(interface L); child u(L); endmodule")
            result = subprocess.run([sys.executable, "-m", "svcsp", "channels", str(source), "--strict"],
                                    cwd=ROOT, capture_output=True, text=True)
            self.assertEqual(result.returncode, 2, result.stderr)
            self.assertFalse(json.loads(result.stdout)["modules"][0]["analysis_complete"])


if __name__ == "__main__":
    unittest.main()
