# SVCSP Asynchronous Compiler

Compiler from a supported subset of **SystemVerilog CSP (SVCSP)** to structural,
synthesizable **asynchronous RTL**, with the longer-term goal of gate-level
asynchronous implementation.

Initial backend target:

```text
four-phase bundled-data half-buffer
```

## Target Source Model

One supported top-level process represents one transaction:

```text
N independent Channel Receive(s)
              |
              v
      Combinational Logic
              |
              v
M independent Channel Send(s)
```

with:

```text
N >= 1
M >= 1
```

Receive and Send operations may be unconditional or conditional.

Multiple independent communications written sequentially are interpreted as
concurrent, but the compiler should warn and recommend explicit `fork` / `join`.

Example:

```systemverilog
A.Receive(a);
B.Receive(b);
```

is accepted as concurrent input communication, while the preferred source form
is:

```systemverilog
fork
    A.Receive(a);
    B.Receive(b);
join
```

The same rule applies to multiple independent Sends.

Ordered or multi-stage communication is outside the current target model.

## Conditional Communication

Source-level conditional communication is decomposed into:

```text
BODY
+
enable
+
EN_RECV / EN_SEND
```

At M4, `Enable` remains a logical condition and is not fixed to a wire,
Channel, or specific handshake mechanism. The current M6/M7 backend realizes
each Enable as a one-bit four-phase bundled-data Channel: BODY unconditionally
sends exactly one enable token per transaction to the corresponding EN_RECV or
EN_SEND stage. These are BODY control outputs, not ordinary post-join data
outputs; an EN_RECV enable must be available without waiting for the BODY input
communication it controls.

After decomposition:

- BODY-side Receive communication is unconditional;
- BODY-side Send communication is unconditional;
- `EN_RECV` controls whether real external input or dummy/invalid data is
  supplied to BODY;
- `EN_SEND` controls whether BODY-side output is forwarded externally.

EN_RECV and EN_SEND are separate micropipeline stages. EN_RECV performs the
external input only for enable=1, but always emits a BODY token; enable=0 emits
an InvalidPayload/dummy BODY token. EN_SEND always consumes the BODY token;
enable=1 forwards it externally, while enable=0 discards its payload and emits
no external or extra dummy token.

See `docs/communication_decomposition.md` for the detailed semantics.

## Target Compiler Flow

```text
SVCSP Source
    |
    v
1. Frontend / Semantic Analysis
    |
    v
2. Behavioral CSP IR
    |
    v
3. Transaction Extraction
   + Structural Validation
    |
    v
4. Conditional Communication
   Decomposition
    |
    v
5. Dependency / Validity Analysis
   + Semantic Validation
    |
    v
6. Asynchronous Microarchitecture
   Lowering
    |
    v
7. Structural RTL Backend
    |
    v
Structural Asynchronous RTL
    |
    v
Synthesis / Technology Mapping
    |
    v
Gate-Level Asynchronous Implementation
```

The compiler architecture is not restricted to bundled-data or four-phase
handshaking; those are the first backend targets.

## Compile an SVCSP File

`compile_async_file()` is the supported SVCSP-file to asynchronous-RTL
entrypoint. It runs the authoritative frontend through M7 flow and returns
structural SystemVerilog:

```python
from svcsp_compiler import compile_async_file

rtl = compile_async_file("design.sv")
```

Regenerate the checked-in receive/invert/send demo RTL with:

```bash
source .venv/bin/activate
python -c "from pathlib import Path; from svcsp_compiler import compile_async_file; Path('examples/generated/receive_invert_send_async.sv').write_text(compile_async_file('examples/receive_invert_send.sv'))"
```

## Validation

Unsupported source must fail explicitly rather than be silently reinterpreted.

Examples include:

```systemverilog
// interleaved communication
A.Receive(a);
B.Send(a);
C.Receive(c);
```

```systemverilog
// dependent Receive
A.Receive(a);

if (a[0])
    B.Receive(b);
```

```systemverilog
// Receive after computation begins
A.Receive(a);

x = f(a);

B.Receive(b);
```

```systemverilog
// repeated endpoint communication
A.Receive(a);
A.Receive(b);
```

## Development Strategy

Implementation proceeds in compiler-flow order:

```text
M1  Frontend / Semantic Analysis
M2  Behavioral CSP IR
M3  Transaction Extraction + Structural Validation
M4  Conditional Communication Decomposition
M5  Dependency / Validity + Semantic Validation
M6  Asynchronous Microarchitecture Lowering
M7  Structural RTL Backend
```

For each milestone:

```text
define expected behavior
        |
        v
add or update tests
        |
        v
modify implementation
        |
        v
all tests pass
```

## Documentation

Authoritative target-specification documents:

- `docs/supported_architectures.md` — supported and rejected source structures;
- `docs/compiler_flow.md` — target compiler stages and responsibilities;
- `docs/communication_decomposition.md` — conditional communication semantics;
- `docs/verification_plan.md` — implementation and verification roadmap.

Developer-specific migration guidance is kept in `AGENTS.md`.
