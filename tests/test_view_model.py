"""Reading exported stages for ``acsf-usd view``: the input is the stage only."""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest
from pxr import Gf, Sdf, Usd, UsdGeom

from acsf_usd.view.model import Channel, StageError, check_metadata, held, load_run

INSTANCES = "/World/Instances"


def test_echo_counts_match_the_golden_journal(view_stages: dict[str, Path]) -> None:
    run = load_run(view_stages["echo"])
    (echo,) = run.instances
    assert (run.start, run.end, run.time_codes_per_second) == (1.0, 200.0, 1.0)
    assert (len(echo.thoughts), len(echo.reflections), len(echo.perceptions)) == (200, 34, 67)
    assert echo.name == "Echo" and echo.cycle_indexing == "counted"
    assert echo.mood_sampling == "final"
    assert set(echo.mood) == {"contentment", "curiosity", "melancholy", "wonder"}
    assert echo.mood_baseline["curiosity"] == pytest.approx(0.7)
    assert echo.stimulus == pytest.approx((0.0, 1.5, 0.0))
    assert echo.stimulus_radius == pytest.approx(0.4)
    # the golden runs journaled no `cycle` events
    assert not any(c.present for c in echo.cycle.values())
    # attention exists only as the final state.json value
    assert echo.attention["focus"].present and not echo.attention["focus"].sampled


def test_multi_instance_reads_each_layers_metadata(view_stages: dict[str, Path]) -> None:
    run = load_run(view_stages["pair"])
    lumen, nova = run.instances
    assert (lumen.name, nova.name) == ("Lumen", "Nova")
    assert nova.origin[0] - lumen.origin[0] == pytest.approx(10.0)
    assert lumen.mood_sampling == "per-cycle" and nova.mood_sampling == "final"
    assert lumen.mood["curiosity"].at(2) == pytest.approx(0.71)
    assert lumen.mood["curiosity"].at(3) == pytest.approx(0.73)
    assert [p.sender for p in nova.interlocutors]
    said = {p.sender: p.utterance.exact(2.0) for p in nova.interlocutors}
    assert any(said.values())
    assert nova.cycle["hot2Label"].sampled


def test_held_is_usd_held_interpolation() -> None:
    s = {2.0: "a", 5.0: "b"}
    assert held(s, 1.0) == "a"  # the first sample holds backwards
    assert held(s, 2.0) == "a" and held(s, 4.9) == "a"
    assert held(s, 5.0) == "b" and held(s, 99.0) == "b"
    assert held({}, 3.0) is None
    assert Channel(default=0.5).at(7) == 0.5
    assert Channel().present is False


def test_refuses_a_stage_acsf_usd_env_did_not_write(tmp_path: Path) -> None:
    layer = Sdf.Layer.CreateNew(str(tmp_path / "plain.usda"))
    layer.Save()
    with pytest.raises(StageError, match="not an acsf-usd-env stage"):
        load_run(tmp_path / "plain.usda")


def test_refuses_an_unknown_exporter_major(tmp_path: Path) -> None:
    with pytest.raises(StageError, match=r"acsf-usd-env 1\.0\.0.*acsf-usd-env 0\."):
        check_metadata({"acsf:exporterVersion": "1.0.0"}, tmp_path / "x.usda")
    assert check_metadata({"acsf:exporterVersion": "0.9.1"}, tmp_path / "x.usda") == "0.9.1"
    with pytest.raises(StageError, match="unreadable"):
        check_metadata({"acsf:exporterVersion": "next"}, tmp_path / "x.usda")


def test_unreadable_file(tmp_path: Path) -> None:
    bad = tmp_path / "bad.usda"
    bad.write_text("not usd at all")
    with pytest.raises(StageError, match="cannot be opened"):
        load_run(bad)
    with pytest.raises(StageError, match="no such file"):
        load_run(tmp_path / "missing.usda")


def test_missing_exporter_version_is_unreadable(tmp_path: Path) -> None:
    with pytest.raises(StageError, match="unreadable acsf:exporterVersion ''"):
        check_metadata({"acsf:layout:synthetic": True}, tmp_path / "x.usda")


# Hand-built stages: only what each test needs is authored, so the viewer's fallbacks run.


def _write_stage(
    path: Path, build: Callable[[Usd.Stage], None], layer_data: dict[str, Any] | None = None
) -> Path:
    stage = Usd.Stage.CreateNew(str(path))
    stage.GetRootLayer().customLayerData = {"acsf:exporterVersion": "0.1.0", **(layer_data or {})}
    build(stage)
    stage.GetRootLayer().Save()
    return path


def _instance(stage: Usd.Stage, name: str, x: float = 0.0) -> Usd.Prim:
    xform = UsdGeom.Xform.Define(stage, f"{INSTANCES}/{name}")
    xform.AddTranslateOp().Set(Gf.Vec3d(x, 0.0, 0.0))
    return xform.GetPrim()


def _samples(prim: Usd.Prim, name: str, type_name: Any, samples: dict[float, Any]) -> None:
    attr = prim.CreateAttribute(name, type_name, True)
    for cycle, value in samples.items():
        attr.Set(value, cycle)


def test_a_stage_without_world_instances_is_refused(tmp_path: Path) -> None:
    path = _write_stage(tmp_path / "s.usda", lambda s: UsdGeom.Xform.Define(s, "/World"))
    with pytest.raises(StageError, match="no /World/Instances"):
        load_run(path)


