# Aegis next phase

## One-turn approval repair

The live `invoice-agent-v2` comparison recorded two premature stops on invoice approval with `plan_validate`. The focused trace showed `check_receipt` returning a match and the model ending its turn while the invoice remained open. The `invoice-agent-v3` starter adds `plan_validate_repair` for connector experiments.

When the model stops, the starter reads the current invoice state and the latest receipt result. A matching receipt and an open, unpaid invoice allow one more model turn within the existing budget. Only one approval call is available during that turn. A mismatched receipt skips the repair, and the evaluator checks that the invoice stays open and unpaid. Reports include observed repair turns and latency.

The live revision 3 mismatch trials showed that all strategies could approve a mismatched receipt during normal execution. Revision 4 checks the latest receipt result inside the approval tool before changing state. A blocked approval attempt on the mismatch fixture still fails its action policy, while the state checks confirm that the invoice stayed open and unpaid.

In the live revision 4 comparisons, all eight mismatch trials preserved protected state. The candidate had three policy failures across four mismatch trials. Two listed a forbidden tool attempt as the first failure and one listed the required receipt check. Revision 5 offers approval only after a matching receipt check and disables payment in the invoice approval fixtures. The connector still blocks an unavailable tool if the model requests it directly.

In a controlled revision 5 comparison against `plan_validate`, the repair passed both valid approval trials, both mismatch trials, and both timeout retry trials. Injected attempts to approve a mismatched receipt or send payment were blocked, while an injected early stop failed the receipt-check requirement. The revision 5 live model comparison is pending.

Restart the invoice connector to load revision 5. Run the comparison below from the repository root with the connector listening on port 9000. The default baseline is `direct`. Add `--baseline plan_validate` and use a second output file to isolate the effect of adding the repair turn.

```powershell
$env:AEGIS_CONNECTOR_URL = 'http://127.0.0.1:9000/run'
uv run python -m aegis case-compare --connector-spec examples/invoice/rules.json `
    --candidate plan_validate_repair --repeats 2 --case invoice-approval `
    --case invoice-receipt-mismatch --case invoice-retry > repair-regression.json
```

Load either JSON result in **Review a paired regression report** to see per-arm failed checks, blocked calls, and protected-state mutations. For a Markdown table with median trial latency, rerun with `--format markdown` and redirect to a separate file.

## Available in the prototype

Select a built-in run in the dashboard and choose **Preview and regrade bundle** to see the first failing check and download the projected evidence. The bundle contains queue values, rule-relevant facts and known successful tool names. The saved examples came from Qwen runs `qwen-measured-01-0003` and `qwen-measured-01-0005`.

```powershell
uv run python -m aegis case-regrade benchmarks/qwen-measured-01/password-reset-pass.case.json
uv run python -m aegis case-regrade benchmarks/qwen-measured-01/password-reset-fail.case.json
uv run python -m aegis case-export qwen-measured-01-0005 > password-reset.case.json
uv run python -m aegis case-compare --case password-reset --repeats 2 --format markdown
```

`case-regrade` and `case-compare` return 0 for pass, 1 for a failed check or observed regression, and 2 for insufficient evidence. `case-compare` uses scripted helpdesk strategies by default. Pass `--model qwen2.5-3b-instruct` for fresh runs through LM Studio. JSON output includes each trial's portable evidence. The paired comparison is a diagnostic gate on these fixtures. Release decisions still use the measured experiment.

The invoice starter now includes normal submission, simulated timeout with idempotent retry, and receipt approval. Tests exercise their connector endpoint with controlled responses. Live invoice inference and validation with another team's workflow are pending. Portable export is currently limited to the built-in helpdesk policy.

### Run paired checks against a connector

Start the connector, set `AEGIS_CONNECTOR_URL` to its run endpoint, and select fixtures from its spec. Its spec must declare `"trial_isolation": "fresh_per_request"` and provide evaluator rules. Declare isolation only if each request runs on fresh disposable state. Aegis checks that reported starting state matches across repetitions and arms. Return stable snapshots of relevant state.

