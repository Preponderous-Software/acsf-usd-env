"""`acsf-usd validate` internals: checks exercised directly on an in-memory stage.

The end-to-end validate command is covered in test_e2e.py; these tests pin the branches it
does not reach (a missing instance prim, an ``acsf:cycleIndexing`` mismatch, a missing time
sample, samples outside the journal's cycles, utterances without a recipient, finding a single
run's prim by ``acsf:name``, truncated mismatch values, and the report format).
"""

from __future__ import annotations

from dataclasses import replace

import pytest
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


def test_a_cycle_indexing_mismatch_is_reported() -> None:
    run, stage = _nova()
    counted = replace(run, journal=replace(run.journal, indexing="counted"))
    report = validate_instance(stage, counted, "Nova")
    assert report.problems == ["acsf:cycleIndexing is 'journaled', the journal needs 'counted'"]


def test_a_cycle_without_a_time_sample_is_reported() -> None:
    run, stage = _nova()
    stage.GetPrimAtPath(f"{NOVA}/Stimulus").GetAttribute("acsf:perception").ClearAtTime(2)
    report = validate_instance(stage, run, "Nova")
    assert report.problems == ["Stimulus.acsf:perception cycle 2: no time sample"]


@pytest.mark.parametrize("extras", [{}, {"to": ""}, {"to": 7}])
def test_an_utterance_without_a_recipient_is_checked_as_unknown(extras: dict[str, object]) -> None:
    run = read_instance(INSTANCES / "Nova")
    events = tuple(
        replace(i, event=replace(i.event, extras=extras))
        if i.event.type == "utterance" and i.event.extras.get("to") == "Dana"
        else i
        for i in run.journal.events
    )
    run = replace(run, journal=replace(run.journal, events=events))
    stage = build_instance_stage(run)
    report = validate_instance(stage, run, "Nova")
    assert report.problems == []
    assert report.checked["utterance"] == 2
    utterance = stage.GetPrimAtPath(f"{NOVA}/Interlocutors/unknown").GetAttribute("acsf:utterance")
    utterance.Set("tampered", 2)
    assert validate_instance(stage, run, "Nova").problems == [
        "Interlocutors[unknown].acsf:utterance cycle 2: stage has 'tampered', "
        "journal has 'Hello Dana. I was thinking about tide pools.'"
    ]


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
        f"Stream.acsf:thought cycle 1: stage has '{'x' * 56}..., journal has 'Nova thought one.'"
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
