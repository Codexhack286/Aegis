# Engineering evidence archive

## Measured Qwen benchmark

Flagsmith assigned 40 synthetic helpdesk runs across five fixtures. Both strategies used Qwen2.5 3B Instruct in LM Studio. The experiment was closed in Development after its totals matched the Aegis ledger.

| Measure | Direct tool calling | Plan and validate |
| --- | ---: | ---: |
| Flagsmith exposures | 19 | 21 |
| Successful runs and matching Flagsmith conversions | 19 (100%) | 15 (71.4%) |
| Median latency | 1,781 ms | 2,578 ms |
| Median tokens | 1,549 | 2,001 |

**Release decision in Aegis: Hold the candidate and keep direct as the baseline.** The fixture-adjusted change was −30 percentage points against a target of +5. In all three failed password-reset runs, the candidate reached the expected queue but assigned the ticket before looking up routing rules. Trace `qwen-measured-01-0005` shows this sequence. The three other failures came from unclear requests that the candidate left unassigned. This result applies to the tested model, strategies, and repeated synthetic fixtures.

- [Frozen benchmark manifest](history/qwen-measured-01/manifest.json)
- [Reconciled Flagsmith counts](history/qwen-measured-01/flagsmith-counts.json)
- [Saved decision and review snapshot](history/qwen-measured-01/decision.json)

## Verified and remaining work

- Verified Flagsmith Cloud and self-hosted assignment and event attribution. The measured Qwen benchmark ran on Cloud. The self-hosted check paired a synthetic event batch with a successful local Qwen task.
- The packaged SDK passed an internal integration with a temporary ticket router. The local Qwen invoice connector passed all 12 comparison trials and reported insufficient evidence on a real outage. Independent-team validation remains open.

[Official rules and submission requirements](https://www.wemakedevs.org/hackathons/flagsmith/rules) · [Hackathon tracks](https://www.wemakedevs.org/hackathons/flagsmith) · [Self-hosted experimentation](https://docs.flagsmith.com/experimentation/self-hosting)

## Verified local smoke test

The `selfhost-01` batch enrolled 20 identities per arm and submitted synthetic conversions for 9 control and 11 candidate identities. A subsequent live password-reset run, `selfhost-qwen-01`, used Qwen2.5 3B Instruct via LM Studio. Flagsmith assigned it to `direct`, and Aegis verified the routing and note. The run took 5,906 ms and reported 1,500 tokens.

| Strategy | Local Flagsmith exposures | Local Flagsmith conversions |
| --- | ---: | ---: |
| `direct` | 21 | 10 |
| `plan_validate` | 20 | 11 |

The dashboard totals matched the expected counts after the live run. This verifies local assignment, model execution, and event attribution. The totals include synthetic events from the smoke batch. Use a separate experiment to compare strategies.


## Invoice reports

- [Early invoice regression](history/invoice-history/regression.json)
- [Repair versus direct](history/invoice-history/repair-vs-direct.json)
- [Repair versus plan and validate](history/invoice-history/repair-vs-plan.json)

These reports capture earlier revisions. The more recent live and controlled outcomes are listed in [the engineering notes](NEXT_PHASE.md).
