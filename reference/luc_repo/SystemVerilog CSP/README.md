# SystemVerilog CSP compiler

A Python **asynchronous SystemVerilog CSP → structural Verilog compiler** for
the [supported language subset](docs/COMPILER.md). The working pipeline is:

1. Parse SystemVerilog into a serializable, unelaborated source AST.
2. Bind parameters, variables, channel directions and widths; validate data use
   and reject unsupported constructs or parallel access conflicts.
3. Lower sequential/parallel communication and computation to handshake IR.
4. Emit a structural Verilog module targeting four-phase bundled-data cells.

The examples use blocking `interface.Send(...)` / `interface.Receive(...)`
operations and no global clock. Generated netlists can be simulated with the
included functional cell models. This is **not yet a full-language compiler or
a physically validated synthesis flow**: physical implementation requires
technology-specific asynchronous cells and bundled-data timing constraints.
The original AST-to-JSON workflow remains available on its own.

## Run

From this directory:

```bash
python3 -m venv .venv
.venv/bin/python -m pip install -e .
.venv/bin/python -m svcsp ast examples/async_adder.sv
.venv/bin/python -m svcsp ir examples/async_adder.sv
.venv/bin/python -m svcsp compile examples/async_adder.sv -o build/adder.v
.venv/bin/python -m svcsp channels examples/async_adder.sv --strict
.venv/bin/python -m unittest discover -s tests -v
```

`ast`, `ir`, and `channels` emit JSON; `compile` emits Verilog. Use `-o path`
to save any output. Supply
multiple source files to parse them together; use `-I path/to/includes` for
include directories. Missing includes and malformed syntax fail with source
diagnostics rather than producing a partial result. Exit status is 0 on success,
1 for input/parse/compile errors, and 2 for incomplete channel analysis with
`--strict`. Compilation errors leave an existing output file untouched.

Run the compiled adder with Icarus Verilog (`iverilog` and `vvp`):

```bash
iverilog -g2005 -s tb -o build/adder.vvp \
  build/adder.v svcsp/rtl/csp_cells.v examples/tb_async_adder.v
vvp build/adder.vvp
```

The demo verifies sums 22 and 510 with skewed input arrival and output
backpressure, then prints `PASS`. The simulator-dependent tests are explicitly
skipped if Icarus is absent; they have been run in this workspace.

## Example result

[`examples/async_adder.sv`](examples/async_adder.sv) receives two operands in
parallel, waits for both receives to finish, computes their sum, and sends it.
When instantiating this module, connect `WIDTH`-bit input channels and a
`WIDTH+1`-bit output channel. The generated Verilog flattens each channel into
`<name>_data`, `<name>_req`, and `<name>_ack` and adds an active-low `reset_n`.
The included harness simulates the compiled structure against expected values;
it does not simulate the original CSP source using a separate CSP library.

| Endpoint | Scope | Inferred direction | Evidence |
| --- | --- | --- | --- |
| A | port | input | `A.Receive(a)` |
| B | port | input | `B.Receive(b)` |
| SUM | port | output | `SUM.Send(sum)` |

The AST's executable portion, abbreviated for readability:

```text
Module async_adder
└── Process always
    └── Sequence
        ├── Parallel (join)
        │   ├── Call A.Receive(a)
        │   └── Call B.Receive(b)
        ├── Assignment sum = {1'b0, a} + {1'b0, b}
        └── Call SUM.Send(sum)
```

The complete generated outputs are in
[`examples/generated/async_adder.ast.json`](examples/generated/async_adder.ast.json)
and [`examples/generated/async_adder.channels.json`](examples/generated/async_adder.channels.json),
plus the [lowered IR](examples/generated/async_adder.ir.json) and
[structural Verilog](examples/generated/async_adder.v).

## Python API and code layout

```python
from svcsp import parse_files, analyze_channels, lower, compile_ast

ast = parse_files(["examples/async_buffer.sv"])
ast_json = ast.to_dict()
channel_report = analyze_channels(ast)
compiler_ir = lower(ast)
verilog = compile_ast(ast)
```

- `svcsp/parser.py`: uses `pyslang` for lexing, preprocessing and parsing, then
  removes delimiters/trivia and normalizes names, calls, processes, modules and
  sequential/parallel blocks. Expression precedence and join modes are retained.
