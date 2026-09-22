# Target Compiler Flow

This document defines the authoritative compiler flow for the target SVCSP
asynchronous compiler.

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
```

The initial backend target is:

```text
bundled-data + four-phase handshake
```

The compiler architecture itself is not restricted to that backend.

---

# 1. Frontend / Semantic Analysis

## Input

```text
SVCSP source
```

## Responsibility

Parse SystemVerilog and resolve the source semantics required by later stages.

Relevant information includes:

- modules and processes;
- Channel endpoints;
- variables and parameters;
- lexical scope;
- payload widths;
- `Receive()` / `Send()`;
- assignments;
- `if` / `else`;
- `fork` / `join`;
- expressions;
- source locations.

The frontend should use `pyslang`.

## Output

```text
resolved semantic source representation
```

## Boundary

No asynchronous hardware architecture decisions are made here.

---

# 2. Behavioral CSP IR

## Input

```text
resolved semantic source representation
```

## Responsibility

Convert parser-specific structures into a canonical compiler-owned behavioral
representation.

Core concepts include:

```text
Sequence
Parallel
If
Receive
Send
Assign
Skip
```

The IR preserves source behavior, semantic identities, expressions, and control
structure.

A source-level `Sequence` does not by itself require serialized hardware
communication.

## Output

```text
Behavioral CSP IR
```

## Boundary

No controller, storage, handshake topology, or matched-delay decisions are made
here.

---

# 3. Transaction Extraction + Structural Validation

## Input

```text
Behavioral CSP IR
```

## Responsibility

Extract one single-stage transaction:

```text
Receive Region
      |
      v
Combinational Region
      |
      v
Send Region
```

The target transaction model is:

```text
N independent Channel Receive(s)
              |
              v
      Combinational Logic
              |
              v
M independent Channel Send(s)
```

Multiple sequentially written independent Receives or Sends are accepted as
concurrent communication, with a warning recommending explicit `fork` / `join`.

Reject structural violations such as:

```text
Receive -> Send -> Receive
Receive after the computation region begins
repeated communication on one endpoint
```

## Output

```text
Structurally Validated Transaction
```

containing identified input, computation, and output regions.

---

# 4. Conditional Communication Decomposition

## Input

```text
Structurally Validated Transaction
```

## Responsibility

Transform source-level conditional communication into:

```text
BODY
+
enable
+
EN_RECV / EN_SEND
```

while keeping BODY-side Channel communication unconditional.

`enable` remains an abstract control concept at this stage.

Detailed semantics are defined in:

```text
docs/communication_decomposition.md
```

## Output

```text
Decomposed Transaction
```

containing BODY communication, enable relations, and required EN_RECV /
EN_SEND components.

---

# 5. Dependency / Validity Analysis + Semantic Validation

## Input

```text
Decomposed Transaction
```

## Responsibility

Analyze:

```text
data dependencies
control / enable dependencies
conditional data validity
communication independence
```

This stage catches semantic violations that structural validation alone cannot
detect.

Example:

```systemverilog
A.Receive(a);

if (a[0])
    B.Receive(b);
```

must be rejected because `B.Receive` depends on data produced by `A.Receive`.

For conditional Receive, the compiler must also prove that received data is
used only where it is valid.

## Output

```text
Semantically Validated Transaction
+
dependency information
+
validity information
```

---

# 6. Asynchronous Microarchitecture Lowering

## Input

```text
Semantically Validated Transaction
+
dependency / validity information
```

## Responsibility

Map transaction semantics onto explicit asynchronous hardware structure.

For the general transaction:

```text
N Channel Receives
       |
       v
Input Synchronization
       |
       v
Combinational Logic
       |
       v
Storage
       |
       v
Output Distribution
       |
       v
M Channel Sends
```

This stage makes architecture decisions including:

- input synchronization;
- output distribution;
- controller structure;
- storage requirements;
- matched-delay requirements;
- EN_RECV placement;
- EN_SEND placement.

The initial implementation target uses bundled-data with a four-phase
handshake.

## Output

```text
Asynchronous Microarchitecture
```

---

# 7. Structural RTL Backend

## Input

```text
Asynchronous Microarchitecture
```

## Responsibility

Bind the selected microarchitecture to structural hardware components and emit
synthesizable SystemVerilog.

This includes:

- component instances;
- ports;
- parameters;
- payload widths;
- signal connections;
- deterministic generated names.

## Boundary

The RTL backend must not make new architecture decisions.

It must not invent:

```text
synchronization topology
communication ordering
storage
arbitration
matched-delay placement
```

## Output

```text
Structural Synthesizable Asynchronous RTL
```

---

# Stage Model

The current target assumes:

```text
one supported top-level process
=
one transaction
=
one asynchronous stage
```

There is therefore no pipeline-partitioning or stage-formation pass in the
current flow.

A future multi-stage compiler may insert stage formation between dependency
analysis and asynchronous microarchitecture lowering.

---

# Summary

```text
SVCSP
  |
  v
Semantic Source Representation
  |
  v
Behavioral CSP IR
  |
  v
Structurally Validated Transaction
  |
  v
Decomposed Transaction
  |
  v
Semantically Validated Transaction
  |
  v
Asynchronous Microarchitecture
  |
  v
Structural Async RTL
```