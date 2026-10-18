"""Milestone 4: time-sampled event channels and the export command."""

from __future__ import annotations

import json
import shutil
from pathlib import Path

import pytest
from pxr import Usd

from acsf_usd.cli import main
from acsf_usd.reader import read_instance
from acsf_usd.stage.build import ExportOptions, build_instance_stage, write_stage
from acsf_usd.stage.channels import perception_source, sender_prim_names
from tests.conftest import GOLDEN, INSTANCES

NOVA = "/World/Instances/Nova"


def _export(directory: Path, tmp_path: Path, binary: bool = False, **opts: object) -> Usd.Stage:
    out = tmp_path / ("out.usdc" if binary else "out.usda")
    stage = build_instance_stage(read_instance(directory), ExportOptions(**opts))  # type: ignore[arg-type]
    write_stage(stage, out, binary=binary)
    opened = Usd.Stage.Open(str(out))
    assert opened is not None
    return opened


def _samples(stage: Usd.Stage, path: str, name: str) -> dict[float, object]:
    attr = stage.GetPrimAtPath(path).GetAttribute(name)
    assert attr.IsValid(), f"{path}.{name} missing"
    return {t: attr.Get(t) for t in attr.GetTimeSamples()}


@pytest.fixture(scope="module")
def nova(tmp_path_factory: pytest.TempPathFactory) -> Usd.Stage:
    return _export(INSTANCES / "Nova", tmp_path_factory.mktemp("nova"))


def test_timeline_spans_first_to_last_cycle(nova: Usd.Stage) -> None:
    assert (nova.GetStartTimeCode(), nova.GetEndTimeCode()) == (1, 4)
    assert nova.GetTimeCodesPerSecond() == 1


def test_time_codes_per_second_is_configurable(tmp_path: Path) -> None:
    stage = _export(INSTANCES / "Nova", tmp_path, time_codes_per_second=4.0)
    assert stage.GetTimeCodesPerSecond() == 4


def test_stream_thought_matches_journal_byte_for_byte(nova: Usd.Stage) -> None:
    journal = [
        json.loads(line)
        for line in (INSTANCES / "Nova" / "journal.jsonl").read_text().splitlines()
        if line.startswith('{"')
    ]
    thoughts = [e["content"] for e in journal if e["type"] == "thought"]
    samples = _samples(nova, f"{NOVA}/Stream", "acsf:thought")
    assert {t: v for t, v in samples.items() if v} == dict(
        zip([1.0, 2.0, 4.0], thoughts, strict=True)
    )
    assert samples[3.0] == ""  # failed cycle 3 journaled no thought


def test_reflection_is_empty_on_cycles_without_one(nova: Usd.Stage) -> None:
    assert _samples(nova, f"{NOVA}/Stream", "acsf:reflection") == {
        1.0: "",
        2.0: "I am someone who answers when spoken to.",
        3.0: "",
        4.0: "",
    }


def test_timestamp_is_the_cycles_thought_time(nova: Usd.Stage) -> None:
    assert _samples(nova, f"{NOVA}/Stream", "acsf:timestamp") == {
        1.0: "2026-10-17T12:00:02.000000+00:00",
        2.0: "2026-10-17T12:00:07.000000+00:00",
        3.0: "",
        4.0: "2026-10-17T12:00:13.000000+00:00",
    }


def test_stimulus_values_do_not_leak_into_later_cycles(nova: Usd.Stage) -> None:
    perception = _samples(nova, f"{NOVA}/Stimulus", "acsf:perception")
    assert perception[4.0] == ""
    assert perception[2.0] == "[speech: Dana says] Hello Nova, what are you thinking about?"
    # Held value between cycles: the cycle-2 value at 3.5 would be wrong; cycle 4 is empty.
    attr = nova.GetPrimAtPath(f"{NOVA}/Stimulus").GetAttribute("acsf:perception")
    assert attr.Get(3) == "" and attr.Get(3.5) == ""  # failed cycle 3: nothing held over
    assert attr.Get(4.5) == ""
    assert _samples(nova, f"{NOVA}/Stimulus", "acsf:perceptionSource") == {
        1.0: "wikipedia",
        2.0: "speech",
        3.0: "",
        4.0: "",
    }
    assert nova.GetPrimAtPath(f"{NOVA}/Stimulus").GetTypeName() == "Xform"


def test_interlocutors_per_recipient(nova: Usd.Stage) -> None:
    dana = f"{NOVA}/Interlocutors/Dana"
    assert nova.GetPrimAtPath(dana).GetAttribute("acsf:sender").Get() == "Dana"
    assert _samples(nova, dana, "acsf:utterance") == {
        1.0: "",
        2.0: "Hello Dana. I was thinking about tide pools.",
        3.0: "",
        4.0: "",
    }
    assert _samples(nova, dana, "acsf:inReplyTo")[2.0] == (
        "Hello Nova, what are you thinking about?"
    )
    assert _samples(nova, f"{NOVA}/Interlocutors/Robin", "acsf:utterance")[4.0] == (
        "Goodbye, Robin."
    )


