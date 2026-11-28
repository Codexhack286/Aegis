# Aegis | AI agent release experiments

A helpdesk agent can say “I routed the ticket” while leaving it in the wrong queue. Aegis asks whether a proposed strategy actually fixes errors like this, and what the fix costs in latency, tokens and extra tool calls. Aegis includes a scripted recovery drill and a benchmark runner for a live Flagsmith experiment.

## Repository layout

| Directory | Contents |
| --- | --- |
| `aegis/` | Python backend, CLI, benchmark engine, connector, and SDK. |
| `ui/src/pages/` | Overview, run workspace, and guide. Shared review and trace components live in `ui/src/components/`. |
| `examples/invoice/` | Invoice starter fixture and its matching evaluator rules. |
| `docs/` | [Engineering notes](docs/NEXT_PHASE.md), [historical evidence](docs/EVIDENCE.md), and its raw files in `docs/history/`. |
| `deploy/flagsmith/` | Docker Compose setup for a local Flagsmith experiment. |
| `tests/` | Python regression tests. |

Run the documented commands from the repository root. The default SQLite ledger is `aegis.sqlite3` in that directory.

## Configuration

Copy `.env.example` to `.env` once. Use that file for every live command and for dashboard startup. Set one or both Flagsmith SDK environment keys and change `FLAGSMITH_MODE` to select the instance.

| Mode | Required key | Endpoint |
| --- | --- | --- |
| `cloud` | `FLAGSMITH_CLOUD_ENVIRONMENT_KEY` | Flagsmith Cloud |
| `self-hosted` | `FLAGSMITH_LOCAL_ENVIRONMENT_KEY` | `FLAGSMITH_ORIGIN`, defaulting to `http://localhost:8000` |

The dashboard backend loads `.env` from the working directory at startup. Settings exported in the shell take precedence. Restart the backend after editing `.env`. Use the same Flagsmith mode when preparing and resuming a live benchmark.

## Hackathon decision

Enter **AI Agents and Feature Flags**. The user is an engineer deciding whether to expand a new tool-use strategy. Show one release decision from the flag assignment through to the ticket mutation that explains it. The benchmark uses synthetic tickets, so the conclusion applies to this workload. Production impact would need separate evidence.

Flagsmith supplies the experimental assignment and primary readout. Aegis contributes the part an ordinary conversion dashboard cannot explain on its own: whether the agent took permitted actions, how the ticket changed, and why a higher completion rate can still warrant holding a release. Keep Aegis as a working name pending a public naming check.

## First integration gate

