"""Derive a per-cycle "mind timeline" of the bundled golden runs FROM their USD exports.

Each golden run in ``tests/fixtures/golden/`` is exported with the project's own CLI
(``python -m acsf_usd export``) into a temporary directory, and every series below is then
read back from the resulting stage with ``usd-core`` — the USD stage is the data source, not
the journal. Two outputs are written:

* a PNG (matplotlib): one swim-lane panel per instance on a shared cycle axis, plus each
  instance's final mood; and
* ``timeline.json``: the same per-cycle series, with event labels that are generic event
  types only (no thought, perception, reflection or memory text).

Usage (matplotlib is not a project dependency, so it is pulled in for this script only)::

    uv run --python 3.12 --no-project --with . --with matplotlib \\
        python scripts/timeline.py --png docs/images/golden-timeline.png --json timeline.json
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any

from pxr import Usd

REPO = Path(__file__).resolve().parent.parent
GOLDEN = REPO / "tests" / "fixtures" / "golden"
AGENTS = ("Echo", "Rafael", "Sage", "Wren")


def export(golden_dir: Path, out_dir: Path) -> Path:
    """Export one golden run with the project's CLI; return the written .usda path."""
    out = out_dir / f"{golden_dir.name}.usda"
    subprocess.run(
        [sys.executable, "-m", "acsf_usd", "export", str(golden_dir), "-o", str(out)],
        check=True,
        capture_output=True,
    )
    return out


def _samples(attr: Usd.Attribute) -> dict[int, Any]:
    if not attr or not attr.IsValid():
        return {}
    return {int(t): attr.Get(t) for t in attr.GetTimeSamples()}


def read_stage(path: Path) -> dict[str, Any]:
    """Read the per-cycle series of the single instance in an exported stage."""
    stage = Usd.Stage.Open(str(path))
    layer_data = stage.GetRootLayer().customLayerData
    first, last = int(stage.GetStartTimeCode()), int(stage.GetEndTimeCode())
    cycles = list(range(first, last + 1))
    (inst,) = stage.GetPrimAtPath("/World/Instances").GetChildren()
    name = inst.GetAttribute("acsf:name").Get()

    def channel(prim: str, attr: str) -> dict[int, Any]:
        p = inst.GetPrimAtPath(prim) if prim else inst
        return _samples(p.GetAttribute(attr)) if p and p.IsValid() else {}

    thought = channel("Stream", "acsf:thought")
    reflection = channel("Stream", "acsf:reflection")
    perception = channel("Stimulus", "acsf:perception")
    source = channel("Stimulus", "acsf:perceptionSource")
    self_concept = channel("Identity", "acsf:selfConcept")
    long_term = channel("Memory", "acsf:longTermCount")

    utter_counts = dict.fromkeys(cycles, 0)
    people = inst.GetPrimAtPath("Interlocutors")
    if people and people.IsValid():
        for person in people.GetChildren():
            for c, text in _samples(person.GetAttribute("acsf:utterance")).items():
                if text:
                    utter_counts[c] += 1

    def present(samples: dict[int, Any]) -> list[int]:
        return [1 if samples.get(c) else 0 for c in cycles]

    # Memory count is a state value: held from its last sample, null before the first.
    memory: list[int | None] = []
    held: int | None = None
    for c in cycles:
        if c in long_term:
            held = int(long_term[c])
        memory.append(held)

    series = {
        "thoughts": present(thought),
        "perceptions": present(perception),
        "utterances": [utter_counts[c] for c in cycles],
        "reflections": present(reflection),
        "identity_shifts": present(self_concept),
        "memory_count": memory,
    }

    events: list[dict[str, Any]] = []
    for i, c in enumerate(cycles):
        if series["perceptions"][i]:
            src = source.get(c) or "unknown"
            events.append({"cycle": c, "type": "perception", "short_label": f"perception:{src}"})
        if series["utterances"][i]:
            events.append({"cycle": c, "type": "utterance", "short_label": "utterance"})
        if series["reflections"][i]:
            events.append({"cycle": c, "type": "reflection", "short_label": "reflection"})
        if series["identity_shifts"][i]:
            events.append({"cycle": c, "type": "identity_shift", "short_label": "identity shift"})
        if not series["thoughts"][i]:
            events.append({"cycle": c, "type": "no_thought", "short_label": "no thought journaled"})

    mood_prim = inst.GetPrimAtPath("Mood")
    mood_final: dict[str, float] = {}
    if mood_prim and mood_prim.IsValid():
        for attr in mood_prim.GetAttributes():
            n = attr.GetName()
            if n.startswith("acsf:mood:"):
                samples = attr.GetTimeSamples()
                value = attr.Get(samples[-1]) if samples else attr.Get()
                mood_final[n.removeprefix("acsf:mood:")] = round(float(value), 4)

    return {
        "name": name,
        "cycles": len(cycles),
        "first_cycle": first,
        "cycle_indexing": layer_data.get("acsf:cycleIndexing"),
        "mood_final": dict(sorted(mood_final.items())),
        "mood_sampling": layer_data.get("acsf:moodSampling"),
        "series": series,
        "events": events,
    }