def test_cycle_channel(nova: Usd.Stage) -> None:
    cycle = f"{NOVA}/Cycle"
    assert _samples(nova, cycle, "acsf:hot2Label") == {
        1.0: "metacognitive-stable",
        2.0: "metacognitive-surprised",
        4.0: "metacognitive-stable",
    }
    assert _samples(nova, cycle, "acsf:predictionError") == {1.0: 0.4, 2.0: 2.5, 4.0: 0.4}
    assert _samples(nova, cycle, "acsf:reflectionP") == {1.0: 0.25, 2.0: 0.6, 4.0: 0.25}
    assert _samples(nova, cycle, "acsf:reflectionFired") == {1.0: False, 2.0: True, 4.0: False}
    assert _samples(nova, cycle, "acsf:workspaceSize") == {1.0: 3, 2.0: 4, 4.0: 5}
    assert _samples(nova, cycle, "acsf:retrievedCount") == {1.0: 0, 2.0: 2, 4.0: 1}


def test_attention_channel_from_cycle_events(nova: Usd.Stage) -> None:
    attention = f"{NOVA}/Attention"
    assert _samples(nova, attention, "acsf:focus") == {
        1.0: "thought",
        2.0: "perception",
        4.0: "thought",
    }
    assert _samples(nova, attention, "acsf:salience") == {1.0: 0.8, 2.0: 0.95, 4.0: 0.8}


def test_memory_counts_only(nova: Usd.Stage, tmp_path: Path) -> None:
    memory = f"{NOVA}/Memory"
    assert _samples(nova, memory, "acsf:longTermCount") == {1.0: 6, 2.0: 6, 4.0: 8}
    assert _samples(nova, memory, "acsf:consolidationStored") == {
        1.0: 2,
        2.0: 0,
        3.0: 0,
        4.0: 0,
    }
    out = tmp_path / "nova.usda"
    write_stage(build_instance_stage(read_instance(INSTANCES / "Nova")), out)
    text = out.read_text(encoding="utf-8")
    assert "SECRET-LTM" not in text  # acceptance criterion 13


def test_identity_self_concept_from_identity_shift(nova: Usd.Stage) -> None:
    assert _samples(nova, f"{NOVA}/Identity", "acsf:selfConcept") == {
        1.0: "",
        2.0: "I am Nova, a mind that answers.",
    }


def test_unknown_event_type_passthrough(nova: Usd.Stage) -> None:
    assert _samples(nova, f"{NOVA}/Stream", "acsf:event:custom_probe") == {
        1.0: "",
        2.0: "",
        3.0: "",
        4.0: "an event type this exporter does not know",
    }


def test_unknown_types_that_sanitise_alike_stay_distinct(tmp_path: Path) -> None:
    directory = tmp_path / "U"
    directory.mkdir()
    lines = [
        {"type": "thought", "content": "t"},
        {"type": "probe-x", "content": "dash"},
        {"type": "probe_x", "content": "underscore"},
    ]
    (directory / "journal.jsonl").write_text("".join(json.dumps(x) + "\n" for x in lines))
    stage = _export(directory, tmp_path)
    stream = "/World/Instances/U/Stream"
    assert _samples(stage, stream, "acsf:event:probe_x") == {1.0: "dash"}
    assert _samples(stage, stream, "acsf:event:probe_x_2") == {1.0: "underscore"}


def test_mood_final_when_cycle_events_lack_it(nova: Usd.Stage) -> None:
    data = nova.GetRootLayer().customLayerData
    assert data["acsf:moodSampling"] == "final"
    assert "acsf:moodFirstCycle" not in data
    attr = nova.GetPrimAtPath(f"{NOVA}/Mood").GetAttribute("acsf:mood:curiosity")
    assert attr.GetTimeSamples() == [] and attr.Get() == 0.75


def test_mood_journaled_when_cycle_events_carry_it(tmp_path: Path) -> None:
    stage = _export(INSTANCES / "Lumen", tmp_path)
    data = stage.GetRootLayer().customLayerData
    assert data["acsf:moodSampling"] == "journaled"
    assert data["acsf:moodFirstCycle"] == 2
    mood = "/World/Instances/Lumen/Mood"
    assert _samples(stage, mood, "acsf:mood:curiosity") == {2.0: 0.71, 3.0: 0.73}
    assert _samples(stage, mood, "acsf:mood:wonder") == {2.0: 0.6, 3.0: 0.58}
    assert data["acsf:cycleIndexing"] == "journaled"


def test_journaled_and_snapshot_mood_share_dimension_names(tmp_path: Path) -> None:
    directory = tmp_path / "M"
    directory.mkdir()
    (directory / "journal.jsonl").write_text(
        json.dumps({"type": "thought", "content": "t"})
        + "\n"
        + json.dumps({"type": "cycle", "thought_count": 1, "mood": {"self_confidence": 0.9}})
        + "\n"
    )
    (directory / "state.json").write_text(
        json.dumps({"identity": {"mood": {"self-confidence": 0.1, "self_confidence": 0.9}}})
    )
    stage = _export(directory, tmp_path)
    mood = stage.GetPrimAtPath("/World/Instances/M/Mood")
    assert mood.GetAttribute("acsf:mood:self_confidence").Get() == 0.1
    assert _samples(stage, "/World/Instances/M/Mood", "acsf:mood:self_confidence_2") == {1.0: 0.9}


