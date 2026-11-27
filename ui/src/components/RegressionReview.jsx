import React, { useState } from 'react'

const verdicts = {
  pass: 'Pass',
  regression: 'Regression',
  insufficient_evidence: 'Insufficient evidence',
}
const sources = { connector: 'Connector', lmstudio: 'LM Studio', scripted: 'Scripted' }
const arms = ['direct', 'plan_validate']

function parseReport(text) {
  const report = JSON.parse(text)
  const validVerdict = value => Object.hasOwn(verdicts, value)
  const validTrials = trials => Array.isArray(trials) && trials.length === report.repeats
    && trials.every(trial => trial && [true, false, null].includes(trial.success)
      && (trial.first_failure == null || typeof trial.first_failure === 'string'))
  if (report?.version !== 1 || !validVerdict(report.verdict) || !Object.hasOwn(sources, report.source)
    || !Number.isInteger(report.repeats) || report.repeats < 1 || report.repeats > 20
    || !Array.isArray(report.cases) || !report.cases.length || report.cases.length > 100
    || report.distinct_cases !== report.cases.length
    || !report.cases.every(row => typeof row?.case === 'string' && validVerdict(row.verdict)
      && arms.every(arm => validTrials(row.outcomes?.[arm])))) {
    throw new Error('Choose a version 1 JSON report from case-compare.')
  }
  return report
}

function countTrials(trials) {
  const missing = trials.filter(trial => trial.success === null).length
  const passed = trials.filter(trial => trial.success === true).length
  return passed + '/' + (trials.length - missing) + (missing ? ' (' + missing + ' unavailable)' : '')
}

export default function RegressionReview() {
  const [report, setReport] = useState(null)
  const [error, setError] = useState('')
  const [busy, setBusy] = useState(false)

  async function loadReport(event) {
    const file = event.target.files[0]
    event.target.value = ''
    if (!file) return
    setReport(null)
    setError('')
    setBusy(true)
    try {
      if (file.size > 4 * 1024 * 1024) throw new Error('Choose a JSON report under 4 MiB.')
      setReport(parseReport(await file.text()))
    } catch (failure) {
      setError(failure instanceof SyntaxError ? 'Choose a valid JSON report from case-compare.' : failure.message)
    } finally {
      setBusy(false)
    }
  }

  return <details className="panel benchmark">
    <summary>Review a paired regression report</summary>
    <p><label>Load a case-compare JSON report <input type="file" accept=".json,application/json" disabled={busy} onChange={loadReport} /></label></p>
    {error && <p role="alert">{error}</p>}
    {report && <>
      <p role="status"><strong>{verdicts[report.verdict]}</strong> · {sources[report.source]} · {report.distinct_cases} fixtures · {report.repeats} trials per arm and fixture</p>
      <div style={{ overflowX: 'auto' }}><table className="fixture-table">
        <thead><tr><th>Fixture</th><th>Direct</th><th>Plan and validate</th><th>Result</th></tr></thead>
        <tbody>{report.cases.map((row, index) => <tr key={index}>
          <th scope="row">{row.case}</th>
          {arms.map(arm => <td key={arm}>{countTrials(row.outcomes[arm])}</td>)}
          <td>{verdicts[row.verdict]}
            {row.state_mismatch === true && <div>Different starting states</div>}
            {[...new Set(row.outcomes.plan_validate.map(trial => trial.first_failure).filter(Boolean))]
              .map(failure => <div key={failure}>{failure}</div>)}
          </td>
        </tr>)}</tbody>
      </table></div>
    </>}
  </details>
}
