# Project Goal

Compile the supported SVCSP subset to structural asynchronous RTL.

Initial backend:

```text
four-phase bundled-data half-buffer
```

Target architecture is defined by documentation, not by existing or legacy
implementation code.

# Authoritative Specifications

Use:

```text
docs/supported_architectures.md
docs/communication_decomposition.md
docs/four_phase_bundled_data_backend.md
docs/compiler_flow.md
docs/verification_plan.md
```

Responsibilities:

```text
supported_architectures.md
    source programs accepted/rejected

communication_decomposition.md
    conditional Receive/Send semantics

four_phase_bundled_data_backend.md
    M6 hardware topology and implementation boundary

compiler_flow.md
    compiler-stage responsibilities

verification_plan.md
    required tests
```

Do not duplicate architecture rules across documents.

# Compiler Rules

- Use `pyslang` for SystemVerilog parsing.
- Preserve exact Channel, variable, expression, width, and source identities.
- Fail closed on unsupported or unresolved semantics.
- Do not silently resize, truncate, extend, or reinterpret payloads.
- Keep hardware architecture decisions out of frontend and behavioral IR.
- M6 owns hardware topology.
- M7 only binds and emits the M6-selected topology.
- Do not infer target architecture from current Python or RTL.
- Treat `reference/` as read-only reference material.

# Backend Rule

For M6/M7 work, use
`docs/four_phase_bundled_data_backend.md` as the authoritative hardware
specification.

Ordinary BODY control and storage are structural.

User combinational logic may remain behavioral/continuous RTL.

EN_RECV and EN_SEND remain physical micropipeline stages, but their controllers
may remain behavioral until their final control circuits are defined.

Matched-delay realization remains a technology-binding concern.

Conditional-split stages are not part of the current backend.

# Conditional Communication

M4 produces:

```text
BODY
+
logical Enable
+
EN_RECV / EN_SEND
```

BODY-side communication is unconditional.

The current backend realizes each Enable as a one-bit four-phase bundled-data
Channel to its corresponding EN stage.

EN_RECV:

```text
enable=1 -> external Receive -> real BODY payload
enable=0 -> external untouched -> dummy/InvalidPayload BODY payload
```

EN_SEND:

```text
always consume BODY payload
enable=1 -> external Send
enable=0 -> suppress external Send
```

Detailed semantics remain in `communication_decomposition.md`.

# Validation Rules

Reject at least:

```text
Receive -> Send -> Receive
Receive after computation begins
dependent Receive
repeated endpoint communication
invalid conditional receive data use
other non-independent communication
```

# Development Rule

Work tests-first and in compiler-flow order.

Before modifying implementation:

```text
read only the relevant authoritative specification
add/update focused tests
make the smallest implementation change
run focused tests
run full regression
```

Do not redesign later phases to make an earlier failure disappear.

# Development Commands

```bash
source .venv/bin/activate
export PATH=/home/cli78217/local/bin:$PATH
pytest -q
```

Before committing:

```bash
git diff --check
pytest -q
```