# Compiler contract and current scope

`svcsp compile` compiles a selected behavioral CSP module into a structural
Verilog-2005 module named `<source_module>_structural`. It specializes parameters
to constants, emits combinational datapath helper modules, and instantiates
storage and asynchronous handshake cells. There are no `always`, `initial`,
`wait`, interface declarations, or task calls in the generated netlist.

The accompanying `csp_cells.v` contains **functional simulation models** with
procedural waits and delays. These are an executable target-cell specification,
not a synthesis library. The structural compiler is working for the subset
below; claiming a full SystemVerilog compiler or fabrication-ready hardware
would be premature.

## Commands

```bash
# Source -> JSON AST (independent of compiler subset restrictions)
.venv/bin/svcsp ast design.sv -o build/design.ast.json

# Source -> checked, parameter-specialized handshake IR
.venv/bin/svcsp ir design.sv --top my_module -P WIDTH=16 \
  -W OUT=17 -o build/design.ir.json

# Source -> structural Verilog
.venv/bin/svcsp compile design.sv --top my_module -P WIDTH=16 \
  -W OUT=17 -o build/design.v

# Optional self-contained simulation file (adds behavioral cell models)
.venv/bin/svcsp compile design.sv --with-models -o build/design_sim.v
```

Use `python -m svcsp` instead of the installed `svcsp` executable if desired.
Multiple source files and `-I` include paths are supported. Without `--top`,
there must be exactly one source module. A selected module is not automatically
elaborated through instances. The compilation unit currently permits module
declarations only; included packages/interfaces and library imports are not yet
handled by the compiler, although the AST parser can represent them. Inputs
should therefore be behavioral modules using the supported channel contract.

`-P NAME=INTEGER` overrides a parameter before dependent ranges/constants are
evaluated. `-W NAME=INTEGER` sets a channel width. Unknown or duplicate overrides
fail. The `--channel-type` option belongs to the standalone channel analyzer;
the compiler currently accepts `interface` and `Channel` ports only.

The public Python APIs are `parse_files`, `lower`, `compile_ast`, and
`compile_files`, with `CompileError` for semantic/unsupported-input failures.
Generated IR is inspectable and JSON serializable; it is not a promised stable
third-party input format for `emit_verilog`.

## Accepted language

| Feature | Accepted form |
| --- | --- |
| Module | Flat module; exactly one ordinary `always` process |
| Channels | ANSI generic `interface` or `Channel` ports, inherited declarations allowed |
| Parameters | Integral `parameter`/`localparam` constants; command-line specialization |
| Storage | Module-level unsigned `logic`, `reg`, or `bit`; scalar or one packed `[N:0]` range |
| Initialization | Constant declaration initializers become reset values; `bit` defaults to zero |
| Communication | Blocking `C.Send(expression)` and `C.Receive(variable)`, one positional argument |
| Assignment | Blocking `variable = expression` to a whole declared variable |
| Sequence | `begin ... end`, including nested unnamed blocks |
| Parallel | `fork ... join`, with independent branch accesses |
| Selection | Ordinary `if ... else`, including omitted else and nested branches |
| Repetition | Implicit repetition of `always`; constant `repeat(0..256)` unrolled |
| Expressions | Known integer literals, variables, parameters, arithmetic/bitwise/logical/comparison operators, unary reductions, concatenation/replication, ternary selection, constant in-range bit/part selects |

Width limits are 1..65536 bits; expanded bodies are limited to 10000 statements.
SystemVerilog's evaluator handles constant sizing and signedness. Emitted
datapath expressions preserve expression structure and destination width
context. Signed data variables, dynamic selects, casts, system functions,
unbased unsized `'0`/`'1`, X/Z literals and unsupported operators are rejected.
Runtime arithmetic can still produce unknown values (for example division by
zero); this is not statically proved impossible for arbitrary input data.

