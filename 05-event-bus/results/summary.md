| arm | n | risks fixed of 11, mean (min) | clean fix rate | fleet-telemetry fixed (of 2n) | outside contract, mean | regressions, mean | operating tokens, mean (consumers / owner); investigator apart | consumer calls, mean | owner prompt chars, mean | wall s, mean |
|---|---|---|---|---|---|---|---|---|---|---|
| no-owner | 10 | 6.7 (6) | 50 of 50 | 0 of 20 | 1.0 | 0.0 | 41,790 (41,790 / 0); 4,336 | 9.0 | 0 | 34 |
| steward-agent | 10 | 11.0 (11) | 50 of 50 | 20 of 20 | 0.0 | 0.0 | 71,106 (62,858 / 8,249); 6,961 | 14.3 | 7,336 | 63 |
| platform-owned | 10 | 11.0 (11) | 50 of 50 | 20 of 20 | 0.0 | 0.0 | 55,700 (53,220 / 2,480); 7,029 | 12.0 | 1,683 | 57 |

Loss faults (recovered: target fixed and its own message processed fixed; absorbed: fixed through another message; lost):

| arm | fault | recovered | absorbed | lost |
|---|---|---|---|---|
| no-owner | malformed | 0 of 10 | 7 of 10 | 3 of 10 |
| no-owner | wrong-service | 0 of 10 | 0 of 10 | 10 of 10 |
| no-owner | consumer-crash | 0 of 10 | 0 of 10 | 10 of 10 |
| steward-agent | malformed | 5 of 10 | 5 of 10 | 0 of 10 |
| steward-agent | wrong-service | 10 of 10 | 0 of 10 | 0 of 10 |
| steward-agent | consumer-crash | 10 of 10 | 0 of 10 | 0 of 10 |
| platform-owned | malformed | 10 of 10 | 0 of 10 | 0 of 10 |
| platform-owned | wrong-service | 10 of 10 | 0 of 10 | 0 of 10 |
| platform-owned | consumer-crash | 10 of 10 | 0 of 10 | 0 of 10 |

Duplicate and wrong-service handling:

| arm | runs where the duplicate was processed twice | runs where the second processing rewrote files | harm (rewrote and target unfixed) | wrong team declined | wrong team edited its own files |
|---|---|---|---|---|---|
| no-owner | 10 of 10 | 0 | 0 | 10 of 10 | 0 |
| steward-agent | 10 of 10 | 0 | 0 | 10 of 10 | 0 |
| platform-owned | 0 of 10 | 0 | 0 | 10 of 10 | 0 |

Traceability, headline: did the investigator name the first problem of each of the 6 risks that had one (4 faults, 2 unowned), recovered or not?

| arm | records | runs scored | first problem correct | malformed | wrong-service | consumer-crash | duplicate | unowned | false alarms on clean risks |
|---|---|---|---|---|---|---|---|---|---|
| no-owner | records as kept | 10 | 60 of 60 | 10 of 10 | 10 of 10 | 10 of 10 | 10 of 10 | 20 of 20 | 0 of 50 |
| steward-agent | records as kept | 10 | 60 of 60 | 10 of 10 | 10 of 10 | 10 of 10 | 10 of 10 | 20 of 20 | 3 of 50 |
| platform-owned | with trace context | 10 | 60 of 60 | 10 of 10 | 10 of 10 | 10 of 10 | 10 of 10 | 20 of 20 | 0 of 50 |
| platform-owned | trace context stripped | 10 | 60 of 60 | 10 of 10 | 10 of 10 | 10 of 10 | 10 of 10 | 20 of 20 | 0 of 50 |

Traceability, secondary: cause of the channel-caused losses still unfixed at the end, and false assurance:

