import React, { useState } from 'react'
import { cases, labels, ms } from '../presentation.js'

export default function BenchmarkReview({ review, showAttribution = false }) {
  const [selectedFixture, selectFixture] = useState('')
  const statuses = { hold: 'Hold', reject: 'Reject', review_for_expansion: 'Review for expansion' }
  const fixtures = [...new Set(Object.values(review.arms).flatMap(arm => Object.keys(arm.by_fixture)))].sort()
  const fixture = fixtures.includes(selectedFixture) ? selectedFixture : ''
  return <section className="panel benchmark">
    <div className="section-head">
      <div><h2>Release review</h2><small>{review.batch}{review.model && ` · ${review.model}`}</small></div>
      <span className={`pill ${review.recommendation === 'review_for_expansion' ? 'ok' :
        review.recommendation === 'reject' ? 'blocked' : 'invalid'}`}>
        {statuses[review.recommendation]}
      </span>
    </div>
    <div className="evidence-body">
      {review.reasons.length > 0 && <ul>{review.reasons.map(reason => <li key={reason}>{reason}</li>)}</ul>}
      <label className="result-filter">Compare task success
        <select value={fixture} onChange={event => selectFixture(event.target.value)}>
          <option value="">All fixtures</option>
          {fixtures.map(id => <option key={id} value={id}>{cases[id] || id}</option>)}
        </select>
      </label>
      <div className="success-bars" aria-live="polite">
        {Object.entries(review.arms).map(([arm, stats]) => {
          const result = fixture ? stats.by_fixture[fixture] : stats
          const evaluated = result?.evaluated ?? 0
          const passed = result?.successful ?? 0
          return <div className="success-bar" key={arm}>
            <strong>{labels[arm] || arm}</strong>
            <span>{evaluated ? `${passed} / ${evaluated} passed` : 'No evaluated runs'}</span>
            <div className="bar-track" aria-hidden="true"><i style={{ width: `${evaluated ? passed / evaluated * 100 : 0}%` }} /></div>
          </div>
        })}
      </div>
      <details className="raw totals"><summary>Batch totals and attribution</summary>
      <div className="compare-grid">{Object.entries(review.arms).map(([arm, stats]) => <div key={arm}>
        <strong>{labels[arm]}</strong>
        {showAttribution && review.flagsmith_reconciled && review.expected_flagsmith_counts?.[arm] && <span>
          {review.expected_flagsmith_counts[arm].exposures} exposures · {review.expected_flagsmith_counts[arm].conversions} conversions
        </span>}
        <span>{stats.successful} / {stats.evaluated} passed · {stats.blocked_calls} blocked calls</span>
        {review.mode === 'live' && <span>
          {ms(stats.median_latency_ms)} median · {stats.median_tokens ?? '—'} median tokens
          {' · '}{stats.token_coverage}/{stats.evaluated} measured
        </span>}
      </div>)}</div>
      </details>
      <div className="review-foot">
        {review.fixture_adjusted_gain_points != null && <span>
          <strong>{review.fixture_adjusted_gain_points > 0 ? '+' : ''}{review.fixture_adjusted_gain_points.toFixed(1)} pp</strong>
          {' '}fixture-adjusted gain
        </span>}
        {review.mode === 'live' && <span className={`pill ${review.flagsmith_reconciled ? 'ok' : 'invalid'}`}>
          {review.flagsmith_reconciled ? 'Flagsmith reconciled' : 'Reconciliation pending'}
        </span>}
      </div>
      {fixtures.length > 0 && <details className="raw">
        <summary>Fixture results</summary>
        <table className="fixture-table">
          <thead><tr><th scope="col">Fixture</th><th scope="col">Baseline passed</th><th scope="col">Candidate passed</th></tr></thead>
          <tbody>{fixtures.map(fixture => <tr key={fixture}>
            <th scope="row">{cases[fixture] || fixture}</th>
            {['direct', 'plan_validate'].map(arm => {
              const result = review.arms[arm].by_fixture[fixture]
              return <td key={arm} className={result && result.successful < result.evaluated ? 'regression' : ''}>
                {result ? `${result.successful} / ${result.evaluated}` : '—'}
              </td>
            })}
          </tr>)}</tbody>
        </table>
      </details>}
    </div>
  </section>
}
