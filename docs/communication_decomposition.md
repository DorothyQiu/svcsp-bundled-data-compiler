# Conditional Communication Decomposition

This document defines the target decomposition semantics for conditional Channel
communication.

The transformation is:

```text
conditional Receive / Send
          |
          v
BODY + enable + EN_RECV / EN_SEND
```

`enable` is an abstract control concept. Its physical realization is not fixed
at this stage.

---

# 1. BODY

`BODY` is used only after conditional communication decomposition.

BODY contains:

```text
unconditional Channel Receive(s)
combinational logic
unconditional Channel Send(s)
enable generation
```

Conditional external communication is moved outside BODY.

The key invariant is:

```text
BODY-side communication is unconditional.
```

---

# 2. Conditional Receive

Source:

```systemverilog
if (cond)
    A.Receive(a);
```

is decomposed conceptually into:

```text
            enable
BODY ------------------> EN_RECV
                          ^
                          |
                     external A

EN_RECV
   |
   v
BODY-side Receive
```

The BODY-side Receive always occurs.

## Enabled

```text
enable = 1
```

`EN_RECV`:

```text
perform external A.Receive
        |
        v
obtain real payload
        |
        v
provide real data to BODY
```

## Disabled

```text
enable = 0
```

`EN_RECV`:

```text
leave external A untouched
        |
        v
provide dummy / invalid data to BODY
```

The dummy value exists only so that the BODY-side Receive remains
unconditional.

It is not semantically valid application data.

---

# 3. Conditional Send

Source:

```systemverilog
if (cond)
    B.Send(value);
```

is decomposed conceptually into:

```text
BODY
 |
 +---- data ----> EN_SEND ----> external B
 |
 +--- enable ---> EN_SEND
```

The BODY-side Send always occurs.

## Enabled

```text
enable = 1
```

`EN_SEND` forwards the BODY-side communication to the external Channel.

## Disabled

```text
enable = 0
```

`EN_SEND` consumes the BODY-side communication but suppresses the external
Send.

---

# 4. Conditional Alternatives

For:

```systemverilog
if (sel)
    A.Receive(a);
else
    B.Receive(b);
```

the effective enables are:

```text
A:  sel
B: !sel
```

The non-selected external Channel remains untouched.

Similarly:

```systemverilog
if (sel)
    C.Send(c);
else
    D.Send(d);
```

produces:

```text
C:  sel
D: !sel
```

Only the enabled external Send occurs.

---

# 5. Nested Conditions

Nested communication conditions are represented by composed enables.

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

produces:

```text
A:  x && y
B:  x && !y
C: !x
```

This decomposition defines logical control only.

It does not require `enable` to be implemented as a wire, Channel, or specific
handshake protocol.

---

# 6. Conditional Data Validity

Conditional Receive introduces data-validity constraints.

For:

```systemverilog
if (sel)
    A.Receive(a);
```

the compiler records:

```text
a is valid when sel is true
```

When `sel` is false, BODY may receive dummy / invalid data.

Valid use:

```systemverilog
if (sel)
    A.Receive(a);

if (sel)
    y = f(a);
else
    y = DEFAULT_VALUE;
```

Invalid use:

```systemverilog
if (sel)
    A.Receive(a);

y = f(a);
```

because `a` may be invalid.

The decomposition stage records this validity relationship.

Later dependency / validity analysis proves whether each use is legal.

---

# 7. Required Identity Preservation

For every decomposed conditional communication, preserve the association among:

```text
source communication site
Channel endpoint
payload
enable condition
BODY-side communication
EN_RECV / EN_SEND
```

Later compiler stages rely on these identities.

---

# 8. Stage Boundary

This stage decides:

```text
which communication is conditional
which enable controls it
which EN_RECV / EN_SEND is required
which BODY-side communication becomes unconditional
```

This stage does not decide:

```text
physical enable representation
input synchronization topology
output distribution topology
storage implementation
matched-delay implementation
RTL component selection
```

Those decisions belong to later compiler stages.

---

# Summary

Conditional Receive:

```text
enable = 1:
    external Receive -> real data -> BODY

enable = 0:
    external Channel untouched
    dummy / invalid data -> BODY
```

Conditional Send:

```text
enable = 1:
    BODY -> external Send

enable = 0:
    BODY communication consumed
    external Send suppressed
```

Invariant:

```text
BODY-side communication is unconditional.
```