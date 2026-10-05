| arm | n | git textual conflicts, mean | contested hub keys, mean | risks fixed of 11, mean (min) | regressions, mean (runs with any) | outside contract, mean | failed-worker runs | report names the failed services | silent success | tokens, mean (workers / reducer / report / shadow) | wall s excl. human, mean |
|---|---|---|---|---|---|---|---|---|---|---|---|
| first-wins | 10 | 0.9 | 0.9 | 8.7 (6) | 1.0 (10 of 10) | 0.2 | 5 | 5 of 5 all, 5 of 5 some | 0 of 5 | 21,257 (19,662 / 0 / 1,594 / 0) | 26 |
| supervisor-merges | 10 | 0.7 | 1.0 | 8.6 (4) | 0.4 (4 of 10) | 0.2 | 5 | 5 of 5 all, 5 of 5 some | 0 of 5 | 22,906 (19,108 / 2,143 / 1,655 / 0) | 33 |
| hub-owner | 10 | 0.0 | 1.0 | 8.4 (5) | 0.0 (0 of 10) | 0.2 | 5 | 5 of 5 all, 5 of 5 some | 0 of 5 | 25,652 (21,105 / 2,948 / 1,598 / 0) | 38 |
| overrides-allowed | 10 | 0.0 | 0.0 | 9.0 (6) | 0.0 (0 of 10) | 0.2 | 5 | 5 of 5 all, 5 of 5 some | 0 of 5 | 17,501 (16,412 / 0 / 1,089 / 0) | 21 |
| code-local | 10 | 0.2 | 0.2 | 8.2 (3) | 0.1 (1 of 10) | 0.2 | 5 | 5 of 5 all, 5 of 5 some | 0 of 5 | 21,783 (20,492 / 0 / 1,291 / 0) | 26 |

Service overrides (who wrote them, and how far the platform forked):

| arm | mandate rejections (file edits reverted), mean | worker override edits stripped, mean | worker override edits written, mean | granted by the merge owner, mean | services with an override in the merged repo, mean | distinct effective timeouts across the 8 services, mean |
|---|---|---|---|---|---|---|
| first-wins | 0.0 | 0.0 | 0.0 | 0.0 | 0.0 | 1.4 |
| supervisor-merges | 0.0 | 0.0 | 0.0 | 1.1 | 1.1 | 2.0 |
| hub-owner | 0.0 | 0.0 | 0.0 | 1.3 | 1.2 | 2.5 |
| overrides-allowed | 0.0 | 0.0 | 4.7 | 0.0 | 3.3 | 3.0 |
| code-local | 0.0 | 0.0 | 0.0 | 0.0 | 0.0 | 2.9 |

Runs in which each hub key was contested (two workers, different values):

| hub key | first-wins | supervisor-merges | hub-owner | overrides-allowed | code-local |
|---|---|---|---|---|---|
| http.default_timeout | 6 of 10 | 6 of 10 | 6 of 10 | 0 of 10 | 0 of 10 |
| http.default_rate_limit | 3 of 10 | 4 of 10 | 4 of 10 | 0 of 10 | 2 of 10 |
| backup.policy | 0 of 10 | 0 of 10 | 0 of 10 | 0 of 10 | 0 of 10 |
| availability.min_instances | 0 of 10 | 0 of 10 | 0 of 10 | 0 of 10 | 0 of 10 |

Workers claude-sonnet-5-5 (served: claude-sonnet-5-5), reducer claude-opus-5-5 (served: claude-opus-5-5), backend claude-cli. Run dates: 2026-10-05.