def test_golden_echo_counts(tmp_path: Path) -> None:
    stage = _export(GOLDEN / "Echo", tmp_path)
    echo = "/World/Instances/Echo"
    thought = _samples(stage, f"{echo}/Stream", "acsf:thought")
    perception = _samples(stage, f"{echo}/Stimulus", "acsf:perception")
    assert len(thought) == 200  # acceptance criterion 2
    assert len([v for v in perception.values() if v]) == 67
    assert stage.GetRootLayer().customLayerData["acsf:cycleIndexing"] == "counted"
    assert stage.GetRootLayer().customLayerData["acsf:moodSampling"] == "final"
    assert not stage.GetPrimAtPath(f"{echo}/Mood").GetAttribute("acsf:mood:wonder").GetTimeSamples()


def test_binary_export_holds_the_same_samples(tmp_path: Path) -> None:
    text = _export(INSTANCES / "Nova", tmp_path / "a")
    binary = _export(INSTANCES / "Nova", tmp_path / "b", binary=True)
    for prim in text.Traverse():
        other = binary.GetPrimAtPath(prim.GetPath())
        assert other.IsValid()
        for attr in prim.GetAttributes():
            twin = other.GetAttribute(attr.GetName())
            assert twin.GetTimeSamples() == attr.GetTimeSamples()
            assert [twin.Get(t) for t in twin.GetTimeSamples()] == [
                attr.Get(t) for t in attr.GetTimeSamples()
            ]
    assert (tmp_path / "b" / "out.usdc").read_bytes()[:8] == b"PXR-USDC"


@pytest.mark.parametrize(
    ("content", "source"),
    [
        ("[wikipedia: Tide pool] text", "wikipedia"),
        ("[speech: Dana says] hi", "speech"),
        ("[mock: x] y", "mock"),
        ("no prefix", ""),
    ],
)
def test_perception_source(content: str, source: str) -> None:
    assert perception_source(content) == source


def test_sender_prim_names_are_collision_free() -> None:
    assert sender_prim_names(["Dana?", "Dana!", "Robin"]) == {
        "Dana!": "Dana_",
        "Dana?": "Dana__2",
        "Robin": "Robin",
    }


# --- export command -------------------------------------------------------------------


def test_export_command_default_text_output(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.chdir(tmp_path)
    assert main(["export", str(INSTANCES / "Nova")]) == 0
    assert (tmp_path / "Nova.usda").read_bytes().startswith(b"#usda 1.0")
    assert "wrote Nova.usda" in capsys.readouterr().out


def test_export_command_binary(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.chdir(tmp_path)
    assert main(["export", str(INSTANCES / "Nova"), "--binary"]) == 0
    assert (tmp_path / "Nova.usdc").read_bytes().startswith(b"PXR-USDC")
    assert main(["export", str(INSTANCES / "Nova"), "--binary", "-o", "n.usd"]) == 0
    assert (tmp_path / "n.usd").read_bytes().startswith(b"PXR-USDC")
    assert main(["export", str(INSTANCES / "Nova"), "-o", "t.usd"]) == 0
    assert (tmp_path / "t.usd").read_bytes().startswith(b"#usda 1.0")


@pytest.mark.parametrize(
    "args",
    [
        ["-o", "x.usdc"],
        ["--binary", "-o", "x.usda"],
        ["--time-codes-per-second", "0"],
        ["--time-codes-per-second", "inf"],
        ["--time-codes-per-second", "1e309"],
        ["--time-codes-per-second", "nan"],
        ["--time-codes-per-second", "fast"],
    ],
)
def test_export_usage_errors(
    args: list[str], tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(tmp_path)
    with pytest.raises(SystemExit) as excinfo:
        main(["export", str(INSTANCES / "Nova"), *args])
    assert excinfo.value.code == 2
    assert list(tmp_path.iterdir()) == []


def test_export_refuses_output_inside_input(tmp_path: Path) -> None:
    directory = tmp_path / "Nova"
    shutil.copytree(INSTANCES / "Nova", directory)
    with pytest.raises(SystemExit) as excinfo:
        main(["export", str(directory), "-o", str(directory / "x.usda")])
    assert excinfo.value.code == 2
    assert sorted(p.name for p in directory.iterdir()) == ["journal.jsonl", "state.json"]


def test_export_missing_input_exits_1(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("CONSCIOUSNESS_HOME", str(tmp_path))
    assert main(["export", "Nobody"]) == 1
    assert "no instance named 'Nobody'" in capsys.readouterr().err


def test_export_records_source_commit(tmp_path: Path) -> None:
    stage = _export(INSTANCES / "Nova", tmp_path, source_commit="64486d9")
    assert stage.GetRootLayer().customLayerData["acsf:sourceCommit"] == "64486d9"
