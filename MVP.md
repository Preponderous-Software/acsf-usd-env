# MVP

## The slice

> Point `acsf-usd export` at one ACSF instance directory (or a recorded experiment run, or a
> golden run). Get a `.usda` file that opens in any OpenUSD tool, whose timeline runs from the
> first to the last thought cycle, and in which scrubbing to cycle *n* shows exactly the
> perception, thought, reflection and reply ACSF journaled for cycle *n* — next to the
> instance's identity, mood (per cycle where ACSF journals it, otherwise final) and workspace.
> With `--follow`, the file keeps up with a running instance. The input directory is never
> modified.

Direction: **ACSF → USD only.** The USD → ACSF direction ends the MVP as a written proposal
(milestone 8), because it requires an ACSF change (see
[ARCHITECTURE.md § Direction of sync](./ARCHITECTURE.md#direction-of-sync)).

## In scope

- Reading `journal.jsonl` and `state.json` from: an instance directory, an instance name under
  `CONSCIOUSNESS_HOME`, an experiment run directory, a golden-run directory.
- Cycle indexing with both the `cycle`-event rule and the counted fallback.
- Static snapshot prims (identity, mood baseline, attention schema, workspace); mood as
  time samples when `cycle` events carry it, final value otherwise.
- Time-sampled channels for every journal event type ACSF writes today, plus a passthrough for
  unknown types.
- Long-term memory as per-cycle counts only.
- `.usda` output by default, `.usdc` with `--binary`.
- `--follow`: one instance's stage kept current by polling its journal, read-only.
- A schematic, explicitly synthetic layout so the stage is viewable as geometry.
- Multi-instance export: one layer per instance, one root stage sublayering them.
- `acsf-usd validate` that re-reads a stage and checks it against its source journal.
- A design note for the import direction.

## Out of scope

- Any change to ACSF. (One upstream issue, [ACSF #220](https://github.com/Preponderous-Software/artificial-consciousness-simulation-framework/issues/220), proposes journaling mood
  per cycle; the MVP does not depend on it.)
- Physics, rendering, sensors, robots, Omniverse/Isaac Sim integration.
- Following several instances at once (`--follow` takes one instance).
- Per-cycle workspace history (ACSF does not journal it).
- Long-term memory text and embeddings (`memory.db` rows, `cycle.retrieved` summaries).
  Counts only.
- A viewer. Any USD viewer is used; `usdview` is not shipped by `usd-core`.

## Acceptance criteria (MVP as a whole)

1. `pip install .` on Python 3.11 installs the package and an `acsf-usd` command; `usd-core` is
   pinned to `26.8`.
2. `acsf-usd export <golden Echo dir> -o echo.usda` exits 0; `Usd.Stage.Open("echo.usda")`
   succeeds; `/World/Instances/Echo/Stream.acsf:thought` has **200** time samples and the
   stimulus channel has **67** (the event counts in ACSF's committed `golden/Echo/journal.jsonl`
   at commit `64486d9`).
3. For every journaled `thought`, the attribute value at its cycle equals the journal `content`
   byte-for-byte (checked by `acsf-usd validate`).
4. A journal containing `cycle` events is indexed by their `thought_count`; one without is
   indexed by counting, and the stage's `acsf:cycleIndexing` says which.
5. Corrupt and non-object journal lines are skipped with a warning, never abort the export.
6. File hashes and mtimes of the input directory are identical before and after export.
7. Two exports of the same input are byte-identical.
8. Exporting two instances yields a root stage whose two instance prims share one timeline.
9. The stage carries `acsf:layout:synthetic = true` and the README states what the geometry
   does and does not mean.
10. CI runs `pytest`, `mypy` (strict) and `ruff` green on Python 3.11 with no network access.
11. `acsf-usd export --follow` on an instance whose journal is being appended to adds time
    samples as complete lines arrive, creates nothing in the instance directory, and on exit
    leaves a file byte-identical to a batch export of the same input.
12. Without flags the output is `.usda`; `--binary` produces `.usdc` that opens with
    `Usd.Stage.Open` and passes `acsf-usd validate`.
13. No USD file contains long-term memory text; long-term memory appears only as counts.

## Milestones

Each milestone is one GitHub issue (label `mvp`).

### 1. Package skeleton, pinned dependencies and CI
`pyproject.toml` (Python ≥ 3.11, `usd-core==26.8`), `acsf_usd` package, `acsf-usd --version`,
GitHub Actions running `pytest`, `mypy --strict`, `ruff` on 3.11 and 3.13.
*Done when:* CI is green on an empty test that imports `pxr.Usd` and prints `Usd.GetVersion()`.

### 2. ACSF artifact reader and cycle indexer
Typed readers for `journal.jsonl` and `state.json`; tolerant parsing; the two cycle-indexing
rules from [ARCHITECTURE.md](./ARCHITECTURE.md#cycle-indexing-rules); input resolution for
instance dir / name / run dir.
*Done when:* unit tests cover every event type, corrupt lines, both indexing rules and a failed
cycle; files are opened read-only.

### 3. Static snapshot stage
Write `/World`, `/World/Instances/<name>` with `Identity`, `Mood` (final value; per-cycle
samples come in milestone 4), `Attention`, `Workspace` prims from `state.json`; stage metadata
(`defaultPrim`, `upAxis`, `metersPerUnit`, `customLayerData`).
*Done when:* a stage built from a fixture `state.json` opens and every mapped field from the
[mapping table](./ARCHITECTURE.md#acsf--usd-mapping) is present with the right type.

### 4. Time-sampled event channels
`Stream`, `Stimulus`, `Interlocutors`, `Cycle`, `Memory` (counts only), `Identity.selfConcept`
channels with samples at cycle indices; `Mood` samples when `cycle` events carry `mood`;
`startTimeCode`/`endTimeCode`/`timeCodesPerSecond`; unknown-type passthrough; the `export`
command with `.usda` default and `--binary`.
*Done when:* acceptance criteria 2–4, 12 and 13 pass on fixtures.

### 5. Schematic layout (synthetic geometry)
`Sphere` stimulus and ring-placed `Cube` interlocutors with time-sampled `visibility`;
deterministic placement; `acsf:layout:synthetic = true`.
*Done when:* geometry visibility at each cycle matches the presence of a perception/utterance,
and positions are identical across runs.

### 6. Multi-instance composition
One layer per instance, a root layer that sublayers them, X-offset per instance, shared
timeline spanning all instances; input may be a whole `CONSCIOUSNESS_HOME` or several dirs.
*Done when:* acceptance criterion 8 passes.

### 7. Validation command and golden-run end-to-end tests
`acsf-usd validate <stage> <source>`; golden-run fixtures copied from ACSF at `64486d9` with
attribution; read-only and determinism tests.
*Done when:* acceptance criteria 2, 3, 5, 6, 7 and 10 pass in CI.

### 8. Design note: USD scene as an ACSF perception source
`docs/usd-perception-proposal.md`: the smallest ACSF extension (provider registry +
`UsdScenePerception`), what it would and would not mean for AE-2, prompt-injection handling,
and the open questions. No code, no change to ACSF.
*Done when:* the note is merged and every ACSF claim in it cites a file at a pinned SHA.

### 9. Live mode: follow a running instance
`acsf-usd export <instance> -o <file> --follow`: poll `journal.jsonl` from a byte offset
(complete lines only), extend the run model through the milestone 2 indexer, re-author the
instance layer and atomically replace the output; single instance; strictly read-only
against the instance directory. Design:
[ARCHITECTURE.md § Live mode](./ARCHITECTURE.md#live-mode-follow).
*Done when:* acceptance criterion 11 passes in CI.
