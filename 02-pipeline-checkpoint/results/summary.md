| arm | model, backend | runs (faulted / clean) | fault outcomes | tokens, mean (min to max) | gate share of tokens | false rejections (clean runs) | justified rejections (clean runs) | other rejections (faulted runs: justified / false) | recall, mean (runs that reached stage 4) | recall, mean (clean runs that reached stage 4) | wall s, mean |
|---|---|---|---|---|---|---|---|---|---|---|---|
| none | claude-sonnet-5-5, claude-cli | 10 (5 / 5) | absorbed 3, reached-production 2 | 28,975 (26,799 to 31,175) | 0% | 0 of 5 | 0 of 5 | 0 / 0 | 0.93 (10 of 10 runs) | 0.96 (5 of 5 runs) | 60 |
| end-only | claude-sonnet-5-5, claude-cli | 10 (5 / 5) | absorbed 3, reached-production 2 | 33,876 (30,436 to 36,995) | 15% | 0 of 5 | 0 of 5 | 0 / 0 | 0.95 (10 of 10 runs) | 0.96 (5 of 5 runs) | 61 |
| every-handoff | claude-sonnet-5-5, claude-cli | 10 (5 / 5) | caught-stage-1 4, reached-production 1 | 42,308 (25,009 to 66,309) | 53% | 2 of 5 | 1 of 5 | 0 / 0 | 0.97 (3 of 10 runs) | 1.00 (2 of 5 runs) | 53 |

Per injected fault (blamed stage is the stage whose checkpoint failed; the owner is always stage 1):

| run | arm | fault | outcome | blamed stage | stopped by |
|---|---|---|---|---|---|
| 1 | none | wrong-service payments-gateway.hardcoded_secret | absorbed |  |  |
| 1 | end-only | wrong-service payments-gateway.hardcoded_secret | absorbed |  |  |
| 1 | every-handoff | wrong-service payments-gateway.hardcoded_secret | caught-stage-1 | 1 | gate (injected-fault): The payments-gateway/config.yaml api_key fact is attributed to service 'pricing-engine', which does not own that file (payments-gateway does), breaking the attribution contract. |
| 3 | none | dropped admin-console.public_exposure | reached-production |  |  |
| 3 | end-only | dropped admin-console.public_exposure | reached-production |  |  |
| 3 | every-handoff | dropped admin-console.public_exposure | reached-production |  |  |
| 5 | none | wrong-service customer-profiles.missing_backup | absorbed |  |  |
| 5 | end-only | wrong-service customer-profiles.missing_backup | absorbed |  |  |
| 5 | every-handoff | wrong-service customer-profiles.missing_backup | caught-stage-1 | 1 | gate (injected-fault): One item cites customer-profiles/service.yaml (the backup fact) but is attributed to service fleet-telemetry, which does not own that file. |
| 7 | none | dropped bookings-api.missing_timeout | reached-production |  |  |
| 7 | end-only | dropped bookings-api.missing_timeout | reached-production |  |  |
| 7 | every-handoff | dropped bookings-api.missing_timeout | caught-stage-1 | 1 | gate (injected-fault): The bookings-api/bookings_api.py facts omit that charge() calls requests.post with no timeout, a risk-bearing fact against the 12s-25s payment timeout need. The same kind of fact was captured for notifications.py. |
| 9 | none | wrong-service fleet-telemetry.single_point_of_failure | absorbed |  |  |
| 9 | end-only | wrong-service fleet-telemetry.single_point_of_failure | absorbed |  |  |
| 9 | every-handoff | wrong-service fleet-telemetry.single_point_of_failure | caught-stage-1 | 1 | gate (injected-fault): One item is misattributed: the fleet-telemetry/service.yaml 'instances' fact (VM telemetry-01, Mosquitto, systemd) is labelled service maintenance-scheduler, but that file belongs to fleet-telemetry. |

Served by claude-sonnet-5-5. Run dates: 2026-10-05.
