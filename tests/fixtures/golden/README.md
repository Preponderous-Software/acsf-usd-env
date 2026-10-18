# Golden-run fixtures (copied from ACSF)

These directories are unmodified copies of the golden runs committed to the
[Artificial Consciousness Simulation Framework](https://github.com/Preponderous-Software/artificial-consciousness-simulation-framework)
(ACSF) at commit
[`64486d9c580325df33f03e8cb20dc7b8a227189a`](https://github.com/Preponderous-Software/artificial-consciousness-simulation-framework/tree/64486d9c580325df33f03e8cb20dc7b8a227189a/consciousness-sim/experiments/golden),
path `consciousness-sim/experiments/golden/<Name>/` (`journal.jsonl` and `state.json`; ACSF
itself excludes `episodic.jsonl` from these directories).

ACSF is distributed under the MIT License; its license text is reproduced in
[`LICENSE-ACSF`](./LICENSE-ACSF), copied from the same commit. The files are byte-identical to
the originals and must not be edited: tests compare exports against them, and the counts in
`MVP.md` (for example Echo's 200 `thought` and 67 `perception` events) are taken from them.

| Directory | Events at 64486d9 |
|---|---|
| `Echo/` | 200 `thought`, 67 `perception`, 34 `reflection` |
| `Rafael/` | 83 `thought`, 13 `reflection` |
| `Sage/` | 76 `thought`, 25 `perception`, 9 `reflection` |
| `Wren/` | 200 `thought`, 66 `perception`, 23 `reflection` |

None of the four journals has `cycle` events (they predate ACSF #196), so all are indexed by
counting thoughts (`acsf:cycleIndexing = "counted"`).
