# acsf-usd-env

A standalone, read-only exporter that turns a run of the [Artificial Consciousness Simulation Framework](https://github.com/Preponderous-Software/artificial-consciousness-simulation-framework) (ACSF) into an [OpenUSD](https://openusd.org) stage — each instance as a prim, its perceptions, thoughts, reflections and replies as time-sampled attributes indexed by thought cycle — so a run can be inspected and scrubbed through in any USD tool. ACSF has no spatial world, so the stage's layout is a **schematic, explicitly synthetic** one; nothing in it claims the agent has a body or a position.

![Per-cycle timeline of the four ACSF golden runs, read back from their USD exports](./docs/images/golden-timeline.png)

*What the four bundled ACSF golden runs journaled at each thought cycle, read back with usd-core from `acsf-usd export` stages ([`scripts/timeline.py`](./scripts/timeline.py)); a record of logged events, not of a mind, a body or a place.*

![Animated render of the Echo and Wren golden-run stages in the synthetic schematic layout](./docs/images/golden-stage.gif)

*A USD stage of the Echo and Wren golden runs' journaled events, in acsf-usd-env's synthetic schematic layout, animated over cycles in headless Blender from the stage's own time samples ([`scripts/render_stage.py`](./scripts/render_stage.py)): one node per thought, perception pulses from the `Stimulus` sphere, rings for reflections, a tint for the final mood. The spiral, sizes and colours are drawing choices — synthetic schematic layout, not a body or a place.*

**Status: MVP complete.** All nine MVP milestones are implemented: batch export of one or several instances, `--follow` live mode, `validate` and `describe`, plus the design note for the import direction. The tool reads ACSF's file formats at commit `64486d9`; it is research tooling, version 0.1.0, with no stability guarantee for its prim and attribute names yet.

- **[CONCEPT.md](./CONCEPT.md)** — the problem, who it is for, what ACSF's "environment" actually is, non-goals, prior art
- **[MVP.md](./MVP.md)** — the smallest end-to-end slice, acceptance criteria, and the milestone list (one GitHub issue each)
- **[ARCHITECTURE.md](./ARCHITECTURE.md)** — components, data flow, the ACSF → USD mapping table, dependencies, testing strategy, open questions
- **[ACSF](https://github.com/Preponderous-Software/artificial-consciousness-simulation-framework)** — the framework whose runs this tool reads

The exporter reads ACSF's on-disk artifacts (`journal.jsonl`, `state.json`) and never writes to an instance directory. The reverse direction — an ACSF instance perceiving a USD scene — needs a small change inside ACSF and is written up as a proposal, not built here: [docs/usd-perception-proposal.md](./docs/usd-perception-proposal.md) (see also [ARCHITECTURE.md § Direction of sync](./ARCHITECTURE.md#direction-of-sync)).

This is research tooling, not production infrastructure. Like ACSF itself, it avoids claims beyond observable functional behaviour: a prim named after an instance is a visualisation of logged events, not a representation of a mind.

## Install

Requires Python 3.11 or newer.

```bash
pip install .            # installs the package and the `acsf-usd` command
pip install ".[dev]"     # plus pytest, mypy and ruff for development
acsf-usd --version
```

The only runtime dependency is [`usd-core`](https://pypi.org/project/usd-core/), pinned to `26.8`, which provides the `pxr` Python bindings. It does **not** ship `usdview` or any imaging plugins, so viewing a stage needs a full OpenUSD build or another USD-capable tool.

## Usage

```bash
acsf-usd export ~/.consciousness/Echo -o echo.usda   # an instance directory
acsf-usd export Echo                                 # an instance name under $CONSCIOUSNESS_HOME
acsf-usd export path/to/run-dir --binary             # binary <name>.usdc instead of text .usda
acsf-usd export Echo Wren -o compare.usda            # two instances on one shared timeline
acsf-usd export ~/.consciousness                     # every instance: run.usda + one layer each
```

An input may be an instance directory, an experiment run directory or a golden-run directory,
an instance name under `CONSCIOUSNESS_HOME` (default `~/.consciousness`), or a directory of
instance directories (a whole `CONSCIOUSNESS_HOME`, or an experiment run with `replicate-<i>/`
subdirectories). Inputs are only read: `acsf-usd` refuses an output path inside any of them.
Several instances are written as one layer each plus a root layer that sublayers them and
offsets each instance along X (see
[ARCHITECTURE.md § Multi-instance composition](./ARCHITECTURE.md#multi-instance-composition)).
Without `-o`, the output of a single-instance export is
`<name>.usda` (or `<name>.usdc` with `--binary`) in the current directory, and of a
multi-instance export `run.usda` (`run.usdc`); an `-o` extension
that contradicts `--binary` is a usage error. `--time-codes-per-second N` sets the playback
rate (default one cycle per second) and `--source-commit SHA` records the ACSF commit a run was
produced with.

```bash
acsf-usd export ~/.consciousness/Echo -o echo.usda --follow   # keep echo.usda current; Ctrl-C to stop
```

`--follow` keeps one running instance's stage current. It polls `journal.jsonl` every
`--poll-interval` seconds (default 0.5) and consumes only complete lines. Each update
re-authors the stage and replaces the output atomically, so a USD tool never sees a
half-written file. The instance directory is only read; nothing is created in it. On Ctrl-C
or SIGTERM it makes a final write and exits 0. That final file is byte-identical to a batch
export of the same `journal.jsonl` and `state.json` (see
[ARCHITECTURE.md § Live mode](./ARCHITECTURE.md#live-mode-follow)). USD tools that cache
layers may need to reload the file to see updates.

```bash
acsf-usd validate echo.usda ~/.consciousness/Echo    # exit 0 when the stage matches the journal
acsf-usd describe Echo                               # what an export would contain; writes nothing
```

`validate` re-reads a stage (`.usda`, `.usdc`, or a multi-instance root) and checks it against
the same input(s). Every journaled thought, reflection, perception and reply must be held
byte-for-byte at its cycle, every other cycle must hold an empty string, and
`acsf:cycleIndexing` must match. Any mismatch is reported by cycle, and the exit status is 1.

The USD time code is the ACSF thought cycle: scrubbing to frame *n* shows what ACSF journaled
for cycle *n*. Prim paths and attributes are listed in
[ARCHITECTURE.md § ACSF → USD mapping](./ARCHITECTURE.md#acsf--usd-mapping).

### What the geometry means — and does not

ACSF has no spatial world, so a stage has no real geometry to show. To make it viewable in an
ordinary USD tool, the exporter adds a **schematic, synthetic** layout: a sphere above each
instance's origin that is visible only on cycles with a perception, and one cube per person
the instance replied to, on a ring around the origin, visible only on cycles with a reply to
that person. The geometry shows *when* something was journaled — nothing else. The sphere's
height, the ring, a cube's angle (a hash of the person's name) and the X offset between
instances are arbitrary drawing choices: they are not positions, distances or directions
ACSF knows, the instance has no body or location, and nothing in the stage feeds back into
ACSF. Every stage records `acsf:layout:synthetic = true`, and each geometry prim's
documentation says the same.

## Development

```bash
ruff check . && ruff format --check .
mypy            # strict; configured in pyproject.toml
pytest
```

CI runs the same commands on Python 3.11 and 3.13. Tests use no network and no LLM.

## License

MIT — see [LICENSE](./LICENSE). The runtime dependency `usd-core` is distributed under its own license (the Tomorrow Open Source Technology License 1.0; see [ARCHITECTURE.md § Dependencies](./ARCHITECTURE.md#dependencies)).
