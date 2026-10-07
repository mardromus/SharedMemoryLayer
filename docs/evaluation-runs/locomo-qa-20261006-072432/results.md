# LoCoMo answer accuracy - run 20261006-072432

135 questions (stratified sample of 1540, seed 2026); answers by `gemma4:latest`, judged by `gemma4:latest` (LLM-as-a-judge, Mem0 protocol); top-10 memories as context.

| Configuration | Accuracy J | 95 % CI | Context tokens | Acc. when evidence retrieved | McNemar p vs hybrid |
|---|---|---|---|---|---|
| This system (hybrid search) | 0.726 | 0.645-0.794 | 513 | 0.819 | - |
| BM25 | 0.674 | 0.591-0.747 | 441 | 0.8081 | 0.29621 |
| Plain vector store (all-MiniLM-L6-v2) | 0.548 | 0.464-0.630 | 445 | 0.8125 | 7e-05 |
| No memory | 0.000 | 0.000-0.028 | 0 | - | 0.0 |