Other explicit exclusions include hierarchy/internal channel instances,
generate, channel arrays and modports, non-ANSI/signal ports, multiple processes,
tasks/functions/classes/packages/imports, timing/event controls, `initial`,
nonblocking/compound/partial assignments, `case`, dynamic loops, `join_any`,
`join_none`, `SplitSend`, `SplitReceive`, `Peek`, `Probe`, and bidirectional or
unused endpoints. These are compile-time errors, even where AST generation or
the standalone channel analyzer supports the syntax.

## Width and dataflow rules

Receive width is inferred from its whole destination variable. Send width is
inferred from the expression's natural SystemVerilog width. Unsized decimal
integers are normally 32 bits: `OUT.Send(x+1)` can infer a wider port than
`OUT.Send(x+8'd1)`. A conflicting inferred width across uses is an error.

An explicit `-W OUT=9` evaluates a send expression in that output's width context,
allowing extension/truncation according to Verilog assignment rules. Receive
destinations must match an explicitly supplied input width exactly. Direction
comes from usage; it is not inferred from a channel's name.

Every data read must be definitely initialized by its declaration, a receive,
or an assignment. Both branches of an if must establish an initialization used
afterward. Independent fork branches contribute their initialized variables at
join. A fork rejects concurrent writes, cross-branch read/write conflicts, or
reuse of the same channel, including potentially exclusive nested branches.
This deliberately conservative check does not attempt scheduling or arbitration.

Every possible `always` iteration must execute at least one communication.
This rejects pure-computation autonomous loops; it does not prove freedom from
deadlock with the external environment. Data-dependent control is analyzed
conservatively, without proving branches unreachable.

## Four-phase protocol and reset

Input channel ports are `input <C>_data`, `input <C>_req`, `output <C>_ack`.
Output channel directions are reversed. Each transfer follows:

```text
sender presents data -> req rises -> receiver captures -> ack rises
                     -> req falls -> ack falls -> next transfer
```

The sender holds its sampled payload through acknowledgment returning low.
Generated leaf operations complete only after the full channel return-to-zero.
Backpressure can delay a handshake indefinitely. There is no global clock.

Internal commands also use four phases: `go` rises, `done` rises, `go` falls,
`done` falls. Sequence connects each completion to the next command; parallel
commands share activation and use a completion join. Branch cells sample their
condition once per command. Assignment cells snapshot data before pulsing a
storage write, so self-dependent assignments such as `x=x+1` update once.
Multiple sequential uses share a channel through explicitly exclusive wiring;
the send mux is not an arbiter.

Assert `reset_n` low before use, then release it to start the repeating process.
Reset restores declaration initializer values (otherwise internal storage is
zeroed but definite-initialization checks prevent reading uninitialized `logic`).
Reset the connected network together. Reset cancels in-flight transactions;
no delivery guarantee is made across a reset. Model `DELAY` parameters are
positive simulation delays, not technology delay estimates.

The protocol choice follows the standard
[four-phase bundled-data handshake](https://www.cl.cam.ac.uk/~djg11/wwwhpr/fourphase/fourphase.html).
Physical mapping must supply the corresponding storage, completion/control,
selection and matched-delay behavior, and verify data-before-request timing,
reset release, hazards and implementation-specific timing assumptions.

## Verification performed

The test suite covers parsing/preprocessing, frontend review regressions,
parameter/width binding, initialization, parallel conflicts, unsupported-input
diagnostics, isolated cell handshakes, and complete compiled-netlist simulations.
Tests compile generated output with `iverilog -g2005` and run it using `vvp`.

Transaction scoreboards check buffers, adders (including overflow carry),
accumulated state, branch decisions, repeated channel use, multiple writes,
parallel fanout, signed constants, output backpressure, skewed input arrivals,
and reset during an in-flight transfer. Seeds are deterministic. These are
functional tests, not formal equivalence proofs or post-layout timing checks.

## Work remaining toward full coverage

1. Elaborate hierarchy, interface instances, packages, and generate constructs.
2. Expand control/data support and bind helper tasks without losing concurrency.
3. Specify and lower split communications and additional channel protocols.
4. Add differential testing against a source-level CSP simulator and formal
   checks of the control lowering.
5. Map the abstract asynchronous cells to a chosen physical library and verify
   implementation timing, hazards, reset, and synthesis constraints.
