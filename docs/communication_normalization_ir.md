# Communication Normalization IR

Phase 3 transforms a `BehavioralModule` with
`normalize_communication(module)` into a separate `NormalizedModule`.

Ordinary unconditional `Send` and `Receive` operations remain behavioral
operations.

Each conditional communication occurrence is represented explicitly by:

- `CommunicationSite`: source-order/control anchor;
- unique symbolic `Enable`;
- `BodyChannel`: internal BODY/wrapper token identity;
- `BodySend` or `BodyReceive`: unconditional BODY-side communication;
- `NormalizedSend` or `NormalizedReceive`: wrapper-side external semantics.

A conditional communication is NOT represented by a behavioral `Skip`.

`CommunicationSite` is a standalone Phase 3 marker. It preserves control and
source ordering but does not itself perform the BODY token transfer.

The site, BODY communication, BODY channel, enable, and normalized wrapper
share exact semantic object identities. Phase 4 uses those identities directly
rather than reconstructing associations from source locations.

Nested conditions may be composed symbolically in Phase 3.

## Conditional Receive IR

`NormalizedReceive` represents conditional external consumption.

Its shared `DummyToken(data_is_valid=False)` represents the disabled path:

- no external token is consumed or acknowledged;
- BODY-side communication still produces one token.

`BodyReceive.data_valid_when` references the exact shared `Enable`.

Later dependency analysis must ensure consumers cannot use invalid dummy data
outside the guard that makes the receive valid.

## Conditional Send IR

`NormalizedSend` represents conditional external Send semantics.

`BodySend` represents the unconditional BODY-side token transfer.

`BodySend.payload_valid_when` references the exact shared `Enable`.

Source continuation after a conditional Send is ordered downstream after
normalized wrapper completion, rather than merely after internal BODY-token
handoff.

## Verification View

Decomposed SVCSP may be emitted directly from this same `NormalizedModule`.

It is:

- an output/verification view;
- not a second IR;
- not reparsed into the Phase 4-7 backend.

The emitted decomposed SVCSP may introduce executable CSP enable, BODY, and
completion Channels to realize the normalized semantics, but those emitted
Channels do not replace the Phase 3 symbolic IR identities.

## Non-Goals

Phase 3 introduces no:

- pipeline stages;
- bundled-data controllers;
- storage primitives;
- matched delays;
- structural RTL.