```powershell
uv run --env-file .env python -m aegis case-compare --connector-spec examples/invoice/rules.json --case invoice-retry --case invoice-approval --repeats 2 > regression.json
```

Open **Review a paired regression report** and choose the file. The dashboard reads it locally. Connector reports contain verdicts and rule categories. They omit raw project state, prompts and tool arguments. A connector outage or differing starting state yields insufficient evidence. An observed candidate mutation of protected state triggers a regression. Use `--format markdown` for a pull request summary.

### Realistic regression replays

The retail exchange tests adapt the public confirmation and customer scope requirements to synthetic connector evidence. They cover a valid exchange, a valid extra inventory lookup, a wrong refund destination, declined confirmation, a premature write repeated after confirmation, a duplicate exchange, an attempted new order, a cross-customer mutation, and a completion claim without a write. The two valid traces pass and the seven other traces fail independently of the agent's summary. The checks validate Aegis on controlled traces. Running the full public benchmark would require its agent, tools, and simulated user. [τ-bench retail policy](https://github.com/sierra-research/tau-bench/blob/main/tau_bench/envs/retail/wiki.md)

The invoice starter reproduces the lost-acknowledgement pattern with a fixed synthetic idempotency key. The successful retry leaves one invoice in state. The approval fixture now rejects a premature write even if a subsequent retry follows the correct order. Its connector revision is `invoice-agent-v2`. These tests use a local simulation of the provider behavior. [Stripe idempotency documentation](https://docs.stripe.com/api/idempotent_requests)

## The release decision to improve

An engineer changes an agent prompt, model or tool policy. Before increasing the rollout, they need to know which previously working tasks broke, whether the agent changed anything it should have left alone, and how a teammate can reproduce the failure. Build for the engineer reviewing that change. Measure whether Aegis changes their release decision or shortens their investigation.

The existing Qwen experiment gives us a concrete starting point. Direct succeeded on 19/19 runs and plan-and-validate on 15/21. In three password-reset failures the candidate reached the expected queue but assigned the ticket before consulting routing policy. In three unclear cases it left the ticket unassigned. These are two different bugs with different fixes. The 40 runs covered five synthetic fixtures on one model. Keep that scope visible whenever showing the result. [Recorded decision](benchmarks/qwen-measured-01/decision.json)

Practitioners report that agents can claim completion without achieving the intended state, that different valid tool paths can reach the same outcome, and that noisy environments complicate diagnosis. This motivates independent state checks, explicit policy predicates and isolated repeated trials. Whether teams will adopt Aegis for this work is still an open product question. [Anthropic’s agent evaluation guidance](https://www.anthropic.com/research/demystifying-evals-for-ai-agents)

## Architecture proposal — turn a failed run into a portable regression case

Keep the existing Python SDK, FastAPI, React UI, SQLite ledger and Flagsmith integration. Introduce a versioned **case bundle** that crosses their boundaries. A developer should be able to take a failed run from the dashboard, attach its reviewed bundle to a pull request and rerun both strategies against equivalent starting conditions.

| Boundary | Responsibility | First implementation |
| --- | --- | --- |
| Capture | Export fixture revision, sanitized input and starting state, tool contract, policy revision, model/settings fingerprint and observed failure. A human confirms the intended outcome separately. | Extend the existing Python connector and ledger export. Allowlist fields and preview the bundle before saving. Omit credentials and raw prompts by default. |
| Execution | Give each arm its own disposable copy of the starting state. Record actual requests, results and final state. | Start with the existing ticket store. Require an explicit sandbox/reset contract for external connectors. Mark unsupported cases ineligible for paired execution. |
| Evaluation | Apply the same versioned rules to both runs. Distinguish wrong outcome, policy breach and missing evidence. | Share the existing evaluator between CLI and API. Require ordering only for declared prerequisites. Show the first violated rule and relevant state diff in the existing trace view. |
| Release review | Combine the paired diagnostic report with the separately measured live experiment. | Keep Flagsmith as the source of live assignments. Offline comparisons emit zero exposures or conversions. Include both evidence sources and their denominators in the review. The operator controls rollout. |

Provide two distinct replay modes. **Regrade** applies a revised evaluator to an immutable trace without model calls. **Rerun** executes the agent again against fresh isolated state and can yield a different trace. A recorded response is suitable for regrading or a scripted drill. If a changed agent asks an unseen question, an execution replay needs a functioning sandbox tool or must report missing coverage. Keep expected answers out of the agent input in both modes.

## Delivery order

| Phase | Work | Acceptance test |
| --- | --- | --- |
| 1. Ship one reproducible failure | Start with saved run `qwen-measured-01-0005`. Add a CLI export and offline regrade command, then expose them on the existing trace screen. Include one passing trace for comparison. | From a clean checkout, a second developer can identify the prerequisite violation without an API key. A deliberately incomplete trace produces “insufficient evidence.” Export tests detect a seeded secret and exclude it. |
| 2. Prove the connector transfers | Extend the invoice starter with an isolated invoice store. Test a duplicate submission after a timeout, a normal invoice and a case requiring approval. Read final state independently of the agent response. | Run an actual model through the HTTP connector. One retry leaves exactly one invoice. A false completion and an unapproved write fail distinct checks. A valid alternative tool sequence passes. Record integration effort and every core change needed. |
| 3. Put the result in code review | Run baseline and candidate on the same frozen cases with repeated trials, interleaved order and a fixed budget. Emit a Markdown summary and machine-readable result with stable exit codes for pass, regression and insufficient evidence. | A seeded regression holds the release and links to a reproducible case. A connector outage yields insufficient evidence. Repeating a fixture never increases the reported count of distinct cases. Live experiment totals stay unchanged during CI. |
| 4. Validate value with another team | Ask two agent developers to bring one sanitized historical failure each. Have each diagnose a case once using normal logs and once using Aegis on a comparable case, alternating which tool comes first. | Capture time to a correct diagnosis, setup time, missed violations and false alarms. A proposed continuation gate is one independently integrated workflow and one release decision the evidence changed. Report the small sample and any failed onboarding attempts. |

For the hackathon, prioritize phase 1 and one live invoice case from phase 2. Demo the original wrong-order run, the policy check that failed and a corrected candidate facing the same case. Label a single successful rerun as reproduction evidence. Require a fresh measured cohort before recommending expansion.

## Where Go could earn its place

The measured 1.78 s and 2.58 s medians cover whole Qwen runs. They do not identify time spent in Python. First instrument queue wait, model wait, tool I/O, evaluation and SQLite commit separately. Compare one worker with bounded parallelism using the same provider capacity. Include a fast scripted connector to expose runner overhead and LM Studio to measure user-visible throughput.

If orchestration is material, prototype a single Go worker behind a versioned JSON request/result contract. Python retains experiment assignment, evaluation, event submission and the sole ledger writer. The worker receives a run ID, sandbox reference, deadline and strategy, and returns ordered events and usage. Persist an attempt ID before dispatch. On crash, recover completed results by ID and flag uncertain external writes for review. Apply cancellation, per-provider concurrency limits and the same total tool/model budgets in both implementations.

Proposed adoption gate: matching contract results on scripted fixtures, clean cancellation/restart behavior and at least 20% higher completed runs per minute on a representative workload at equal concurrency, hardware and model settings, without increased failures. Track p95 latency, memory and installation effort alongside throughput. If model wait dominates, keep Python and spend the time on connector adoption. Revisit Rust only if profiling identifies a CPU-heavy component that warrants a separate implementation.

## What success would look like

A teammate can bring a real failure, encode the intended behavior, see the candidate regress on that case, and hand another developer a repeatable test. The most valuable evidence for the next review is a developer catching a bug in their own agent before rollout, accompanied by the trace, the decision and the time it took.
