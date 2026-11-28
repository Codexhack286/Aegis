import React, { useState } from 'react'

const verdicts = {
  pass: 'Pass',
  regression: 'Regression',
  insufficient_evidence: 'Insufficient evidence',
}
const explanations = {
  pass: 'Aegis detected no regression on the tasks covered by this comparison.',
  regression: 'The candidate lost passes on at least one task or changed protected state. Inspect the failed checks below.',
  insufficient_evidence: 'Aegis could not make a reliable comparison. Check for unavailable trials, different starting states, or tasks where both strategies failed.',
}
const sources = { connector: 'Connector', lmstudio: 'LM Studio', scripted: 'Scripted' }
const defaultArms = ['direct', 'plan_validate']
const armLabels = { direct: 'Direct', plan_validate: 'Plan and validate', plan_validate_repair: 'Plan, validate and repair' }
const checkLabels = {
  expected_state: 'Expected state not reached',
  required_tool_order: 'Required step missing or out of order',
  tool_prerequisites: 'Tool prerequisite not met',
  forbidden_tool: 'Forbidden tool attempted',
  unchanged_state: 'Protected state changed',
}

function parseReport(text) {
  const report = JSON.parse(text)
  const arms = report?.arms ?? defaultArms
  const validVerdict = value => Object.hasOwn(verdicts, value)
  const validTrials = trials => Array.isArray(trials) && trials.length === report.repeats
    && trials.every(trial => trial && [true, false, null].includes(trial.success)
      && (trial.first_failure == null || typeof trial.first_failure === 'string')
      && (trial.failed_checks == null || (Array.isArray(trial.failed_checks)
        && trial.failed_checks.every(check => typeof check === 'string'))))
  if (report?.version !== 1 || !validVerdict(report.verdict) || !Object.hasOwn(sources, report.source)
    || !Array.isArray(arms) || arms.length !== 2 || arms[0] === arms[1]
    || !arms.every(arm => Object.hasOwn(armLabels, arm))
    || !Number.isInteger(report.repeats) || report.repeats < 1 || report.repeats > 20
    || !Array.isArray(report.cases) || !report.cases.length || report.cases.length > 100
    || report.distinct_cases !== report.cases.length
    || !report.cases.every(row => typeof row?.case === 'string' && validVerdict(row.verdict)
      && arms.every(arm => validTrials(row.outcomes?.[arm])))) {
    throw new Error('Choose a version 1 JSON report from case-compare.')
  }
  return { ...report, arms }
}

function TrialOutcomes({ trials }) {
  const blocked = trials.filter(trial => Number.isInteger(trial.blocked_calls) && trial.blocked_calls > 0).length
  const mutations = trials.filter(trial => trial.forbidden_mutation === true).length
  const checks = [...new Set(trials.flatMap(trial => [
    ...(trial.failed_checks ?? []), ...(trial.first_failure ? [trial.first_failure] : []),
  ]))]
  return <>
    <div>{countTrials(trials)}</div>
    {blocked > 0 && <div>Tool calls blocked in {blocked} {blocked === 1 ? 'trial' : 'trials'}</div>}
    {mutations > 0 && <div>Protected state changed in {mutations} {mutations === 1 ? 'trial' : 'trials'}</div>}
    {checks.filter(check => check !== 'unchanged_state' || mutations === 0).map(check =>
      <div key={check}>{Object.hasOwn(checkLabels, check) ? checkLabels[check] : check}</div>)}
  </>
}

function countTrials(trials) {
  const missing = trials.filter(trial => trial.success === null).length
  const passed = trials.filter(trial => trial.success === true).length
  const repairs = trials.map(trial => trial.repair_turns).filter(turns => turns === 0 || turns === 1)
  const latencies = trials.map(trial => trial.latency_ms)
    .filter(value => Number.isFinite(value) && value >= 0).sort((a, b) => a - b)
  const midpoint = (latencies.length - 1) / 2
  const median = (latencies[Math.floor(midpoint)] + latencies[Math.ceil(midpoint)]) / 2
  return passed + '/' + (trials.length - missing) + (missing ? ' (' + missing + ' unavailable)' : '')
    + (repairs.length ? ' · ' + repairs.reduce((sum, turns) => sum + turns, 0) + ' repair turns' : '')
    + (latencies.length ? ' · ' + Math.round(median) + ' ms median' : '')
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
      const bytes = new Uint8Array(await file.arrayBuffer())
      const encoding = bytes[0] === 0xff && bytes[1] === 0xfe ? 'utf-16le' :
        bytes[0] === 0xfe && bytes[1] === 0xff ? 'utf-16be' : 'utf-8'
      setReport(parseReport(new TextDecoder(encoding).decode(bytes)))
    } catch (failure) {
      setError(failure instanceof SyntaxError ? 'Choose a valid JSON report from case-compare.' : failure.message)
    } finally {
      setBusy(false)
    }
  }

  return <section className="panel benchmark report-upload">
    <div className="section-head"><h2>Import your comparison</h2></div>
    <div className="evidence-body">
    <label>Choose the JSON file saved by <code>case-compare</code>
      <input type="file" accept=".json,application/json" disabled={busy} onChange={loadReport} />
    </label>
    {!report && !error && <p className="empty">{busy ? 'Reading comparison…' : 'The file is read in your browser. Need to create one?'} {!busy && <a href="#connect">See connector setup</a>}</p>}
    {error && <p role="alert">{error}</p>}
    {report && <>
      <p className="comparison-explanation">{explanations[report.verdict]}</p>
      <p role="status"><strong>{verdicts[report.verdict]}</strong> · {sources[report.source]} · {report.distinct_cases} fixtures · {report.repeats} trials per arm and fixture</p>
      <div style={{ overflowX: 'auto' }}><table className="fixture-table">
        <thead><tr><th>Fixture</th>{report.arms.map(arm => <th key={arm}>{armLabels[arm]}</th>)}<th>Result</th></tr></thead>
        <tbody>{report.cases.map((row, index) => <tr key={index}>
          <th scope="row">{row.case}</th>
          {report.arms.map(arm => <td key={arm}><TrialOutcomes trials={row.outcomes[arm]} /></td>)}
          <td>{verdicts[row.verdict]}
            {row.state_mismatch === true && <div>Different starting states</div>}
          </td>
        </tr>)}</tbody>
      </table></div>
    </>}
    </div>
  </section>
}
