Paired comparison: `blind/runs.jsonl` against `test_compare_to_writes_compare0/published.jsonl`, rows keyed by (run, arm), worker replies identical per run index.

| arm | n | regressions: this file (runs) | regressions: other file (runs) | final hub timeout 300ms: this file | final hub timeout 300ms: other file | paired flips (runs where the final hub timeout differs) | management tokens, mean: this file | management tokens, mean: other file | ratio |
|---|---|---|---|---|---|---|---|---|---|
| flat | 1 | 0 of 1 | 0 of 1 | 1 of 1 | 1 of 1 | 0 | 1,562 | 1,609 | 0.97 |
| by-team | 1 | 1 of 1 | 0 of 1 | 0 of 1 | 1 of 1 | 1 (run 1) | 6,281 | 6,527 | 0.96 |
| by-runtime | 1 | 1 of 1 | 0 of 1 | 0 of 1 | 1 of 1 | 1 (run 1) | 6,279 | 6,524 | 0.96 |
| by-runtime-registry | 1 | 0 of 1 | 0 of 1 | 1 of 1 | 1 of 1 | 0 | 6,546 | 6,732 | 0.97 |
