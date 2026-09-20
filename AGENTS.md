# Project Goal

Build an automated compiler from SystemVerilog CSP (SVCSP) behavioral
descriptions to synthesizable structural bundled-data asynchronous RTL.

This is not merely a SystemVerilog syntax translator.

# Compiler Architecture

SVCSP
-> Frontend / semantic analysis
-> Behavioral CSP IR
-> Communication normalization
-> Dependency analysis
-> Pipeline synthesis
-> Controller/template selection
-> Bundled-data microarchitecture IR
-> Structural SystemVerilog codegen
-> Verification

# Key Design Rules

- Use Python for compiler implementation.
- Use pyslang for SystemVerilog parsing.
- Keep Behavioral CSP IR separate from bundled-data microarchitecture IR.
- Each transformation should be an explicit compiler pass.
- Codegen must not make architecture decisions.
- Preserve source locations where practical.
- Every phase requires focused pytest coverage.
- Do not implement later phases unless explicitly requested.
- Treat reference/ as read-only reference material.

# Conditional Communication

Conditional communication is normalized using enable signals plus dedicated
SEND/RECV wrapper representations.

Conditional Receive:
- enable=1: consume the external token and forward real data internally.
- enable=0: do not consume the external token; inject a dummy internal token.

Conditional Send:
- consume the BODY-side internal token every iteration.
- enable=1: communicate externally.
- enable=0: suppress external communication.

The BODY should eventually be suitable for unconditional bundled-data
pipeline stages.

Detailed semantics are in:
docs/communication_normalization.md

# Repository Layout

- src/svcsp_compiler/: compiler implementation
- tests/: pytest tests
- examples/: input SVCSP examples
- docs/: architecture/specification
- rtl_lib/: bundled-data RTL templates
- reference/: read-only reference implementations

# Development

- Python >= 3.10
- Run pytest -q after changes
- Keep each change scoped to the requested phase
