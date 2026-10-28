# Test fixtures

`instances/` holds small **synthetic** ACSF instance directories written for these tests
(not output of a real ACSF run). Their event shapes follow what ACSF journals at commit
`64486d9c580325df33f03e8cb20dc7b8a227189a`:

- `Nova/` — a journal with `cycle` events covering every event type ACSF writes, a failed
  cycle (no cycle 3), an unknown event type, a corrupt line, a non-object line and a blank line.
- `Ash/` — a journal without `cycle` events (indexed by counting thoughts, like the golden runs).
- `Lumen/` — a journal whose `cycle` events carry `mood` from cycle 2 on (the shape proposed
  upstream in ACSF #220).

`golden/` holds copies of ACSF's committed golden runs, attributed and pinned in
[`golden/README.md`](./golden/README.md). `view/` holds the reference images the Strata
viewer tests compare their headless renders with (Echo at cycles 1, 116 and 200; the four
golden runs at cycle 60, Wren's panel).

Tests never write into these directories; they copy them to a temporary directory first when
a test needs to modify an instance.
