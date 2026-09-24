# SVCSP Asynchronous Compiler

This project compiles a supported SystemVerilog CSP (SVCSP) subset into
structural asynchronous SystemVerilog RTL. The current target is a four-phase
bundled-data half-buffer backend.

## Supported transaction model

One top-level `always` process describes one transaction:

```text
independent Channel Receive(s)
            -> combinational BODY logic
            -> independent Channel Send(s)
```

At least one Receive and one Send are required. Independent operations written
sequentially are interpreted as concurrent communications; explicit
`fork` / `join` is preferred for that intent.

Conditional Receive and Send are supported. The compiler decomposes them into
unconditional BODY communication plus a logical Enable and an EN_RECV or
EN_SEND stage. Conditional Receive guards may use only pre-input sources;
conditional Send guards may additionally use valid BODY data.

R9 preserves combinational source semantics in one coherent BODY
`always_comb` implementation: blocking-Assign order, `if` / `else` control,
and exact lvalues are retained. BODY targets may be whole local Variables or
static literal bit/range selects on concrete-width local Variables, such as
`x[0]` and `x[7:4]`. Parallel BODY branches are accepted only when their data
accesses are proven noninterfering.

The compiler fails closed rather than infer behavior for unsupported cases,
including ordered/multi-stage communication, dependent Receives, invalid uses
of conditionally received data, undeclared local reads, dynamic or
parameter-dependent data-lvalue selects, selected lvalues on symbolic-width
Variables, dynamic Channel selection, and unsupported parameter arithmetic.

The complete accepted/rejected subset is defined in
[supported architectures](docs/supported_architectures.md).

## Compiler flow

```text
M1  Frontend parsing and source resolution
M2  Behavioral CSP IR
M3  Transaction extraction and structural validation
M4  Conditional communication decomposition
M5  Dependency, validity, and semantic validation
M6  Asynchronous microarchitecture lowering
M7  Structural RTL binding and emission
```

M6 selects the asynchronous topology; M7 binds that selected topology and
renders RTL without inventing scheduling, storage, control channels, or state.
See the detailed [compiler flow](docs/compiler_flow.md).

## Repository layout

```text
src/svcsp_compiler/  compiler implementation
rtl_lib/             asynchronous RTL-library primitives
examples/            compilable supported-subset source examples
tests/               unit, flow, generated-RTL, and simulation regressions
docs/                authoritative source and backend contracts
```

## Install and test

```bash
source .venv/bin/activate
# Generated-RTL simulation tests require iverilog and vvp on PATH.
pytest -q
```

## Compile and run examples

Every top-level source example in `examples/` is compiled through the complete
flow by the lightweight examples test:

```bash
source .venv/bin/activate
pytest -q tests/test_examples.py
```

Compile one source file programmatically:

```bash
python -c "from pathlib import Path; from svcsp_compiler import compile_async_file; Path('/tmp/receive_invert_send_async.sv').write_text(compile_async_file('examples/receive_invert_send.sv'))"
```

`examples/generated/receive_invert_send_async.sv` is the checked-in output for
that source. Run the generated-RTL Icarus simulations with:

```bash
# iverilog and vvp must be on PATH.
pytest -q tests/test_async_generated_rtl.py
```

Examples include simple passthrough, parallel join/compute/send, conditional
Receive fallback, conditional Send, symbolic parameter widths, and static
selected lvalues.

## Detailed documentation

- [Supported source architectures](docs/supported_architectures.md)
- [Conditional communication decomposition](docs/communication_decomposition.md)
- [Four-phase bundled-data backend](docs/four_phase_bundled_data_backend.md)
- [Compiler-stage responsibilities](docs/compiler_flow.md)
- [Verification plan](docs/verification_plan.md)

Developer workflow rules are in [AGENTS.md](AGENTS.md).
