Paired comparison: `blind/runs.jsonl` against `results/runs.jsonl`, rows keyed by (run, arm), worker replies identical per run index.

| arm | n | regressions: this file (runs) | regressions: other file (runs) | final hub timeout 300ms: this file | final hub timeout 300ms: other file | paired flips (runs where the final hub timeout differs) | management tokens, mean: this file | management tokens, mean: other file | ratio |
|---|---|---|---|---|---|---|---|---|---|
| flat | 10 | 0 of 10 | 0 of 10 | 10 of 10 | 10 of 10 | 0 | 3,840 | 3,947 | 0.97 |
| by-team | 10 | 0 of 10 | 0 of 10 | 10 of 10 | 10 of 10 | 0 | 22,765 | 23,018 | 0.99 |
| by-runtime | 10 | 2 of 10 | 0 of 10 | 8 of 10 | 10 of 10 | 2 (run 6, 8) | 16,085 | 17,681 | 0.91 |
| by-runtime-registry | 10 | 0 of 10 | 0 of 10 | 9 of 10 | 9 of 10 | 2 (run 6, 7) | 16,329 | 17,973 | 0.91 |
