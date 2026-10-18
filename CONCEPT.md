# Concept

All ACSF references below are pinned to commit
[`64486d9`](https://github.com/Preponderous-Software/artificial-consciousness-simulation-framework/tree/64486d9c580325df33f03e8cb20dc7b8a227189a)
(`main`, 2026-10-17). Paths are relative to the ACSF repository root.

## What ACSF's "environment" actually is

The premise of this project — "the environment agents inhabit" — has to be checked against
what ACSF really has. It is stated plainly here so the rest of the design does not pretend
otherwise.

**ACSF has no spatial world.** A search of its source for positions, coordinates, grids,
maps or locations finds none. An ACSF *instance* is one named, LLM-driven agent running an
indefinite asynchronous thought loop in its own process
([`core/consciousness.py` L439–L440](https://github.com/Preponderous-Software/artificial-consciousness-simulation-framework/blob/64486d9c580325df33f03e8cb20dc7b8a227189a/consciousness-sim/core/consciousness.py#L439-L440)).
Several instances can run at once, but they share nothing: each lives in its own directory
`<CONSCIOUSNESS_HOME>/<name>/`
([`persistence/paths.py` L31–L39](https://github.com/Preponderous-Software/artificial-consciousness-simulation-framework/blob/64486d9c580325df33f03e8cb20dc7b8a227189a/consciousness-sim/persistence/paths.py#L31-L39)).

What stands in for an environment is:

| ACSF concept | What it is | Source |
|---|---|---|
| **Perception** | A text snippet (`source`, `title`, `content`, `url`, `fetched_at`) fetched every N cycles from a `PerceptionProvider` — `wikipedia` (random or topic-narrowed article summary) or `mock` (fixed five-item corpus) | [`llm/perception.py` L48–L66, L68–L80](https://github.com/Preponderous-Software/artificial-consciousness-simulation-framework/blob/64486d9c580325df33f03e8cb20dc7b8a227189a/consciousness-sim/llm/perception.py#L48-L80) |
| **Speech** | A message a person queues in `inbox.jsonl`; the oldest unheard one becomes a `speech` perception titled `"<sender> says"` and pre-empts the Wikipedia cadence | [`core/thought_loop.py` L131–L134](https://github.com/Preponderous-Software/artificial-consciousness-simulation-framework/blob/64486d9c580325df33f03e8cb20dc7b8a227189a/consciousness-sim/core/thought_loop.py#L131-L134), [`persistence/inbox.py`](https://github.com/Preponderous-Software/artificial-consciousness-simulation-framework/blob/64486d9c580325df33f03e8cb20dc7b8a227189a/consciousness-sim/persistence/inbox.py) |
| **The only outward action** | An `utterance`: a reply to a message, journaled with `to` and `in_reply_to` | [`core/consciousness.py` L974–L980](https://github.com/Preponderous-Software/artificial-consciousness-simulation-framework/blob/64486d9c580325df33f03e8cb20dc7b8a227189a/consciousness-sim/core/consciousness.py#L974-L980) |
| **Internal state** | Identity document (name, values, purpose, self-concept, amendments), a mood vector of named dimensions in `[0, 1]`, an attention schema (`focus`, `theme`, `salience`, `history`), a short-term workspace of `MemoryItem`s, episodic and long-term memory | [`core/identity.py`](https://github.com/Preponderous-Software/artificial-consciousness-simulation-framework/blob/64486d9c580325df33f03e8cb20dc7b8a227189a/consciousness-sim/core/identity.py), [`memory/short_term.py` L47–L55](https://github.com/Preponderous-Software/artificial-consciousness-simulation-framework/blob/64486d9c580325df33f03e8cb20dc7b8a227189a/consciousness-sim/memory/short_term.py#L47-L55) |

ACSF's own indicator audit says the same thing: AE-2 (embodiment — modelling how one's
actions affect incoming sensations) is marked unmet, and perception is "text-only (no other
modalities)"
([`INDICATORS.md` L15, L63](https://github.com/Preponderous-Software/artificial-consciousness-simulation-framework/blob/64486d9c580325df33f03e8cb20dc7b8a227189a/INDICATORS.md#L63)).

**The "tick"** is the thought cycle. The run loop increments `thought_count` once per cycle
([`core/consciousness.py` L893–L894](https://github.com/Preponderous-Software/artificial-consciousness-simulation-framework/blob/64486d9c580325df33f03e8cb20dc7b8a227189a/consciousness-sim/core/consciousness.py#L893-L894))
and, in a successful cycle, appends to the append-only `journal.jsonl` in a fixed order:
`perception` (when one arrived) → `thought` → `cycle` (structured internals: retrieved
memories, PP-1 prediction, HOT-2 label, reflection odds, attention schema, timings) →
`utterance` → `reflection` → `identity_shift` → `memory`
([L956–L1011](https://github.com/Preponderous-Software/artificial-consciousness-simulation-framework/blob/64486d9c580325df33f03e8cb20dc7b8a227189a/consciousness-sim/core/consciousness.py#L956-L1011),
[`_journal_cycle_trace` L629–L669](https://github.com/Preponderous-Software/artificial-consciousness-simulation-framework/blob/64486d9c580325df33f03e8cb20dc7b8a227189a/consciousness-sim/core/consciousness.py#L629-L669)).
A background consolidator writes `consolidation` events on its own timer. `state.json` is a
whole-state **snapshot** (identity incl. mood and attention schema, short-term buffer,
`thought_count`, `health`), overwritten on save
([L801–L808](https://github.com/Preponderous-Software/artificial-consciousness-simulation-framework/blob/64486d9c580325df33f03e8cb20dc7b8a227189a/consciousness-sim/core/consciousness.py#L801-L808)).
Mood is **not** journaled per cycle at this commit, so its history is not recoverable from a
run — only its latest value. An upstream issue proposes adding it to the `cycle` event
([ACSF #220](https://github.com/Preponderous-Software/artificial-consciousness-simulation-framework/issues/220)); the exporter uses it when present and falls back to the final value
otherwise ([ARCHITECTURE.md § Decisions](./ARCHITECTURE.md#decisions)).

## Problem

An ACSF run is inspected today through a live dashboard, a TUI, `scripts/inspect.py`, or
by reading JSONL. All of these are views of a *stream*. None of them gives an inspectable,
scrubbable, composable **artifact** of a run: a file that can be opened later, stepped
through cycle by cycle, layered next to another run, and diffed. The experiment harness
records runs as directories of JSONL + JSON
([`consciousness-sim/README.md` L184–L238](https://github.com/Preponderous-Software/artificial-consciousness-simulation-framework/blob/64486d9c580325df33f03e8cb20dc7b8a227189a/consciousness-sim/README.md#L184-L238)),
which are good for metrics and poor for looking at "what was in the workspace when the
agent produced thought 143, and what had it just perceived?".

OpenUSD already solves "a hierarchy of named things whose properties change over time,
written once and opened by many tools": a stage is a prim hierarchy, any attribute can
carry **time samples**, and stages compose through sublayers and references. Mapping an
ACSF run onto it gives a run a durable, tool-agnostic playback format without inventing one.

## Who it is for

- People running ACSF experiments who want to step through a recorded run (including the
  committed golden runs in `consciousness-sim/experiments/golden/`) at cycle granularity.
- People comparing runs: two instances, or two replicates, composed into one stage and
  scrubbed in lock-step on a shared cycle axis.
- Anyone who later wants to give ACSF a spatial or embodied environment: this repository
  fixes the vocabulary (prim paths, `acsf:` attribute namespace) that such an environment
  would be described in, and writes up the smallest ACSF change that would let an instance
  perceive a USD scene.

## What makes it interesting

- **USD as a log format for a non-spatial agent.** USD is usually geometry; here its
  hierarchy, typed attributes, held (non-interpolated) time samples for strings and
  composition are used as a general time-indexed state container. Geometry is added only
  as an honest, labelled visual aid.
- **Cycle-indexed time.** USD time codes are unitless ordinates; using the thought cycle as
  the time code makes "frame 143" mean "cycle 143", which is the axis ACSF's own metrics
  use. Wall-clock time is kept as data, not as the timeline.
- **Composition for comparison.** One layer per instance, one root stage sublayering them,
  gives side-by-side comparison of runs with no bespoke tooling.
- **A concrete path toward AE-2.** A USD scene is a natural candidate for the "next
  perception" source ACSF's indicator notes ask for; this project specifies the mapping
  before anything is built on the ACSF side.

## Non-goals

- **No physics, rendering, sensors or robotics.** Nothing here simulates a body.
- **No invented spatial semantics.** The schematic layout never feeds back into ACSF and is
  marked `acsf:layout:synthetic = true` on the stage.
- **No changes to ACSF in the MVP.** The tool is read-only against ACSF artifacts; the
  import direction is a written proposal only.
- **No writing to instance directories.** The exporter never modifies `journal.jsonl`,
  `state.json`, `memory.db` or any other file under `CONSCIOUSNESS_HOME`.
- **Not a dashboard replacement.** Live viewing stays with ACSF's web dashboard;
  `--follow` only keeps a stage file current for USD tools.
- **No long-term memory contents in the stage.** Neither `memory.db` embeddings (thousands of
  dimensions per row) nor memory text; long-term memory appears as counts only.

## Prior art

- **[OpenUSD](https://github.com/PixarAnimationStudios/OpenUSD)** (Pixar). The scene
  description system this tool targets. The `usd-core` PyPI package "provides the core USD
  libraries. It does not provide any of the optional plugins or imaging features from the
  full USD distribution" ([PyPI](https://pypi.org/project/usd-core/)) — so `usdview` is not
  included and must come from a full build or another viewer. Stage time scaling via
  `timeCodesPerSecond` and `startTimeCode`/`endTimeCode` is documented on
  [`UsdStage`](https://openusd.org/release/api/class_usd_stage.html).
- **[NVIDIA Isaac Sim](https://github.com/isaac-sim/IsaacSim)** — inspiration only.
  Its README describes it as "a simulation platform built on NVIDIA Omniverse, designed to
  develop, test, train, and deploy AI-powered robots in realistic virtual environments",
  and its [documentation](https://docs.isaacsim.omniverse.nvidia.com/latest/index.html)
  lists USD import and a "USD-native" scene representation ("One scene representation from
  asset import to deployment"). The idea borrowed is *the agent's world is a USD stage*.
  What is not borrowed: physics, RTX sensors, robot assets, ROS — none of which correspond
  to anything ACSF has. [Isaac Lab](https://github.com/isaac-sim/IsaacLab) (robot-learning
  framework on Isaac Sim) is the same family and equally out of scope.
- **[Generative Agents](https://arxiv.org/abs/2304.03442)** (Park et al., 2023;
  [code](https://github.com/joonspk-research/generative_agents), Apache-2.0). LLM agents
  with memory and reflection placed in a shared 2D town. It is the closest prior art for
  "LLM agent architecture + an environment", and the contrast is instructive: there the
  world is the primary input; in ACSF the world is a text feed. This project does not add
  a town to ACSF.
- **ACSF's own observability** — the web dashboard's journal tailer and `cycle` event
  (#196) are the out-of-process read contract this tool reuses rather than replaces.
- **Butlin et al. (2023), [*Consciousness in Artificial Intelligence*](https://arxiv.org/abs/2308.08708)**
  — the indicator framework ACSF maps itself against; cited here only for AE-2.
