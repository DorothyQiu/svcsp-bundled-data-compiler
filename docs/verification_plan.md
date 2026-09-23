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
external inputs, local variables, and parameters
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

Verify that explicit external-input identities are preserved separately from
local-variable identities.

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
effective enable-condition composition
conditional Receive validity-predicate recording
BODY-side communication remains unconditional
exact source/endpoint/payload/enable/BODY/EN-stage identity
```

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

Also reject reads with no reaching definition unless they identify an explicit
external input or parameter.

## M6. Asynchronous Microarchitecture Lowering

Use `four_phase_bundled_data_backend.md` as the expected architecture.

For `1R1S`, `2R1S`, `1R2S`, and `2R2S`, verify that all ordinary BODY
topology decisions match the backend specification:

```text
input request join or direct connection
input ACK fanout or direct connection
output request fanout or direct connection
output ACK join or direct connection
exactly one base half-buffer controller
```
Also include representative `N > 2` and `M > 2` cases to verify that M6
selects the same generic request-join, fanout, and acknowledgement-join
architecture without introducing a different topology.

Also verify:

```text
ordinary structural storage placement
one matched-delay requirement per ordinary BODY output

one Enable Channel per decomposed conditional communication
Enable Channel identity
EN_RECV PRE_INPUT availability
EN_RECV enable launch does not depend on its controlled BODY input
EN_SEND POST_INPUT availability
EN_SEND Enable Channel participates in transaction completion

EN_RECV / EN_SEND placement
one structural payload-storage resource per EN stage
one outgoing matched-delay requirement per EN stage
exact stage/storage/delay/port identity
```

M6 is complete when M7 requires no new architecture decisions.

## M7. Structural RTL Backend

### M7A. Structural Binding and Emission

Verify:

```text
exact M6 resource -> RTL instance traceability
component instances
ports and parameters
payload widths
signal connectivity
request/acknowledge directions
storage connectivity
matched-delay connectivity
Enable Channel sender connectivity
EN_RECV / EN_SEND connectivity
expression rendering
deterministic names and RTL output
```

Verify that M7 does not independently change:

```text
handshake topology
storage placement
matched-delay placement
Enable Channel availability
EN_RECV / EN_SEND physical resources
```

### M7B. RTL Library Components

Verify:

```text
structural Muller C-element behavior
ordinary half-buffer controller behavior
ordinary control reset
input request join
input ACK fanout
output request fanout
output ACK join
N > 2 / M > 2 monotonic C-element reduction behavior
structural latch-bank transparency and retention
no architectural payload-reset requirement
matched-delay control behavior
Enable Channel sender behavior
EN_RECV controller behavior
EN_SEND controller behavior
EN-stage payload retention
```

### M7C. Generated-RTL Simulation

Simulation coverage should include:

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

For each applicable scenario, verify:

```text
functional result
four-phase completion and return to idle
payload stability during active communication

multi-input correctness under different input-request arrival orders
multi-output correctness under different output-ACK arrival orders

enabled conditional external communication occurs
disabled conditional external communication remains untouched or suppressed

