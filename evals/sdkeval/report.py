"""Metrics and reports over a run's ``results.jsonl``.

Rates come with a 95% Wilson interval: with a few dozen tasks per category, the
interval is the difference between "model B is better" and "model B drew easier
variants", and a report that omits it invites the wrong conclusion.

``pass^k`` (every one of k samples passed) is reported next to ``pass@k`` (at
least one passed) when ``samples > 1``. For an agent that writes financial
models unattended, ``pass^k`` is the number that matters: a model that is right
two times out of three is a model whose forecast you cannot trust without
checking it.

Samples that failed because the *model endpoint* errored are excluded from
every rate and counted separately — an outage is not an accuracy result.
"""

from __future__ import annotations

import json
import math
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any, Iterable

from .schema import CATEGORIES, FAILURE_CLASSES


def wilson(successes: int, n: int, z: float = 1.96) -> tuple[float, float]:
    if n == 0:
        return (0.0, 0.0)
    p = successes / n
    denom = 1 + z * z / n
    centre = (p + z * z / (2 * n)) / denom
    half = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / denom
    return (max(0.0, centre - half), min(1.0, centre + half))


def pass_at_k(n: int, c: int, k: int) -> float:
    """Unbiased pass@k from n samples with c correct (Chen et al. 2021)."""
    if n - c < k:
        return 1.0
    return 1.0 - math.comb(n - c, k) / math.comb(n, k)


def _rate(rows: list[dict], key: str = "passed") -> dict[str, Any]:
    n = len(rows)
    k = sum(1 for r in rows if r[key])
    lo, hi = wilson(k, n)
    return {"n": n, "passed": k, "rate": (k / n) if n else 0.0, "ci95": [lo, hi]}


def summarize(records: Iterable[dict]) -> dict[str, Any]:
    records = list(records)
    errored = [r for r in records if r.get("failure_class") == "model_error"]
    rows = [r for r in records if r.get("failure_class") != "model_error"]

    by_instance: dict[str, list[dict]] = defaultdict(list)
    for r in rows:
        by_instance[r["instance_id"]].append(r)
    ks = sorted({len(v) for v in by_instance.values()})
    samples = ks[-1] if ks else 0

    def group(key: str) -> dict[str, Any]:
        buckets: dict[str, list[dict]] = defaultdict(list)
        for r in rows:
            buckets[str(r[key])].append(r)
        return {
            name: {**_rate(b), "first_turn_rate": _rate(b, "passed_first_turn")["rate"]}
            for name, b in sorted(buckets.items())
        }

    hygiene = Counter()
    hygiene_clean = 0
    failing_checks = Counter()
    for r in rows:
        violations = [c["label"] for c in r["checks"] if not c["critical"] and not c["passed"]]
        hygiene.update(violations)
        hygiene_clean += not violations
        if not r["passed"]:
            failing_checks.update(f"{r['family']}: {c['label']}" for c in r["checks"] if c["critical"] and not c["passed"])

    tokens_in = sum(a.get("input_tokens") or 0 for r in rows for a in r["attempts"])
    tokens_out = sum(a.get("output_tokens") or 0 for r in rows for a in r["attempts"])
    latency = [a["latency_s"] for r in rows for a in r["attempts"] if a.get("latency_s")]

    consistency: dict[str, Any] = {}
    if samples > 1:
        complete = {i: v for i, v in by_instance.items() if len(v) == samples}
        consistency = {
            "samples": samples,
            "instances": len(complete),
            f"pass@{samples}": sum(pass_at_k(samples, sum(r["passed"] for r in v), samples) for v in complete.values()) / max(1, len(complete)),
            f"pass^{samples}": sum(all(r["passed"] for r in v) for v in complete.values()) / max(1, len(complete)),
            "pass@1": sum(pass_at_k(samples, sum(r["passed"] for r in v), 1) for v in complete.values()) / max(1, len(complete)),
        }

    failures = Counter(r["failure_class"] for r in rows if not r["passed"])
    return {
        "overall": {**_rate(rows), "first_turn_rate": _rate(rows, "passed_first_turn")["rate"],
                    "mean_turns": (sum(r["turns"] for r in rows) / len(rows)) if rows else 0.0},
        "hygiene_clean_rate": (hygiene_clean / len(rows)) if rows else 0.0,
        "model_errors": len(errored),
        "consistency": consistency,
        "by_category": group("category"),
        "by_family": group("family"),
        "by_difficulty": group("difficulty"),
        "by_split": group("split"),
        "failure_classes": {k: failures.get(k, 0) for k in FAILURE_CLASSES if failures.get(k)},
        "hygiene_violations": dict(hygiene.most_common()),
        "top_failing_checks": dict(failing_checks.most_common(25)),
        "tokens": {"input": tokens_in, "output": tokens_out},
        "latency_s": {"mean": (sum(latency) / len(latency)) if latency else None, "total": sum(latency)},
    }


def _pct(x: float) -> str:
    return f"{100 * x:.1f}%"


def _ci(entry: dict) -> str:
    lo, hi = entry["ci95"]
    return f"{_pct(entry['rate'])} [{_pct(lo)}–{_pct(hi)}]"


