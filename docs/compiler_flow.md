# Target Compiler Flow

This document defines the authoritative compiler flow.

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
4. Conditional Communication Decomposition
    |
    v
5. Dependency / Validity Analysis
   + Semantic Validation
    |
    v
6. Asynchronous Microarchitecture Lowering
    |
    v
7. Structural RTL Backend
    |
    v
Structural Asynchronous RTL
```

The initial backend is four-phase bundled-data. Its hardware architecture is
defined in `four_phase_bundled_data_backend.md`.

## 1. Frontend / Semantic Analysis

Parse SystemVerilog with `pyslang` and resolve the source semantics required by
later stages, including:

```text
modules and processes
Channel endpoints
external inputs, local variables, and parameters
lexical scope
payload widths
Receive / Send
assignments
if / else
fork / join
expressions
source locations
```

Resolve explicit non-channel ANSI input ports as external-input identities;
they are not local variables or Channel endpoints.

Output:

```text
resolved semantic source representation
```

No asynchronous hardware decisions are made here.

## 2. Behavioral CSP IR

Convert parser-specific structures into compiler-owned behavioral concepts:

```text
Sequence
Parallel
If
Receive
Send
Assign
Skip
```

Preserve source behavior, semantic identities, expressions, widths, and control
structure.

Preserve the module-owned identities of explicit external inputs separately
from local variables.  Do not create persistent-state semantics.

Preserve `BehavioralModule.parameters` as the canonical ordered source-
parameter identity set. Every behavioral parameter expression and every
`PayloadWidth.parameters` entry must refer to the exact object in that tuple;
M2 must not recreate structurally equal parameter objects for symbolic widths.

A source-level `Sequence` does not by itself imply serialized hardware
communication.

No controller, storage, handshake topology, or delay decisions are made here.

## 3. Transaction Extraction + Structural Validation

Extract one supported transaction:

```text
Receive Region
      |
      v
Combinational Region
      |
      v
Send Region
```

Multiple independent Receives or Sends may be written sequentially but are
interpreted as concurrent communication.

Reject structural violations such as:

```text
Receive -> Send -> Receive
Receive after computation begins
repeated endpoint communication
```

Output:

```text
Structurally Validated Transaction
```

M3 preserves source-parameter identities and creates no parameter semantics.

## 4. Conditional Communication Decomposition

Transform conditional communication into:

```text
BODY
+
Enable
+
EN_RECV / EN_SEND
```

BODY-side communication remains unconditional.

At this stage, `Enable` is a logical control relation rather than a specific
wire, Channel, controller, or gate structure.

Detailed semantics are defined in `communication_decomposition.md`.

Output:

```text
Decomposed Transaction
```

M4 preserves source-parameter identities in expressions and effective enables;
it creates no parameter semantics.

## 5. Dependency / Validity Analysis + Semantic Validation

Analyze:

```text
data dependencies
control / enable dependencies
conditional data validity
communication independence
```

Validate guard sources and availability.  A read with no reaching local
definition is valid only for an explicit external input or parameter; otherwise
reject it.  Enforce the pre-input Conditional Receive and post-input
Conditional Send source restrictions defined in
`supported_architectures.md`.

Reject semantically unsupported communication, including dependent Receives and
invalid uses of conditionally received data.

Output:

```text
Semantically Validated Transaction
+
dependency information
+
validity information
```

M5 preserves source-parameter identities and validates their existing allowed
uses; it does not add parameter type or width inference.

## 6. Asynchronous Microarchitecture Lowering

Map validated transaction semantics onto explicit asynchronous hardware.

M6 decides all architecture required by the RTL backend, including:

```text
ordinary BODY handshake topology
ordinary controller structure
ordinary BODY storage
ordinary matched-delay requirements

Enable Channel realization and availability
EN_RECV / EN_SEND placement
EN-stage payload storage
EN-stage matched-delay requirements
```

M6 realizes only the availability selected for a validated Enable; it does not
invent an external input, persistent state, or a control Channel.
It preserves source-parameter identities and creates no parameter semantics.

For the current backend, use
`four_phase_bundled_data_backend.md` as the authoritative hardware
specification.

Output:

```text
Asynchronous Microarchitecture
```

## 7. Structural RTL Backend

Bind the M6-selected architecture to RTL-library components and emit
SystemVerilog.

M7 handles:

```text
component instances
ports
parameters
payload widths
signal connections
deterministic names
expression rendering
exact M6-resource-to-RTL-instance traceability
```

M7 emits each explicit external input as a public module input port with its
preserved identity.  It must not infer an external input from a local variable
that lacks a definition.

M7 binds each exact source `Parameter` to a source-module-parameter
representation distinct from RTL-library instance parameter bindings. It
preserves all `BehavioralModule.parameters` in source order, including unused
parameters, and emits their declarations before the generated module port list.
It preserves names exactly and fails closed on duplicate or conflicting
module-scope names. An emitted parameter expression or symbolic width must
resolve through its exact source-module-parameter binding; M7 must not emit an
undeclared symbolic parameter name.

Before transmitting an M4 logical Enable on its one-bit enable Channel, M7
booleanizes the condition to one logical bit. This binding action does not
change M6 availability or topology.

M7 must not invent:

```text
handshake topology
communication ordering
controller structure
storage placement
arbitration
matched-delay placement
Enable Channel realization or availability
EN_RECV / EN_SEND placement or physical resources
```

Output:

```text
Structural Asynchronous RTL
```

## Stage Model

The current target assumes:

```text
one supported top-level process
=
one transaction
=
one M6 transaction architecture
```

A transaction may contain multiple physical stages because conditional
communication may introduce EN_RECV or EN_SEND stages.

Pipeline partitioning across multiple source transactions is outside the
current flow.
