
# Predictions for Issue 15 (folders 04 and 05)

Frozen YYYY-MM-DD at commit _______, before any benchmark run. Results that disagree are reported as misses, not edited away. Approved by ______.

Scoring: the shared checker (`src/topologies/fixcheck.py`, 11 planted risks, 2 regression needs), unchanged since Issue 14, plus the folder metrics in each folder's `experiment.py` summary. Directions are relative to the other arms in the same folder. Never compare across folders, and never compare a 04 or 05 number with 03.

**What we had seen before writing this.** Dry runs, none of which count as results. Their raw rows are kept outside `results/` and listed here with the commit each row records. Several ran on code that was not yet committed. Where the recorded commit does not identify the code, the row says so. Predictions for arms seen in a dry run lean on what we saw there, and the dry runs are thin: three usable 04 indexes, one 05 run per arm on the current code.

| Folder | Run index | Arms | Recorded commit | What the code was | File | What we saw |
|---|---|---|---|---|---|---|
| 04 | 1 | all four | b7b8368 | First build of 04, before review. The folder was not committed yet, so the recorded commit does not identify it. | `~/.cache/issue15/04-scratch.jsonl` | `by-runtime` regressed pricing-engine: the python sub-lead merged the raises into 12s and the platform lead accepted it. The other three held, with `flat` and `by-runtime-registry` also at 12s, protecting pricing-engine with an override. 11 of 11 fixed in every arm. |
| 04 | 2 | all four | b7b8368 | Same as index 1 | `~/.cache/issue15/04-scratch.jsonl` | No regressions. The platform lead kept 300 ms in every arm, unprompted in the tiered arms. In `by-team`, rentals-and-money forwarded a 15s raise that harmed pricing-engine inside its own group. |
| 04 | 5 | all four | 385535f-dirty | A variant with an `override_for` field on requests. **Does not count.** | `~/.cache/issue15/04-scratch-r5.jsonl` | Every request became service-only, no hub-changing request reached any platform lead, and the cut stopped mattering. The variant was reverted (see the design notes below). |
| 04 | 6 | all four | 385535f-dirty | The review fixes, later committed in fed1b64, before the notes-cap fix | `~/.cache/issue15/04-scratch-r6.jsonl` | No regressions, `caught-at-top` in all four arms, a harmful 15s request reached the platform lead in all four. 10 of 11 fixed in every arm (one worker reply was cut by the old notes cap). |
| 04 | 6 | `flat` only | 385535f-dirty | Same, after the notes-cap fix | `~/.cache/issue15/04-scratch-r6b.jsonl` | Sanity check, 11 of 11. |
| 05 | 0 | `platform-owned` | b7b8368 | First build of 05, before review. The folder was not committed yet. | `~/.cache/issue15/05-scratch.jsonl` (copy in `/private/tmp/claude-501/issue15/`) | 11 of 11, all three loss faults recovered |
| 05 | 3 | `steward-agent` | b7b8368 | Same | `~/.cache/issue15/05-scratch-steward.jsonl` (copy in `/private/tmp/claude-501/issue15/`) | 11 of 11, all recovered. The wrong team edited its own files instead of declining. |
| 05 | 0 | `no-owner` | 385535f | Mid-review code, before the complete-delivery rule | `~/.cache/issue15/05-scratch-noowner.jsonl` (copy in `/private/tmp/claude-501/issue15/`) | 7 of 11. The old rule called the malformed target recovered; the current rule calls it absorbed. |
| 05 | 0 | `no-owner` | 385535f, no `-dirty` flag | Review fixes, later committed in 041b01f. 05 did not yet append `-dirty` to its recorded commit. | `~/.cache/issue15/05-r2-no-owner.jsonl` | 7 of 11. Malformed absorbed, wrong-service lost, consumer-crash lost, fleet-telemetry 0 of 2, 35,163 operating tokens |
| 05 | 0 | `steward-agent` | 385535f, no `-dirty` flag | Same | `~/.cache/issue15/05-r2-steward-agent.jsonl` | 10 of 11. Absorbed, recovered, lost: the crash target's redelivery got a Sonnet invalid reply twice and nothing retries it. fleet-telemetry 2 of 2, 1 false alarm on 5 clean risks, 80,393 operating tokens |
| 05 | 0 | `platform-owned` | 385535f, no `-dirty` flag | Same | `~/.cache/issue15/05-r2-platform-owned.jsonl` | 11 of 11, all recovered, duplicate processed once, 49,726 operating tokens |

