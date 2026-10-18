"""Milestone 6: multi-instance composition."""

from __future__ import annotations

import shutil
import subprocess
import sys
from pathlib import Path

import pytest
from pxr import Usd, UsdGeom

from acsf_usd.cli import main
from acsf_usd.reader import read_instance, resolve_inputs
from acsf_usd.stage.build import build_instance_stage, write_stage
from acsf_usd.stage.compose import INSTANCE_SPACING, disambiguate, export_composed
from tests.conftest import GOLDEN, INSTANCES


def _open(path: Path) -> Usd.Stage:
    stage = Usd.Stage.Open(str(path))
    assert stage is not None
    return stage


def _x(stage: Usd.Stage, path: str) -> float:
    xform = UsdGeom.Xformable(stage.GetPrimAtPath(path))
    return float(xform.ComputeLocalToWorldTransform(Usd.TimeCode(1)).ExtractTranslation()[0])


def test_two_instances_share_one_timeline(tmp_path: Path) -> None:
    runs = [read_instance(INSTANCES / "Nova"), read_instance(GOLDEN / "Echo")]
    result = export_composed(runs, tmp_path / "run.usda")
    stage = _open(result.root)
    assert stage.GetRootLayer().subLayerPaths == ["./Echo.usda", "./Nova.usda"]
    echo = stage.GetPrimAtPath("/World/Instances/Echo")
    nova = stage.GetPrimAtPath("/World/Instances/Nova")
    assert echo.IsValid() and nova.IsValid()
    # Acceptance criterion 8: one timeline spanning both instances.
    assert (stage.GetStartTimeCode(), stage.GetEndTimeCode()) == (1, 200)
    thought = stage.GetPrimAtPath("/World/Instances/Nova/Stream").GetAttribute("acsf:thought")
    assert thought.Get(2) == read_instance(INSTANCES / "Nova").journal.events[6].event.content
    assert stage.GetRootLayer().customLayerData["acsf:layout:synthetic"] is True
    assert stage.GetDefaultPrim().GetPath() == "/World"


def test_instances_are_offset_along_x(tmp_path: Path) -> None:
    runs = [read_instance(INSTANCES / n) for n in ("Nova", "Ash", "Lumen")]
    stage = _open(export_composed(runs, tmp_path / "run.usda").root)
    assert _x(stage, "/World/Instances/Ash") == 0.0
    assert _x(stage, "/World/Instances/Lumen") == INSTANCE_SPACING
    assert _x(stage, "/World/Instances/Nova") == 2 * INSTANCE_SPACING
    # Child geometry moves with its instance.
    sphere = "/World/Instances/Nova/Stimulus/Sphere"
    assert _x(stage, sphere) == 2 * INSTANCE_SPACING


def test_each_instance_layer_opens_on_its_own_and_matches_single_export(tmp_path: Path) -> None:
    runs = [read_instance(INSTANCES / "Nova"), read_instance(INSTANCES / "Ash")]
    result = export_composed(runs, tmp_path / "multi" / "run.usda")
    single = tmp_path / "Nova.usda"
    write_stage(build_instance_stage(read_instance(INSTANCES / "Nova")), single)
    nova_layer = tmp_path / "multi" / "Nova.usda"
    assert nova_layer in result.layers
    assert nova_layer.read_bytes() == single.read_bytes()
    alone = _open(tmp_path / "multi" / "Ash.usda")
    assert alone.GetPrimAtPath("/World/Instances/Ash").IsValid()
    assert not alone.GetPrimAtPath("/World/Instances/Nova").IsValid()


def _two_novas(tmp_path: Path) -> tuple[Path, Path]:
    a, b = tmp_path / "a" / "Nova", tmp_path / "b" / "Nova"
    shutil.copytree(INSTANCES / "Nova", a)
    shutil.copytree(INSTANCES / "Ash", b)
    return a, b


def test_name_collisions_are_disambiguated_deterministically(tmp_path: Path) -> None:
    a, b = _two_novas(tmp_path)
    forward = disambiguate([read_instance(a), read_instance(b)])
    backward = disambiguate([read_instance(b), read_instance(a)])
    assert forward == ["Nova", "Nova_2"]
    assert backward == ["Nova_2", "Nova"]
    result = export_composed([read_instance(b), read_instance(a)], tmp_path / "out" / "run.usda")
    stage = _open(result.root)
    assert stage.GetPrimAtPath("/World/Instances/Nova").GetAttribute("acsf:name").Get() == "Nova"
    assert stage.GetPrimAtPath("/World/Instances/Nova_2").GetAttribute("acsf:name").Get() == "Nova"
    # Nova_2 is the copy of Ash: no thought "Nova thought one." there.
    nova2 = stage.GetPrimAtPath("/World/Instances/Nova_2/Stream").GetAttribute("acsf:thought")
    assert nova2.Get(1) == "Ash one."


