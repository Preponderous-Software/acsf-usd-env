"""Milestone 3: static snapshot stage from state.json."""

from __future__ import annotations

import logging
import shutil
from pathlib import Path

import pytest
from pxr import Sdf, Usd, UsdGeom

from acsf_usd.model import Snapshot
from acsf_usd.reader import read_instance
from acsf_usd.stage.build import build_instance_stage, write_stage
from acsf_usd.stage.common import prim_name, unique_names
from acsf_usd.stage.snapshot import author_snapshot
from tests.conftest import INSTANCES

T = Sdf.ValueTypeNames
NOVA = "/World/Instances/Nova"


def _export(directory: Path, tmp_path: Path) -> Usd.Stage:
    out = tmp_path / "out.usda"
    write_stage(build_instance_stage(read_instance(directory)), out)
    stage = Usd.Stage.Open(str(out))
    assert stage is not None
    return stage


def _attr(stage: Usd.Stage, path: str, name: str) -> Usd.Attribute:
    attr = stage.GetPrimAtPath(path).GetAttribute(name)
    assert attr.IsValid(), f"{path}.{name} missing"
    return attr


def test_stage_metadata(tmp_path: Path) -> None:
    stage = _export(INSTANCES / "Nova", tmp_path)
    assert stage.GetDefaultPrim().GetPath() == Sdf.Path("/World")
    assert UsdGeom.GetStageUpAxis(stage) == UsdGeom.Tokens.y
    assert UsdGeom.GetStageMetersPerUnit(stage) == 1.0
    data = stage.GetRootLayer().customLayerData
    assert data["acsf:layout:synthetic"] is True
    assert data["acsf:cycleIndexing"] == "journaled"
    assert data["acsf:exporterVersion"]


def test_instance_and_identity_fields(tmp_path: Path) -> None:
    stage = _export(INSTANCES / "Nova", tmp_path)
    instance = stage.GetPrimAtPath(NOVA)
    assert instance.GetTypeName() == "Xform"
    assert _attr(stage, NOVA, "acsf:name").Get() == "Nova"
    identity = f"{NOVA}/Identity"
    assert stage.GetPrimAtPath(identity).GetTypeName() == ""
    expected = {
        "acsf:identity:originStory": (T.String, "A synthetic fixture instance.\n"),
        "acsf:identity:purpose": (T.String, "To exercise every mapped field."),
        "acsf:identity:selfConcept": (T.String, "I am Nova, a mind that answers."),
        "acsf:identity:values": (T.StringArray, ["curiosity", "care"]),
        "acsf:identity:personalityTraits": (T.StringArray, ["calm"]),
        "acsf:identity:amendments": (T.StringArray, ["I am someone who answers when spoken to"]),
    }
    for name, (type_name, value) in expected.items():
        attr = _attr(stage, identity, name)
        assert attr.GetTypeName() == type_name
        got = attr.Get()
        assert (list(got) if type_name == T.StringArray else got) == value


def test_mood_is_final_value_only_with_baseline(tmp_path: Path) -> None:
    stage = _export(INSTANCES / "Nova", tmp_path)
    mood = f"{NOVA}/Mood"
    for dim, final, baseline in (("curiosity", 0.75, 0.7), ("wonder", 0.5, 0.6)):
        attr = _attr(stage, mood, f"acsf:mood:{dim}")
        assert attr.GetTypeName() == T.Double
        assert attr.Get() == final
        assert attr.GetTimeSamples() == []
        base = _attr(stage, mood, f"acsf:moodBaseline:{dim}")
        assert base.Get() == baseline and base.GetTimeSamples() == []


def test_attention_snapshot(tmp_path: Path) -> None:
    stage = _export(INSTANCES / "Nova", tmp_path)
    attention = f"{NOVA}/Attention"
    assert _attr(stage, attention, "acsf:focus").GetTypeName() == T.Token
    assert _attr(stage, attention, "acsf:focus").Get() == "thought"
    assert _attr(stage, attention, "acsf:theme").Get() == "tides"
    assert _attr(stage, attention, "acsf:salience").Get() == 0.8


