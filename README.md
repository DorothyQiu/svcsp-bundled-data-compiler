# SVCSP Asynchronous Compiler

Compiler from a supported subset of SystemVerilog CSP (SVCSP) to structural
asynchronous RTL.

Initial backend:

```text
four-phase bundled-data half-buffer
```

## Source Model

One supported top-level process represents one transaction:

```text
N independent Channel Receive(s)
              |
              v
      combinational logic
              |
              v
M independent Channel Send(s)
```

with `N >= 1` and `M >= 1`.

Receive and Send operations may be unconditional or conditional.

Independent sequentially written communications are interpreted as concurrent;
explicit `fork` / `join` is preferred.

Ordered or multi-stage source communication is outside the current target.

## Conditional Communication

Conditional communication is decomposed into:

```text
BODY
+
Enable
+
EN_RECV / EN_SEND
```

The current backend realizes EN_RECV and EN_SEND as separate physical
micropipeline stages.

See `docs/communication_decomposition.md`.

## Backend

Ordinary BODY stages use one canonical 1x1 half-buffer controller with
request/acknowledgement join or fanout logic added only when required by
multiple Channels.

Implementation boundary:

```text
ordinary control/storage     structural
user combinational logic     behavioral/continuous RTL allowed
EN_RECV/EN_SEND controllers  behavioral for now
matched delay                technology-binding abstraction
```

See `docs/four_phase_bundled_data_backend.md`.

## Compiler Flow

```text
SVCSP
  |
  v
Frontend / Semantic Analysis
  |
  v
Behavioral CSP IR
  |
  v
Transaction Extraction
  |
  v
Conditional Communication Decomposition
  |
  v
Dependency / Validity Analysis
  |
  v
Asynchronous Microarchitecture Lowering
  |
  v
Structural RTL Backend
  |
  v
Structural Asynchronous RTL
```

See `docs/compiler_flow.md`.

## Compile a File

`compile_async_file()` is the supported file-to-RTL entrypoint:

```python
from svcsp_compiler import compile_async_file

rtl = compile_async_file("design.sv")
```

Regenerate the checked-in demo:

```bash
source .venv/bin/activate
python -c "from pathlib import Path; from svcsp_compiler import compile_async_file; Path('examples/generated/receive_invert_send_async.sv').write_text(compile_async_file('examples/receive_invert_send.sv'))"
```

The current demo compiles:

```systemverilog
A.Receive(a);
y = ~a;
B.Send(y);
```

to generated asynchronous RTL and verifies multiple four-phase transactions
with Icarus.

## Development Rule

Unsupported source must fail explicitly rather than be silently reinterpreted.

Implementation proceeds tests-first in compiler-flow order:

```text
M1 Frontend
M2 Behavioral IR
M3 Transaction Extraction
M4 Communication Decomposition
M5 Semantic Validation
M6 Microarchitecture
M7 RTL Backend
```

## Documentation

Authoritative specifications:

- `docs/supported_architectures.md` — accepted and rejected source structures
- `docs/communication_decomposition.md` — conditional communication semantics
- `docs/four_phase_bundled_data_backend.md` — backend hardware architecture
- `docs/compiler_flow.md` — compiler-stage responsibilities
- `docs/verification_plan.md` — implementation and regression checklist

Developer rules are in `AGENTS.md`.