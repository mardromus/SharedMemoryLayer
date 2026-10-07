#!/usr/bin/env python3
"""Answer-accuracy benchmark on LoCoMo, step 2 of 2: answer with an LLM, grade with an LLM judge.

Reads contexts.json written by locomo_qa_retrieve.py. For every sampled question and configuration
(sml_hybrid, bm25, dense, none) an answering LLM receives the retrieved memories and writes a short
answer; a judge LLM labels it CORRECT or WRONG against the gold answer (the LLM-as-a-judge protocol
of the Mem0 paper, with a lenient judge prompt modelled on theirs). Progress is checkpointed to
answers.jsonl, so the run can be resumed.

Outputs results.json / results.md with accuracy J per configuration (overall and per category, 95 %
Wilson intervals), exact McNemar tests against sml_hybrid, accuracy conditioned on retrieval, and
optionally the agreement of a second judge model on a random subset (--verify-model).

Runs on the host against a local Ollama server, e.g.:
    python evaluation/locomo_qa_answer.py evaluation/results/locomo-qa-<run> --model gemma4:latest \
        --verify-model llama3:latest --verify-n 100
"""
from __future__ import annotations

import argparse
import json
import math
import random
import re
import statistics
import sys
import time
import urllib.request
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

CONFIGS = ("sml_hybrid", "bm25", "dense", "none")
NAMES = {"sml_hybrid": "This system (hybrid search)", "bm25": "BM25", "dense": "Plain vector store (all-MiniLM-L6-v2)", "none": "No memory"}

ANSWER_SYSTEM = ("You answer questions about a long-running conversation between two people, using memories retrieved from "
                 "that conversation.")
ANSWER_TEMPLATE = """Memories (each line is one message, with the date it was sent):
{memories}

Question: {question}

Instructions:
- Answer from the memories. If a message uses a relative time ("yesterday", "last week", "next month"), convert it to an absolute date using the date of that message.
- Give a short answer: a few words or one short sentence. Do not explain your reasoning.
- If the memories do not contain the answer, give your best guess."""
NO_MEMORY_TEMPLATE = """No memories are available.

Question: {question}

Give a short answer: a few words or one short sentence. If you do not know, give your best guess."""
JUDGE_TEMPLATE = """Your task is to label an answer to a question as CORRECT or WRONG. You are given the question, a gold (ground truth) answer and a generated answer.
Be generous with your grading: the generated answer is CORRECT if it refers to the same fact, entity or event as the gold answer, even if it is worded differently, is longer, or adds detail. For questions about time, it is CORRECT if it refers to the same date or time period as the gold answer, even in a different format (for example "May 7th" versus "7 May"). Otherwise it is WRONG.

Question: {question}
Gold answer: {gold}
Generated answer: {generated}

Reply with exactly one word: CORRECT or WRONG."""


