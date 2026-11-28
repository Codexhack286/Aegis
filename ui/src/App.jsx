import React, { useEffect, useState } from 'react'
import RegressionReview from './components/RegressionReview.jsx'
import Guide from './pages/Guide.jsx'
import RunWorkspace from './pages/RunWorkspace.jsx'
import Start from './pages/Start.jsx'

const pages = {
  overview: ['Evaluate your agent', 'Check what your agent did, what changed, and whether a new strategy is ready for rollout.'],
  runs: ['Run workspace', 'Choose a sample task or review runs from your configured project.'],
  live: ['Run workspace', 'Choose a sample task or review runs from your configured project.'],
  guide: ['Guide', 'Find setup instructions and learn how to read your run evidence.'],
  connect: ['Connect your agent', 'Use your project’s own tasks, tools, and policy checks.'],
  compare: ['Review a comparison', 'Open a comparison you generated to see which tasks passed and which checks failed.'],
}

function readPage() {
  const page = window.location.hash.slice(1)
  return page === 'reports' ? 'compare' : Object.hasOwn(pages, page) ? page : 'overview'
}

export default function App() {
  const [page, setPage] = useState(readPage)
  const [opened, setOpened] = useState(() => new Set([readPage()]))
  useEffect(() => {
    function navigate() {
      const next = readPage()
      setPage(next)
      setOpened(previous => previous.has(next) ? previous : new Set([...previous, next]))
      window.scrollTo(0, 0)
    }
    window.addEventListener('hashchange', navigate)
    return () => window.removeEventListener('hashchange', navigate)
  }, [])
  const [title, description] = pages[page]

  return <>
    <header className="top"><div className="shell top-inner">
      <a className="brand" href="#overview" aria-label="Aegis overview"><span className="logo"><i /></span>Aegis</a>
      <nav className="navigation" aria-label="Main navigation">
        {Object.entries({ overview: 'Start', runs: 'Runs', guide: 'Guide' }).map(([key, label]) => {
          const current = page === key || (key === 'runs' && ['live', 'compare'].includes(page)) || (key === 'guide' && page === 'connect')
          return <a key={key} href={'#' + key} aria-current={current ? 'page' : undefined}>{label}</a>
        })}
      </nav>
    </div></header>
    <main className="shell">
      <div className="hero">
        <div><h1>{title}</h1><p>{description}</p></div>
      </div>
      {page === 'overview' && <Start />}
      {opened.has('runs') && <div hidden={page !== 'runs'}><RunWorkspace mode="offline" /></div>}
      {opened.has('live') && <div hidden={page !== 'live'}><RunWorkspace mode="live" /></div>}
      {opened.has('compare') && <div hidden={page !== 'compare'}><RegressionReview /></div>}
      {(page === 'guide' || page === 'connect') && <Guide connectionOnly={page === 'connect'} />}
    </main>
  </>
}
