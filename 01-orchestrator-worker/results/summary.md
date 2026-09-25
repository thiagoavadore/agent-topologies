| arm | n | workers (planned → dispatched) | total tokens, mean (min to max) | router + synthesis tokens, mean | recall, mean (min) | extra findings, mean | invalid payloads | wall s, mean |
|---|---|---|---|---|---|---|---|---|
| uncapped | 5 | 4 → 4 | 36,573 (26,379 to 41,999) | 4,610 | 1.00 (1.00) | 3.6 | 0 | 178 |
| cap2 | 5 | 3/4 → 2 | 23,278 (19,874 to 26,261) | 3,790 | 0.98 (0.92) | 1.0 | 0 | 116 |
| router-killed | 5 | 2 → 2 | 22,994 (18,538 to 26,897) | 2,267 | 0.98 (0.92) | 0.8 | 0 | 99 |

Supervisor / worker models: claude-opus-5 / claude-haiku-4-5 via claude-cli. Run dates: 2026-09-25.