1. Use the official Flagsmith Docker Compose deployment on localhost. Enable EXPERIMENTATION_ENABLED=true on its API and select **Flagsmith database** under Environment Settings → Warehouse. This storage mode is suitable for evaluating the beta. No hosted inference or warehouse is needed. [Docker guide](https://docs.flagsmith.com/deployment-self-hosting/hosting-guides/docker)
2. Create a dedicated smoke-test environment. Enable agent_strategy and use direct as its base value, which Flagsmith treats as the control. Add the candidate with value plan_validate and a nonempty variant key such as candidate. Set experiment entry to 100% with a 50% candidate allocation, leaving 50% for control. Define task_success as an occurrence metric and start the experiment. [Experiment setup](https://docs.flagsmith.com/experimentation/set-up-with-a-coding-agent)
3. In a short Python process, resolve a fixed set of synthetic identities against the local API. Log the identity, resolved value and handler actually entered. Re-evaluate one identity to check sticky routing. Record task_success for a known subset only, using the same identity. Configure the Python SDK to send each event immediately. Stop the run on missing, disabled or unknown variations. A fallback would conceal a broken integration.
4. Check both arms in the Flagsmith results screen and reconcile unique exposed and converted identities against the smoke ledger. Save the tested server image, SDK version, settings and counts. If results are empty, inspect experiment start time, variation keys, event spelling, identity reuse and the local events URL. Exit this gate only when both assignment and attribution work. Start a fresh experiment for measured runs.

## Smallest end-to-end implementation

| Component | Contract | Acceptance example |
| --- | --- | --- |
| Fixtures and tools | Each case seeds a fresh local ticket store. Expose get_ticket, search_knowledge_base, assign_ticket and add_internal_note. Keep evaluator expectations outside the prompt. | Routing one ticket leaves a second ticket untouched. |
| Runner | Resolve the identity once and dispatch the returned value. Use one pinned Ollama model, common tool schemas, decoding settings and total call budget. Baseline calls tools directly. Candidate plans, validates arguments and gets one correction within that budget. | Invalid arguments produce a recorded attempt and no mutation. Both arms share tool-side enforcement. |
| Evaluator | Check final queue, required note, untouched tickets and ordered trace predicates where policy requires order. Count forbidden attempts even if the tool blocks them. | An agent that claims completion without assigning the ticket fails. A correct final state with a forbidden attempt still trips the guardrail. |
| Ledger | SQLite stores experiment, case revision, identity, arm, model digest, settings, calls, before/after state, elapsed time, tokens and delivery status. Use a unique experiment/identity constraint. | A restart can recover the trace. An ambiguous event delivery is flagged for reconciliation before rerunning. |
| Report | Show counts and denominators by arm, case mix, completion, invalid attempts, policy violations, latency and token coverage. Let the reviewer inspect a failing trace and record a decision. | A reviewer can explain one failed ticket directly from its arguments and state diff. |

The Python CLI and FastAPI share the same runner and SQLite ledger. The React dashboard uses Vite for development, and FastAPI serves the production build.

## Experiment design and release decision

Freeze a manifest of identities and cases before inspecting assignments. Include straightforward routing, ambiguous tickets requiring a lookup, incomplete information requiring escalation, and tickets already in the correct queue. Separate independent ticket instances from repeated measurements of the same fixture. Publish counts by case family and arm. A small split can place most difficult cases in one arm.

Use one experimental identity per ticket instance and keep it stable across retries. Track terminal failures and timeouts among exposed runs. Emit the occurrence event only on actual success. Exclude pre-assignment setup failures from the exposed denominator and report them separately. If a process dies after exposure, reconcile the outstanding entry before interpreting success rates. Record cold-start latency separately and alternate execution order across the frozen manifest to reduce warm-up effects.

Choose a minimum sample size and acceptable latency/token overhead with the team before the measured run. Record the reasoning alongside the manifest. Proposed decision order:

1. Reject expansion if the candidate makes a forbidden mutation. Inspect blocked attempts separately.
2. Hold if attribution is incomplete, coverage is inadequate, results are inconclusive, or a predeclared overhead budget is exceeded.
3. Recommend expansion for human approval only when the primary outcome meets the predeclared improvement criterion and all guardrails pass. Keep the actual rollout under operator control.

Planning and validation change together in this experiment. The comparison estimates the effect of the combined strategy. Isolating the contribution of planning would require another arm. A tiny demo cohort can establish that the system works and legitimately return Hold.

## Project evidence

See the [engineering evidence archive](docs/EVIDENCE.md) for the measured benchmark, earlier invoice reports, and verification status.

## Run the prototype

From the repository root with Python 3.11 or later, run both strategy paths with scripted tool calls and inspect the JSON trace. The CLI also writes each run to the local SQLite ledger. Scripted runs establish workflow correctness. Model quality and latency require live inference.

```powershell
python -m unittest discover -s tests -v
python -m aegis offline --strategy direct
python -m aegis offline --strategy plan_validate --case duplicate-charge
```

The fixtures cover password resets, duplicate charges, escalation and a routing recovery drill. Success requires a knowledge base lookup before assignment, the expected queue and an internal note. Blocked tool attempts appear in the trace as a separate guardrail. Both strategies have six model calls and eight tool calls available. The candidate uses one model call for planning and has at most one correction after invalid arguments.

### Try self-hosted Flagsmith

Start Docker Desktop with the Linux container engine. The included Compose stack starts PostgreSQL, runs the Flagsmith database migration, and launches the dashboard and task processor. It uses development credentials and binds the dashboard to loopback. Set `FLAGSMITH_IMAGE` in your shell if you want to pin a specific published image.

```powershell
docker compose -f deploy/flagsmith/compose.yaml up -d
docker compose -f deploy/flagsmith/compose.yaml ps -a
Invoke-RestMethod http://localhost:8000/health/liveness/
```

Open http://localhost:8000 to create the local admin and project. In Development, enable **Flagsmith database** in **Environment Settings → Warehouse**, then create the smoke experiment using the flag and metric above. Set these values in `.env`:

```dotenv
FLAGSMITH_MODE=self-hosted
FLAGSMITH_ORIGIN=http://localhost:8000
FLAGSMITH_LOCAL_ENVIRONMENT_KEY=your_local_development_key
```

```powershell
uv run --extra experiment --env-file .env python -m aegis flagsmith-smoke --batch local-smoke-01
```

Compare the printed counts with the local experiment results. Use a fresh batch prefix for each smoke run. [Flagsmith self-hosting guide](https://docs.flagsmith.com/experimentation/self-hosting)

### Test either Flagsmith instance

With the dedicated smoke experiment running, confirm that `.env` selects the matching instance and Development SDK key. Choose a fresh batch prefix for every smoke run. Compare the printed assignments and success submissions with the Flagsmith dashboard after refreshing the results.

```powershell
uv run --extra experiment --env-file .env python -m aegis flagsmith-smoke --batch setup-01
```

Use a fresh running experiment for model runs. Start Ollama with a tool-capable model available locally, then pass its exact installed tag and a unique identity from the run manifest. Flagsmith chooses the strategy, while Ollama executes its prompts. Defaults are localhost ports 8000 and 11434. Use FLAGSMITH_ORIGIN and OLLAMA_ORIGIN to override the service origins.

```powershell
uv run --extra experiment --env-file .env python -m aegis live --identity trial-01-password-reset --model qwen2.5:3b
```

A live run saves its trace before submitting a success event. A status of event_submitted_unverified means the SDK call returned and the dashboard count still needs reconciliation. Preserve pending rows after an interrupted run so you can check attribution before replaying. Use a frozen benchmark manifest to pin the model digest and record it with each trace.



## Run the live end-to-end demo

Load Qwen2.5-3B-Instruct in LM Studio and start its local API (port 1234 by default). Use the exact model ID listed by the API. From the repo root, run:

```powershell
npm.cmd --prefix ui ci
npm.cmd --prefix ui run build
uv run --extra ui python -m examples.invoice.live_demo --model qwen2.5-3b-instruct --serve
```

The command starts the invoice connector, runs real inference, and saves a fresh session under `demo-runs/`. It shuts down the connector to check outage handling, then serves the dashboard on port 5010. Stop the dashboard with Ctrl+C. Use `--connector-port` or `--dashboard-port` if a port is already occupied. Omit `--serve` for a CLI-only run.

| Show | What to look for |
| --- | --- |
| `retry-trace.json` | The first submission returns a simulated timeout. The retry leaves exactly one invoice, `INV-1`. |
| **Review a comparison** → `comparison.json` | Both strategies run against fresh copies of the approval, mismatch, and retry fixtures. Aegis compares the final state and tool evidence. Repair turns are counted. A live run may finish without needing one. |
| **Review a comparison** → `outage.json` | With the connector stopped, both trials lack evidence and Aegis reports **Insufficient evidence**. |

The live comparison reports the observed verdict. Exit codes are 0 for a passing comparison and retry check, 1 for regression or a failed retry check, and 2 for insufficient evidence or a setup error. The expected outage report is saved separately.

### Include Flagsmith assignment and attribution

Point `.env` at a dedicated, running demo experiment with the `agent_strategy` flag (`direct` and `plan_validate`) and `task_success` metric. Use a fresh experiment if you plan to reconcile counts. Each invocation generates a new batch and submits real exposure and success events only when you opt in:

```powershell
uv run --extra ui --extra experiment python -m examples.invoice.live_demo --model qwen2.5-3b-instruct --flagsmith --serve
```

Open **Flagsmith live** to see the assigned runs in the demo ledger. Expand **Review your benchmark** to see coverage and rollout guards. Compare the expected counts in `flagsmith-review.json` with the observed Flagsmith counts. This small batch is a demonstration of assignment and evidence collection. To verify attribution, save the observed counts in the same format as `expected_flagsmith_counts` and pass that file to `benchmark-review --flagsmith-counts` with the demo manifest and ledger.

## Walk through the dashboard

1. Open **Start** and select **Open scripted runs**. Choose **Routing recovery drill** and run both strategies.
2. In **Run evidence**, inspect the invalid tool call and the first divergence. Preview and regrade a case bundle, then download it for a teammate.
3. Select **Flagsmith live** to review runs from your own experiment. For custom tasks, start at **Connect your agent** on the Start page.
4. Once you generate a paired comparison, open **Review your comparison** from the connector guide or the live workspace and choose your JSON file.

The scripted walkthrough needs no model server or Flagsmith key.

## Run the dashboard

Use Python 3.11 or later and Node.js 22.12+ or 24+. From the repository root, build the UI and start FastAPI with Uvicorn.

```powershell
npm --prefix ui ci
npm --prefix ui run build
uv run --extra ui python -m uvicorn aegis.web:create_app --factory --host 127.0.0.1 --port 5000
```

Open http://127.0.0.1:5000. For UI development, run Vite in a second shell and open http://localhost:5173.

```powershell
npm --prefix ui run dev
```

FastAPI serves the compiled UI and exposes API docs at http://127.0.0.1:5000/docs. For Flagsmith live runs, add `--extra experiment` to the `uv run` command and configure `.env`. Use `AEGIS_DB` to point the CLI and dashboard at the same project-specific SQLite ledger.

Select **Routing recovery drill** and **Run both strategies** to bring up the trace comparison. Both scripts initially attempt to assign an invalid queue. The baseline script stops and the candidate script retries with the correct queue. The evidence view shows the first differing action and each final queue. This drill verifies the workflow and illustrates the release review experience. Use the **Flagsmith live** tab for measured runs after installing the experiment extra and configuring Flagsmith and Ollama.

The dashboard compares a selected run with the most recent completed run from the other strategy on the same fixture in the visible history. Treat the comparison as individual trace evidence. The live summary reflects the local ledger. Reconcile exposure and conversion counts in Flagsmith before using those runs for a release decision.

**Flagsmith live** shows the live benchmark selected through `AEGIS_MANIFEST`, or runs started from the current page session if no live benchmark is configured. Choose **All saved live runs** in **Run history** to browse past experiments.


## Benchmark and release review

Rehearse the workflow from the project root. Each scripted fixture runs in both strategies. The review returns Hold because scripted runs only establish that the workflow works.

```powershell
python -m aegis benchmark-prepare demo-manifest.json --batch demo-01
python -m aegis benchmark-run demo-manifest.json
python -m aegis benchmark-review demo-manifest.json
$env:AEGIS_MANIFEST = 'demo-manifest.json'
```

Set AEGIS_MANIFEST before starting FastAPI to show the same review in the dashboard. The manifest records the fixture digest, seeded execution order, identities, limits and review thresholds. Preparation requires a fresh filename. The runner skips existing identities, including interrupted rows, to avoid duplicate exposure events. Missing or pending outcomes trigger Hold and appear in the report.

For a measured batch, use a dedicated running Flagsmith experiment and a fresh batch prefix. Warm up Ollama before the batch. Start by checking both services and adjust the frozen thresholds as needed before preparing the manifest.

```powershell
uv run --extra experiment --env-file .env python -m aegis benchmark-prepare measured-manifest.json --batch measured-01 --live --model qwen2.5:3b --repeats 6
uv run --extra experiment --env-file .env python -m aegis benchmark-run measured-manifest.json
python -m aegis benchmark-review measured-manifest.json
```

Replace the model tag with your exact installed Ollama tag. Aegis checks its digest before each new live identity. Each strategy needs at least 10 completed runs and coverage of all five live fixtures by default. The initial review thresholds require a 5 percentage point gain and cap median latency and tokens at 1.5 times baseline. These are heuristic defaults for a synthetic workload. Repeating a fixture measures execution variability on that fixture.

After the measured batch completes, enter the dedicated Flagsmith experiment's observed exposure and conversion counts in a JSON file. Use the same per-strategy structure as expected_flagsmith_counts in the review. Then rerun the review with --flagsmith-counts and the file path. For the dashboard, set AEGIS_MANIFEST to the measured manifest and AEGIS_FLAGSMITH_COUNTS to the counts file. Matching counts are checked against the batch ledger. A recommendation to review for expansion still requires a human rollout decision.

The gain threshold weighs each fixture equally across strategies. The report includes both the adjusted gain and raw per-fixture counts. The decision command writes a local JSON record containing the chosen decision, rationale, and a snapshot of the supporting review. The operator applies rollout changes separately in Flagsmith.

```powershell
python -m aegis benchmark-decision demo-manifest.json --decision hold --reason "Scripted rehearsal" --output demo-decision.json
```

## Test an LM Studio model

Start the local server from LM Studio's Developer tab and load Qwen2.5 3B Instruct. Copy its API identifier from the tab or GET http://127.0.0.1:1234/v1/models. Run the following from the project root and substitute the exact identifier.

```powershell
uv run --env-file .env python -m aegis model-test --model '<api-model-id>' --strategy direct --case password-reset
uv run --env-file .env python -m aegis model-test --model '<api-model-id>' --strategy plan_validate --case password-reset
```

The CLI uses LM Studio's OpenAI-compatible local endpoint at http://127.0.0.1:1234 by default. Set LM_STUDIO_ORIGIN if you use another local port and LM_STUDIO_API_KEY if authentication is enabled. These manually assigned model checks write traces under model-test in SQLite and do not generate Flagsmith experiment events.

### Use LM Studio for a measured benchmark

Start a fresh Flagsmith experiment with the same flag and metric as the smoke test. Keep the model loaded in LM Studio, then use the exact API model ID and its GGUF file. From PowerShell:

```powershell
$gguf = Join-Path $env:USERPROFILE '.lmstudio\models\Qwen\Qwen2.5-3B-Instruct-GGUF\qwen2.5-3b-instruct-q4_k_m.gguf'
uv run --extra experiment --env-file .env python -m aegis benchmark-prepare new-measurement.json --batch new-measured-01 --live --provider lmstudio --model qwen2.5-3b-instruct --model-file $gguf --repeats 6
uv run --extra experiment --env-file .env python -m aegis benchmark-run new-measurement.json --model-file $gguf --progress
uv run --env-file .env python -m aegis benchmark-review new-measurement.json
```

Preparation and execution check that LM Studio lists the model ID. The manifest fingerprints the GGUF you supply, and execution verifies that file before enrolling any identities. Use the file that is actually loaded for that API ID, and keep the LM Studio model and runtime settings fixed through the batch. The API model list does not attest to the loaded file hash. The manifest stores the hash, provider and decoding settings without the local file path.

For a single live task, choose LM Studio in the dashboard or run the live command with --provider lmstudio. Use an identity outside the measured batch in a separate experiment if you want to reconcile the measured experiment totals.

## Connect another agent project

The HTTP connector lets a project keep its own runtime, fixtures and evaluator. Expose a POST endpoint in a trusted test environment. Each request uses a fresh fixture instance. Aegis sends the assigned strategy and run identity and handles Flagsmith events, trace storage and release review.

Create a connector spec such as project-spec.json. The revision identifies the version of the agent, fixtures and evaluator that the endpoint serves.

```json
{"revision": "support-agent-v1", "cases": ["invoice-match", "missing-approval"]}
```

The endpoint receives this shape. Map direct to your current strategy and plan_validate to your candidate. The names remain stable because Aegis reads those flag values from Flagsmith.

```json
{"version": 1, "case": "invoice-match", "strategy": "direct", "identity": "project-01-0001", "revision": "support-agent-v1"}
```

Run the named fixture, evaluate its outcome, and return JSON with the revision the endpoint actually executed, a boolean success, and a boolean forbidden_mutation. Optional fields include tokens, summary, before, after and tools. Tool traces use tool, status and optional arguments and result. Supported statuses are ok, invalid and blocked.

```json
{
  "revision": "support-agent-v1",
  "success": true,
  "forbidden_mutation": false,
  "tokens": 420,
  "summary": "Matched the invoice to the order.",
  "before": {"invoice": "unmatched"},
  "after": {"invoice": "matched"},
  "tools": [{"tool": "match_invoice", "status": "ok", "arguments": {"invoice_id": "fixture-1"}}]
}
```

Start a dedicated Flagsmith experiment and set the endpoint in your shell. Connector benchmarks launch from the CLI and appear in the dashboard's Flagsmith live history.

```powershell
$env:AEGIS_CONNECTOR_URL = 'http://127.0.0.1:9000/run'
uv run --extra experiment --env-file .env python -m aegis benchmark-prepare project-manifest.json --batch project-01 --live --provider connector --connector-spec project-spec.json --repeats 8
uv run --extra experiment --env-file .env python -m aegis benchmark-run project-manifest.json --progress
uv run --env-file .env python -m aegis benchmark-review project-manifest.json
```

The connector owns its evaluator. Aegis measures the HTTP round-trip latency and checks the result format and revision. A forbidden mutation fails the run even if the project reports success. A failed HTTP request or invalid response leaves an incomplete ledger entry and stops the batch. Inspect that identity before resuming. Report token counts for the existing release gate to evaluate cost.

### Bring an agent and let Aegis evaluate it

Use the optional rules block in the connector spec. The [invoice example](examples/invoice/rules.json) checks that the invoice ends approved, the agent checks the receipt before approval, it never attempts payment, and payment state stays unchanged.

| Rule | What passes |
| --- | --- |
| expected_state | Each listed top-level key in the after object equals its configured JSON value. |
| required_tool_order | The listed successful tool calls appear in order. Other calls can occur between them. |
| tool_prerequisites | Each successful call to a mapped tool occurs after all its listed prerequisites have succeeded. Use with `required_tool_order` or `expected_state` to require completion. |
| forbidden_tools | The trace contains no attempt to call any listed tool, including blocked attempts. |
| unchanged_state_keys | Each listed key exists before the run and retains the same value after it. |

Supply one rule set per fixture. The endpoint returns the revision, before and after objects, and the ordered tools array. It can omit its own success and forbidden_mutation fields. Aegis calculates both from the evidence and freezes the rules in the benchmark manifest. A changed protected field rejects the candidate in release review. A forbidden tool attempt holds the candidate. The dashboard shows each check alongside the trace.

When a prerequisite represents authorization, emit an `ok` event only for an authorization that actually succeeded. A late successful check cannot make an earlier successful write pass.

Validate a connector against a test fixture before enrolling any Flagsmith identities. This command executes the selected strategy against the configured test endpoint.

```powershell
$env:AEGIS_CONNECTOR_URL = 'http://127.0.0.1:9000/run'
python -m aegis connector-check examples/invoice/rules.json --case invoice-approval --strategy direct
```

The example spec assumes your agent endpoint implements that invoice fixture. Projects with custom grading logic can continue using the original response format and omit rules from their spec.

### Use the optional starter agent

Teams without an agent can use the FastAPI starter with an OpenAI-compatible tool-calling service. The default API base URL is Groq. Copy the starter settings from .env.example into .env and set AEGIS_CLOUD_MODEL to a tool-capable model available on your account. Put the corresponding API key in AEGIS_CLOUD_API_KEY. Use synthetic fixture data because the model provider receives the task, initial state and tool results.

Set `AEGIS_STARTER_LMSTUDIO_MODEL=qwen2.5-3b-instruct` in `.env` to use LM Studio for the starter. Run the same Uvicorn command and change the connector check to `--case invoice-retry` to exercise the simulated lost acknowledgement and verify that the recovered invoice state contains exactly one entry.

The [starter configuration](examples/invoice/starter.json) defines each task, its initial state and its mock tools. Each tool takes an empty object and can return fixed data or update fields in that fixture's state. The companion invoice rules spec owns the expected outcome. Use the same revision in both files and update it when changing the model, fixture, tools or grading rules.

```powershell
uv run --extra ui --env-file .env python -m uvicorn aegis.starter:create_app --factory --host 127.0.0.1 --port 9000
```

In a second shell, run the connector check or use this endpoint in the live connector benchmark described above.

```powershell
$env:AEGIS_CONNECTOR_URL = 'http://127.0.0.1:9000/run'
uv run python -m aegis connector-check examples/invoice/rules.json --case invoice-approval --strategy direct
```

The connector check makes real inference requests. Provider usage limits and charges depend on your account. The local LM Studio and Ollama workflows still work with their own configuration.

### Integrate from Python with the SDK

Install the local Aegis checkout from your agent project's environment. The SDK uses the same versioned HTTP contract as connectors in other languages.

```powershell
uv pip install 'C:/path/to/Aegis[ui]'
```

In your project's connector module, wrap the handler that runs your agent. Aegis passes the fixture, identity and Flagsmith-selected strategy to the handler.

```python
from aegis.sdk import RunEvidence, RunRequest, ToolCall, create_connector_app


def run(request: RunRequest) -> RunEvidence:
    outcome = run_your_agent(request.case, request.strategy, request.identity)
    return RunEvidence(
        before=outcome.before,
        after=outcome.after,
        tools=[ToolCall(**call) for call in outcome.tools],
        tokens=outcome.tokens,
    )


app = create_connector_app("your-project-v1", run)
```

Serve the module with Uvicorn on localhost and set AEGIS_CONNECTOR_URL to its /run endpoint. Use the same revision in the connector spec. Aegis can evaluate the evidence using spec rules, or you can supply both success and forbidden_mutation on RunEvidence to use your own evaluator. The cloud starter serves its endpoint through this same SDK.
