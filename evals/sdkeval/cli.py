"""Command line: ``uv run python -m sdkeval <command>`` from ``evals/``.

Commands::

    list                     families, categories, difficulty
    show <family> [-v N]     print one instance: prompt, setup, reference, checks
    run --model SPEC ...     run a suite and write a report
    report <run_dir>         rebuild summary.json / report.md from results.jsonl
    compare <run_a> <run_b>  side-by-side of two runs
    regrade <run_dir>        re-execute a run's final scripts against today's SDK
    selftest                 reference must score 100%, noop must score 0%
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from . import report as reporting
from .models import Sampling, resolve
from .runner import RunConfig, load_results, regrade, run_suite
from .sandbox import Limits
from .tasks import FAMILIES, suite

RUNS = Path(__file__).resolve().parent.parent / "runs"


def _suite_args(p: argparse.ArgumentParser) -> None:
    p.add_argument("--family", action="append", dest="families", help="restrict to a family (repeatable)")
    p.add_argument("--category", action="append", dest="categories", help="restrict to a category (repeatable)")
    p.add_argument("--variants", type=int, default=3, help="seeded variants per family (default 3)")
    p.add_argument("--split", default="dev", choices=["dev", "heldout", "all"])
    p.add_argument("--max-difficulty", type=int, default=None)


def _tasks(args):
    return suite(families=args.families, categories=args.categories, variants=args.variants,
                 split=args.split, max_difficulty=args.max_difficulty)


def _progress(result, done, total):
    mark = "PASS" if result.passed else f"FAIL[{result.failure_class}]"
    print(f"[{done:>4}/{total}] {mark:<24} {result.instance_id} s{result.sample} t{result.turns}", flush=True)


def cmd_list(args) -> int:
    print(f"{'family':<24} {'category':<13} diff  description")
    for name, fam in sorted(FAMILIES.items(), key=lambda kv: (kv[1].category, kv[0])):
        print(f"{name:<24} {fam.category:<13} {fam.difficulty:>4}  {fam.description}")
    print(f"\n{len(FAMILIES)} families")
    return 0


def cmd_show(args) -> int:
    from .tasks import instance

    task = instance(args.family, args.variant)
    print(f"# {task.instance_id} ({task.category}, difficulty {task.difficulty}, split {task.split})\n")
    print("## Prompt\n\n" + task.prompt + "\n")
    if task.setup:
        print("## Setup (hidden from the model unless the prompt quotes it)\n\n```python\n" + task.setup + "```\n")
    print("## Reference solution\n\n```python\n" + task.reference + "```\n")
    print("## Checks\n")
    for c in task.checks:
        print(f"- [{'critical' if c.critical else 'hygiene'}] {c.label} — {c.kind} {json.dumps(c.args)[:160]}")
    return 0


def cmd_run(args) -> int:
    tasks = _tasks(args)
    config = RunConfig(
        model=args.model, context=args.context, samples=args.samples, max_turns=args.max_turns, jobs=args.jobs,
        temperature=args.temperature, max_tokens=args.max_tokens, timeout_s=args.timeout,
        families=args.families, categories=args.categories, variants=args.variants, split=args.split,
        max_difficulty=args.max_difficulty, label=args.label,
    )
    model = resolve(args.model, Sampling(temperature=args.temperature, max_tokens=args.max_tokens))
    print(f"{len(tasks)} instances × {args.samples} sample(s) → {model.name}", flush=True)
    run_dir = run_suite(model, tasks, config, out_root=Path(args.out), resume=Path(args.resume) if args.resume else None,
                        progress=None if args.quiet else _progress)
    summary = reporting.write(run_dir, load_results(run_dir))
    o = summary["overall"]
    print(f"\npass {o['passed']}/{o['n']} = {100 * o['rate']:.1f}% "
          f"(first turn {100 * o['first_turn_rate']:.1f}%) — report: {run_dir / 'report.md'}")
    if args.min_pass is not None and o["rate"] < args.min_pass:
        print(f"below --min-pass {args.min_pass}", file=sys.stderr)
        return 1
    return 0


def cmd_report(args) -> int:
    run_dir = Path(args.run_dir)
    reporting.write(run_dir, load_results(run_dir))
    print((run_dir / "report.md").read_text())
    return 0


def cmd_compare(args) -> int:
    a, b = Path(args.run_a), Path(args.run_b)
    sa = reporting.summarize(load_results(a))
    sb = reporting.summarize(load_results(b))
    print(reporting.compare(sa, sb, name_a=a.name, name_b=b.name))
    return 0


def cmd_regrade(args) -> int:
    from .tasks import instance

    run_dir = Path(args.run_dir)
    ids = {r["instance_id"] for r in load_results(run_dir)}
    tasks = {}
    for iid in ids:
        name, _, variant = iid.partition("#")
        if name in FAMILIES:
            tasks[iid] = instance(name, int(variant))
    rows = regrade(run_dir, tasks, limits=Limits(timeout_s=args.timeout))
    flipped = [r for r in rows if r["regraded_passed"] != r["passed"]]
    print(f"{sum(r['regraded_passed'] for r in rows)}/{len(rows)} pass today "
          f"(were {sum(r['passed'] for r in rows)}); {len(flipped)} changed")
    for r in flipped:
        print(f"  {r['instance_id']} s{r['sample']}: {r['passed']} → {r['regraded_passed']} ({r['regraded_failure_class']})")
    return 0


def cmd_selftest(args) -> int:
    """The eval's own gate: the reference solves everything and an empty reply solves nothing."""
    tasks = _tasks(args)
    failures = 0
    for spec, expected in (("reference", 1.0), ("noop", 0.0)):
        config = RunConfig(model=spec, jobs=args.jobs, variants=args.variants, split=args.split, label=f"selftest-{spec}")
        run_dir = run_suite(resolve(spec), tasks, config, out_root=Path(args.out))
        records = load_results(run_dir)
        summary = reporting.write(run_dir, records)
        rate = summary["overall"]["rate"]
        ok = rate == expected
        failures += not ok
        print(f"{spec:<10} {100 * rate:5.1f}% (expected {100 * expected:.0f}%) {'OK' if ok else 'FAIL'}  {run_dir}")
        if not ok:
            for r in records:
                if r["passed"] != bool(expected):
                    bad = [f"{c['label']}: {c['detail']}" for c in r["checks"] if c["critical"] and not c["passed"]]
                    exc = ((r["attempts"][-1].get("observation") or {}).get("exception") or "")[-300:] if r["attempts"] else ""
                    print(f"   {r['instance_id']}: {r['failure_class']} {bad[:2]} {exc}")
    return 1 if failures else 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="sdkeval", description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("list").set_defaults(fn=cmd_list)

    p = sub.add_parser("show")
    p.add_argument("family")
    p.add_argument("-v", "--variant", type=int, default=0)
    p.set_defaults(fn=cmd_show)

    p = sub.add_parser("run")
    p.add_argument("--model", required=True, help="model spec, e.g. anthropic:claude-sonnet-5-5 (see models.py)")
    p.add_argument("--context", default="reference", help="'reference' (default), 'none', or a path to a context file")
    p.add_argument("--samples", type=int, default=1, help="independent samples per instance (pass^k needs >1)")
    p.add_argument("--max-turns", type=int, default=1, help="agent turns with diagnostic feedback (default 1)")
    p.add_argument("--jobs", type=int, default=4)
    p.add_argument("--temperature", type=float, default=0.0)
    p.add_argument("--max-tokens", type=int, default=4096)
    p.add_argument("--timeout", type=float, default=60.0, help="seconds per script execution")
    p.add_argument("--label", default=None, help="run directory label (default: the model spec)")
    p.add_argument("--out", default=str(RUNS))
    p.add_argument("--resume", default=None, help="continue an interrupted run directory")
    p.add_argument("--min-pass", type=float, default=None, help="exit 1 below this pass rate (CI gate)")
    p.add_argument("--quiet", action="store_true")
    _suite_args(p)
    p.set_defaults(fn=cmd_run)

    p = sub.add_parser("report")
    p.add_argument("run_dir")
    p.set_defaults(fn=cmd_report)

    p = sub.add_parser("compare")
    p.add_argument("run_a")
    p.add_argument("run_b")
    p.set_defaults(fn=cmd_compare)

    p = sub.add_parser("regrade")
    p.add_argument("run_dir")
    p.add_argument("--timeout", type=float, default=60.0)
    p.set_defaults(fn=cmd_regrade)

    p = sub.add_parser("selftest")
    p.add_argument("--jobs", type=int, default=4)
    p.add_argument("--out", default=str(RUNS))
    _suite_args(p)
    p.set_defaults(fn=cmd_selftest)

    args = parser.parse_args(argv)
    return args.fn(args)


if __name__ == "__main__":
    raise SystemExit(main())
