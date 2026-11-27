import React, { useEffect, useState } from 'react'
import BenchmarkReview from './components/BenchmarkReview.jsx'
import { Comparison, ToolTrace } from './components/Trace.jsx'
import { cases, humanize, labels, ms, resultLabel } from './presentation.js'

async function api(path, options) {
  const response = await fetch(`/api${path}`, options)
  const data = await response.json().catch(() => ({}))
  if (!response.ok) throw new Error(data.error || `API request failed (${response.status}).`)
  return data
}

export default function App() {
  const [mode, setMode] = useState('offline')
  const [data, setData] = useState(null)
  const [current, setCurrent] = useState(null)
  const [reference, setReference] = useState(null)
  const [fixture, setFixture] = useState('password-reset')
  const [strategy, setStrategy] = useState('direct')
  const [model, setModel] = useState('qwen2.5:3b')
  const [provider, setProvider] = useState('ollama')
  const [identity, setIdentity] = useState('')
  const [busy, setBusy] = useState(false)
  const [epoch, refresh] = useState(0)
  const [selected, select] = useState(null)
  const [error, setError] = useState('')
  const [review, setReview] = useState(null)
  const [reviewError, setReviewError] = useState('')
  const [batchOnly, setBatchOnly] = useState(true)
  const [failuresOnly, setFailuresOnly] = useState(false)
  const scoped = batchOnly && review?.mode === mode

  useEffect(() => {
    let active = true
    api('/benchmark-review').then(payload => {
      if (!active) return
      setReview(payload.review)
      setReviewError('')
    }).catch(error => {
      if (!active) return
      setReview(null)
      setReviewError(error.message)
    })
    return () => { active = false }
  }, [epoch, mode])

  useEffect(() => {
    let active = true
    api(`/workspace?mode=${mode}&benchmark_only=${Boolean(scoped)}`).then(payload => {
      if (!active) return
      setData(payload)
      select(previous => payload.runs.some(run => run.identity === previous)
        ? previous : payload.runs[0]?.identity ?? null)
    }).catch(error => { if (active) setError(error.message) })
    return () => { active = false }
  }, [mode, epoch, scoped])

  const other = current?.report && data?.runs.find(run => run.case_id === current.case_id
    && run.model === current.model
    && run.provider === (current.report.provider ?? (mode === 'live' ? 'ollama' : null))
    && run.manifest_id === (current.report.manifest_id ?? null)
    && run.strategy && run.strategy !== current.strategy && run.success != null)?.identity
  useEffect(() => {
    let active = true
    if (selected) api(`/runs/${encodeURIComponent(selected)}`).then(payload => {
      if (active) setCurrent(payload)
    }).catch(error => { if (active) setError(error.message) })
    return () => { active = false }
  }, [selected, epoch])

  useEffect(() => {
    let active = true
    if (other) api(`/runs/${encodeURIComponent(other)}`).then(payload => {
      if (active) setReference(payload)
    }).catch(error => { if (active) setError(error.message) })
    return () => { active = false }
  }, [other, epoch])

  async function start(event) {
    event.preventDefault()
    setBusy(true)
    setError('')
    try {
      const submit = arm => api('/runs', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ mode, case: fixture, strategy: arm, model, identity, provider }),
      })
      const compare = mode === 'offline' && event.nativeEvent.submitter?.value === 'compare'
      if (compare) await submit('direct')
      const result = await submit(compare ? 'plan_validate' : strategy)
      setCurrent(null)
      setBatchOnly(false)
      setFailuresOnly(false)
      select(result.identity)
      if (mode === 'live') setIdentity('')
    } catch (error) {
      setError(error.message)
    } finally {
      refresh(value => value + 1)
      setBusy(false)
    }
  }

  const report = current?.identity === selected ? current.report : null
  const visibleRuns = data?.runs.filter(run => !failuresOnly || run.success === false) ?? []
  return <>
    <header className="top"><div className="shell top-inner">
      <a className="brand" href="/" aria-label="Aegis dashboard"><span className="logo"><i /></span>Aegis</a>
      <span className="top-label">Agent evaluation workspace</span>
      <div className="modes" aria-label="Run mode">
        {[['offline', 'Scripted'], ['live', 'Flagsmith live']].map(([value, title]) =>
          <button key={value} type="button" disabled={busy} aria-pressed={mode === value}
            onClick={() => { if (value !== mode) { setMode(value); setData(null); setCurrent(null); select(null); setError('') } }}>
            {title}
          </button>)}
      </div>
    </div></header>

    <main className="shell">
      <div className="hero">
        <h1>Strategy evaluation</h1>
        <p>Compare agent outcomes and inspect the actions behind each result.</p>
      </div>

      {error && <div className="alert" role="alert">{error}</div>}
      {review?.mode === mode && <BenchmarkReview review={review} />}
      {reviewError && <div className="alert benchmark" role="alert">{reviewError}</div>}
      <div className="workspace-head">
        <h2>{scoped ? 'Benchmark workspace' : 'Run workspace'}</h2>
        {review?.mode === mode && <label>
          <input type="checkbox" checked={batchOnly} onChange={event => {
            setBatchOnly(event.target.checked)
            setData(null)
          }} /> Show configured benchmark only
        </label>}
      </div>
      <div className="overview">
        <div className="arm-grid">{['direct', 'plan_validate'].map((arm, index) => {
          const stats = data?.arms.find(item => item.strategy === arm)
          const fraction = stats?.evaluated ? stats.successful / stats.evaluated : 0
          return <article key={arm} className="panel arm">
            <span className={`arm-tag arm-${index}`}>{index === 0 ? 'Baseline' : 'Candidate'}</span>
            <h2>{labels[arm]}</h2>
            <div className="score">{stats?.evaluated ? Math.round(fraction * 100) + '%' : '—'}<span>task success</span></div>
            <div className={`meter meter-${index}`} aria-hidden="true"><i style={{ width: `${fraction * 100}%` }} /></div>
            <p className="coverage">{stats ? `${stats.successful} passed / ${stats.evaluated} evaluated` : 'Loading…'}</p>
            <dl className="metrics">
              <div><dt>Blocked calls</dt><dd>{stats?.blocked_calls ?? '—'}</dd></div>
              <div><dt>Invalid calls</dt><dd>{stats?.invalid_calls ?? '—'}</dd></div>
              <div><dt>Median latency</dt><dd>{mode === 'offline' ? '—' : ms(stats?.median_latency_ms)}</dd></div>
            </dl>
            {stats && stats.assigned > stats.evaluated && <small>{stats.assigned - stats.evaluated} assigned runs awaiting evaluation</small>}
          </article>
        })}</div>

        <form className="panel run-form" onSubmit={start}>
          <h2>New standalone run</h2>
          <label>Ticket fixture<select value={fixture} onChange={event => setFixture(event.target.value)}>
            {Object.entries(cases).map(([key, title]) => <option key={key} value={key}>{title}</option>)}
          </select></label>
          {mode === 'offline' ? <label>Strategy<select value={strategy} onChange={event => setStrategy(event.target.value)}>
            {Object.entries(labels).map(([key, title]) => <option key={key} value={key}>{title}</option>)}
          </select></label> : <>
            <label>Inference provider<select value={provider} onChange={event => {
              const next = event.target.value
              setProvider(next)
              setModel(next === 'lmstudio' ? 'qwen2.5-3b-instruct' : 'qwen2.5:3b')
            }}>
              <option value="ollama">Ollama</option>
              <option value="lmstudio">LM Studio</option>
            </select></label>
            <label>Model ID<input required value={model} onChange={event => setModel(event.target.value)} /></label>
            <label>Experiment identity<input required placeholder="trial-01-password-reset" value={identity} onChange={event => setIdentity(event.target.value)} /></label>
          </>}
          <button className="primary" disabled={busy || (mode === 'live' && !data?.live_configured)}>
            {busy ? 'Running…' : mode === 'offline' ? 'Run scripted check' : 'Run experiment task'} <span aria-hidden="true">→</span>
          </button>
          {mode === 'offline' && <button className="secondary" value="compare" disabled={busy}>Run both strategies</button>}
          <small>{mode === 'offline' ? (fixture === 'recovery-drill' ? 'Both scripts send one invalid queue. The candidate script then repairs it.' : 'Executes a scripted tool sequence locally.') : (data?.live_configured
            ? 'Flagsmith assigns the strategy for this identity.' : 'Set the Flagsmith environment key to enable live runs.')}</small>
        </form>
      </div>

      <div className="workbench">
        <section className="panel run-list">
          <div className="section-head"><h2>Run history</h2><span>{data?.run_count ?? '—'} total</span></div>
          <div className="history-controls">
            <label><input type="checkbox" checked={failuresOnly} onChange={event => {
              setFailuresOnly(event.target.checked)
              const failed = data?.runs.find(run => run.success === false)
              if (event.target.checked && !data?.runs.some(run => run.identity === selected && run.success === false)) {
                select(failed?.identity ?? null)
              } else if (!event.target.checked && !selected) {
                select(data?.runs[0]?.identity ?? null)
              }
            }} /> Failures only</label>
          </div>
          {visibleRuns.length ? <ul>{visibleRuns.map(run => <li key={run.identity}>
            <button type="button" aria-pressed={selected === run.identity} onClick={() => { setCurrent(null); select(run.identity); if (selected === run.identity) refresh(value => value + 1) }}>
              <span><strong>{cases[run.case_id] || run.case_id}</strong><small>{labels[run.strategy] || 'Awaiting assignment'}</small></span>
              <span className={`pill ${run.success ? 'ok' : run.success === false ? 'blocked' : ''}`}>{resultLabel(run.success)}</span>
            </button>
          </li>)}</ul> : <p className="empty">{!data ? 'Waiting for run history.' :
            failuresOnly ? 'No failures in the displayed history.' : 'No runs in this workspace yet.'}</p>}
        </section>

        <section className="panel inspection">
          <div className="section-head"><h2>Run evidence</h2>{report && <span className={`pill ${report.success ? 'ok' : 'blocked'}`}>{resultLabel(report.success)}</span>}</div>
          {report ? <>
            <div className="evidence-body">
              <p className="run-context">{cases[report.case] || report.case} · {labels[report.strategy]}</p>
              <dl className="states">
                {report.provider !== 'connector' && <>
                  <div><dt>Queue change</dt><dd>{report.before['T-1'].queue} → {report.after['T-1'].queue}</dd></div>
                  <div><dt>Expected queue</dt><dd>{report.expected_queue}</dd></div>
                </>}
                <div><dt>Terminal status</dt><dd>{humanize(report.terminal)}</dd></div>
                {report.provider === 'connector' && <div>
                  <dt>Forbidden mutation</dt><dd>{report.forbidden_mutation ? 'Yes' : 'No'}</dd>
                </div>}
              </dl>
              {report.final_text && <blockquote>{report.final_text}</blockquote>}
              {report.checks?.length > 0 && <>
                <h3>Evaluation checks</h3>
                <ul>{report.checks.map((check, index) => <li key={index}>
                  <span className={`pill ${check.passed ? 'ok' : 'blocked'}`}>{resultLabel(check.passed)}</span>
                  {' '}{humanize(check.kind)} · {check.subject}
                </li>)}</ul>
              </>}
              {report.provider === 'connector' && (report.before != null || report.after != null) &&
                <details className="raw"><summary>Project state before and after</summary>
                  <pre>{JSON.stringify({ before: report.before, after: report.after }, null, 2)}</pre>
                </details>}
              <h3>Tool trajectory</h3>
              <ToolTrace tools={report.tools} />
              {reference?.report && reference.identity === other && <Comparison current={current} reference={reference} />}
              <details className="raw"><summary>Full run record</summary><pre>{JSON.stringify(current, null, 2)}</pre></details>
            </div>
          </> : <p className="empty">{current?.identity === selected && current
            ? `No trace recorded. Ledger status: ${humanize(current.status)}.`
            : 'Select a run to inspect its tool calls and ticket state.'}</p>}
        </section>
      </div>
    </main>
  </>
}
