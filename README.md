# acsf-usd-env

A standalone, read-only exporter that turns a run of the [Artificial Consciousness Simulation Framework](https://github.com/Preponderous-Software/artificial-consciousness-simulation-framework) (ACSF) into an [OpenUSD](https://openusd.org) stage — each instance as a prim, its perceptions, thoughts, reflections and replies as time-sampled attributes indexed by thought cycle — so a run can be inspected and scrubbed through in any USD tool. ACSF has no spatial world, so the stage's layout is a **schematic, explicitly synthetic** one; nothing in it claims the agent has a body or a position.

**Status: in development.** The MVP is being implemented milestone by milestone (the `mvp`-labelled issues); until the last one lands, parts of what the design documents describe do not exist yet.

- **[CONCEPT.md](./CONCEPT.md)** — the problem, who it is for, what ACSF's "environment" actually is, non-goals, prior art
- **[MVP.md](./MVP.md)** — the smallest end-to-end slice, acceptance criteria, and the milestone list (one GitHub issue each)
- **[ARCHITECTURE.md](./ARCHITECTURE.md)** — components, data flow, the ACSF → USD mapping table, dependencies, testing strategy, open questions
- **[ACSF](https://github.com/Preponderous-Software/artificial-consciousness-simulation-framework)** — the framework whose runs this tool reads

The exporter reads ACSF's on-disk artifacts (`journal.jsonl`, `state.json`) and never writes to an instance directory. The reverse direction — an ACSF instance perceiving a USD scene — needs a small change inside ACSF and is written up as a proposal, not built here (see [ARCHITECTURE.md § Direction of sync](./ARCHITECTURE.md#direction-of-sync)).

This is research tooling, not production infrastructure. Like ACSF itself, it avoids claims beyond observable functional behaviour: a prim named after an instance is a visualisation of logged events, not a representation of a mind.

## Install

Requires Python 3.11 or newer.

```bash
pip install .            # installs the package and the `acsf-usd` command
pip install ".[dev]"     # plus pytest, mypy and ruff for development
acsf-usd --version
```

The only runtime dependency is [`usd-core`](https://pypi.org/project/usd-core/), pinned to `26.8`, which provides the `pxr` Python bindings. It does **not** ship `usdview` or any imaging plugins, so viewing a stage needs a full OpenUSD build or another USD-capable tool.

## Development

```bash
ruff check . && ruff format --check .
mypy            # strict; configured in pyproject.toml
pytest
```

CI runs the same commands on Python 3.11 and 3.13. Tests use no network and no LLM.

## License

MIT — see [LICENSE](./LICENSE). The planned runtime dependency `usd-core` is distributed under its own license (the Tomorrow Open Source Technology License 1.0; see [ARCHITECTURE.md § Dependencies](./ARCHITECTURE.md#dependencies)).