class Ollama:
    def __init__(self, base: str):
        self.base = base.rstrip("/")

    def chat(self, model: str, system: str | None, user: str, num_predict: int) -> tuple[str, float, dict]:
        messages = ([{"role": "system", "content": system}] if system else []) + [{"role": "user", "content": user}]
        payload = {"model": model, "messages": messages, "stream": False, "think": False,
                   # use_mmap=False keeps the weights in process memory instead of a file mapping that
                   # Windows trims under memory pressure (which made calls 5-10x slower)
                   "options": {"temperature": 0, "num_ctx": 3072, "num_predict": num_predict, "use_mmap": False}}
        for attempt in range(5):
            try:
                req = urllib.request.Request(f"{self.base}/api/chat", data=json.dumps(payload).encode(), headers={"Content-Type": "application/json"})
                t0 = time.perf_counter()
                with urllib.request.urlopen(req, timeout=1200) as r:
                    out = json.loads(r.read())
                return out["message"]["content"].strip(), time.perf_counter() - t0, out
            except Exception as exc:  # transient server errors, model loading
                if attempt == 4:
                    raise
                print(f"  retry after error: {exc}", flush=True)
                time.sleep(5 * (attempt + 1))
        raise RuntimeError("unreachable")

    def unload(self, model: str) -> None:
        """Free the model's memory before another model is loaded (CPU machines rarely fit two 8B models)."""
        req = urllib.request.Request(f"{self.base}/api/generate", data=json.dumps({"model": model, "keep_alive": 0}).encode(),
                                     headers={"Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=120) as r:
            r.read()


def judge_label(text: str) -> bool | None:
    m = re.search(r"\b(CORRECT|WRONG)\b", text.upper())
    return None if not m else m.group(1) == "CORRECT"


def wilson(k: int, n: int, z: float = 1.96) -> tuple[float, float]:
    if n == 0:
        return (0.0, 0.0)
    p = k / n
    d = 1 + z * z / n
    c = (p + z * z / (2 * n)) / d
    h = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / d
    return (max(0.0, c - h), min(1.0, c + h))


def mcnemar_exact(b: int, c: int) -> float:
    n = b + c
    if n == 0:
        return 1.0
    tail = sum(math.comb(n, i) for i in range(0, min(b, c) + 1)) / 2 ** n
    return min(1.0, 2 * tail)


def cohen_kappa(a: list[bool], b: list[bool]) -> float:
    n = len(a)
    po = sum(x == y for x, y in zip(a, b)) / n
    pa, pb = sum(a) / n, sum(b) / n
    pe = pa * pb + (1 - pa) * (1 - pb)
    return (po - pe) / (1 - pe) if pe < 1 else 1.0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("run_dir")
    ap.add_argument("--model", default="gemma4:latest", help="answering and judging model")
    ap.add_argument("--judge-model", default=None, help="judge model (default: same as --model, as in the Mem0 protocol)")
    ap.add_argument("--verify-model", default=None, help="second judge for an agreement check")
    ap.add_argument("--verify-n", type=int, default=100)
    ap.add_argument("--ollama", default="http://127.0.0.1:11434")
    ap.add_argument("--limit", type=int, default=None)
    args = ap.parse_args()
    if sys.platform == "win32":  # keep the machine from idle-sleeping while this process runs (no settings changed)
        import ctypes

        ctypes.windll.kernel32.SetThreadExecutionState(0x80000000 | 0x00000001)  # ES_CONTINUOUS | ES_SYSTEM_REQUIRED
    run = Path(args.run_dir)
    data = json.loads((run / "contexts.json").read_text(encoding="utf-8"))
    records = data["records"][: args.limit] if args.limit else data["records"]
    judge_model = args.judge_model or args.model
    llm = Ollama(args.ollama)

    ckpt = run / "answers.jsonl"
    done = {}
    if ckpt.exists():
        for line in ckpt.read_text(encoding="utf-8").splitlines():
            row = json.loads(line)
            done[(row["id"], row["config"])] = row
    t_start = time.time()
    with ckpt.open("a", encoding="utf-8") as fh:
        for i, rec in enumerate(records):
            for cfg in CONFIGS:
                if (rec["id"], cfg) in done or cfg not in rec["contexts"]:
                    continue
                ctx = rec["contexts"][cfg]
                prompt = ANSWER_TEMPLATE.format(memories="\n".join(ctx), question=rec["question"]) if cfg != "none" else NO_MEMORY_TEMPLATE.format(question=rec["question"])
                answer, t_ans, raw = llm.chat(args.model, ANSWER_SYSTEM, prompt, 64)
                verdict_text, t_judge, _ = llm.chat(judge_model, None, JUDGE_TEMPLATE.format(question=rec["question"], gold=rec["answer"], generated=answer), 8)
                row = {"id": rec["id"], "config": cfg, "category": rec["category"], "question": rec["question"], "gold": rec["answer"],
                       "answer": answer, "judge": verdict_text, "correct": judge_label(verdict_text),
                       "context_chars": sum(len(c) for c in ctx), "prompt_tokens": raw.get("prompt_eval_count"),
                       "answer_seconds": round(t_ans, 2), "judge_seconds": round(t_judge, 2),
                       "evidence_recall": rec["evidence_recall"].get(cfg)}
                done[(rec["id"], cfg)] = row
                fh.write(json.dumps(row) + "\n")
                fh.flush()
            if (i + 1) % 10 == 0:
                rate = (time.time() - t_start) / (i + 1)
                acc = {c: statistics.fmean(float(bool(done[(r['id'], c)]['correct'])) for r in records[: i + 1] if (r['id'], c) in done) for c in CONFIGS}
                print(f"[{datetime.now():%H:%M:%S}] {i + 1}/{len(records)} questions | " + " ".join(f"{c}={v:.3f}" for c, v in acc.items())
                      + f" | {rate:.0f}s/question this session", flush=True)

    rows = [done[(r["id"], c)] for r in records for c in CONFIGS if (r["id"], c) in done]
    unparsed = sum(1 for r in rows if r["correct"] is None)
    summary = {}
    for cfg in CONFIGS:
        rs = [r for r in rows if r["config"] == cfg]
        k = sum(1 for r in rs if r["correct"])
        lo, hi = wilson(k, len(rs))
        cats = sorted({r["category"] for r in rs})
        summary[cfg] = {
            "n": len(rs), "correct": k, "accuracy": round(k / len(rs), 4), "ci95": [round(lo, 4), round(hi, 4)],
            "by_category": {c: {"n": sum(1 for r in rs if r["category"] == c), "accuracy": round(statistics.fmean(float(bool(r["correct"])) for r in rs if r["category"] == c), 4)} for c in cats},
            "mean_context_tokens": round(statistics.fmean(r["context_chars"] for r in rs) / 4),
            "mean_answer_seconds": round(statistics.fmean(r["answer_seconds"] for r in rs), 1),
        }
        if cfg != "none":
            hit = [r for r in rs if r["evidence_recall"] is not None and r["evidence_recall"] > 0]
            miss = [r for r in rs if r["evidence_recall"] == 0]
            summary[cfg]["accuracy_when_evidence_retrieved"] = round(statistics.fmean(float(bool(r["correct"])) for r in hit), 4) if hit else None
            summary[cfg]["accuracy_when_evidence_missed"] = round(statistics.fmean(float(bool(r["correct"])) for r in miss), 4) if miss else None
            summary[cfg]["share_with_evidence_retrieved"] = round(len(hit) / len(rs), 4)
    paired = {}
    base = {r["id"]: bool(r["correct"]) for r in rows if r["config"] == "sml_hybrid"}
    for cfg in CONFIGS[1:]:
        other = {r["id"]: bool(r["correct"]) for r in rows if r["config"] == cfg}
        ids = base.keys() & other.keys()
        b = sum(1 for i in ids if base[i] and not other[i])
        c = sum(1 for i in ids if not base[i] and other[i])
        paired[cfg] = {"hybrid_only_correct": b, "other_only_correct": c, "mcnemar_p": round(mcnemar_exact(b, c), 5)}

    verify = None
    if args.verify_model:
        if args.verify_model not in (args.model, judge_model):
            llm.unload(args.model)
            llm.unload(judge_model)
        rng = random.Random(7)
        subset = rng.sample([r for r in rows if r["config"] != "none"], min(args.verify_n, len(rows)))
        second = []
        for r in subset:
            text, _, _ = llm.chat(args.verify_model, None, JUDGE_TEMPLATE.format(question=r["question"], gold=r["gold"], generated=r["answer"]), 8)
            second.append(judge_label(text))
        pairs = [(bool(a["correct"]), bool(b)) for a, b in zip(subset, second) if b is not None]
        verify = {"model": args.verify_model, "n": len(pairs), "agreement": round(sum(x == y for x, y in pairs) / len(pairs), 4),
                  "cohen_kappa": round(cohen_kappa([x for x, _ in pairs], [y for _, y in pairs]), 4)}
        (run / "verify_judgments.json").write_text(json.dumps([{**r, "second_judge": s} for r, s in zip(subset, second)], indent=1), encoding="utf-8")

    results = {"meta": data["meta"], "answer_model": args.model, "judge_model": judge_model, "questions": len(records),
               "unparsed_judgments": unparsed, "summary": summary, "paired_vs_sml_hybrid": paired, "judge_agreement": verify,
               "finished_at": datetime.now(timezone.utc).isoformat()}
    (run / "results.json").write_text(json.dumps(results, indent=2), encoding="utf-8")
    lines = [f"# LoCoMo answer accuracy - run {data['meta']['run_id']}", "",
             f"{len(records)} questions (stratified sample of {data['meta']['questions_total']}, seed {data['meta']['seed']}); answers by `{args.model}`, "
             f"judged by `{judge_model}` (LLM-as-a-judge, Mem0 protocol); top-{data['meta']['k']} memories as context.", "",
             "| Configuration | Accuracy J | 95 % CI | Context tokens | Acc. when evidence retrieved | McNemar p vs hybrid |", "|---|---|---|---|---|---|"]
    for cfg in CONFIGS:
        s = summary[cfg]
        p = paired.get(cfg, {}).get("mcnemar_p", "-")
        ev = s.get("accuracy_when_evidence_retrieved")
        lines.append(f"| {NAMES[cfg]} | {s['accuracy']:.3f} | {s['ci95'][0]:.3f}-{s['ci95'][1]:.3f} | {s['mean_context_tokens']} | {ev if ev is not None else '-'} | {p} |")
    if verify:
        lines += ["", f"Second judge `{verify['model']}` on {verify['n']} random judgments: agreement {verify['agreement']:.3f}, Cohen's kappa {verify['cohen_kappa']:.3f}."]
    (run / "results.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    print("\n".join(lines))
    return 0


if __name__ == "__main__":
    sys.exit(main())
