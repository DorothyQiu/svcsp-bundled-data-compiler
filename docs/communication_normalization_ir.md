# Communication Normalization IR

Phase 3 transforms a `BehavioralModule` with
`normalize_communication(module)` into a separate `NormalizedModule`. The
normalized BODY reuses immutable Behavioral CSP nodes: unconditional `Send` and
`Receive` pass through unchanged, while each conditional external communication
is replaced by `Skip` and recorded as a dedicated wrapper.

Each wrapper has a unique symbolic `Enable(name, occurrence, condition)`. An
enable is an IR identity and symbolic expression, not an RTL signal or channel.
Nested conditions are combined symbolically with `&&` and `!`.

`NormalizedReceive` records that it consumes externally only when enabled. Its
`DummyToken(data_is_valid=False)` represents the disabled path: no external
token is consumed or acknowledged, while an internal BODY-side token is made
available. `NormalizedSend` records that it consumes its BODY-side token on
every iteration, communicates externally only when enabled, and suppresses the
external communication when disabled.

The pass does not prove that a BODY computation ignores disabled receive data;
the dummy token explicitly marks that data as invalid for a later dependency
analysis phase. It introduces no implementation channels, controllers, storage,
delays, pipeline stages, or RTL.