multi-transaction re-arming with changed payloads
conditional re-arming with changed enable values
```

## R7. External Input / Guard Source Contract

Verify:

```text
explicit non-channel ANSI input ports are accepted and retain exact identity
external inputs remain distinct from local variables in behavioral IR
PRE_INPUT Receive guards accept literals, parameters, and expressions of external inputs
PRE_INPUT Receive guards reject BODY Receive data and local assigned data
POST_INPUT Send guards accept valid unconditional Receive and computed data
M7 emits each explicit external input as a public RTL input port
M7 does not emit an explicit external input as an internal body_var_* signal
generated RTL drives PRE_INPUT enables from the public external-input port
PRE_INPUT simulation holds an external input stable through enable capture
```
Parameter guard semantics are verified independently of external-input identity.
End-to-end RTL module-parameter emission is outside the R7 external-input scope.
Keep permanent negative coverage for an undefined local guard: it must not be
accepted as an implicit module input.

## R8. Source Module Parameter Binding and RTL Emission

Verify:

```text
only supported source parameter int NAME / parameter int NAME = DEFAULT syntax
exact Expression Parameter identity against BehavioralModule.parameters
exact PayloadWidth Parameter identity against BehavioralModule.parameters
source-order source-module-parameter binding, including unused parameters
default and no-default declaration emission without invented values
symbolic-width declaration before every generated [W-1:0] or .WIDTH(W) use
strict separation of source-module parameters from RTL-library instance parameters
exact parameter-expression binding before generated expression rendering
integer parameter guard booleanization, including P = 2
multi-bit external-input guard booleanization
generated symbolic-width RTL compilation
```

Reject permanently:

```text
unowned or structurally-equal-but-not-identical Parameter references
duplicate or conflicting source parameter/public-port module-scope names
emission of a symbolic parameter use without its exact generated declaration
```

Ordinary parameter arithmetic and data-expression width inference, including
`x + P` and `Send(P)`, remain outside R8. Existing failure-closed width proof
rules continue to own those cases.

## R9. Control-Preserving Combinational Lowering

Verify source-order and enclosing-control preservation for every Assign in the
transaction combinational region. Generated RTL must have one coherent
combinational implementation for multiply written lvalues; it must not change
blocking assignments into multiple unconditional continuous drivers or infer
persistent state/latches. Verify the dedicated M7 receive-value signal per M6
InputPort and one source-preserving `always_comb` BODY data realization; M6
topology remains unchanged and structural/control wiring remains separate.

### R9A — Whole-Variable Control/Order Preservation

Verify whole-Variable ReceiveWrite and AssignWrite; Sequence and If/else
control; blocking assignment semantics; parallel noninterference for
whole-Variable accesses; same-target concurrent Receive rejection; and real
source-file generated-RTL simulation. R9A deliberately does not implement
selected lvalues: they remain fail-closed until R9B.

Permanent R9A generated-RTL simulation coverage includes if/else assignment
mux behavior; sequential blocking reassignment; nested guarded assignments;
ordinary single assignment; and conditional Receive valid-data fallback:

```systemverilog
if (sel) A.Receive(a);
if (sel) y = a;
else y = 0;
B.Send(y);
```

### R9B — Static Selected Lvalues

Verify normalized static lvalue representation; interval/coverage-aware M5
definitions; literal bit/range Receive targets; literal bit/range Assign
targets; partial-write coverage; overlap handling; no whole-variable widening
of selected writes; partial write followed by whole-variable read rejection;
unknown/default handling for unassigned bits; and real generated-RTL
simulation for every positive case.

Permanent negative coverage rejects dynamic or parameter-dependent data
lvalues such as `x[i] = value`, `A.Receive(x[i])`, `x[P] = value`, and
`x[HI:LO] = value`; a dynamic Channel selector such as `A[sel].Receive(x)`;
and an unsupported general symbolic packed range such as `logic [W:0] x`.
These cases must fail before M7. Constant/static Channel selectors and
normalized symbolic widths such as `[W-1:0]` remain separately covered
supported forms.

For Parallel combinational branches, permanently reject overlapping Write/Write
and Write/Read accesses, whole-Variable/selected overlap, undecidable overlap,
and concurrent Receives targeting the same or overlapping source lvalue.

Verification follow-up, not a claimed correctness defect: comparison/logical
guards; bit/range-select Send expressions; concatenation guards; ordinary
external-input payload data; and generated 3R/3S integration.

## Permanent Negative Regression

Rejection behavior defined by `supported_architectures.md` and by the owning
compiler stage must remain permanently covered.

Keep focused rejection tests at the stage that owns each rule.

At minimum preserve coverage for:

```text
interleaved / multi-stage communication
Receive after computation begins
repeated endpoint communication
dependent communication
invalid conditional receive data use
other unsupported communication ordering
```

Also keep a small public-entrypoint rejection smoke set through
`compile_async_file()` so that stage-local validation cannot disappear from
the integrated compiler flow.
