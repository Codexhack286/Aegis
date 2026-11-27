import React from 'react'
import { cases, labels, ms } from '../presentation.js'

export default function BenchmarkReview({ review }) {
  const statuses = { hold: 'Hold', reject: 'Reject', review_for_expansion: 'Review for expansion' }
  const fixtures = [...new Set(Object.values(review.arms).flatMap(arm => Object.keys(arm.by_fixture)))].sort()
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
      <div className="compare-grid">{Object.entries(review.arms).map(([arm, stats]) => <div key={arm}>
        <strong>{labels[arm]}</strong>
        <span>{stats.successful} / {stats.evaluated} passed · {stats.blocked_calls} blocked calls</span>
        {review.mode === 'live' && <span>
          {ms(stats.median_latency_ms)} median · {stats.median_tokens ?? '—'} median tokens
          {' · '}{stats.token_coverage}/{stats.evaluated} measured
        </span>}
      </div>)}</div>
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
