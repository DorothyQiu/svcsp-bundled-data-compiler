# Communication Normalization

## Goal

Normalize conditional channel communication before bundled-data pipeline synthesis.

The main computation body should not directly contain conditional external
Send/Receive operations. Conditional communication is represented using
enable signals and dedicated SEND/RECV wrapper structures.

## Conditional Receive

Conceptually, transform:

    if (cond)
        X.Receive(x)

into:

    BODY computes X_enable

plus a conditional receive wrapper.

Semantics:

- enable = 1:
  - consume one token from the external channel
  - forward the real data token to the internal BODY-side channel

- enable = 0:
  - do not consume or acknowledge the external token
  - generate a dummy internal token so the BODY-side pipeline can continue

The BODY must not depend on the dummy data value when enable = 0.

## Conditional Send

Conceptually, transform:

    if (cond)
        X.Send(x)

into:

    BODY produces X_enable and internal X data

plus a conditional send wrapper.

Semantics:

- the BODY-side token is consumed every iteration
- enable = 1:
  - send the token to the external channel
- enable = 0:
  - suppress external communication

## Compiler Role

This is a normalization pass between Behavioral CSP IR and dependency /
pipeline analysis.

It does not select the final bundled-data controller implementation.

Input:
    Behavioral CSP IR

Output:
    Normalized CSP representation containing:
    - BODY behavior
    - enable values/channels
    - conditional SEND wrappers
    - conditional RECV wrappers

The downstream pipeline synthesis stage may then operate on a more regular
BODY with conditional external communication isolated at dedicated wrappers.