def test_workspace_items_named_by_seq(tmp_path: Path) -> None:
    stage = _export(INSTANCES / "Nova", tmp_path)
    workspace = stage.GetPrimAtPath(f"{NOVA}/Workspace")
    assert [c.GetName() for c in workspace.GetChildren()] == ["Item_7", "Item_3"]
    item = f"{NOVA}/Workspace/Item_3"
    assert _attr(stage, item, "acsf:kind").GetTypeName() == T.Token
    assert _attr(stage, item, "acsf:kind").Get() == "perception"
    assert _attr(stage, item, "acsf:content").Get() == "[speech: Dana says] Hello Nova"
    assert _attr(stage, item, "acsf:importance").Get() == 2.0
    assert _attr(stage, item, "acsf:timestamp").Get() == "2026-10-17T12:00:07.000000+00:00"


def test_workspace_items_without_seq_named_by_position(tmp_path: Path) -> None:
    stage = _export(INSTANCES / "Ash", tmp_path)
    workspace = stage.GetPrimAtPath("/World/Instances/Ash/Workspace")
    assert [c.GetName() for c in workspace.GetChildren()] == ["Item_0"]
    assert stage.GetRootLayer().customLayerData["acsf:cycleIndexing"] == "counted"


def test_missing_state_omits_snapshot_prims_with_warning(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    directory = tmp_path / "Nova"
    directory.mkdir()
    shutil.copy(INSTANCES / "Nova" / "journal.jsonl", directory / "journal.jsonl")
    with caplog.at_level(logging.WARNING):
        stage = _export(directory, tmp_path)
    instance = stage.GetPrimAtPath(NOVA)
    assert instance.IsValid()
    for child in ("Identity", "Mood", "Attention", "Workspace"):
        assert not stage.GetPrimAtPath(f"{NOVA}/{child}").IsValid()
    assert any("no state.json snapshot" in r.getMessage() for r in caplog.records)


@pytest.mark.parametrize(
    ("raw", "expected"),
    [("Echo", "Echo"), ("my-agent", "my_agent"), ("2026-10-17", "_2026_10_17"), ("", "_")],
)
def test_prim_name(raw: str, expected: str) -> None:
    assert prim_name(raw) == expected


def test_unique_names() -> None:
    assert unique_names(["self-confidence", "self_confidence", "self_confidence_2", "x"]) == {
        "self-confidence": "self_confidence",
        "self_confidence": "self_confidence_2",
        "self_confidence_2": "self_confidence_2_2",
        "x": "x",
    }


def test_mood_dimensions_that_sanitise_alike_stay_distinct() -> None:
    stage = Usd.Stage.CreateInMemory()
    path = Sdf.Path("/World/Instances/X")
    snapshot = Snapshot(
        mood={"self-confidence": 0.1, "self_confidence": 0.2},
        initial_mood={"self_confidence": 0.3, "only-baseline": 0.4, "only_baseline": 0.5},
    )
    author_snapshot(stage, path, snapshot)
    mood = stage.GetPrimAtPath(path.AppendChild("Mood"))
    values = {a.GetName(): a.Get() for a in mood.GetAttributes()}
    assert values == {
        "acsf:mood:self_confidence": 0.1,
        "acsf:mood:self_confidence_2": 0.2,
        "acsf:moodBaseline:self_confidence_2": 0.3,
        "acsf:moodBaseline:only_baseline": 0.4,
        "acsf:moodBaseline:only_baseline_2": 0.5,
    }


def test_instance_name_is_sanitised_and_kept(tmp_path: Path) -> None:
    directory = tmp_path / "replicate-1"
    shutil.copytree(INSTANCES / "Ash", directory)
    stage = _export(directory, tmp_path)
    assert _attr(stage, "/World/Instances/replicate_1", "acsf:name").Get() == "replicate-1"
