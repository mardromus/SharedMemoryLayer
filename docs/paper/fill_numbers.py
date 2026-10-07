#!/usr/bin/env python3
"""Fills main.template.tex with the measured numbers of the placement experiments and writes main.tex.

Tokens (all values come from docs/evaluation-runs/ablation-*/results.json):
  @@E1:<locomo|scifact>:<rho>:<metric>[:fmt]@@    experiment 1 (placement of authorisation)
  @@E2:<metric>[:fmt]@@                           experiment 2 (collection-statistics side channel)
  @@E3:<rho>:<metric[.field]>[:fmt]@@             experiment 3 (filtered HNSW)
  @@E3:delta_ms@@                                 mean extra median latency of filtered vs unfiltered search
Formats: default 3 decimals (latencies 1 decimal), pct = percent with a thin space and %, pctnum = bare
percent for plot coordinates, relpct = value relative to authorise-before-rank recall, in percent.
"""
import json
import re
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
RESULTS = HERE.parent / "evaluation-runs"


def latest_ablation() -> dict:
    runs = sorted(RESULTS.glob("ablation-*/results.json"))
    if not runs:
        sys.exit("no ablation results found")
    data = json.loads(runs[-1].read_text(encoding="utf-8"))
    if "e1_filtering_strategies_scifact" not in data:
        sys.exit(f"{runs[-1]} predates the SciFact experiment")
    return data


def latest_qa() -> dict | None:
    runs = sorted(p for p in RESULTS.glob("locomo-qa-*/results.json") if "pilot" not in str(p))
    return json.loads(runs[-1].read_text(encoding="utf-8")) if runs else None


def fmt(value, style: str | None, key: str) -> str:
    if style == "pct":
        return f"{value * 100:.1f}\\,\\%"
    if style == "pctnum":
        return f"{value * 100:.2f}"
    if isinstance(value, int) or (isinstance(value, float) and value.is_integer() and value > 1):
        return f"{int(value):,}".replace(",", "{,}")
    if "latency" in key or key.endswith("p50") or key.endswith("p95"):
        return f"{value:.1f}"
    return f"{value:.3f}"


def main() -> int:
    data = latest_ablation()
    e1 = {"locomo": data["e1_filtering_strategies"], "scifact": data["e1_filtering_strategies_scifact"]}
    e2, e3 = data["e2_statistics_side_channel"], data["e3_filtered_hnsw"]

    def resolve(token: str) -> str:
        parts = token.split(":")
        kind = parts[0]
        if kind == "E1":
            _, corpus, rho, metric, *style = parts
            row = e1[corpus][rho]
            if metric == "n":  # question-seed pairs with readable evidence
                n_q = data["setup"]["questions"] if corpus == "locomo" else data["setup"]["scifact"]["questions"]
                pairs = round((1 - row.get("skipped_no_readable_evidence", 0.0)) * n_q * len(data["setup"]["seeds"]))
                return f"{pairs:,}".replace(",", "{,}")
            value = row[metric]
            if metric.endswith("_returned"):
                return f"{value:.1f}"
            if style and style[0] == "relpct":
                return f"{value / row['pre_recall'] * 100:.0f}\\,\\%"
            return fmt(value, style[0] if style else None, metric)
        if kind == "E2":
            _, metric, *style = parts
            return fmt(e2[metric], style[0] if style else None, metric)
        if kind == "E3":
            if parts[1] == "delta_ms":
                deltas = [r["latency_ms_filtered"]["p50"] - r["latency_ms_unfiltered"]["p50"] for r in e3.values()]
                return f"{sum(deltas) / len(deltas):.1f}"
            _, rho, metric, *style = parts
            value = e3[rho]
            for field in metric.split("."):
                value = value[field]
            return fmt(value, style[0] if style else None, metric)
        if kind == "QA":
            return resolve_qa(parts[1:])
        raise KeyError(token)

    qa = latest_qa()

    def resolve_qa(parts: list[str]) -> str:
        """@@QA:n@@, @@QA:total@@, @@QA:model@@, @@QA:<cfg>:<metric>[:pct]@@, @@QA:p:<cfg>@@, @@QA:verify:<metric>@@."""
        if qa is None:
            raise SystemExit("QA tokens present but no locomo-qa results archived")
        head = parts[0]
        if head == "n":
            return str(qa["questions"])
        if head == "total":
            return f"{qa['meta']['questions_total']:,}".replace(",", "{,}")
        if head == "p":
            p = qa["paired_vs_sml_hybrid"][parts[1]]["mcnemar_p"]
            return "p < 0.001" if p < 0.001 else f"p = {p:.3f}"
        if head in ("b", "c"):
            key = "hybrid_only_correct" if head == "b" else "other_only_correct"
            return str(qa["paired_vs_sml_hybrid"][parts[1]][key])
        if head == "verify":
            v = qa["judge_agreement"]
            value = v[parts[1]]
            return fmt(value, parts[2] if len(parts) > 2 else None, parts[1]) if parts[1] != "n" else str(value)
        s = qa["summary"][head]
        metric = parts[1]
        style = parts[-1] if parts[-1] in ("pct",) else None
        if metric == "ci_lo":
            value = s["ci95"][0]
        elif metric == "ci_hi":
            value = s["ci95"][1]
        elif metric == "cat":
            value = s["by_category"][parts[2]]["accuracy"]
        elif metric == "catn":
            return str(s["by_category"][parts[2]]["n"])
        elif metric == "tokens":
            return f"{s['mean_context_tokens']:,}".replace(",", "{,}")
        elif metric == "secs":
            return f"{s['mean_answer_seconds']:.1f}"
        elif metric == "ev_hit":
            value = s["accuracy_when_evidence_retrieved"]
        elif metric == "ev_miss":
            value = s["accuracy_when_evidence_missed"]
        elif metric == "ev_share":
            value = s["share_with_evidence_retrieved"]
        else:
            value = s[metric]
        return fmt(value, style, metric)

    template = (HERE / "main.template.tex").read_text(encoding="utf-8")
    filled = re.sub(r"@@([^@]+)@@", lambda m: resolve(m.group(1)), template)
    left = re.findall(r"@@[^@]*@@", filled)
    if left:
        sys.exit(f"unresolved tokens: {left}")
    (HERE / "main.tex").write_text(filled, encoding="utf-8", newline="\n")
    print(f"main.tex written ({len(re.findall(r'@@', template)) // 2} numbers filled)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