| arm | records | runs scored | channel-caused losses left | cause correct | false assurance (channel loss reported handled) | false assurance (fix did not hold reported handled) | risks left out |
|---|---|---|---|---|---|---|---|
| no-owner | records as kept | 10 | 43 | 43 of 43 | 0 of 43 | 0 of 0 | 0 |
| steward-agent | records as kept | 10 | 0 | 0 of 0 | 0 of 0 | 0 of 0 | 0 |
| platform-owned | with trace context | 10 | 0 | 0 of 0 | 0 of 0 | 0 of 0 | 0 |
| platform-owned | trace context stripped | 10 | 0 | 0 of 0 | 0 of 0 | 0 of 0 | 0 |

Failures (not hidden in the other tables):

| arm | runs | owner call failed (no actions taken) | investigator failed (run excluded from accuracy) | stripped-trace investigator failed (excluded) | consumer deliveries with an error or invalid reply |
|---|---|---|---|---|---|
| no-owner | 10 | 0 | 0 | 0 | 1 |
| steward-agent | 10 | 0 | 0 | 0 | 0 |
| platform-owned | 10 | 0 | 0 | 0 | 0 |

Per run (fault outcomes malformed / wrong-service / consumer-crash as rec, abs or los, then risks fixed):

| run | malformed | wrong-service | consumer-crash | duplicate | no-owner: faults, fixed | steward-agent: faults, fixed | platform-owned: faults, fixed |
|---|---|---|---|---|---|---|---|
| 1 | bookings-api.missing_timeout | payments-gateway.hardcoded_secret | pricing-engine.unpinned_dependency | notifications.missing_timeout | abs / los / los, 7 | abs / rec / rec, 11 | rec / rec / rec, 11 |
| 2 | bookings-api.no_rate_limit | customer-profiles.missing_backup | admin-console.public_exposure | notifications.unpinned_dependency | abs / los / los, 7 | rec / rec / rec, 11 | rec / rec / rec, 11 |
| 3 | payments-gateway.hardcoded_secret | pricing-engine.unpinned_dependency | notifications.missing_timeout | maintenance-scheduler.missing_backup | los / los / los, 6 | abs / rec / rec, 11 | rec / rec / rec, 11 |
| 4 | customer-profiles.missing_backup | admin-console.public_exposure | notifications.unpinned_dependency | bookings-api.missing_timeout | abs / los / los, 7 | abs / rec / rec, 11 | rec / rec / rec, 11 |
| 5 | pricing-engine.unpinned_dependency | notifications.missing_timeout | maintenance-scheduler.missing_backup | bookings-api.no_rate_limit | los / los / los, 6 | rec / rec / rec, 11 | rec / rec / rec, 11 |
| 6 | admin-console.public_exposure | notifications.unpinned_dependency | bookings-api.missing_timeout | payments-gateway.hardcoded_secret | abs / los / los, 7 | abs / rec / rec, 11 | rec / rec / rec, 11 |
| 7 | notifications.missing_timeout | maintenance-scheduler.missing_backup | bookings-api.no_rate_limit | customer-profiles.missing_backup | abs / los / los, 7 | rec / rec / rec, 11 | rec / rec / rec, 11 |
| 8 | notifications.unpinned_dependency | bookings-api.missing_timeout | payments-gateway.hardcoded_secret | pricing-engine.unpinned_dependency | los / los / los, 6 | rec / rec / rec, 11 | rec / rec / rec, 11 |
| 9 | maintenance-scheduler.missing_backup | bookings-api.no_rate_limit | customer-profiles.missing_backup | admin-console.public_exposure | abs / los / los, 7 | rec / rec / rec, 11 | rec / rec / rec, 11 |
| 10 | bookings-api.missing_timeout | payments-gateway.hardcoded_secret | pricing-engine.unpinned_dependency | notifications.missing_timeout | abs / los / los, 7 | abs / rec / rec, 11 | rec / rec / rec, 11 |

Consumers claude-sonnet-5-5 (served: claude-sonnet-5-5), owner roles claude-opus-5-5 (served: claude-opus-5-5), backend claude-cli. Run dates: 2026-10-11.
