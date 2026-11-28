import React from 'react'

export default function Start() {
  return <div className="start-grid">
    <section className="panel start-feature">
      <div>
        <h2>Connect your own agent</h2>
        <p>Define the result you expect, record the tools your agent used, and test both strategies on the same starting state.</p>
        <a className="primary action-link" href="#connect">Set up a connector <span aria-hidden="true">→</span></a>
      </div>
      <dl className="start-flow">
        <div><dt>Define</dt><dd>Tasks and expected results</dd></div>
        <div><dt>Compare</dt><dd>Baseline and candidate strategies</dd></div>
        <div><dt>Inspect</dt><dd>Tool calls, state changes, and failed checks</dd></div>
      </dl>
    </section>
    <section className="panel start-option">
      <h2>Try a sample task</h2>
      <p>Run both strategies on a built-in helpdesk case and compare their tool calls. No model or credentials needed.</p>
      <a className="secondary action-link" href="#runs">Open scripted runs <span aria-hidden="true">→</span></a>
    </section>
    <section className="panel start-option">
      <h2>Evaluate a live experiment</h2>
      <p>Use Flagsmith to assign strategies. Inspect your project’s runs and review a configured benchmark before expanding rollout.</p>
      <a className="secondary action-link" href="#live">Open live runs <span aria-hidden="true">→</span></a>
    </section>
  </div>
}