def markdown(summary: dict[str, Any], config: dict[str, Any] | None = None, records: list[dict] | None = None) -> str:
    out = ["# CashKit SDK-call eval", ""]
    if config:
        out += [
            f"- **Model:** `{config.get('model')}`  ",
            f"- **Context:** `{config.get('context')}` · **split:** `{config.get('split')}` · "
            f"**variants:** {config.get('variants')} · **samples:** {config.get('samples')} · "
            f"**max turns:** {config.get('max_turns')} · **temperature:** {config.get('temperature')}",
            "",
        ]
    o = summary["overall"]
    out += [
        "## Headline",
        "",
        "| Metric | Value |",
        "|---|---|",
        f"| Pass rate (final turn) | {_ci(o)} — {o['passed']}/{o['n']} |",
        f"| Pass rate (first turn) | {_pct(o['first_turn_rate'])} |",
        f"| Mean turns | {o['mean_turns']:.2f} |",
        f"| Hygiene-clean samples | {_pct(summary['hygiene_clean_rate'])} |",
    ]
    for key, value in (summary.get("consistency") or {}).items():
        if key.startswith("pass"):
            out.append(f"| {key} | {_pct(value)} |")
    if summary["model_errors"]:
        out.append(f"| Model endpoint errors (excluded) | {summary['model_errors']} |")
    out.append(f"| Tokens in / out | {summary['tokens']['input']:,} / {summary['tokens']['output']:,} |")
    out.append("")

    out += ["## By category", "", "| Category | Pass (95% CI) | First turn | n |", "|---|---|---|---|"]
    order = {c: i for i, c in enumerate(CATEGORIES)}
    for name, entry in sorted(summary["by_category"].items(), key=lambda kv: order.get(kv[0], 99)):
        out.append(f"| {name} | {_ci(entry)} | {_pct(entry['first_turn_rate'])} | {entry['n']} |")
    out += ["", "## By difficulty", "", "| Difficulty | Pass (95% CI) | n |", "|---|---|---|"]
    for name, entry in summary["by_difficulty"].items():
        out.append(f"| {name} | {_ci(entry)} | {entry['n']} |")
    out += ["", "## By family", "", "| Family | Pass | First turn | n |", "|---|---|---|---|"]
    for name, entry in sorted(summary["by_family"].items(), key=lambda kv: (kv[1]["rate"], kv[0])):
        out.append(f"| `{name}` | {_pct(entry['rate'])} | {_pct(entry['first_turn_rate'])} | {entry['n']} |")
    if summary["failure_classes"]:
        out += ["", "## Why samples failed", "", "| Class | Samples |", "|---|---|"]
        for name, count in summary["failure_classes"].items():
            out.append(f"| {name} | {count} |")
    if summary["top_failing_checks"]:
        out += ["", "## Most-failed checks", "", "| Check | Samples |", "|---|---|"]
        for name, count in summary["top_failing_checks"].items():
            out.append(f"| {name} | {count} |")
    if summary["hygiene_violations"]:
        out += ["", "## Hygiene violations", "", "| Rule | Samples |", "|---|---|"]
        for name, count in summary["hygiene_violations"].items():
            out.append(f"| {name} | {count} |")
    if records:
        failed = [r for r in records if not r["passed"] and r.get("failure_class") != "model_error"]
        if failed:
            out += ["", "## Failure examples", ""]
            seen: set[str] = set()
            for r in failed:
                if r["family"] in seen:
                    continue
                seen.add(r["family"])
                details = [f"{c['label']}: {c['detail']}" for c in r["checks"] if c["critical"] and not c["passed"]]
                last = r["attempts"][-1] if r["attempts"] else {}
                obs = last.get("observation") or {}
                if obs.get("exception"):
                    details.insert(0, "exception: " + obs["exception"].strip().splitlines()[-1][:200])
                out.append(f"- **{r['instance_id']}** ({r['failure_class']}): " + " · ".join(d[:220] for d in details[:3]))
                if len(seen) >= 15:
                    break
    out.append("")
    return "\n".join(out)


def compare(a: dict[str, Any], b: dict[str, Any], *, name_a: str = "A", name_b: str = "B") -> str:
    """Side-by-side of two summaries, by category and family, with deltas."""
    out = [f"# {name_a} vs {name_b}", "", "| | " + name_a + " | " + name_b + " | Δ |", "|---|---|---|---|"]
    oa, ob = a["overall"], b["overall"]
    out.append(f"| **overall** | {_ci(oa)} | {_ci(ob)} | {100 * (ob['rate'] - oa['rate']):+.1f} pt |")
    out.append(f"| first turn | {_pct(oa['first_turn_rate'])} | {_pct(ob['first_turn_rate'])} | "
               f"{100 * (ob['first_turn_rate'] - oa['first_turn_rate']):+.1f} pt |")
    for section in ("by_category", "by_family"):
        out += ["", f"### {section.replace('_', ' ')}", "", f"| | {name_a} | {name_b} | Δ |", "|---|---|---|---|"]
        for key in sorted(set(a[section]) | set(b[section])):
            ea, eb = a[section].get(key), b[section].get(key)
            ra = ea["rate"] if ea else None
            rb = eb["rate"] if eb else None
            delta = f"{100 * (rb - ra):+.1f} pt" if ra is not None and rb is not None else "—"
            out.append(f"| `{key}` | {_pct(ra) if ra is not None else '—'} | {_pct(rb) if rb is not None else '—'} | {delta} |")
    out.append("")
    return "\n".join(out)


def write(run_dir: Path, records: list[dict]) -> dict[str, Any]:
    summary = summarize(records)
    config = json.loads((run_dir / "config.json").read_text()) if (run_dir / "config.json").exists() else None
    (run_dir / "summary.json").write_text(json.dumps(summary, indent=2))
    (run_dir / "report.md").write_text(markdown(summary, config, records))
    return summary
