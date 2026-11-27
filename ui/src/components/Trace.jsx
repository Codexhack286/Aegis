import React, { useState } from 'react'
import { labels, resultLabel } from '../presentation.js'

export function CaseBundle({ identity, api }) {
  const [review, setReview] = useState(null)
  const [error, setError] = useState('')
  const [loading, setLoading] = useState(false)

  async function reviewBundle() {
    setLoading(true)
    setError('')
    try {
      setReview(await api('/runs/' + encodeURIComponent(identity) + '/case-bundle'))
    } catch (failure) {
      setError(failure.message)
    } finally {
      setLoading(false)
    }
  }

  return <section className="comparison">
    <h3>Portable regression case</h3>
    {!review && <button type="button" disabled={loading} onClick={reviewBundle}>
      {loading ? 'Regrading…' : 'Preview and regrade bundle'}
    </button>}
    {error && <p role="alert">{error}</p>}
    {review && <>
      <p role="status">{review.grade.first_failure || 'All helpdesk checks passed.'}</p>
      <ul>{review.grade.checks.map(check => <li key={check.rule}>
        <span className={`pill ${check.passed ? 'ok' : 'blocked'}`}>{resultLabel(check.passed)}</span>
        {' '}{check.rule}
      </li>)}</ul>
      <details className="raw"><summary>Bundle preview</summary><pre>{JSON.stringify(review.bundle, null, 2)}</pre></details>
      <a download={review.bundle.case + '-' + review.bundle.strategy + '.case.json'} href={'data:application/json;charset=utf-8,' +
        encodeURIComponent(JSON.stringify(review.bundle, null, 2) + '\n')}>Download case bundle</a>
    </>}
  </section>
}

export function ToolTrace({ tools }) {
  if (!tools.length) return <p className="empty">The agent made no tool calls.</p>
  return <ol className="timeline">
    {tools.map((tool, index) => <li key={index}>
      <span className={`step ${tool.status}`}>{String(index + 1).padStart(2, '0')}</span>
      <details>
        <summary><code>{tool.tool || 'Unknown tool'}</code><span className={`pill ${tool.status}`}>{tool.status}</span></summary>
        <pre>{JSON.stringify({ arguments: tool.arguments, result: tool.result }, null, 2)}</pre>
      </details>
    </li>)}
  </ol>
}

function sameArguments(first, second) {
  if (first == null || second == null || typeof first !== 'object' || typeof second !== 'object') {
    return JSON.stringify(first) === JSON.stringify(second)
  }
  return JSON.stringify(Object.entries(first).sort()) === JSON.stringify(Object.entries(second).sort())
}

export function Comparison({ current, reference }) {
  const a = current.report.tools
  const b = reference.report.tools
  const position = Array.from({ length: Math.max(a.length, b.length) }, (_, index) => index)
    .find(index => a[index]?.tool !== b[index]?.tool || a[index]?.status !== b[index]?.status
      || !sameArguments(a[index]?.arguments, b[index]?.arguments))
  const describe = tool => tool ? `${tool.tool || 'Unknown tool'} · ${tool.status}` : 'No action'
  return <section className="comparison">
    <h3>First tool divergence</h3>
    <p>{position == null ? 'Tool actions and arguments matched across these runs.' : `The traces first diverged at step ${position + 1}.`}</p>
    <div className="compare-grid">{[current, reference].map((run, index) => <div key={run.identity}>
      <strong>{labels[run.strategy]}</strong>
      {position != null && <>
        <code>{describe([a, b][index][position])}</code>
        <pre>{JSON.stringify([a, b][index][position]?.arguments ?? null, null, 2)}</pre>
      </>}
      {run.report.provider === 'connector'
        ? <span>{resultLabel(run.report.success)}</span>
        : <span>Final queue · {run.report.after['T-1'].queue}</span>}
    </div>)}</div>
    <small>Two executions of the same fixture. This is trace evidence from two individual runs.</small>
  </section>
}
