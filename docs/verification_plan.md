# Verification and Implementation Plan

For every milestone:

```text
define expected behavior
-> add/update tests
-> modify implementation
-> run regression
-> proceed only when stable
```

Architecture definitions belong in the corresponding specification documents;
this file only records what must be verified.

## M1. Frontend / Semantic Analysis

Verify:

```text
modules and processes
Channel endpoints
variables and parameters
lexical scope
payload widths
Receive / Send
assignments
if / else
nested conditions
fork / join
supported expressions
source locations
```

Required properties:

```text
use pyslang
preserve semantic identity
fail closed on unresolved required information
make no asynchronous hardware decisions
```

## M2. Behavioral CSP IR

Verify representation and identity preservation for:

```text
Sequence
Parallel
If
Receive
Send
Assign
Skip
```

A behavioral `Sequence` must not imply serialized hardware handshakes.

## M3. Transaction Extraction + Structural Validation

Positive coverage:

```text
1R1S
2R1S
1R2S
2R2S
explicit fork/join
sequential independent Receives
sequential independent Sends
```

Negative coverage:

```text
Receive -> Send -> Receive
Receive after computation begins
repeated endpoint communication
```

Sequential independent communications are accepted as concurrent and should
produce the documented warning.

## M4. Conditional Communication Decomposition

Verify:

```text
conditional Receive
conditional Send
input alternatives
output alternatives
nested conditions
exact source/endpoint/payload/enable identity
```

BODY communication remains unconditional.

Detailed expected semantics are defined in
`communication_decomposition.md`.

## M5. Dependency / Validity Analysis

Verify:

```text
data dependencies
control dependencies
conditional receive validity
communication independence
expression-local guards
cross-parallel dependencies
```

Reject at least:

```text
Receive-dependent Receive enable
invalid conditional receive data use
other non-independent communication
```

## M6. Asynchronous Microarchitecture Lowering

Use `four_phase_bundled_data_backend.md` as the expected architecture.

Verify at minimum:

```text
1R1S:
    request join bypass
    one base half-buffer controller
    ACK join bypass

2R1S:
    input request join
    one base half-buffer controller
    output ACK direct

1R2S:
    input request direct
    one base half-buffer controller
    output request fanout
    output ACK join

2R2S:
    input request join
    one base half-buffer controller
    output request fanout
    output ACK join
```

Also verify:

```text
structural storage placement
one matched-delay requirement per ordinary BODY output
EN_RECV placement
EN_SEND placement
enable Channel availability and identity
```

M6 is complete when M7 requires no new architecture decisions.

## M7. Structural RTL Backend

Verify:

```text
component instances
ports and parameters
payload widths
signal connectivity
request/acknowledge directions
storage connectivity
matched-delay connectivity
EN_RECV / EN_SEND connectivity
deterministic RTL output
```

Generated-RTL simulation coverage should include:

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
four-phase completion
payload stability
input independence
output independence
conditional communication behavior
multi-transaction re-arming
```

## Permanent Negative Regression

Keep rejection tests for:

```text
Receive -> Send -> Receive
Receive after computation begins
repeated endpoint communication
dependent Receive
invalid conditional data use
other unsupported communication ordering
```