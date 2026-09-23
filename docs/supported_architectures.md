# Supported Source Architectures

This document defines the source programs accepted by the target compiler.

## Target Transaction Model

One supported top-level process represents one transaction, not necessarily one
physical stage:

```text
N independent Channel Receive(s)
              |
              v
      Combinational Logic
              |
              v
M independent Channel Send(s)
```

with:

```text
N >= 1
M >= 1
```

Receive and Send operations may be unconditional or conditional.

The current model assumes:

```text
input communication
-> combinational computation
-> output communication
```

Ordered or multi-stage communication is outside the current target.

## External Inputs and Guard Sources

An SVCSP module may declare explicit non-channel ANSI input ports, for example:

```systemverilog
module select_input(input logic sel, interface A, B);
```

These ports are environment-supplied transaction control or data inputs.  They
are distinct from local variables and may be read in supported expressions.
An undefined local variable is not an implicit module input.  This source model
does not introduce persistent state.

Conditional Receive guards must be computable before BODY input acquisition.
They may use literals, parameters, explicit external inputs, and expressions
composed from those pre-input sources.  They may not use BODY Receive data or
transaction-local assigned data.

Conditional Send guards may additionally use valid unconditional Receive data
and transaction-local computed values, subject to dependency and validity
analysis.

Parameters are valid semantic guard sources. Their source and generated-RTL
contract is defined below.

## Source Module Parameters

The supported source parameter subset is limited to module-header declarations:

```systemverilog
parameter int NAME
parameter int NAME = DEFAULT
```

For example:

```systemverilog
module sized #(parameter int W = 8) (Channel #(W) A, B);
```

Other parameter types, `localparam`, and type parameters are outside the
current source language.

`BehavioralModule.parameters` is the canonical ordered identity set for source
parameters. Every `Parameter` reference in a behavioral `Expression`, and
every `Parameter` recorded by `PayloadWidth.parameters`, must identify the
exact canonical object owned by that tuple. A structurally equal replacement is
not an equivalent source identity.

Source parameters may be used where the current source and width rules can
already prove the expression, including symbolic payload widths and guards.
R8 does not add parameter-aware width or type inference for ordinary data
expressions. Expressions such as `x + P` and `Send(P)` remain unsupported when
the existing proof rules cannot establish their width; the compiler must fail
closed rather than resize or reinterpret them.

Generated RTL preserves every source parameter in source order, including an
otherwise unused parameter. It declares them before the module port list:

```systemverilog
module sized #(
    parameter int W = 8
) (
    // ports
);
```

If a supported source `Parameter` has no default, generated RTL emits its
corresponding parameter declaration without inventing a value. Names are
preserved exactly. Duplicate or conflicting generated module-scope names are
errors; the compiler must not silently rename a source parameter.

Generated component-instance parameters, such as `.WIDTH(W)`, are distinct
from source-module parameters. A source parameter defines the public generated
module interface; an instance parameter configures an RTL-library component.
An emitted parameter expression or symbolic width must resolve through the
exact bound source-parameter identity. Generated RTL must not use `W` in
`[W-1:0]` or `.WIDTH(W)` unless that exact source parameter is declared in the
generated module parameter list.

## Unconditional Transactions

### 1 Receive -> 1 Send

```systemverilog
always begin
    A.Receive(a);
    b = f(a);
    B.Send(b);
end
```

### Multiple Receives

```systemverilog
always begin
    A.Receive(a);
    B.Receive(b);

    c = f(a, b);

    C.Send(c);
end
```

Independent Receives written sequentially are interpreted as concurrent.

The compiler should accept this form but emit a warning recommending explicit
concurrency:

```systemverilog
fork
    A.Receive(a);
    B.Receive(b);
join
```

Explicit `fork` / `join` is the preferred form.

### Multiple Sends

```systemverilog
always begin
    A.Receive(a);

    c = f(a);
    d = g(a);

    C.Send(c);
    D.Send(d);
end
```

Independent Sends written sequentially are also interpreted as concurrent.

The compiler should warn and recommend:

```systemverilog
fork
    C.Send(c);
    D.Send(d);
join
```

### General N-Input / M-Output Transaction

