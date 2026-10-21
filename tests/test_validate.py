"""`acsf-usd validate` internals: checks exercised directly on an in-memory stage.

The end-to-end validate command is covered in test_e2e.py; these tests pin the branches it
does not reach (a missing instance prim, samples outside the journal's cycles, finding a
single run's prim by ``acsf:name``, truncated mismatch values, and the report format).
"""

from __future__ import annotations

from pxr import Sdf, Usd

from acsf_usd.model import InstanceRun
from acsf_usd.reader import read_instance
from acsf_usd.stage.build import build_instance_stage
from acsf_usd.validate import InstanceReport, format_report, instance_prims, validate_instance
from tests.conftest import INSTANCES

NOVA = "/World/Instances/Nova"


def _nova() -> tuple[InstanceRun, Usd.Stage]:
    run = read_instance(INSTANCES / "Nova")
    return run, build_instance_stage(run)


def test_an_unaltered_stage_has_no_problems_and_counts_every_channel() -> None:
    run, stage = _nova()
    report = validate_instance(stage, run, "Nova")
    assert report.problems == []
    assert report.cycles == 4  # cycles 1–4, including the failed cycle 3
    assert report.checked == {"perception": 2, "reflection": 1, "thought": 3, "utterance": 2}


def test_a_missing_instance_prim_is_the_only_problem_reported() -> None:
    run, stage = _nova()
    report = validate_instance(stage, run, "Missing")
    assert report.problems == ["/World/Instances/Missing: instance prim not found"]
    assert report.cycles == 0
    assert report.checked == {}


def test_samples_outside_the_journals_cycles_are_reported() -> None:
    run, stage = _nova()
    stage.GetPrimAtPath(f"{NOVA}/Stream").GetAttribute("acsf:thought").Set("late", 9)
    report = validate_instance(stage, run, "Nova")
    assert report.problems == ["Stream.acsf:thought: samples outside the journal's cycles: [9.0]"]


def test_long_mismatching_values_are_truncated_in_the_report() -> None:
    run, stage = _nova()
    stage.GetPrimAtPath(f"{NOVA}/Stream").GetAttribute("acsf:thought").Set("x" * 100, 1)
    report = validate_instance(stage, run, "Nova")
    assert report.problems == [
        f"Stream.acsf:thought cycle 1: stage has '{'x' * 56}..., "
        "journal has 'Nova thought one.'"
    ]


def test_a_single_runs_prim_is_its_sanitised_name_when_present() -> None:
    run, stage = _nova()
    assert instance_prims(stage, [run]) == ["Nova"]


def test_a_single_runs_prim_is_found_by_acsf_name_when_renamed() -> None:
    run = read_instance(INSTANCES / "Nova")
    stage = build_instance_stage(run, prim="Renamed")
    assert instance_prims(stage, [run]) == ["Renamed"]
    assert validate_instance(stage, run, "Renamed").problems == []


def test_an_ambiguous_acsf_name_falls_back_to_the_sanitised_name() -> None:
    run = read_instance(INSTANCES / "Nova")
    stage = build_instance_stage(run, prim="Renamed")
    other = stage.DefinePrim("/World/Instances/Other")
    other.CreateAttribute("acsf:name", Sdf.ValueTypeNames.String).Set("Nova")
    assert instance_prims(stage, [run]) == ["Nova"]
    assert validate_instance(stage, run, "Nova").problems == [
        "/World/Instances/Nova: instance prim not found"
    ]


def test_format_report_ok_line_lists_counts_in_type_order() -> None:
    report = InstanceReport(
        name="replicate-1", prim="replicate_1", cycles=4, checked={"thought": 3, "perception": 2}
    )
    assert format_report(report) == [
        "replicate-1 (replicate_1): OK — 4 cycles; 2 perception, 3 thought"
    ]


def test_format_report_indents_each_problem_under_the_summary() -> None:
    report = InstanceReport(name="Nova", prim="Nova", cycles=4, problems=["first", "second"])
    assert format_report(report) == [
        "Nova (Nova): 2 problem(s) — 4 cycles; ",
        "  first",
        "  second",
    ]
