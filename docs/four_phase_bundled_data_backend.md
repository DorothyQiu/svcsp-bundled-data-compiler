# Four-Phase Bundled-Data Backend

This document is the authoritative hardware specification for the initial
four-phase bundled-data backend.

Source-language support is defined in `supported_architectures.md`.
Conditional communication semantics are defined in
`communication_decomposition.md`.

## 1. Ordinary BODY Stage

An ordinary BODY transaction has:

```text
N independent input Channels
        |
        v
combinational logic
        |
        v
M independent output Channels
```

with `N >= 1` and `M >= 1`.

The target control structure is:

```text
Lreq[0..N-1]
      |
      v
Request Join
      |
      v
 base_Lreq
      |
      v
+-----------------------+
| Base 1x1 Half-Buffer  |
| Controller            |
+-----------------------+
   |                ^
   |                |
base_Lack        base_Rack
   |                |
   v                |
ACK Fanout       ACK Join
   |                ^
   v                |
Lack[0..N-1]   Rack[0..M-1]


Base controller q
      |
      v
Request Fanout
      |
      v
raw_req[0..M-1]
      |
      v
per-output matched delays
      |
      v
Rreq[0..M-1]
```

The bundled datapath is:

```text
Ldata[0..N-1]
      |
      v
combinational logic
      |
      v
structural output latch banks
      |
      v
Rdata[0..M-1]
```

The control and datapath structures are distinct but timing-related through
the matched-delay requirements.

## 2. Base Half-Buffer Controller

Every ordinary BODY stage contains exactly one canonical 1x1 half-buffer
controller.

Its state is:

```text
q = C(base_Lreq, !base_Rack)
```

where `C` is a Muller C-element.

The controller drives:

```text
base_Lack      = q
base_raw_Rreq  = q
storage_enable = q
```

The ordinary controller must be structural: a C-element primitive plus simple
gates/wires. It must not be implemented as a behavioral FSM.

For `N = 1`, the input request join is bypassed.

For `M = 1`, the output acknowledgement join is bypassed.

Therefore a `1R1S` ordinary stage reduces to:

```text
Lreq ----------\
                > Base Half-Buffer CTRL -----> Lack
Rack ----------/           |
                            +----> storage_enable
                            |
                            +----> matched delay ----> Rreq

Ldata -> combinational logic -> latch bank -> Rdata
```

These bypasses are M6 architecture decisions, not M7 optimizations.

## 3. Join, Fanout, Storage, and Delay

### Input request join

For `N > 1`, input requests are combined using Muller C-element logic before
driving `base_Lreq`.

The join must preserve four-phase C-element semantics:

```text
all requests high -> output high
all requests low  -> output low
mixed values      -> retain state
```

Its exact C-element network is an RTL-library implementation decision and must
be validated for the supported handshake assumptions.

### Input acknowledgement fanout

`base_Lack` is returned to all participating input Channels.

This is structural wiring or buffering.

### Output request fanout

`base_raw_Rreq` is fanned out to all ordinary output branches.

Each branch then passes through its own matched delay.

### Output acknowledgement join

For `M > 1`, output acknowledgements are combined using Muller C-element logic
before driving `base_Rack`.

### Payload storage

Ordinary BODY storage is a structural level-sensitive latch bank.

```text
storage_enable = q
```

For width `W`, the generic RTL library may instantiate `W` latch primitives.
The storage block must not invent additional handshake state.

### Matched delay

Each ordinary BODY output has one matched-delay requirement:

```text
raw_req[i] -> matched_delay[i] -> Rreq[i]
```

The delay corresponds to the worst-case bundled-data path for that output plus
the required timing margin.

The compiler decides placement and datapath association. Physical realization
is a technology-binding concern and may later use characterized or
post-silicon-programmable delay structures.

The current RTL library may use a simulation-oriented delay model.

## 4. Implementation Boundary

The target implementation level is:

```text
Component                         Target representation
--------------------------------  --------------------------------
Base half-buffer controller       structural C-element/gates
Input request join                structural C-element logic
Input ACK fanout                  structural wiring/buffers
Output request fanout             structural wiring/buffers
Output ACK join                   structural C-element logic
Ordinary payload storage          structural latch bank
User combinational logic          behavioral/continuous RTL
EN_RECV controller                behavioral for now
EN_SEND controller                behavioral for now
Matched delay                     technology-binding abstraction
```

C-elements and latch cells may remain explicit RTL-library primitive
boundaries until technology mapping supplies their final implementations.

## 5. EN_RECV and EN_SEND Stages

Conditional communication is implemented using separate EN_RECV and EN_SEND
micropipeline stages, not conditional-split stages.

Each EN stage remains a physical stage with:

```text
controller
+
structural payload storage
+
matched delay where an outgoing bundled-data request is launched
+
four-phase Channel interfaces
```

The controller differs from the ordinary base half-buffer controller because
it must also consume and interpret an enable token.

For the current backend, the EN_RECV and EN_SEND controllers may remain
behavioral until their final control circuits are defined.

EN_RECV semantics:

```text
enable = 1:
    consume enable
    perform external Receive
    store real payload
    launch BODY communication

enable = 0:
    consume enable
    leave external Channel untouched
    store InvalidPayload/dummy payload
    launch BODY communication
```

EN_SEND semantics:

```text
always:
    consume enable
    consume BODY payload

enable = 1:
    store payload
    launch external Send

enable = 0:
    suppress external communication
```

Detailed enable-token semantics remain defined in
`communication_decomposition.md`.

Lecture-style conditional split stages are outside the current backend.

## 6. RTL Library and M6/M7 Contract

The RTL library should expose architecture-level components such as:

```text
muller_c_element
four_phase_half_buffer_controller
request_join
ack_fanout
request_fanout
ack_join
latch_cell
bundled_data_latch_bank
matched_delay
en_receive_controller
en_send_controller
```

Exact filenames and primitive decomposition may evolve.

M6 must determine:

```text
request join or N=1 bypass
base half-buffer controller
input ACK fanout/direct connection
output request fanout/direct connection
ACK join or M=1 bypass
storage placement
per-output matched-delay placement
EN_RECV / EN_SEND placement
```

M7 only binds and emits the M6-selected structure.

M7 must not infer, insert, remove, or reorganize handshake topology.