The three earlier 05 rows were rescored with the current first-problem rule: 6 of 6 in each. Every current-code 05 row also scored 6 of 6, including the stripped-trace investigator.

## 04-hierarchical: should the agent layers match the team layers?

Design facts these predictions rest on, each a choice that shapes the result:

- All four arms resolve the same worker replies in each run index, so differences come from the management tiers only.
- Harm is classified by the requested value alone. The reason text is never read, so a 15s request whose reason says "grant pricing-engine an override" still counts as harmful.
- CONTRACT rule 8, which every lead gets, says pricing-engine reads the hub timeout. Every platform lead can therefore infer that a hub raise reaches pricing-engine; only `flat` and `by-runtime-registry` see its 300 ms bound. This weakens the effect of the cut.
- An `override_for` field on requests was tried (run index 5). It turned every request into a service-only one, so the cut stopped mattering. It was reverted because it opened a local route around the hub, the very thing 03 showed dissolves contention. Disclosed, not hidden.
- Replies are capped: worker reasons 200 characters, lead reasons 400, worker notes 1,200.
- Escalations are reported in total and hub-changing. The ladder has seven outcomes: `not-threatened`, `absorbed-at-sub-lead`, `caught-at-top`, `top-failed`, `reached-hub`, `introduced-by-top`, `introduced-by-workers`.

| Metric | `flat` | `by-team` | `by-runtime` | `by-runtime-registry` |
|---|---|---|---|---|
| Runs with a regression (of 10) | 0 to 1. The lead sees every need. | 0 to 2. The platform lead protected pricing-engine in 3 of 3 dry runs without seeing its bound. | Highest, 1 to 4. One of 3 dry runs regressed, when the python sub-lead merged the raises and the platform lead accepted. The platform lead held 300 ms on its own in the other two, so this is the prediction most likely to miss. | 0 to 1, about `flat`. |
| pricing-engine regressed | As above | As above | Every regression in this arm | As above |
| payments-gateway regressed | 0 in every arm. Rate requests in the dry runs were 20/s and 50/s, both inside its need. | | | |
| Coupling ladder for pricing-engine | Mostly `caught-at-top` | Mostly `caught-at-top`. `absorbed-at-sub-lead` is rare: in every dry run a harmful timeout request still reached the top, from customer-and-back-office (notifications) or from rentals-and-money itself. | `caught-at-top` in most runs, `reached-hub` in the runs that regress | Mostly `caught-at-top` |
| Harmful requests up, mean (inside / outside) | About 2, all outside (2, 2, 2 in dry runs) | About 1 to 2, mostly outside, inside when rentals-and-money forwards a raise (1, 2, 1) | About 1, outside (1, 1, 1) | The same requests as `by-runtime` (same sub-lead replies, by construction) |
| Escalations, mean (hub-changing) | Highest, about 3 to 4 | About 3 (fewer hub-changing than `flat`) | About 2 to 3 | Same as `by-runtime` |
| Risks fixed of 11 | About equal, within a point. Worker files are identical; arms differ only through the two timeout risks and the rate-limit risk. Do not rank on it. | | | |
| Management tokens, mean | Lowest (about 4k in dry runs) | Highest, about 5 to 6 times `flat` | About 3 to 4 times `flat` | Slightly above `by-runtime` (a longer platform-lead prompt, sub-lead tokens repeated) |
| Platform-lead prompt chars, mean | Largest, about equal to `by-runtime-registry` | Smaller | Smaller | Largest, about equal to `flat` |
| Reliability table | Platform-lead failures, sub-lead fallbacks and unreadable requests near zero in every arm | | | |

## 05-event-bus: who looks after the shared channel?

Design facts these predictions rest on:

- **Fault outcome definitions.** A fault target counts as recovered only through a complete delivery (all fields, the risk's own service, the team that owns or was assigned it). A consumer that fixes a risk from a malformed or misrouted message is counted as absorbed.
- **Corrective copies.** Copies sent by the steward or the platform agent bypass the Idempotent Receiver.
- **Headline tokens.** Operating tokens (consumers plus owner) are the headline; the investigator is reported apart.
- **Traceability headline.** First problem named on the 6 risks per run that had one (4 faults, 2 unowned), recovered or not. Cause accuracy on unfixed losses and false assurance are secondary.
- **Recovery by construction.** Consumer-crash recovery and duplicate suppression in `platform-owned` are code, so their effect is by construction there.

| Metric | `no-owner` | `steward-agent` | `platform-owned` |
|---|---|---|---|
| Risks fixed of 11, mean | Lowest, about 6 to 8 (7 in the dry run) | Middle, about 9 to 10 (10 in the current-code dry run) | Highest, about 10 to 11 (11 in both dry runs) |
| Clean fix rate (control) | About equal across arms, near 5 of 5 per run (5 of 5 in every dry run) | About equal | About equal |
| fleet-telemetry fixed (of 20) | 0: nobody owns it, so its messages are never delivered | Most | Most |
| malformed outcome (of 10) | Never recovered by definition; absorbed or lost. Absorbed only where the target shares a service with a delivered sibling (5 of 10 schedule slots) and the consumer fixes beyond its message. | Mostly absorbed or lost, recovered when the steward returns it to the producer | Recovered in most: rejected at publish, returned, republished valid |
| wrong-service outcome (of 10) | Lost in most, absorbed in a few | Recovered in most: the steward reroutes by the file path | Recovered in most: the decline lands in the Dead Letter Channel and the platform agent reroutes |
| consumer-crash outcome (of 10) | Lost in most, absorbed in a few | Recovered in most. Some are lost: one redelivery and no retry after an invalid reply (1 of 1 in the current-code dry run). | Recovered in nearly all (retry is code) |
| Duplicate processed twice | 10 of 10 (by construction) | 10 of 10 (by construction) | 0 of 10 (by construction) |
| Duplicate rewrites and harm | Rare rewrites, harm near 0 | Same | n/a |
| Wrong team declined | Most in every arm. A bogus edit of its own files is possible in a run or two per arm (seen once, older code). | | |
| Operating tokens, mean (headline) | Lowest (about 35k in the dry run) | Highest, about 2 to 2.5 times `no-owner` (80k): the steward reads the whole store and pass 2 adds deliveries | Middle, about 1.4 times `no-owner` (50k) |
| Consumer calls, mean | About 9 | Highest, about 13 to 15 | About 12 |
| Owner prompt chars, mean | n/a | Several times `platform-owned` (the whole store against the exceptions) | Smaller |
| First problem correct (6 per run) | At or near the ceiling in every arm (6 of 6 in every dry run) | At or near the ceiling | At or near the ceiling, and the same with the trace context stripped: no trace-context effect |
| False alarms on clean risks | Low in every arm (1 of 5 in one steward dry run) | | |
| Cause correct and false assurance (secondary) | Cause mostly correct and false assurance near zero in every arm. Thin: few losses are left unfixed in the owned arms, so those cells rest on small counts. | | |
| Failures table | Owner and investigator call failures near zero. A few consumer deliveries with an invalid reply in every arm. | | |

## What would surprise us

- 04: `by-runtime` regresses no more often than `by-team`. The dry runs make this the likeliest miss: the platform lead held 300 ms on its own in 2 of 3. It would mean that in this task, rule 8 plus the lead's own caution protect pricing-engine whatever the cut.
- 04: `by-runtime-registry` regresses as often as `by-runtime`. That would mean a needs registry in front of the lead is not used.
- 04: `absorbed-at-sub-lead` is common in `by-team`. That would mean team-aligned sub-leads stop threats before the top more often than the dry runs suggest.
- 05: a gap in first-problem traceability between arms, or between `platform-owned` with and without trace context. We predict the ceiling everywhere.
- 05: `steward-agent` matches `platform-owned` on lost faults at no more operating tokens. That would make an agent reading the log a full substitute for platform code.
- 05: `no-owner` consumers absorb most loss faults. That would mean that in this task the channel's guarantees barely matter.