LANES = (  # (series key, lane label, colour) — Okabe-Ito, colour-blind safe
    ("thoughts", "thought", "#999999"),
    ("perceptions", "perception", "#0072B2"),
    ("utterances", "utterance", "#009E73"),
    ("reflections", "reflection", "#D55E00"),
    ("identity_shifts", "identity shift", "#CC79A7"),
)


def plot(agents: list[dict[str, Any]], png: Path) -> None:
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    from matplotlib.lines import Line2D

    # Only lanes that carry at least one event in some run are drawn; the rest are named in
    # a note so their absence is stated rather than hidden.
    lanes = [lane for lane in LANES if any(any(a["series"][lane[0]]) for a in agents)]
    empty = [lane[1] for lane in LANES if lane not in lanes]
    has_memory = any(v is not None for a in agents for v in a["series"]["memory_count"])
    if not has_memory:
        empty.append("memory count")
    max_cycle = max(a["first_cycle"] + a["cycles"] - 1 for a in agents)
    moods = sorted({k for a in agents for k in a["mood_final"]})

    plt.rcParams.update({"font.size": 10, "font.family": "DejaVu Sans"})
    fig = plt.figure(figsize=(11, 7.2), dpi=110)
    grid = fig.add_gridspec(
        len(agents), 2, width_ratios=(6, 1.1), hspace=0.35, wspace=0.22,
        left=0.09, right=0.975, top=0.86, bottom=0.15,
    )  # fmt: skip
    axes = []
    for row, agent in enumerate(agents):
        ax = fig.add_subplot(grid[row, 0], sharex=axes[0] if axes else None)
        axes.append(ax)
        first = agent["first_cycle"]
        cycles = range(first, first + agent["cycles"])
        for y, (key, _label, colour) in enumerate(reversed(lanes)):
            xs = [c for c, v in zip(cycles, agent["series"][key], strict=True) if v]
            if key == "thoughts":  # every cycle: draw as a band, gaps would be failed cycles
                ax.broken_barh([(x - 0.5, 1) for x in xs], (y - 0.18, 0.36), color=colour)
            else:
                ax.vlines(xs, y - 0.38, y + 0.38, color=colour, linewidth=1.6)
            if not xs:
                ax.text(first + 1, y, "none journaled", va="center", color="#999999", fontsize=8)
        end = first + agent["cycles"] - 1
        if end < max_cycle:
            ax.axvspan(end + 0.5, max_cycle + 0.5, color="#000000", alpha=0.05, lw=0)
            ax.text(
                (end + max_cycle) / 2, (len(lanes) - 1) / 2, "run ended",
                ha="center", va="center", color="#777777", fontsize=9,
            )  # fmt: skip
        ax.set_yticks(range(len(lanes)), [lane[1] for lane in reversed(lanes)], fontsize=8.5)
        ax.set_ylim(-0.6, len(lanes) - 0.4)
        ax.set_xlim(0.5, max_cycle + 0.5)
        ax.set_title(
            f"{agent['name']}  ·  {agent['cycles']} cycles", loc="left", fontsize=10.5,
            fontweight="bold", pad=3,
        )  # fmt: skip
        for side in ("top", "right"):
            ax.spines[side].set_visible(False)
        ax.tick_params(axis="y", length=0)
        if row < len(agents) - 1:
            ax.tick_params(axis="x", labelbottom=False)

        mx = fig.add_subplot(grid[row, 1])
        values = [agent["mood_final"].get(m, 0.0) for m in moods]
        mx.barh(range(len(moods)), values, color="#56B4E9", height=0.7)
        mx.set_xlim(0, 1)
        mx.set_yticks(range(len(moods)), moods)
        mx.invert_yaxis()
        mx.tick_params(axis="both", length=0, labelsize=8)
        mx.set_xticks([])
        for side in ("top", "right", "bottom", "left"):
            mx.spines[side].set_visible(False)
        if row == 0:
            mx.set_title("final mood (0–1)", fontsize=9, pad=3)

    axes[-1].set_xlabel("ACSF thought cycle (USD time code)")
    fig.suptitle(
        "Golden runs as USD stages: what each instance journaled, cycle by cycle",
        x=0.09, ha="left", fontsize=13, fontweight="bold",
    )  # fmt: skip
    handles = [
        Line2D([], [], color=colour, lw=6 if key == "thoughts" else 2, label=label)
        for key, label, colour in lanes
    ]
    fig.legend(
        handles=handles, loc="upper left", bbox_to_anchor=(0.09, 0.925), ncol=len(handles),
        frameon=False, fontsize=9, handlelength=1.6,
    )  # fmt: skip
    sampling = {a["mood_sampling"] for a in agents}
    note = (
        "Every series is read back with usd-core from `acsf-usd export` stages of the ACSF golden "
        "runs. Cycles are counted from thought events.\n"
        f"Mood: one final value per run (acsf:moodSampling = {', '.join(sorted(sampling))}); "
        "the runs journal no per-cycle mood."
    )
    if empty:
        note += f" Not journaled in these runs, so not drawn: {', '.join(empty)}."
    fig.text(0.09, 0.015, note, fontsize=8, color="#555555", ha="left", va="bottom")
    png.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(png, dpi=110, facecolor="white")
    plt.close(fig)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--png", type=Path, help="write the swim-lane figure here")
    parser.add_argument("--json", type=Path, help="write timeline.json here")
    args = parser.parse_args(argv)

    with tempfile.TemporaryDirectory(prefix="acsf-timeline-") as tmp:
        agents = [read_stage(export(GOLDEN / name, Path(tmp))) for name in AGENTS]

    if args.json:
        doc = {
            "source": "acsf-usd export of tests/fixtures/golden (ACSF 64486d9), read with usd-core",
            "agents": [
                {
                    k: a[k]
                    for k in (
                        "name",
                        "cycles",
                        "first_cycle",
                        "mood_final",
                        "mood_sampling",
                        "series",
                    )
                }
                | {"events": a["events"]}
                for a in agents
            ],
        }
        args.json.parent.mkdir(parents=True, exist_ok=True)
        args.json.write_text(json.dumps(doc, separators=(",", ":")) + "\n", encoding="utf-8")
    if args.png:
        plot(agents, args.png)
    for a in agents:
        counts = {k: sum(v for v in s if v) for k, s in a["series"].items() if k != "memory_count"}
        print(f"{a['name']}: {a['cycles']} cycles, mood {a['mood_sampling']}, {counts}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
