| arm | n | workers (planned → dispatched) | total tokens, mean (min to max) | router + synthesis tokens, mean | recall, mean (min) | extra findings, mean | invalid payloads | wall s, mean |
|---|---|---|---|---|---|---|---|---|
| uncapped | 5 | 3/4 → 3/4 | 38,612 (27,893 to 54,044) | 4,167 | 1.00 (1.00) | 3.0 | 0 | 118 |
| cap2 | 5 | 3/4 → 2 | 27,408 (21,931 to 30,590) | 3,622 | 0.93 (0.92) | 0.8 | 0 | 123 |
| router-killed | 5 | 2 → 2 | 22,000 (16,242 to 27,147) | 2,660 | 0.98 (0.92) | 1.6 | 0 | 121 |

Supervisor / worker models: claude-opus-5 / claude-haiku-4-5 via claude-cli. Run dates: 2026-09-25.
