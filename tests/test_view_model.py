"""Reading exported stages for ``acsf-usd view``: the input is the stage only."""

from __future__ import annotations

from pathlib import Path

import pytest
from pxr import Sdf

from acsf_usd.view.model import Channel, StageError, check_metadata, held, load_run


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
