"""Command-line entry point: ``acsf-usd``."""

from __future__ import annotations

import argparse
import logging
import math
import sys
from collections.abc import Sequence
from pathlib import Path

from acsf_usd import __version__
from acsf_usd.follow import DEFAULT_POLL_INTERVAL, follow
from acsf_usd.model import InstanceRun
from acsf_usd.reader import InputError, read_instance, resolve_inputs
from acsf_usd.stage.build import ExportOptions, build_instance_stage, write_stage
from acsf_usd.stage.compose import export_composed
from acsf_usd.validate import format_report, validate

log = logging.getLogger("acsf_usd")

TEXT_EXT = ".usda"
BINARY_EXT = ".usdc"


class UsageError(Exception):
    """A combination of arguments that cannot be honoured."""


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="acsf-usd",
        description="Export ACSF runs to OpenUSD stages (read-only against ACSF).",
    )
    parser.add_argument("--version", action="version", version=f"acsf-usd {__version__}")
    commands = parser.add_subparsers(dest="command", metavar="COMMAND")

    export = commands.add_parser(
        "export",
        help="export an ACSF instance to a USD stage",
        description=(
            "Export ACSF instances to a USD stage. Each input is an instance directory, an "
            "experiment run or golden-run directory, an instance name under "
            "CONSCIOUSNESS_HOME, or a directory of instance directories (a whole "
            "CONSCIOUSNESS_HOME, or an experiment run with replicate-<i>/ subdirectories). One "
            "instance gives one layer; several give one layer per instance plus a root layer "
            "that sublayers them on one shared timeline. Inputs are only read, never modified."
        ),
    )
    export.add_argument(
        "inputs",
        nargs="+",
        metavar="input",
        help="instance directory, run directory, instance name, or directory of instances",
    )
    export.add_argument(
        "-o",
        "--output",
        type=Path,
        help=(
            "output file (default: <name>.usda for one instance, run.usda for several; "
            ".usdc with --binary). Instance layers of a multi-instance export are written "
            "next to it"
        ),
    )
    export.add_argument(
        "--binary", action="store_true", help="write binary .usdc instead of text .usda"
    )
    export.add_argument(
        "--time-codes-per-second",
        type=_positive_float,
        default=1.0,
        metavar="N",
        help="playback rate in cycles per second (default: 1)",
    )
    export.add_argument(
        "--source-commit",
        metavar="SHA",
        help="ACSF commit the run was produced with; recorded as acsf:sourceCommit",
    )
    export.add_argument(
        "--follow",
        action="store_true",
        help=(
            "keep the output current while the instance runs: poll its journal, rewrite the "
            "output atomically, and exit 0 after a final write on Ctrl-C/SIGTERM "
            "(one instance only)"
        ),
    )
    export.add_argument(
        "--poll-interval",
        type=_positive_float,
        default=DEFAULT_POLL_INTERVAL,
        metavar="S",
        help=f"seconds between journal polls with --follow (default: {DEFAULT_POLL_INTERVAL})",
    )

    check = commands.add_parser(
        "validate",
        help="check a stage against its source journal(s)",
        description=(
            "Re-read a stage written by `acsf-usd export` and check it against the same "
            "input(s): every journaled thought, reflection, perception and utterance must be "
            "held byte-for-byte at its cycle, other cycles must hold an empty string, and "
            "acsf:cycleIndexing must match. Exits 1 on any mismatch. Reads .usda and .usdc."
        ),
    )
    check.add_argument("stage", type=Path, help="stage file (.usda or .usdc; a root layer)")
    check.add_argument("inputs", nargs="+", metavar="source", help="the input(s) it came from")

    describe = commands.add_parser(
        "describe",
        help="summarise ACSF input(s) without writing anything",
        description="Print what an export of the input(s) would contain. Writes nothing.",
    )
    describe.add_argument("inputs", nargs="+", metavar="input", help="as for export")
    return parser


def _positive_float(text: str) -> float:
    try:
        value = float(text)
    except ValueError:
        raise argparse.ArgumentTypeError(f"not a number: {text!r}") from None
    if not (math.isfinite(value) and value > 0):
        raise argparse.ArgumentTypeError("must be a finite number greater than 0")
    return value


def output_path(output: Path | None, name: str, binary: bool) -> Path:
    """Default or check the output path; the flag, not the extension, picks the format."""
    wanted = BINARY_EXT if binary else TEXT_EXT
    if output is None:
        return Path(f"{name}{wanted}")
    ext = output.suffix.lower()
    if ext in (TEXT_EXT, BINARY_EXT) and ext != wanted:
        flag = "with --binary" if binary else "without --binary"
        raise UsageError(f"{output}: a {ext} file cannot be written {flag}; use {wanted}")
    return output


