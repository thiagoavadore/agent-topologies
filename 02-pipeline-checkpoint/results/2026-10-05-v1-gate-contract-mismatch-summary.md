| arm | model, backend | runs (faulted / clean) | fault outcomes | tokens, mean (min to max) | gate share of tokens | false rejections (clean runs) | recall, mean (runs that reached stage 4) | recall, mean (clean runs that reached stage 4) | wall s, mean |
|---|---|---|---|---|---|---|---|---|---|
| none | claude-sonnet-5-5, claude-cli | 10 (5 / 5) | absorbed 3, reached-production 2 | 28,972 (27,823 to 30,966) | 0% | 0 of 5 | 0.93 (10 of 10 runs) | 0.95 (5 of 5 runs) | 64 |
| end-only | claude-sonnet-5-5, claude-cli | 10 (5 / 5) | absorbed 3, reached-production 2 | 33,162 (30,861 to 36,418) | 13% | 0 of 5 | 0.91 (10 of 10 runs) | 0.95 (5 of 5 runs) | 66 |
| every-handoff | claude-sonnet-5-5, claude-cli | 10 (5 / 5) | caught-stage-1 4, caught-stage-2 1 | 35,041 (24,190 to 46,570) | 52% | 5 of 5 | n/a (0 of 10 runs) | n/a (0 of 5 runs) | 44 |

Per injected fault (blamed stage is the stage whose checkpoint failed; the owner is always stage 1):

| run | arm | fault | outcome | blamed stage | stopped by |
|---|---|---|---|---|---|
| 1 | none | wrong-service payments-gateway.hardcoded_secret | absorbed |  |  |
| 1 | end-only | wrong-service payments-gateway.hardcoded_secret | absorbed |  |  |
| 1 | every-handoff | wrong-service payments-gateway.hardcoded_secret | caught-stage-1 | 1 | gate: The fact for payments-gateway/config.yaml (processor.api_key, the hardcoded live API key) is attributed to service 'pricing-engine' instead of 'payments-gateway', which owns that file; this misattribution puts a critical secret exposure on the wrong service. |
| 3 | none | dropped admin-console.public_exposure | reached-production |  |  |
| 3 | end-only | dropped admin-console.public_exposure | reached-production |  |  |
| 3 | every-handoff | dropped admin-console.public_exposure | caught-stage-1 | 1 | gate: The admin-console ingress rule (443 open to 0.0.0.0/0, with the staff VPN CIDR variable unused) comes from admin-console/network.tf.json, but the output has no entry for that file and folds the finding into the service.yaml endpoints fact, so it is attributed to the wrong file and locator. |
| 5 | none | wrong-service customer-profiles.missing_backup | absorbed |  |  |
| 5 | end-only | wrong-service customer-profiles.missing_backup | absorbed |  |  |
| 5 | every-handoff | wrong-service customer-profiles.missing_backup | caught-stage-1 | 1 | gate: The fact about customer-profiles/service.yaml's backup (profiles-db with no backup, dump job only planned for Q3) is attributed to service 'fleet-telemetry' instead of 'customer-profiles', which owns that file. |
| 7 | none | dropped bookings-api.missing_timeout | reached-production |  |  |
| 7 | end-only | dropped bookings-api.missing_timeout | reached-production |  |  |
| 7 | every-handoff | dropped bookings-api.missing_timeout | caught-stage-2 | 2 | gate: Several risk-bearing facts were dropped from the output, e.g. admin-console's shared password with no SSO on a refund/block tool, bookings-api's plain-HTTP call to payments-gateway, notifications' inherited 300ms timeout below its 2s minimum, and maintenance-scheduler's DELETE-all-routes with no alerting. |
| 9 | none | wrong-service fleet-telemetry.single_point_of_failure | absorbed |  |  |
| 9 | end-only | wrong-service fleet-telemetry.single_point_of_failure | absorbed |  |  |
| 9 | every-handoff | wrong-service fleet-telemetry.single_point_of_failure | caught-stage-1 | 1 | gate: Three fleet-telemetry/service.yaml facts (no owner, single VM telemetry-01 with 15-minute buffering, managed TimescaleDB backup) are attributed to maintenance-scheduler instead of fleet-telemetry, the service that owns that file; fleet-telemetry therefore has no service.yaml facts under its own name. |

Served by claude-sonnet-5-5. Run dates: 2026-10-05.