```systemverilog
always begin
    A.Receive(a);
    B.Receive(b);

    c = f(a, b);
    d = g(a, b);

    C.Send(c);
    D.Send(d);
end
```

This is the general current source architecture.

## Communication Independence

Communications within one input or output region must be semantically
independent.

Supported:

```systemverilog
A.Receive(a);
B.Receive(b);
```

when neither communication depends on completion or data from the other.

Not supported:

```systemverilog
A.Receive(a);

if (a[0])
    B.Receive(b);
```

because the decision to perform `B.Receive` depends on data produced by
`A.Receive`.

The same principle applies to output communication.

## Conditional Receive

Conditional Receive is supported:

```systemverilog
if (sel)
    A.Receive(a);
```

Here `sel` must be an explicit external input (or the guard must otherwise use
only the permitted pre-input sources defined above).

The compiler later decomposes this into unconditional BODY-side communication
plus conditional external communication.

At M4, the condition is a logical `Enable`. In the current four-phase
bundled-data half-buffer backend, M6 realizes each Enable as a one-bit
four-phase bundled-data Channel. BODY sends exactly one token per transaction
to the corresponding separate EN_RECV or EN_SEND micropipeline stage. These
are control outputs rather than ordinary post-join data outputs, so an EN_RECV
enable is available independently of the BODY input it controls. EN_RECV always
emits a BODY token (real payload when enabled; InvalidPayload/dummy payload when
disabled). EN_SEND always consumes its BODY token, forwarding it externally
only when enabled and otherwise dropping its payload without an extra dummy
token.

Any use of the received value must be valid under the corresponding condition.

Valid example:

```systemverilog
if (sel)
    A.Receive(a);

if (sel)
    y = f(a);
else
    y = DEFAULT_VALUE;

B.Send(y);
```

Invalid example:

```systemverilog
if (sel)
    A.Receive(a);

y = f(a);

B.Send(y);
```

because `a` is not valid when `sel` is false.

## Conditional Send

Conditional Send is supported:

```systemverilog
A.Receive(a);

c = f(a);

if (sel)
    B.Send(c);
```

The external Send occurs only when the condition is enabled.

## Conditional Alternatives

Input selection is supported:

```systemverilog
if (sel)
    A.Receive(a);
else
    B.Receive(b);

y = sel ? a : b;

C.Send(y);
```

Output selection is supported:

```systemverilog
A.Receive(a);

c = f(a);
d = g(a);

if (sel)
    C.Send(c);
else
    D.Send(d);
```

Combined conditional input and output are also part of the target model.

## Nested Conditions

Nested conditional communication is supported by the target architecture.

Example:

```systemverilog
if (x) begin
    if (y)
        A.Receive(a);
    else
        B.Receive(b);
end
else begin
    C.Receive(c);
end
```

The effective conditions are conceptually:

```text
A: x && y
B: x && !y
C: !x
```

The physical representation of these controls is not defined at the source
architecture level.

## Unsupported Source Structures

### Interleaved communication phases

```systemverilog
A.Receive(a);
B.Send(a);
C.Receive(c);
```

Reject because this requires more than one communication phase.

### Receive after computation begins

```systemverilog
A.Receive(a);

x = f(a);

B.Receive(b);

C.Send(x);
```

Reject because input communication must precede the combinational region.

### Dependent Receive

```systemverilog
A.Receive(a);

if (a[0])
    B.Receive(b);
```

Reject because the Receives are not independent.

### Repeated endpoint communication

```systemverilog
A.Receive(a);
A.Receive(b);
```

or:

```systemverilog
A.Receive(a);

B.Send(a);
B.Send(a);
```

Reject because repeated communication on one endpoint requires explicit
serialization, arbitration, or a richer transaction model.

## Scope Summary

Supported:

```text
one process
one transaction (which may lower to multiple physical micropipeline stages)
N independent input communications
combinational computation
M independent output communications
conditional communication
nested conditions
```

Not currently targeted:

```text
ordered communication
multi-stage transactions
repeated endpoint communication
shared-channel arbitration
```

Conditional communication semantics are defined in
`communication_decomposition.md`.