def refuse_output_inside(output: Path, inputs: Sequence[Path]) -> None:
    resolved = output.resolve()
    for directory in inputs:
        if resolved.is_relative_to(directory.resolve()):
            raise UsageError(
                f"{output}: refusing to write inside the input directory {directory} "
                "(acsf-usd never writes to an ACSF instance directory)"
            )


def describe_run(run: InstanceRun) -> str:
    cycles = run.journal.cycles
    span = f"cycles {cycles[0]}–{cycles[-1]}" if cycles else "no cycles"
    return (
        f"{run.name}: {len(run.journal.events)} events, {span}, "
        f"{run.journal.indexing} indexing, {len(run.warnings)} warnings"
    )


def run_export(args: argparse.Namespace) -> int:
    directories, collection = resolve_inputs(args.inputs)
    # Collection directories themselves are inputs too: nothing may be written inside them.
    protected = directories + [Path(arg) for arg in args.inputs if Path(arg).is_dir()]
    options = ExportOptions(
        time_codes_per_second=args.time_codes_per_second, source_commit=args.source_commit
    )
    if args.follow:
        if len(directories) != 1 or collection:
            raise UsageError("--follow takes exactly one instance (not several or a collection)")
        return run_follow(args, directories[0], protected, options)
    if len(directories) == 1 and not collection:
        run = read_instance(directories[0])
        output = output_path(args.output, run.name, args.binary)
        refuse_output_inside(output, protected)
        write_stage(build_instance_stage(run, options), output, binary=args.binary)
        print(f"wrote {output} ({describe_run(run)})")
        return 0

    output = output_path(args.output, "run", args.binary)
    refuse_output_inside(output, protected)
    runs = [read_instance(directory) for directory in directories]
    try:
        result = export_composed(runs, output, options, binary=args.binary)
    except ValueError as exc:
        raise UsageError(str(exc)) from None
    for layer, run in zip(result.layers, result.runs, strict=True):
        print(f"wrote {layer} ({describe_run(run)})")
    print(f"wrote {output} (root layer sublayering {len(result.layers)} instances)")
    return 0


def run_follow(
    args: argparse.Namespace, directory: Path, protected: list[Path], options: ExportOptions
) -> int:
    output = output_path(args.output, directory.resolve().name, args.binary)
    refuse_output_inside(output, protected)

    def report(run: InstanceRun) -> None:
        print(f"updated {output} ({describe_run(run)})", flush=True)

    print(f"following {directory} -> {output} (Ctrl-C to stop)", flush=True)
    follow(
        directory,
        output,
        options,
        binary=args.binary,
        poll_interval=args.poll_interval,
        on_write=report,
    )
    print(f"stopped; final write to {output}", flush=True)
    return 0


def run_validate(args: argparse.Namespace) -> int:
    directories, _ = resolve_inputs(args.inputs)
    runs = [read_instance(directory) for directory in directories]
    reports = validate(args.stage, runs)
    for report in reports:
        for line in format_report(report):
            print(line)
    failed = sum(1 for report in reports if report.problems)
    if failed:
        print(f"validate: FAIL — {failed} of {len(reports)} instance(s) differ from the journal")
        return 1
    print(f"validate: OK — {len(reports)} instance(s) match their journals")
    return 0


def run_describe(args: argparse.Namespace) -> int:
    directories, _ = resolve_inputs(args.inputs)
    for directory in directories:
        run = read_instance(directory)
        counts: dict[str, int] = {}
        for indexed in run.journal.events:
            counts[indexed.event.type] = counts.get(indexed.event.type, 0) + 1
        mood = any(
            i.event.cycle is not None and i.event.cycle.mood is not None for i in run.journal.events
        )
        print(f"{directory}: {describe_run(run)}")
        print("  events: " + ", ".join(f"{n} {t}" for t, n in sorted(counts.items())))
        print(f"  mood: {'journaled per cycle' if mood else 'final value only'}")
        print(f"  snapshot: {'state.json' if run.snapshot is not None else 'none'}")
    return 0


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if args.command is None:
        parser.print_help()
        return 0
    # Warnings go to the stderr of this invocation (not a process-wide basicConfig).
    handler = logging.StreamHandler(sys.stderr)
    handler.setFormatter(logging.Formatter("acsf-usd: warning: %(message)s"))
    log.addHandler(handler)
    log.setLevel(logging.WARNING)
    log.propagate = False
    try:
        if args.command == "export":
            return run_export(args)
        if args.command == "validate":
            return run_validate(args)
        if args.command == "describe":
            return run_describe(args)
    except UsageError as exc:
        parser.exit(2, f"acsf-usd: error: {exc}\n")
    except (InputError, OSError) as exc:
        print(f"acsf-usd: error: {exc}", file=sys.stderr)
        return 1
    finally:
        log.removeHandler(handler)
        log.propagate = True
    parser.error(f"unknown command {args.command}")
    return 2
