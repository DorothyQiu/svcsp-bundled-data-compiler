# Supported Source Architectures

This document defines the source programs accepted by the target compiler.

## Target Transaction Model

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

The compiler later decomposes this into unconditional BODY-side communication
plus conditional external communication.

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
one transaction stage
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