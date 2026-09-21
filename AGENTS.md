# Project Goal

Build an automated compiler from SystemVerilog CSP (SVCSP) behavioral
descriptions to synthesizable structural bundled-data asynchronous RTL.

This is not merely a SystemVerilog syntax translator.

# Compiler Architecture

Primary compiler path:

SVCSP
-> Phase 1 frontend / semantic analysis
-> Phase 2 Behavioral CSP IR
-> Phase 3 communication normalization
-> Phase 4 dependency analysis
-> Phase 5 pipeline synthesis
-> Phase 6 bundled-data microarchitecture IR
-> Phase 7A structural template binding
-> Phase 7B structural SystemVerilog emission

Phase 3 also has a verification/output branch:

Phase 3 NormalizedModule
-> decomposed SVCSP emitter
-> BODY + SEND/RECV wrappers
-> original-vs-decomposed behavioral equivalence simulation

Decomposed SVCSP is emitted from the SAME Phase 3 NormalizedModule.
It is not a second IR and is never reparsed into the compiler backend.

# Key Design Rules

- Use Python for compiler implementation.
- Use pyslang for SystemVerilog parsing.
- Keep Behavioral CSP IR separate from bundled-data microarchitecture IR.
- Each transformation is an explicit compiler pass.
- Codegen must not make architecture decisions.
- Preserve exact semantic identities instead of reconstructing relationships
  from source locations.
- Fail closed on unsupported or ambiguous cases.
- Every phase requires focused pytest coverage.
- Treat reference/ as read-only reference material.

# Phase 3 Conditional Communication

Phase 3 uses explicit semantic objects:

- CommunicationSite: source-order/control anchor for one conditional
  communication occurrence.
- Enable: symbolic condition identity.
- BodyChannel: internal BODY-to-wrapper or wrapper-to-BODY token identity.
- BodySend / BodyReceive: unconditional BODY-side communication.
- NormalizedSend / NormalizedReceive: external wrapper semantics.

CommunicationSite is not an executed BODY token operation and is not a Skip.
Body communication semantics live in BodySend / BodyReceive.

Exact shared object identity connects a site, enable, BODY channel,
BODY communication, and wrapper. Do not recover this relationship from
source-location matching.

Nested guards may be composed symbolically by Phase 3.

The following describes the executable decomposed-SVCSP realization of the
Phase 3 conditional communication semantics.

# Conditional Receive Semantics

For each iteration, BODY produces an enable token and performs an
unconditional BODY-side Receive.

Enabled:
- RECV wrapper consumes and acknowledges the external token.
- Wrapper forwards the real data token to BODY.
- BODY continues only after that BODY-side Receive completes.

Disabled:
- Wrapper must not consume or acknowledge the external channel.
- Wrapper sends a dummy/invalid BODY-side token.
- BODY still completes its unconditional Receive and continues.
- Downstream use of disabled receive data must remain validity-guarded.

# Conditional Send Semantics

For each iteration, BODY produces an enable token and unconditionally sends
one BODY-side data token to the SEND wrapper.

Enabled:
- wrapper consumes the BODY token;
- wrapper completes the blocking external Send;
- wrapper then sends a completion token back to BODY;
- BODY continuation waits for that completion token.

Disabled:
- wrapper consumes the BODY token;
- wrapper performs no external communication;
- wrapper immediately sends the completion token;
- BODY then continues.

The completion channel is required to preserve original blocking Send
ordering. Internal BODY-token handoff alone is not external Send completion.

# Decomposed SVCSP Emitter Boundary

Current decomposed-SVCSP support is intentionally narrower than Phase 3:

- one direct conditional Send: supported and simulation-equivalence tested;
- one direct conditional Receive: supported and simulation-equivalence tested;
- blocking order after conditional Send: regression tested;
- multiple conditional sites in one module: currently fail closed;
- repeated conditional operations on the same endpoint: fail closed;
- nested conditional sites: fail closed in the emitter;
- conditional communication in fork/join: fail closed in the emitter.

Phase 3 may represent more of these cases than the decomposed emitter supports.

Production decomposed SVCSP remains Channel-based.

Icarus Verilog 12 cannot use SVCSP Channel interfaces as useful module ports,
so behavioral-equivalence tests use a test-only mechanical Channel flattener
to payload/request/acknowledge signals. This flattener is not compiler output
and must not influence production SVCSP syntax or compiler architecture.

# Phase 4 Ordering

Phase 4 dependency analysis is authoritative for source ordering.

Conditional wrappers connect to their exact CommunicationSite through
COMMUNICATION edges. For conditional Send, continuation after the source Send
is sequenced after wrapper completion, not merely after BODY-token handoff.

# Current RTL Boundary

The executable bundled-data RTL MVP currently validates one unconditional
linear Receive; Assign*; Send transaction as one BODY stage.

Conditional communication and JOIN structures exist in compiler IRs but are
not yet implemented as executable end-to-end RTL library support.

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
- Do not commit until tests and requested review are complete
