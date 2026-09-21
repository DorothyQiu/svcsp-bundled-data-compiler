# Communication Normalization

## Goal

Phase 3 normalizes conditional external communication before dependency
analysis and bundled-data pipeline synthesis.

Input:

    Behavioral CSP IR

Output:

    NormalizedModule

The normalized representation isolates conditional external communication
while making BODY-side token communication explicit and unconditional.

## Core Phase 3 Objects

A conditional communication occurrence is represented using:

- `CommunicationSite`: source-order/control anchor for the occurrence;
- `Enable`: symbolic condition identity;
- `BodyChannel`: internal BODY/wrapper token identity;
- `BodySend` or `BodyReceive`: unconditional BODY-side communication;
- `NormalizedSend` or `NormalizedReceive`: external wrapper semantics.

`CommunicationSite` does not execute the BODY token communication.
The authoritative BODY token operation is `BodySend` or `BodyReceive`.

The site, enable, BODY channel, BODY communication, and wrapper share exact
semantic object identities. Later phases must not reconstruct these
relationships by matching source locations.

Ordinary unconditional `Send` and `Receive` operations remain behavioral
operations.

## Conditional Receive

Source form:

    if (cond)
        X.Receive(x);

Normalized semantics:

    BODY produces Enable(cond)
    BODY unconditionally receives one token from an internal BodyChannel
    RECV wrapper conditionally interacts with external X

Enabled:

- consume and acknowledge one external X token;
- forward its real payload to the BODY channel;
- BODY receives valid data.

Disabled:

- do not consume or acknowledge external X;
- send a `DummyToken(data_is_valid=False)` through the BODY channel;
- BODY communication still completes;
- later dependency analysis must prevent invalid dummy data from escaping its
  validity guard.

## Conditional Send

Source form:

    if (cond)
        X.Send(value);

Normalized semantics:

    BODY produces Enable(cond)
    BODY unconditionally sends one token through an internal BodyChannel
    SEND wrapper conditionally interacts with external X

Enabled:

- wrapper consumes the BODY token;
- external Send is performed.

Disabled:

- wrapper still consumes the BODY token;
- external communication is suppressed.

The Phase 3/4 semantic dependency preserves the blocking nature of the
original `Send`: source continuation occurs after normalized Send-wrapper
completion, not merely after BODY-to-wrapper token handoff.

## Decomposed SVCSP Verification View

The same `NormalizedModule` may also be emitted as decomposed SVCSP:

    NormalizedModule
        |
        +--> Phase 4-7 compiler backend
        |
        +--> decomposed SVCSP emitter
             -> BODY + SEND/RECV wrappers
             -> original-vs-decomposed simulation

The decomposed SVCSP is a verification/output view from the same Phase 3 IR.
It is not another compiler IR and is never reparsed into the backend.

The emitted form uses CSP Channels for enable and BODY-side tokens.

### Conditional Send completion

The decomposed SEND wrapper also has a wrapper-to-BODY completion Channel.

Enabled:

- BODY sends enable and BODY data;
- wrapper consumes them;
- wrapper completes the blocking external Send;
- wrapper sends completion to BODY;
- BODY may then continue.

Disabled:

- wrapper consumes the BODY token;
- wrapper performs no external Send;
- wrapper immediately sends completion;
- BODY may then continue.

The completion Channel is required because BODY-to-wrapper handoff alone does
not mean the original blocking external `Send` has completed.

### Conditional Receive progress

Conditional Receive does not require a separate completion Channel.

Enabled:

- wrapper completes the external Receive;
- wrapper sends the real BODY token;
- BODY Receive completes.

Disabled:

- external channel remains untouched;
- wrapper sends a dummy/invalid BODY token;
- BODY Receive still completes.

## Current Support Boundary

Phase 3 can represent multiple conditional occurrences and composed guards.

The current decomposed-SVCSP emitter is intentionally narrower:

- one direct conditional Send: supported and simulation-equivalence tested;
- one direct conditional Receive: supported and simulation-equivalence tested;
- blocking order after conditional Send: regression tested;
- multiple conditional sites in one module: fail closed;
- repeated conditional operations on one external endpoint: fail closed;
- nested conditional sites: fail closed in the emitter;
- conditional communication inside fork/join: fail closed in the emitter.

Production decomposed SVCSP remains Channel-based.

Icarus Verilog 12 cannot pass these Channel interfaces through module ports in
the required way, so behavioral-equivalence tests use a test-only mechanical
Channel flattener to payload/request/acknowledge signals. That flattener is not
compiler output.

## Non-Goals

Phase 3 does not select:

- bundled-data controllers;
- storage primitives;
- matched delays;
- pipeline stages;
- structural RTL templates.
