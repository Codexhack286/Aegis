import React from 'react'

function ConnectorSetup() {
  return <section className="panel guide-section">
    <h2>Run a paired check in your project</h2>
    <ol>
      <li>Wrap your agent with <code>aegis.sdk.create_connector_app</code> and serve <code>POST /run</code>. Use a fresh starting state on every request.</li>
      <li>In a connector spec, list your test cases and the expected state or tool rules. Match the revision in your spec and connector.</li>
      <li>Set <code>AEGIS_CONNECTOR_URL</code> to your running endpoint, then run a comparison from your project environment.</li>
    </ol>
    <pre><code>uv run python -m aegis case-compare --connector-spec rules.json --case your-fixture --repeats 2 --format json &gt; paired-report.json</code></pre>
    <details className="raw">
      <summary>What your connector should return</summary>
      <p>Return <code>RunEvidence</code> with <code>before</code>, <code>after</code>, and ordered <code>ToolCall</code> entries. The spec declares <code>trial_isolation: fresh_per_request</code> and rules such as expected state, tool prerequisites, or protected fields.</p>
      <p>In the checkout, see <code>examples/invoice/rules.json</code> for a complete spec and the README for SDK installation and an endpoint example.</p>
    </details>
    <p><a href="#compare">Review your comparison</a> to see failed checks by task. Use a measured Flagsmith experiment to evaluate a rollout.</p>
  </section>
}

export default function Guide({ connectionOnly = false }) {
  if (connectionOnly) return <div className="guide"><ConnectorSetup /><a href="#guide">See the full guide</a></div>

  return <div className="guide">
    <section className="panel guide-section">
      <h2>Inspect your first run</h2>
      <ol>
        <li>Open <a href="#runs">Scripted runs</a>, choose <strong>Routing recovery drill</strong>, and select <strong>Run both strategies</strong>.</li>
        <li>Select a run. Inspect its final state, expand a tool call to see what happened, and find where the strategies first diverged.</li>
        <li>On a built-in case, select <strong>Preview and regrade bundle</strong> to see the checks and export the evidence as JSON.</li>
      </ol>
      <details className="raw">
        <summary>Regrade an exported case from the terminal</summary>
        <pre><code>uv run python -m aegis case-regrade your-case.case.json</code></pre>
        <p>Regrading evaluates the saved evidence without calling the model again.</p>
      </details>
    </section>
    <section className="panel guide-section">
      <h2>Set up a live experiment</h2>
      <ol>
        <li>Configure a Flagsmith experiment with the <code>agent_strategy</code> flag and the <code>task_success</code> metric. Set <code>FLAGSMITH_MODE</code> and the matching environment key in <code>.env</code>, then restart Aegis with the <code>experiment</code> extra installed.</li>
        <li>Start Ollama or LM Studio. In <a href="#live">Flagsmith live</a>, enter the exact model ID and an eligible experiment identity. Flagsmith assigns its strategy.</li>
        <li>For a measured project benchmark, configure <code>AEGIS_MANIFEST</code> to point at your manifest and <code>AEGIS_FLAGSMITH_COUNTS</code> to your observed counts. Open <strong>Review your benchmark</strong> under run history.</li>
      </ol>
      <p>Set <code>AEGIS_DB</code> to a project-specific SQLite path when you work across projects. Start the CLI and dashboard with the same database.</p>
    </section>
    <ConnectorSetup />
  </div>
}