- `svcsp/ast.py`: parser-independent `Node(kind, fields, location)` data model,
  traversal, and JSON serialization.
- `svcsp/channels.py`: a separate analysis pass over that AST.
- `svcsp/lowering.py`: validated binding, constant evaluation, dataflow checks,
  and control/communication IR.
- `svcsp/backend.py`: structural cell instances, wiring, and combinational
  datapath helper modules.
- `svcsp/rtl/csp_cells.v`: functional simulation models of the target cells.
- `svcsp/__main__.py`: CLI.
- `tests/`: frontend, negative compiler cases, cell protocol, and complete
  generated-netlist simulations, including independent review regressions.

This is an **unelaborated source AST**, not slang's elaborated semantic AST.
It abstracts away concrete punctuation and grammar wrappers; less specialized
nodes retain slang's structured node/field names. Parameters, types, expressions,
and control flow remain available for compiler passes. The `ast` command does
not perform binding or type checking; `ir` and `compile` do those checks for the
supported subset. JSON envelopes carry schema version 1; pinning `pyslang==11.0.0` also
keeps the inherited node names stable for this prototype.

## Channel rules

The analyzer recognizes generic ANSI `interface` ports as CSP endpoints, named
`Channel` ports (including modports), and module-level `Channel` instances.
Additional interface names can be supplied with `--channel-type Link` or the
Python API's `channel_types` argument. Generic interfaces are assumed to follow
the CSP contract; this is not a semantic check of their library implementation.

| Operation | Module-relative role |
| --- | --- |
| `Receive`, `SplitReceive` | input |
| `Send`, `SplitSend` | output |
| `Peek` | input, because data is sampled without completing a receive |
| `Probe` | observation only; does not establish a direction |

Names are case sensitive. Optional extra arguments (for example a split phase)
remain in the AST. Channel records include declarations, array dimensions,
modports, and call-site evidence. Modport names do not determine direction.

An endpoint used for both roles is reported as `bidirectional`; this records
usage and does not imply that it can become a legal Verilog `inout`. Unused or
probe-only endpoints have `unknown` direction. Internal channel instances are
distinguished from module ports. Array direction is the union over all its
elements; each use retains its index expression.

The pass takes the union of syntactic uses across branches and loops. It does
not prove reachability, exclude dead code, check contention, or verify deadlock
freedom. Comments and string literals never count as communication operations.

## Analysis limits and remaining compiler work

The parser accepts substantially more SystemVerilog than the channel analysis
can resolve. `analysis_complete` means the module-local scan encountered none
of its documented unresolved constructs; it does **not** certify elaboration,
simulation, synthesizability, or complete SystemVerilog semantic validity.

Child-module connections, generate scopes, non-ANSI ports, nested task/function
or class definitions, and indirect calls produce diagnostics. The pass does not
descend into unresolved scopes or invent directions from their contents. Calls
with unresolved receivers or unknown channel methods are also flagged. Ordinary
block and loop-variable shadowing is tracked; full SV name binding remains a
future pass. A recognizable direct use can still be reported when other uses
remain unresolved, so callers should inspect `analysis_complete` or use
`--strict` before relying on the report.

The standalone channel analyzer deliberately handles a broader range of syntax
than the compiler accepts. Compiler restrictions are listed in
[the compiler contract](docs/COMPILER.md). Remaining work toward a full compiler
includes hierarchy and generate elaboration, additional control/data constructs,
helper-task binding, split communications, formal equivalence checks, and
technology mapping with physical timing/hazard verification. Unsupported input
is diagnosed rather than silently omitted from generated hardware.

## References

The interface-based communication convention follows
[Saifhashemi and Beerel, *SystemVerilogCSP: Modeling Digital Asynchronous Circuits
Using SystemVerilog Interfaces*](https://www.wotug.org/papers/CPA-2011/SaifhashemiBeerel11/SaifhashemiBeerel11.pdf).
The parsing dependency is [slang / pyslang](https://github.com/MikePopoloski/slang),
whose [parsing documentation](https://www.sv-lang.com/parsing.html) describes its
syntax-tree infrastructure. This project implements the AST normalization and
channel analysis on top of that parser.
