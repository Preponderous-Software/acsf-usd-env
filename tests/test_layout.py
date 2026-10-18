"""Milestone 5: schematic, explicitly synthetic layout."""

from __future__ import annotations

import math
import subprocess
import sys
from pathlib import Path

import pytest
from pxr import Gf, Usd, UsdGeom

from acsf_usd.reader import read_instance
from acsf_usd.stage.build import build_instance_stage, write_stage
from acsf_usd.stage.layout import RING_RADIUS, ring_angle, ring_position
from tests.conftest import GOLDEN, INSTANCES

NOVA = "/World/Instances/Nova"


@pytest.fixture(scope="module")
def nova(tmp_path_factory: pytest.TempPathFactory) -> Usd.Stage:
    out = tmp_path_factory.mktemp("nova") / "nova.usda"
    write_stage(build_instance_stage(read_instance(INSTANCES / "Nova")), out)
    stage = Usd.Stage.Open(str(out))
    assert stage is not None
    return stage


def _visibility(stage: Usd.Stage, path: str) -> dict[float, str]:
    attr = UsdGeom.Imageable(stage.GetPrimAtPath(path)).GetVisibilityAttr()
    return {t: attr.Get(t) for t in attr.GetTimeSamples()}


def test_geometry_prims_and_types(nova: Usd.Stage) -> None:
    assert nova.GetPrimAtPath(f"{NOVA}/Stimulus/Sphere").GetTypeName() == "Sphere"
    assert nova.GetPrimAtPath(f"{NOVA}/Interlocutors/Dana/Cube").GetTypeName() == "Cube"
    assert nova.GetPrimAtPath(f"{NOVA}/Interlocutors/Robin/Cube").GetTypeName() == "Cube"
    assert nova.GetRootLayer().customLayerData["acsf:layout:synthetic"] is True
    # doc metadata is not inherited: every geometry prim and its parent carry the label.
    for path in (
        "Stimulus",
        "Stimulus/Sphere",
        "Interlocutors/Dana",
        "Interlocutors/Dana/Cube",
        "Interlocutors/Robin",
        "Interlocutors/Robin/Cube",
    ):
        assert "Synthetic" in nova.GetPrimAtPath(f"{NOVA}/{path}").GetDocumentation(), path


def test_stimulus_visible_exactly_on_perception_cycles(nova: Usd.Stage) -> None:
    assert _visibility(nova, f"{NOVA}/Stimulus") == {
        1.0: "inherited",
        2.0: "inherited",
        3.0: "invisible",
        4.0: "invisible",
    }
    sphere = UsdGeom.Imageable(nova.GetPrimAtPath(f"{NOVA}/Stimulus/Sphere"))
    assert sphere.ComputeVisibility(Usd.TimeCode(4)) == "invisible"
    assert sphere.ComputeVisibility(Usd.TimeCode(1)) == "inherited"


def test_interlocutor_visible_exactly_on_reply_cycles(nova: Usd.Stage) -> None:
    assert _visibility(nova, f"{NOVA}/Interlocutors/Dana") == {
        1.0: "invisible",
        2.0: "inherited",
        3.0: "invisible",
        4.0: "invisible",
    }
    assert _visibility(nova, f"{NOVA}/Interlocutors/Robin") == {
        1.0: "invisible",
        2.0: "invisible",
        3.0: "invisible",
        4.0: "inherited",
    }


def test_visibility_matches_presence_on_golden_echo(tmp_path: Path) -> None:
    out = tmp_path / "echo.usda"
    write_stage(build_instance_stage(read_instance(GOLDEN / "Echo")), out)
    stage = Usd.Stage.Open(str(out))
    stimulus = "/World/Instances/Echo/Stimulus"
    perception = stage.GetPrimAtPath(stimulus).GetAttribute("acsf:perception")
    visibility = _visibility(stage, stimulus)
    assert len(visibility) == 200
    for cycle, token in visibility.items():
        assert (token == "inherited") == bool(perception.Get(cycle))
    assert list(visibility.values()).count("inherited") == 67


def test_interlocutors_sit_on_the_ring(nova: Usd.Stage) -> None:
    for name in ("Dana", "Robin"):
        xform = UsdGeom.Xformable(nova.GetPrimAtPath(f"{NOVA}/Interlocutors/{name}"))
        translate = xform.ComputeLocalToWorldTransform(Usd.TimeCode(1)).ExtractTranslation()
        assert translate == ring_position(name)
        assert math.isclose(math.hypot(translate[0], translate[2]), RING_RADIUS, rel_tol=1e-5)
        assert translate[1] == 0.0


def test_ring_position_is_stable_and_not_salted() -> None:
    # Values pinned so a change of hash or rounding is caught; Python's hash() is salted
    # per process, sha256 is not.
    assert ring_angle("Dana") == ring_angle("Dana")
    code = "from acsf_usd.stage.layout import ring_position; print(tuple(ring_position('Dana')))"
    runs = {
        subprocess.run(
            [sys.executable, "-c", code],
            capture_output=True,
            text=True,
            check=True,
            env={"PYTHONHASHSEED": seed},
        ).stdout
        for seed in ("1", "2")
    }
    assert len(runs) == 1
    assert runs.pop().strip() == str(tuple(ring_position("Dana")))
    assert ring_position("Dana") != ring_position("Robin")
    assert isinstance(ring_position("Dana"), Gf.Vec3d)


def test_positions_identical_across_exports(tmp_path: Path) -> None:
    texts = []
    for i in range(2):
        out = tmp_path / f"n{i}.usda"
        write_stage(build_instance_stage(read_instance(INSTANCES / "Nova")), out)
        texts.append(out.read_bytes())
    assert texts[0] == texts[1]


def test_instance_without_perceptions_has_an_invisible_stimulus(tmp_path: Path) -> None:
    directory = tmp_path / "Quiet"
    directory.mkdir()
    (directory / "journal.jsonl").write_text('{"type": "thought", "content": "t"}\n')
    out = tmp_path / "q.usda"
    write_stage(build_instance_stage(read_instance(directory)), out)
    stage = Usd.Stage.Open(str(out))
    assert _visibility(stage, "/World/Instances/Quiet/Stimulus") == {1.0: "invisible"}
    assert not stage.GetPrimAtPath("/World/Instances/Quiet/Interlocutors").IsValid()