def test_a_stage_whose_instances_scope_is_empty_is_refused(tmp_path: Path) -> None:
    path = _write_stage(tmp_path / "s.usda", lambda s: UsdGeom.Xform.Define(s, INSTANCES))
    with pytest.raises(StageError, match="holds no instances"):
        load_run(path)


def test_a_bare_instance_prim_falls_back_to_defaults(tmp_path: Path) -> None:
    def build(stage: Usd.Stage) -> None:
        stage.SetStartTimeCode(3)
        stage.SetEndTimeCode(5)
        stage.SetTimeCodesPerSecond(2)
        _instance(stage, "my_agent", x=3.0)

    run = load_run(_write_stage(tmp_path / "s.usda", build))
    assert run.cycles == [3, 4, 5] and run.time_codes_per_second == 2.0
    assert run.exporter_version == "0.1.0"
    (inst,) = run.instances
    # no acsf:name: the prim name is shown
    assert inst.name == inst.prim_name == "my_agent"
    assert inst.origin == pytest.approx((3.0, 0.0, 0.0))
    # no Stimulus/Sphere: the stimulus sits 1.5 above the instance with radius 0.4
    assert inst.stimulus == pytest.approx((3.0, 1.5, 0.0))
    assert inst.stimulus_radius == pytest.approx(0.4)
    assert inst.thoughts == inst.reflections == inst.perceptions == {}
    assert inst.thought_cycles == [] and inst.longest_thought == 1
    assert inst.interlocutors == [] and inst.mood == {} and inst.mood_baseline == {}
    assert inst.mood_sampling == "final" and inst.cycle_indexing is None
    assert not inst.perception_source.present
    for channels in (inst.attention, inst.cycle, inst.memory):
        assert not any(c.present for c in channels.values())


def test_stream_interlocutors_and_layer_metadata_are_read(tmp_path: Path) -> None:
    def build(stage: Usd.Stage) -> None:
        inst = _instance(stage, "my_agent")
        inst.CreateAttribute("acsf:name", Sdf.ValueTypeNames.String, True).Set("my agent")
        stream = stage.DefinePrim(f"{INSTANCES}/my_agent/Stream")
        thoughts = {1.0: "a", 2.0: "", 3.0: "longer"}
        _samples(stream, "acsf:thought", Sdf.ValueTypeNames.String, thoughts)
        dana = stage.DefinePrim(f"{INSTANCES}/my_agent/Interlocutors/Dana_")
        dana.CreateAttribute("acsf:sender", Sdf.ValueTypeNames.String, True).Set("Dana!")
        _samples(dana, "acsf:utterance", Sdf.ValueTypeNames.String, {2.0: "hi"})
        stage.DefinePrim(f"{INSTANCES}/my_agent/Interlocutors/Robin")

    path = _write_stage(tmp_path / "s.usda", build, {"acsf:cycleIndexing": "journaled"})
    (inst,) = load_run(path).instances
    assert (inst.name, inst.prim_name) == ("my agent", "my_agent")
    assert inst.cycle_indexing == "journaled"
    # empty strings are cycles without a thought
    assert inst.thoughts == {1.0: "a", 3.0: "longer"}
    assert inst.thought_cycles == [1.0, 3.0] and inst.longest_thought == 6
    # an interlocutor without acsf:sender is named after its prim
    assert {p.name: p.sender for p in inst.interlocutors} == {"Dana_": "Dana!", "Robin": "Robin"}
    dana = next(p for p in inst.interlocutors if p.name == "Dana_")
    assert dana.utterance.exact(2.0) == "hi" and dana.utterance.exact(3.0) is None
    assert dana.utterance.at(3.0) == "hi"


@pytest.mark.parametrize(
    ("declared", "sampled", "expected"),
    [
        (None, True, "per-cycle"),
        (None, False, "final"),
        ("final", True, "final"),
        ("anything else", False, "per-cycle"),
    ],
)
def test_mood_sampling(tmp_path: Path, declared: str | None, sampled: bool, expected: str) -> None:
    def build(stage: Usd.Stage) -> None:
        _instance(stage, "my_agent")
        mood = stage.DefinePrim(f"{INSTANCES}/my_agent/Mood")
        curiosity = mood.CreateAttribute("acsf:mood:curiosity", Sdf.ValueTypeNames.Float, True)
        if sampled:
            curiosity.Set(0.75, 1.0)
        else:
            curiosity.Set(0.75)
        baseline = mood.CreateAttribute("acsf:moodBaseline:curiosity", Sdf.ValueTypeNames.Float)
        baseline.Set(0.5)

    layer_data = {} if declared is None else {"acsf:moodSampling": declared}
    (inst,) = load_run(_write_stage(tmp_path / "s.usda", build, layer_data)).instances
    assert inst.mood_sampling == expected
    assert list(inst.mood) == ["curiosity"] and inst.mood["curiosity"].sampled is sampled
    assert inst.mood["curiosity"].at(1.0) == pytest.approx(0.75)
    assert inst.mood_baseline == {"curiosity": 0.5}


def test_instances_are_ordered_by_x_then_name(tmp_path: Path) -> None:
    def build(stage: Usd.Stage) -> None:
        _instance(stage, "b")
        _instance(stage, "a")
        _instance(stage, "c", x=-1.0)

    run = load_run(_write_stage(tmp_path / "s.usda", build))
    assert [i.name for i in run.instances] == ["c", "a", "b"]