def test_sanitised_collisions_are_disambiguated() -> None:
    a = read_instance(INSTANCES / "Ash")
    runs = [
        type(a)(name="x-1", source_dir=Path("/p"), journal=a.journal, snapshot=None),
        type(a)(name="x_1", source_dir=Path("/q"), journal=a.journal, snapshot=None),
    ]
    assert disambiguate(runs) == ["x_1", "x_1_2"]


def test_collection_inputs(tmp_path: Path) -> None:
    directories, collection = resolve_inputs([str(INSTANCES)])
    assert collection and [d.name for d in directories] == ["Ash", "Lumen", "Nova"]
    run_dir = tmp_path / "experiment"
    for i in (1, 2):
        shutil.copytree(INSTANCES / "Ash", run_dir / f"replicate-{i}")
    (run_dir / "manifest.yaml").write_text("name: x\n")
    directories, collection = resolve_inputs([str(run_dir)])
    assert collection and [d.name for d in directories] == ["replicate-1", "replicate-2"]
    directories, collection = resolve_inputs([str(INSTANCES / "Ash"), str(INSTANCES / "Ash")])
    assert not collection and len(directories) == 1


def test_empty_collection_is_an_input_error(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    (tmp_path / "empty").mkdir()
    assert main(["export", str(tmp_path / "empty"), "-o", str(tmp_path / "x.usda")]) == 1
    assert "no instance directories" in capsys.readouterr().err


def test_export_command_whole_consciousness_home(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.chdir(tmp_path)
    assert main(["export", str(INSTANCES)]) == 0
    assert sorted(p.name for p in tmp_path.iterdir()) == [
        "Ash.usda",
        "Lumen.usda",
        "Nova.usda",
        "run.usda",
    ]
    assert "root layer sublayering 3 instances" in capsys.readouterr().out
    stage = _open(tmp_path / "run.usda")
    assert len(stage.GetPrimAtPath("/World/Instances").GetChildren()) == 3


def test_export_command_names_under_consciousness_home(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("CONSCIOUSNESS_HOME", str(INSTANCES))
    assert main(["export", "Nova", "Ash", "-o", "pair.usda"]) == 0
    stage = _open(tmp_path / "pair.usda")
    children = stage.GetPrimAtPath("/World/Instances").GetChildren()
    assert sorted(p.GetName() for p in children) == ["Ash", "Nova"]


def test_export_command_binary_multi(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.chdir(tmp_path)
    assert main(["export", str(INSTANCES / "Nova"), str(INSTANCES / "Ash"), "--binary"]) == 0
    assert sorted(p.name for p in tmp_path.iterdir()) == ["Ash.usdc", "Nova.usdc", "run.usdc"]
    for path in tmp_path.iterdir():
        assert path.read_bytes().startswith(b"PXR-USDC")
    assert _open(tmp_path / "run.usdc").GetPrimAtPath("/World/Instances/Nova").IsValid()


def test_root_that_would_overwrite_an_instance_layer_is_a_usage_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(tmp_path)
    with pytest.raises(SystemExit) as excinfo:
        main(["export", str(INSTANCES / "Nova"), str(INSTANCES / "Ash"), "-o", "Nova.usda"])
    assert excinfo.value.code == 2


def test_output_inside_a_collection_is_refused(tmp_path: Path) -> None:
    home = tmp_path / "home"
    shutil.copytree(INSTANCES, home)
    with pytest.raises(SystemExit) as excinfo:
        main(["export", str(home), "-o", str(home / "Nova" / "run.usda")])
    assert excinfo.value.code == 2
    assert sorted(p.name for p in (home / "Nova").iterdir()) == ["journal.jsonl", "state.json"]


def test_composition_writes_without_usd_warnings(tmp_path: Path) -> None:
    script = (
        "import sys; from pathlib import Path\n"
        "from acsf_usd.reader import read_instance\n"
        "from acsf_usd.stage.compose import export_composed\n"
        "runs = [read_instance(Path(p)) for p in sys.argv[2:]]\n"
        "export_composed(runs, Path(sys.argv[1]))\n"
    )
    result = subprocess.run(
        [
            sys.executable,
            "-c",
            script,
            str(tmp_path / "run.usda"),
            *map(str, (INSTANCES / "Nova", INSTANCES / "Ash")),
        ],
        capture_output=True,
        text=True,
        check=True,
    )
    assert "Could not load sublayer" not in result.stderr
    assert "Warning" not in result.stderr


def test_composed_export_is_deterministic(tmp_path: Path) -> None:
    outputs = []
    for i in range(2):
        runs = [read_instance(INSTANCES / n) for n in ("Nova", "Ash")]
        result = export_composed(runs, tmp_path / str(i) / "run.usda")
        outputs.append([p.read_bytes() for p in (result.root, *result.layers)])
    assert outputs[0] == outputs[1]
