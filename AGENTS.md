# Project Goal

Build a compiler from a supported subset of SystemVerilog CSP (SVCSP) to
structural, synthesizable asynchronous RTL.

Initial backend target:

```text
bundled-data + four-phase handshake
```

The target compiler architecture is defined by the documentation, not by legacy
implementation structures.

# Target Source Model

One supported top-level process represents one single-stage transaction:

```text
N independent Channel Receive(s)
              |
              v
      Combinational Logic
              |
              v
M independent Channel Send(s)
```

with `N >= 1` and `M >= 1`.

Receive and Send operations may be unconditional or conditional.

Multiple independent communications written sequentially are accepted and
interpreted as concurrent, but the compiler should warn and recommend explicit
`fork` / `join`.

Explicit `fork` / `join` is the preferred concurrency form.

Reject communication that is not semantically independent.

# Target Compiler Flow

```text
1. Frontend / Semantic Analysis
2. Behavioral CSP IR
3. Transaction Extraction + Structural Validation
4. Conditional Communication Decomposition
5. Dependency / Validity Analysis + Semantic Validation
6. Asynchronous Microarchitecture Lowering
7. Structural RTL Backend
```

This flow is authoritative.

Existing code may contain legacy phase numbering, `PipelineGraph`,
`synthesize_pipeline()`, wrapper terminology, or other structures from the
previous design.

Treat these as migration targets, not architectural requirements.

Do not change the target specification merely to match legacy code.

# Conditional Communication

Source level:

```text
conditional Receive
conditional Send
```

After decomposition:

```text
BODY
enable
EN_RECV
EN_SEND
```

`enable` is an abstract control concept. Do not assume it must be a wire,
Channel, or specific handshake mechanism.

`BODY` is used only after decomposition and contains:

```text
unconditional Channel Receive(s)
combinational logic
unconditional Channel Send(s)
enable generation
```

For conditional Receive:

```text
enable = 1:
    EN_RECV performs external Receive
    BODY receives real data

enable = 0:
    external Channel remains untouched
    BODY receives dummy / invalid data
```

The BODY-side Receive is always unconditional.

For conditional Send:

```text
enable = 1:
    EN_SEND performs external Send

enable = 0:
    EN_SEND suppresses external communication
```

The BODY-side Send is always unconditional.

# Validation Rules

Fail closed on unsupported source.

Reject at least:

```text
Receive -> Send -> Receive
Receive after computation begins
dependent Receive
repeated endpoint communication
invalid use of conditionally received data
other non-independent communication
```

Do not silently reinterpret unsupported source.

# Compiler Rules

- Use `pyslang` for SystemVerilog parsing.
- Keep parser representation separate from compiler-owned IR.
- Preserve exact Channel, variable, expression, width, and source identities.
- Do not silently resize, truncate, extend, or reinterpret payloads.
- Keep hardware architecture decisions out of frontend and behavioral IR.
- Keep RTL emission free of new architecture decisions.
- Treat `reference/` as read-only reference material.
- Add or update tests before modifying each compiler milestone.

# Development Order

Work strictly in compiler-flow order:

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
-> add/update tests
-> modify implementation
-> run regression
-> proceed only when stable
```

Avoid redesigning later phases while an earlier milestone is still being
migrated.

# Authoritative Documentation

Read only the relevant target-spec documents:

```text
docs/supported_architectures.md
docs/compiler_flow.md
docs/communication_decomposition.md
docs/verification_plan.md
```

Use:

- `supported_architectures.md` for accepted/rejected source structures;
- `compiler_flow.md` for phase responsibilities;
- `communication_decomposition.md` for EN_RECV / EN_SEND semantics;
- `verification_plan.md` for milestone tests and completion criteria.

# Development Commands

```bash
source .venv/bin/activate
```

On June:

```bash
export PATH=/home/cli78217/local/bin:$PATH
pytest -q
```

Before committing:

```bash
git diff --check
pytest -q
```