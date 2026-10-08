# Research paper — *Similarity Is Not Authorisation: Need-to-Know Collective Memory for Multi-Team LLM Agents*

`Shared_Memory_Layer_Paper.pdf` is the compiled paper (21 pages, single column). `main.tex` + `references.bib`
form a complete Overleaf project (upload `shared-memory-layer-paper.zip` via **New Project → Upload Project**,
compiler pdfLaTeX); all figures are TikZ/pgfplots, so there are no image files.

## How the numbers get into the paper

`main.template.tex` contains `@@...@@` tokens for every number of the placement experiments (RQ1–RQ2) and of
the answer-accuracy experiment (RQ6). `fill_numbers.py` replaces them from the archived results in
`docs/evaluation-runs/ablation-*/results.json` and `docs/evaluation-runs/locomo-qa-*/results.json` and writes
`main.tex`; it fails if any token is left. Other numbers (security suite, in-house evaluation, LoCoMo,
LongMemEval, BEIR) are copied from the runs documented in `docs/evaluation.md`.

```bash
python fill_numbers.py                     # template + results -> main.tex
conda run -n latex tectonic main.tex       # optional local build (Tectonic from conda-forge)
```

The placement experiments are reproduced with
`docker compose exec backend python /opt/evaluation/ablation_filtering.py`.

The answer-accuracy experiment (RQ6, Mem0 protocol: an LLM answers from the top-10 memories, an LLM judge marks
CORRECT/WRONG) runs in two steps — retrieval through the API in the container, generation and judging with a
local Ollama model on the host:

```bash
docker compose exec backend python /opt/evaluation/locomo_qa_retrieve.py   # writes contexts.json
python evaluation/locomo_qa_answer.py evaluation/results/locomo-qa-<run> --model gemma4:latest
```

The answer script checkpoints to `answers.jsonl`, so an interrupted run resumes where it stopped. The run in the
paper covers the first 135 questions of a 200-question stratified sample; `audit_labels.json` holds the manual
audit of 60 judge decisions.

Author details are set in the `\author{...}` block of `main.template.tex`; edit them there and re-run
`fill_numbers.py`. The first version of the paper is kept in `previous/`.
