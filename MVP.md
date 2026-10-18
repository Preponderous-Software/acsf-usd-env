# MVP

## The slice

> Point `acsf-usd export` at one ACSF instance directory (or a recorded experiment run, or a
> golden run). Get a `.usda` file that opens in any OpenUSD tool, whose timeline runs from the
> first to the last thought cycle, and in which scrubbing to cycle *n* shows exactly the
> perception, thought, reflection and reply ACSF journaled for cycle *n* — next to the
> instance's identity, final mood and workspace. The input directory is never modified.

Direction: **ACSF → USD only.** The USD → ACSF direction ends the MVP as a written proposal
(milestone 8), because it requires an ACSF change (see
[ARCHITECTURE.md § Direction of sync](./ARCHITECTURE.md#direction-of-sync)).

## In scope

- Reading `journal.jsonl` and `state.json` from: an instance directory, an instance name under
  `CONSCIOUSNESS_HOME`, an experiment run directory, a golden-run directory.
- Cycle indexing with both the `cycle`-event rule and the counted fallback.
- Static snapshot prims (identity, final mood, attention schema, workspace).
- Time-sampled channels for every journal event type ACSF writes today, plus a passthrough for
  unknown types.
- A schematic, explicitly synthetic layout so the stage is viewable as geometry.
- Multi-instance export: one layer per instance, one root stage sublayering them.
- `acsf-usd validate` that re-reads a stage and checks it against its source journal.
- A design note for the import direction.

## Out of scope

- Any change to ACSF, any issue or PR on the ACSF repository.
- Physics, rendering, sensors, robots, Omniverse/Isaac Sim integration.
- Live/incremental export (batch re-export only).
- Per-cycle mood or per-cycle workspace history (ACSF does not journal them).
- Long-term memory (`memory.db`) contents and embeddings.
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
Write `/World`, `/World/Instances/<name>` with `Identity`, `Mood`, `Attention`, `Workspace`
prims from `state.json`; stage metadata (`defaultPrim`, `upAxis`, `metersPerUnit`, `customData`).
*Done when:* a stage built from a fixture `state.json` opens and every mapped field from the
[mapping table](./ARCHITECTURE.md#acsf--usd-mapping) is present with the right type.

### 4. Time-sampled event channels
`Stream`, `Stimulus`, `Interlocutors`, `Cycle`, `Memory`, `Identity.selfConcept` channels with
samples at cycle indices; `startTimeCode`/`endTimeCode`/`timeCodesPerSecond`; unknown-type
passthrough; the `export` command.
*Done when:* acceptance criteria 2–4 pass on fixtures.

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
