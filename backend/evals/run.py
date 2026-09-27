"""Run the eval suite and write a JSON + Markdown report.

    python -m evals.run                                   # default local models
    python -m evals.run --targets ollama:qwen2.5:7b scripted
    python -m evals.run --targets anthropic:claude-opus-5 --scenarios inventory-shortage

Targets are provider[:model]. Anthropic targets are skipped, with a note,
when ANTHROPIC_API_KEY is unset.
"""

import argparse
import json
import logging
import sys
from datetime import datetime, timezone
from pathlib import Path

from app.bootstrap import setup
from app.config import settings
from evals.harness import run_scenario
from evals.scenarios import SCENARIOS

RESULTS_DIR = Path(__file__).parent / "results"
DEFAULT_TARGETS = ["ollama:qwen2.5:7b", "ollama:qwen3.5:4b", "scripted"]


def parse_target(target: str) -> tuple[str, str | None]:
    provider, _, model = target.partition(":")
    return provider, model or None


def render_markdown(results: list[dict], skipped: list[str], started: str) -> str:
    targets = list(dict.fromkeys(r["model"] for r in results))
    scenarios = list(dict.fromkeys(r["scenario"] for r in results))
    by_key = {(r["scenario"], r["model"]): r for r in results}

    lines = [
        "# Agent evaluation",
        "",
        f"Run {started}. Each cell is the share of checks passed.",
        "",
        "| Scenario | " + " | ".join(f"`{t}`" for t in targets) + " |",
        "|---|" + "---|" * len(targets),
    ]
    for scenario in scenarios:
        cells = []
        for target in targets:
            r = by_key.get((scenario, target))
            cells.append("—" if r is None else f"{r['score'] * 100:.0f}%")
        lines.append(f"| {scenario} | " + " | ".join(cells) + " |")

    lines.append("| **Overall** | " + " | ".join(
        f"**{sum(r['score'] for r in results if r['model'] == t) / max(1, sum(1 for r in results if r['model'] == t)) * 100:.0f}%**"
        for t in targets
    ) + " |")
    lines.append("| Median seconds | " + " | ".join(
        f"{sorted(r['seconds'] for r in results if r['model'] == t)[len([r for r in results if r['model'] == t]) // 2]:.0f}"
        for t in targets
    ) + " |")

    if skipped:
        lines += ["", "Skipped: " + "; ".join(skipped)]

    lines += ["", "## Failed checks", ""]
    failures = 0
    for r in results:
        for check in r["checks"]:
            if not check["passed"]:
                failures += 1
                lines.append(f"- `{r['model']}` · {r['scenario']} · **{check['name']}** — {check['detail']}")
    if not failures:
        lines.append("None.")
    return "\n".join(lines) + "\n"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--targets", nargs="+", default=DEFAULT_TARGETS)
    parser.add_argument("--scenarios", nargs="+", help="scenario ids (default: all)")
    parser.add_argument("--out", type=Path, default=RESULTS_DIR)
    args = parser.parse_args(argv)

    logging.basicConfig(level=logging.WARNING)
    setup()

    chosen = [s for s in SCENARIOS if not args.scenarios or s.id in args.scenarios]
    started = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    results, skipped = [], []

    for target in args.targets:
        provider, model = parse_target(target)
        if provider == "anthropic" and not settings.anthropic_api_key:
            skipped.append(f"`{target}` (no ANTHROPIC_API_KEY)")
            continue
        for scenario in chosen:
            print(f"[{target}] {scenario.id} ...", end=" ", flush=True)
            result = run_scenario(scenario, provider, model)
            print(f"{result.score * 100:.0f}% in {result.seconds}s")
            results.append(result.to_dict())

    args.out.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M")
    (args.out / f"eval-{stamp}.json").write_text(json.dumps(
        {"started": started, "results": results, "skipped": skipped}, indent=2, default=str
    ))
    report = render_markdown(results, skipped, started) if results else "# Agent evaluation\n\nNothing ran.\n"
    (args.out / f"eval-{stamp}.md").write_text(report, encoding="utf-8")
    (args.out / "latest.md").write_text(report, encoding="utf-8")
    print("\n" + report)
    return 0


if __name__ == "__main__":
    sys.exit(main())
