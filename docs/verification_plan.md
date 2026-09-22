# Verification and Implementation Plan

This document defines the implementation order and verification checklist for
the target compiler.

For every milestone:

```text
define expected behavior
-> add or update tests
-> modify implementation
-> run regression
-> proceed only when stable
```

---

# M1. Frontend / Semantic Analysis

## Goal

Extract the source semantics required by later compiler stages.

## Test coverage

- modules and top-level processes;
- Channel endpoints;
- `Receive()` / `Send()`;
- variables and parameters;
- lexical scope;
- payload widths;
- assignments;
- `if` / `else`;
- nested conditions;
- `fork` / `join`;
- supported expressions;
- source locations.

## Required properties

- use `pyslang`;
- preserve exact semantic identities;
- fail explicitly when required semantic information cannot be resolved;
- make no asynchronous hardware decisions.

## Done when

The frontend provides a stable semantic representation sufficient for M2.

---

# M2. Behavioral CSP IR

## Goal

Convert frontend semantics into compiler-owned behavioral IR.

## Required IR concepts

```text
Sequence
Parallel
If
Receive
Send
Assign
Skip
```

## Test coverage

- single Receive / Send;
- assignment;
- Receive -> logic -> Send;
- sequential statements;
- `fork` / `join`;
- `if`;
- `if` / `else`;
- nested conditions.

Verify preservation of:

```text
Channel identity
variable identity
expressions
payload widths
control structure
```

## Required property

A behavioral `Sequence` preserves source structure but does not by itself imply
serialized hardware handshakes.

## Done when

All supported source structures can be represented without hardware-specific
objects.

---

# M3. Transaction Extraction + Structural Validation

## Goal

Recognize the target single-stage transaction:

```text
N independent Receive(s)
        |
        v
Combinational Logic
        |
        v
M independent Send(s)
```

## Positive tests

- `1R1S`;
- `2R1S`;
- `1R2S`;
- `2R2S`;
- explicit `fork` / `join`;
- sequentially written independent Receives;
- sequentially written independent Sends.

Sequential independent communication must be:

```text
accepted
+
interpreted as concurrent
+
warning emitted recommending fork/join
```

## Negative tests

Reject:

```text
Receive -> Send -> Receive
Receive after computation begins
repeated endpoint communication
```

## Done when

The compiler either produces:

```text
Receive Region
Combinational Region
Send Region
```

or rejects the source explicitly.

---

# M4. Conditional Communication Decomposition

## Goal

Transform conditional communication into:

```text
BODY + enable + EN_RECV / EN_SEND
```

with unconditional BODY-side communication.

## Tests

### Conditional Receive

Verify:

```text
enable = 1:
    external Receive occurs
    BODY receives real data

enable = 0:
    external Channel untouched
    BODY receives dummy / invalid data
```

### Conditional Send

Verify:

```text
enable = 1:
    external Send occurs

enable = 0:
    external Send suppressed
```

### Alternatives

Verify complementary enables for:

```systemverilog
if (sel)
    A.Receive(a);
else
    B.Receive(b);
```

and for conditional output alternatives.

### Nested conditions

Verify composed enables such as:

```text
x && y
x && !y
!x
```

### Identity preservation

Verify the association among:

```text
source communication
Channel endpoint
payload
enable
BODY-side communication
EN_RECV / EN_SEND
```

## Done when

All conditional external communication is decomposed and BODY-side
communication is unconditional.

---

# M5. Dependency / Validity Analysis + Semantic Validation

## Goal

Analyze:

```text
data dependency
control / enable dependency
conditional data validity
communication independence
```

## Tests

### Data dependency

For:

```systemverilog
A.Receive(a);
B.Receive(b);
c = a + b;
C.Send(c);
```

verify dependencies from `a` and `b` to `c`, without adding Receive ordering.

### Conditional validity

Accept:

```systemverilog
if (sel)
    A.Receive(a);

if (sel)
    y = f(a);
else
    y = DEFAULT_VALUE;
```

Reject:

```systemverilog
if (sel)
    A.Receive(a);

y = f(a);
```

### Communication independence

Reject:

```systemverilog
A.Receive(a);

if (a[0])
    B.Receive(b);
```

because `B.Receive` depends on data produced by `A.Receive`.

## Done when

The compiler produces:

```text
semantically validated transaction
+
dependency information
+
validity information
```

or rejects the source explicitly.

---

# M6. Asynchronous Microarchitecture Lowering

## Goal

Map validated transaction semantics to explicit asynchronous hardware.

## Required architecture cases

### A1 — 1R1S

Verify the baseline single-stage structure.

### A2 — 2R1S

Verify input synchronization.

Test both arrival orders:

```text
A then B
B then A
```

### A3 — 1R2S

Verify independent output distribution.

Delay one output acknowledgement and confirm that the other independent output
is not unnecessarily serialized.

### A4 — 2R2S

Verify the general multi-input / multi-output structure.

### Conditional communication

Verify placement and connectivity of:

```text
EN_RECV
EN_SEND
```

## Required architecture decisions

The microarchitecture must fully define:

```text
input synchronization
output distribution
controller structure
storage
matched-delay requirements
EN_RECV / EN_SEND placement
```

## Done when

No new hardware architecture decision is required by the RTL backend.

---

# M7. Structural RTL Backend

## Goal

Emit structural, synthesizable asynchronous SystemVerilog.

Initial backend:

```text
bundled-data + four-phase handshake
```

## Structural tests

Verify:

- component instances;
- ports and parameters;
- payload widths;
- signal connectivity;
- request / acknowledge directions;
- storage connectivity;
- matched-delay connectivity;
- EN_RECV / EN_SEND connectivity;
- deterministic generated names;
- deterministic RTL output.

## Simulation regression

Cover at least:

```text
1R1S
2R1S
1R2S
2R2S
conditional Receive
conditional Send
conditional input selection
conditional output selection
combined conditional communication
nested conditional communication
```

Verify:

```text
functional result
handshake completion
payload stability
input independence
output independence
conditional communication behavior
```

## Done when

Generated RTL preserves the semantics of the validated source transaction.

---

# Negative Regression Set

Keep permanent rejection tests for:

```text
Receive -> Send -> Receive
Receive after computation begins
repeated endpoint communication
dependent Receive
invalid conditional data use
other non-independent communication
```

These cases remain rejected unless the target architecture is explicitly
extended.

---

# Milestone Order

```text
M1  Frontend / Semantic Analysis
M2  Behavioral CSP IR
M3  Transaction Extraction + Structural Validation
M4  Conditional Communication Decomposition
M5  Dependency / Validity + Semantic Validation
M6  Asynchronous Microarchitecture Lowering
M7  Structural RTL Backend
```

Complete and verify one milestone before redesigning the next.