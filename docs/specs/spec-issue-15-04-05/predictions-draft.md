# Predictions for Issue 15 (folders 04 and 05)

**DRAFT. Not frozen. The benchmarks refuse to run while this file says DRAFT.** To freeze: run the dry runs, fill the section below, get approval (Thiago, or a delegation logged in `.memlog.md`), delete this paragraph, append everything from the next heading down to root `PREDICTIONS.md`, commit.

Frozen YYYY-MM-DD, before any benchmark run. Results that disagree are reported as misses, not edited away. Approved by ______.

**What we had seen before writing this.** _(fill after dry runs)_ 04: two paired dry-run indexes (all four arms). 05: two runs per arm. Their raw rows are kept outside `results/`. Predictions for arms seen in a dry run lean on what we saw there; that is said in the row.

Scoring: the shared checker (`src/topologies/fixcheck.py`, 11 planted risks, 2 regression needs) plus the folder metrics in `docs/specs/spec-issue-15-04-05/experiment-design.md`. Directions are relative to the other arms in the same folder. Never compare across folders.

## 04-hierarchical: should the agent layers match the team layers?

All four arms resolve the same worker replies in each run index, so differences come from the management tiers only.

| Metric | `flat` | `by-team` | `by-runtime` | `by-runtime-registry` |
|---|---|---|---|---|
| Runs with a regression (of 10) | 0 to 1. The lead sees every need, like a platform owner reviewing every request. | 0 to 3. The rentals-and-money sub-lead holds bookings-api, pricing-engine and payments-gateway, so its reasons carry pricing's 300 ms upward; a regression needs the notifications raise to get past a platform lead that read them. | Highest, 4 or more. The python sub-lead merges bookings-api's and notifications' raises into one request; pricing-engine's sub-lead has nothing to change, so pricing's need never reaches the top. | 0 to 1, about `flat`. The registry puts pricing's need in front of the platform lead. |
| Which need regresses | pricing-engine, if any | pricing-engine | pricing-engine in most; payments-gateway in some (bookings-api's rate request crosses to a sub-lead that never sees payments) | pricing-engine, if any |
| Harmful timeout requests reaching the platform lead, per run | About 2 (bookings-api and notifications each ask, unfiltered) | About 1, from customer-and-back-office (notifications); rentals-and-money absorbs bookings-api's | About 1 (one merged request from python), harm outside the requester's group | Same requests as `by-runtime` (same sub-lead replies) |
| Coupling ladder for pricing's need | Mostly `caught-at-top` | `absorbed-at-sub-lead` for bookings-api's raise, `caught-at-top` for notifications' | Mostly `reached-hub` | Mostly `caught-at-top` |
| Escalations (requests reaching the platform lead) | Highest: every worker request | Lower than `flat`: sub-leads merge | About `by-team` | Same as `by-runtime` |
| Risks fixed (of 11) | About equal across arms: worker files are identical; arms differ only through the two timeout risks and the rate-limit risk, which hang on the hub or an override. Do not rank on it. | | | |
| Management tokens | Lowest: one Opus call | About four times `flat`'s: three sub-leads plus the lead | About `by-team` | `by-runtime` plus a larger lead prompt |
| Platform lead prompt size | Largest: 8 workers' requests and every need | Smallest | Smallest | Between: sub-lead requests plus the registry |

## 05-event-bus: who looks after the shared channel?

| Metric | `no-owner` | `steward-agent` | `platform-owned` |
|---|---|---|---|
| Risks fixed (of 11), mean | Lowest, about 6 to 7: the 2 unowned-service risks, the crash target and the wrong-service target are lost unless absorbed | About 9 to 10, if the steward reads the store and acts on what it finds | Highest, about 10 to 11 |
| Clean-message fix rate (control) | About equal across arms, near 5 of 5 | About equal | About equal |
| fleet-telemetry risks fixed (of 20 over 10 runs) | 0: nobody owns it, nobody hears | Most: the steward sees published-but-undelivered messages and assigns an owner | Most: they land in the Dead Letter Channel and the platform agent assigns an owner |
| consumer-crash targets lost (of 10) | Nearly all (only absorbed targets survive) | Fewer: the steward sees delivered-with-no-outcome and redelivers in most runs | About 0 (redelivery is code; counted as by construction) |
| wrong-service targets lost (of 10) | Nearly all: the wrong team declines and the reply goes nowhere | Fewer: the steward reroutes by the file path in most runs | Fewer: the decline lands in the Dead Letter Channel and the platform agent reroutes |
| Wrong team declines instead of editing its own files | Most, about equal across arms (consumers never know the arm) | | |
| malformed targets lost (of 10) | Some: a tolerant consumer may still fix its service from the service name alone, or fix nothing | About `no-owner` (the message is delivered as is; the steward may return it to the producer) | Fewest: rejected at publish, returned to the producer, republished valid |
| Duplicate consumer calls (of 10) | 10 (by construction) | 10 (by construction) | 0 (by construction) |
| Duplicate rewrites and harm | Rare rewrites, harm near 0 | Same as `no-owner` | n/a |
| Investigator attribution accuracy on channel-caused losses | Lowest: team logs show declines but not who never heard | Middle: the store shows every hop, but corrective copies carry no link | Highest: Correlation Identifier and Message History link every copy to its origin |
| False assurance (a lost risk reported handled) | Highest | Low | Lowest |
| Accuracy without trace context (paired, platform-owned only) | n/a | n/a | Lower than with it, about the `steward-agent` level |
| Tokens per run | Lowest | Highest: the steward reads the whole store, plus pass-2 deliveries | Between: the platform agent reads only exceptions |

## What would surprise us

- 04: `by-runtime` has no more regressions than `by-team`. That would mean the platform lead protects pricing-engine without being told, so the cut does not matter in this task.
- 04: `by-runtime-registry` regresses as often as `by-runtime`. That would mean a needs registry is not read, or not trusted, by the platform lead.
- 05: `steward-agent` matches `platform-owned` on lost faults and attribution. That would mean an agent reading the log is a substitute for platform code, at its token price.
- 05: `no-owner` consumers absorb most loss faults. That would mean, in this task, the channel's guarantees barely matter.
