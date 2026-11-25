import { useEffect, useMemo, useState } from 'react'
import { MapContainer, TileLayer, CircleMarker, Tooltip } from 'react-leaflet'
import 'leaflet/dist/leaflet.css'
import { GENESIS, HQ, PLACES, placeName, seal, verifyHash, verifyLinkAt } from '../lib/events'
import { ALERT_AT, LABEL, clock, isAlert, logDecision, riskColor, useEvents } from '../lib/store'
import { checkRun, loadIndex, loadRun } from '../lib/recorded'

export const PAGES = [
  ['#/command', 'Command centre', 'The live map, full screen, with layers'],
  ['#/dispatch', 'Dispatch', '112 calls, nearest drone, straight-line ETA'],
  ['#/officer', 'Officer app', 'The alert as a patrol officer sees it'],
  ['#/incident', 'Incident', 'One hotspot: risk over time, alerts, evidence'],
  ['#/incidents', 'Recorded incidents', 'Two real pipeline runs: scrub, snapshots, verify'],
  ['#/evidence', 'Evidence', 'Verify, tamper with a copy, vault and audit'],
  ['#/privacy-lab', 'Privacy lab', 'Face size from the air, watchlist threshold'],
  ['#/campus', 'Campus test', 'Face matching on real footage, with the numbers'],
  ['#/analytics', 'Accuracy', 'How well the crowd count holds up'],
  ['#/results', 'Results', 'Every headline number with its source'],
  ['#/privacy', 'Privacy & legal', 'What is kept, what never is'],
]

const short = h => (h ? `${h.slice(0, 8)}…${h.slice(-4)}` : '—')
const km = (a, b) => {
  const R = 6371, rad = x => (x * Math.PI) / 180
  const dLat = rad(b[0] - a[0]), dLng = rad(b[1] - a[1])
  const h = Math.sin(dLat / 2) ** 2 + Math.cos(rad(a[0])) * Math.cos(rad(b[0])) * Math.sin(dLng / 2) ** 2
  return 2 * R * Math.asin(Math.sqrt(h))
}
export const SourceTag = ({ source }) => (
  <span className="mono caps muted">{source === 'live' ? 'Live backend stream' : 'Simulated stream · real SHA-256 chain'}</span>
)

// Verify every event: its own hash, and its link to the event before it.
export async function verifyChain(list) {
  return Promise.all(list.map(async (e, i) => ({
    e,
    hash: await verifyHash(e),
    link: verifyLinkAt(list, i), // null: earlier events not loaded
  })))
}

export function PageShell({ route, children }) {
  const page = PAGES.find(([h]) => h === route)
  return (
    <main id="main" className="page">
      <header className="page-head">
        <p className="mono caps muted"><a href="#top">Kavrix</a> / Pages / {page?.[1]}</p>
        <nav className="page-tabs" aria-label="Pages">
          {PAGES.map(([h, label]) => <a key={h} href={h} className={h === route ? 'on' : ''} aria-current={h === route ? 'page' : undefined}>{label}</a>)}
        </nav>
      </header>
      {children}
    </main>
  )
}

export function Intro({ title, children }) {
  return (
    <div className="section-head page-intro">
      <h1>{title}</h1>
      <p>{children}</p>
    </div>
  )
}

/* ---------------- Evidence ---------------- */
export function EvidencePage() {
  const { events, source, audit } = useEvents()
  const [tab, setTab] = useState('verify')
  const [result, setResult] = useState(null) // { label, rows, tampered }
  const [busy, setBusy] = useState(false)
  const [lookup, setLookup] = useState('')

  const run = async mode => {
    setBusy(true)
    const list = events.map(e => ({ ...e })) // freeze a copy; the live stream keeps moving
    let tampered = null
    if (mode !== 'original' && list.length > 2) {
      // downplay the most serious event (not the first, so its link is still checkable)
      const i = list.slice(1).reduce((best, e, k) => (e.risk > list[best].risk ? k + 1 : best), 1), before = list[i].risk
      list[i] = { ...list[i], risk: Math.max(0, Math.round((before - 50) * 10) / 10) }
      if (mode === 'rehash') list[i] = await seal(list[i], list[i].prev_hash) // attacker also recomputes the hash
      tampered = { i, before, after: list[i].risk, mode }
    }
    setResult({ rows: await verifyChain(list), tampered, mode })
    setBusy(false)
  }

  const rows = result?.rows ?? []
  const firstBad = rows.findIndex(r => !r.hash || r.link === false)
  const found = lookup.trim() && events.find(e => e.id.startsWith(lookup.trim()))
  const [lookupRes, setLookupRes] = useState(null)
  useEffect(() => {
    if (!found) return setLookupRes(null)
    const i = events.indexOf(found)
    verifyHash(found).then(hash => setLookupRes({ hash, link: verifyLinkAt(events, i) }))
  }, [found, events])

  return (
    <>
      <Intro title="Evidence Verification">
        Every event carries a SHA-256 hash that also covers the previous event’s hash. Verify the whole chain, then tamper with a copy and watch the check catch it. The original is never changed.
      </Intro>
      <div className="ev-tabs" role="tablist" aria-label="Evidence views">
        {[['verify', 'Verify'], ['vault', 'Vault'], ['audit', `Audit · ${audit.length}`]].map(([k, l]) => (
          <button key={k} role="tab" aria-selected={tab === k} className={tab === k ? 'on' : ''} onClick={() => setTab(k)}>{l}</button>
        ))}
      </div>
      {tab === 'vault' && <Vault events={events} source={source} />}
      {tab === 'audit' && <Audit audit={audit} />}
      {tab === 'verify' && (<>
      <div className="ev-bar card">
        <div><strong>{events.length}</strong> events in the chain · <SourceTag source={source} /></div>
        <div className="ev-actions">
          <button className="btn dark" disabled={busy || !events.length} onClick={() => run('original')}>Verify this incident</button>
          <button className="btn" disabled={busy || events.length < 3} onClick={() => run('edit')}>Tamper with a copy</button>
          <button className="btn" disabled={busy || events.length < 3} onClick={() => run('rehash')}>Tamper and re-hash</button>
        </div>
      </div>

      {result && (
        <div className={`ev-verdict ${firstBad < 0 ? 'ok' : 'fail'}`} role="status">
          {firstBad < 0
            ? <>✓ All {rows.length} events verified. Every hash matches and every link points to the event before it.</>
            : <>✗ Chain broken at event #{firstBad + 1}: {rows[firstBad].hash ? 'its link to the previous event does not match' : 'its contents no longer match its hash'}.</>}
          {result.tampered && (
            <span className="ev-why">
              {result.tampered.mode === 'edit'
                ? ` In this copy, event #${result.tampered.i + 1}'s risk was changed from ${result.tampered.before} to ${result.tampered.after}, as if someone downplayed the alert.`
                : ` In this copy, event #${result.tampered.i + 1}'s risk was changed from ${result.tampered.before} to ${result.tampered.after} and its hash recomputed to hide it, so the next event's link breaks instead.`}
            </span>
          )}
        </div>
      )}

      {rows.length > 0 && (
        <div className="ev-table card">
          <table>
            <thead><tr><th>#</th><th>Time</th><th>Type</th><th>Risk</th><th>prev_hash</th><th>evidence_hash</th><th>Hash</th><th>Link</th></tr></thead>
            <tbody>
              {rows.map((r, i) => (
                <tr key={r.e.id} className={(!r.hash || r.link === false) ? 'bad' : result.tampered?.i === i ? 'touched' : ''}>
                  <td>{i + 1}</td><td>{clock(r.e)}</td><td>{r.e.type}</td><td>{r.e.risk}</td>
                  <td className="mono">{short(r.e.prev_hash)}</td><td className="mono">{short(r.e.evidence_hash)}</td>
                  <td>{r.hash ? '✓' : '✗'}</td><td>{r.link === null ? '—' : r.link ? '✓' : '✗'}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}

      <div className="ev-lookup card">
        <label htmlFor="evid"><strong>Check one event</strong> <span className="muted">· paste or pick an event id</span></label>
        <input id="evid" list="evids" value={lookup} onChange={e => setLookup(e.target.value)} placeholder="e.g. 3f2a9c1e…" className="mono" />
        <datalist id="evids">{events.slice(-30).map(e => <option key={e.id} value={e.id}>{LABEL[e.type]} · {clock(e)}</option>)}</datalist>
        {lookup.trim() && !found && <p className="muted">No event with that id in the loaded chain.</p>}
        {found && lookupRes && (
          <ul className="ev-checks">
            <li className={lookupRes.hash ? 'ok' : 'fail'}>{lookupRes.hash ? '✓' : '✗'} Hash: recomputed SHA-256 {lookupRes.hash ? 'matches' : 'does not match'} {short(found.evidence_hash)}</li>
            <li className={lookupRes.link === false ? 'fail' : 'ok'}>{lookupRes.link === null ? '—' : lookupRes.link ? '✓' : '✗'} Chain: {lookupRes.link === null ? 'first loaded event' : lookupRes.link ? 'links to the event before it' : 'does not link to the event before it'}</li>
            <li className="na">— Snapshot: simulated events have no snapshot; <a href="#/incidents">recorded incidents</a> check theirs</li>
          </ul>
        )}
      </div>
      </>)}
    </>
  )
}

// One row per evidence chain: the live session now; the recorded incidents once their files are added.
function Vault({ events, source }) {
  const [res, setRes] = useState(null)
  const check = async () => {
    const t = performance.now(), rows = await verifyChain(events.slice())
    setRes({ ok: rows.every(r => r.hash && r.link !== false), n: rows.length, ms: performance.now() - t })
  }
  const [recorded, setRecorded] = useState(null) // null loading, false failed
  useEffect(() => {
    loadIndex().then(l => Promise.all(l.map(async inc => ({ inc, run: await loadRun(inc), res: null })))).then(setRecorded).catch(() => setRecorded(false))
  }, [])
  const checkOne = async id => {
    const row = recorded.find(r => r.inc.id === id), res = await checkRun(row.run)
    setRecorded(rs => rs.map(r => (r.inc.id === id ? { ...r, res } : r)))
  }
  return (
    <div className="card ev-vault">
      <table className="an-table">
        <thead><tr><th>Chain</th><th>Events</th><th>Chain head (latest hash)</th><th>Verify</th></tr></thead>
        <tbody>
          <tr>
            <td><b>Live session</b><br /><small className="muted">{source === 'live' ? 'backend stream' : 'simulated stream'}</small></td>
            <td>{events.length}</td>
            <td className="mono">{short(events.at(-1)?.evidence_hash)}</td>
            <td>
              <button className="chip dark" onClick={check} disabled={!events.length}>Verify now</button>
              {res && <span className={res.ok ? 'ok' : 'fail'}> {res.ok ? '✓' : '✗'} {res.n} events · {res.ms.toFixed(1)} ms</span>}
            </td>
          </tr>
          {(recorded ?? []).map(({ inc, run, res }) => (
            <tr key={inc.id}>
              <td><b>{inc.title}</b><br /><small className="muted">recorded run · <a href="#/incidents">open ↗</a></small></td>
              <td>{run.summary.chain_events}</td>
              <td className="mono">{short(run.summary.chain_head)}</td>
              <td>
                <button className="chip dark" onClick={() => checkOne(inc.id)}>Verify now</button>
                {res && <span className={res.ok ? 'ok' : 'fail'}> {res.ok ? '✓' : '✗'} {res.events} events · {res.snapshotsChecked} snapshots · {res.ms.toFixed(0)} ms</span>}
              </td>
            </tr>
          ))}
          {recorded === null && <tr className="pending"><td colSpan={4} className="muted">Loading recorded runs…</td></tr>}
          {recorded === false && <tr className="pending"><td colSpan={4} className="muted">Recorded run files could not be loaded.</td></tr>}
        </tbody>
      </table>
    </div>
  )
}

function Audit({ audit }) {
  if (!audit.length) return <div className="card ev-vault"><p className="muted">No decisions yet. Confirm or Reject an alert in the officer app, the command view or the privacy lab, and it appears here.</p></div>
  return (
    <div className="card ev-vault">
      <table className="an-table">
        <thead><tr><th>Time</th><th>Decision</th><th>By</th><th>Alert</th><th>Where</th><th>Event hash</th></tr></thead>
        <tbody>
          {audit.slice().reverse().map((a, i) => (
            <tr key={i}>
              <td className="mono">{a.at.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit', second: '2-digit' })}</td>
              <td className={a.d === 'confirm' ? 'ok' : 'fail'}>{a.d === 'confirm' ? '✓ Confirm' : '✕ Reject'}</td>
              <td>{a.who}</td>
              <td>{LABEL[a.type]} · {a.type === 'watchlist_match' ? `${a.risk}%` : `risk ${a.risk}`}</td>
              <td>{placeName(a)}</td>
              <td className="mono">{short(a.hash)}</td>
            </tr>
          ))}
        </tbody>
      </table>
      <p className="muted small">Decisions are kept in this browser session. Writing them into the hash chain is planned.</p>
    </div>
  )
}

/* ---------------- Incident ---------------- */
function RiskChart({ points }) {
  const W = 640, H = 220, pad = 28
  const xs = i => pad + (i * (W - pad * 2)) / Math.max(1, points.length - 1)
  const ys = r => H - pad - (r / 100) * (H - pad * 2)
  const line = points.map((p, i) => `${xs(i)},${ys(p.risk)}`).join(' ')
  return (
    <svg viewBox={`0 0 ${W} ${H}`} className="risk-chart" role="img" aria-label="Crowd risk over time">
      {[0, 25, 50, 75, 100].map(r => <g key={r}><line x1={pad} x2={W - pad} y1={ys(r)} y2={ys(r)} className="grid" /><text x={4} y={ys(r) + 4} className="svg-mono dim">{r}</text></g>)}
      <line x1={pad} x2={W - pad} y1={ys(ALERT_AT)} y2={ys(ALERT_AT)} className="thresh" />
      <text x={W - pad} y={ys(ALERT_AT) - 6} textAnchor="end" className="svg-mono thresh-label">HIGH ≥ {ALERT_AT}</text>
      <polyline points={line} className="line" />
      {points.map((p, i) => <circle key={p.id} cx={xs(i)} cy={ys(p.risk)} r={3.2} fill={riskColor(p.risk)} />)}
    </svg>
  )
}

export function IncidentPage() {
  const { events, source } = useEvents()
  const counts = useMemo(() => Object.fromEntries(PLACES.map(p => [p.name, events.filter(e => placeName(e) === p.name).length])), [events])
  const [spot, setSpot] = useState('Tank Bund')
  const place = PLACES.find(p => p.name === spot)
  const here = events.filter(e => placeName(e) === spot)
  const points = here.filter(e => e.type === 'crowd_risk').slice(-40)
  const alerts = here.filter(e => isAlert(e) || e.type === 'dispatch').slice(-8).reverse()
  const peak = Math.max(0, ...points.map(p => p.risk))
  const [check, setCheck] = useState(null)
  const verify = async () => {
    const ok = await Promise.all(here.map(e => verifyHash(e)))
    setCheck({ ok: ok.filter(Boolean).length, total: here.length })
  }

  return (
    <>
      <Intro title="Incident Detail">
        One hotspot at a time: how crowd risk moved, which alerts fired, and whether the evidence behind them still verifies.
      </Intro>
      <div className="inc-spots" role="tablist" aria-label="Hotspots">
        {PLACES.map(p => (
          <button key={p.name} role="tab" aria-selected={p.name === spot} className={p.name === spot ? 'on' : ''} onClick={() => { setSpot(p.name); setCheck(null) }}>
            {p.name} <small>{counts[p.name]}</small>
          </button>
        ))}
      </div>
      <div className="inc-grid">
        <div className="card inc-chart">
          <div className="inc-head"><strong>Crowd risk over time</strong><SourceTag source={source} /></div>
          {points.length > 1 ? <RiskChart points={points} /> : <p className="muted pad">Waiting for crowd-risk events at {spot}…</p>}
          <div className="inc-stats">
            <span>Peak <b style={{ color: riskColor(peak) }}>{peak}</b></span>
            <span>Readings <b>{points.length}</b></span>
            <span>Alerts <b>{here.filter(isAlert).length}</b></span>
          </div>
        </div>
        <div className="card inc-map">
          <MapContainer key={spot} center={[place.lat, place.lng]} zoom={15} zoomControl={false} scrollWheelZoom={false} style={{ height: '100%' }}>
            <TileLayer url="https://tile.openstreetmap.org/{z}/{x}/{y}.png" attribution="&copy; OpenStreetMap contributors" />
            <CircleMarker center={[place.lat, place.lng]} radius={34} pathOptions={{ color: riskColor(peak), fillColor: riskColor(peak), fillOpacity: 0.25, weight: 1.5 }}>
              <Tooltip permanent direction="top">{spot}</Tooltip>
            </CircleMarker>
          </MapContainer>
          <p className="inc-coords mono">{place.lat.toFixed(4)}°N {place.lng.toFixed(4)}°E · {km(HQ, [place.lat, place.lng]).toFixed(1)} km from HQ</p>
        </div>
        <div className="card inc-snap">
          <strong>Annotated snapshot</strong>
          <div className="snap-empty">
            <p>Simulated events have no camera frames. Annotated snapshots with detection boxes and the hotspot marker are in the AI pipeline’s recorded runs.</p>
            <a href="#/incidents" className="link-arrow">Open recorded incidents ↗</a>
          </div>
        </div>
        <div className="card inc-alerts">
          <div className="inc-head"><strong>Alerts at {spot}</strong><span className="mono caps muted">risk ≥ {ALERT_AT} or watchlist</span></div>
          {alerts.length === 0 && <p className="muted">No alerts here yet.</p>}
          <ul>
            {alerts.map(a => (
              <li key={a.id}>
                <i style={{ background: a.type === 'watchlist_match' ? 'var(--ink)' : riskColor(a.risk) }} />
                <span><strong>{LABEL[a.type]}</strong> · {clock(a)}</span>
                <span className="mono">{a.type === 'dispatch' ? 'D-01' : `${a.risk}${a.type === 'watchlist_match' ? '%' : ''}`}</span>
              </li>
            ))}
          </ul>
          <div className="inc-verify">
            <button className="btn" onClick={verify} disabled={!here.length}>Verify {here.length} events</button>
            {check && <span className={check.ok === check.total ? 'ok' : 'fail'}>{check.ok === check.total ? '✓' : '✗'} {check.ok}/{check.total} hashes match</span>}
            <a href="#/evidence" className="link-arrow">Full chain check ↗</a>
          </div>
        </div>
      </div>
    </>
  )
}

/* ---------------- Officer ---------------- */
export function OfficerPage() {
  const { events, audit } = useEvents()
  const alerts = events.filter(isAlert).slice(-10)
  const decisions = Object.fromEntries(audit.map(a => [a.id, a]))
  const [check, setCheck] = useState({})
  const [last, setLast] = useState(null)
  // Pin the shown alert until the officer decides: the card must not change under their finger as new alerts arrive.
  const [latest, setLatest] = useState(null)
  useEffect(() => {
    if (!latest || decisions[latest.id]) setLatest(alerts.filter(a => !decisions[a.id]).at(-1) ?? null)
  }, [alerts, decisions, latest])
  const pending = alerts.filter(a => !decisions[a.id] && a.id !== latest?.id)
  useEffect(() => { if (latest) verifyHash(latest).then(ok => setCheck(c => ({ ...c, [latest.id]: ok }))) }, [latest])
  const decide = (a, d) => {
    const at = new Date().toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' })
    logDecision(a, d, 'Officer · Unit 14')
    setLast({ d, at, what: `${LABEL[a.type]} · ${placeName(a)}` })
  }

  return (
    <>
      <Intro title="Officer App">
        What a patrol officer sees on their phone when an alert fires: the hotspot, the evidence check, and a Confirm or Reject that is logged. Nothing is acted on without this tap.
      </Intro>
      <div className="phone-wrap">
        <div className="phone-frame">
          <div className="ph-top"><span className="mono caps">Patrol · Unit 14</span><span className="mono caps"><i className="live-dot" /> On duty</span></div>
          {last && <p className="ph-done" key={last.at + last.what}>{last.d === 'confirm' ? '✓ Confirmed' : '✕ Rejected'} · {last.what} · {last.at} · logged</p>}
          {!latest && <div className="ph-empty"><strong>Standing by</strong><p className="muted">No alerts waiting. New alerts appear here automatically.</p></div>}
          {pending.length > 0 && <p className="ph-queue mono caps">{pending.length} more waiting</p>}
          {latest && (() => {
            const place = [latest.lat, latest.lng]
            return (
              <article className="ph-alert" key={latest.id}>
                <span className="ph-level" style={{ background: latest.type === 'watchlist_match' ? 'var(--ink)' : riskColor(latest.risk) }}>
                  {latest.type === 'watchlist_match' ? 'WATCHLIST' : 'HIGH RISK'}
                </span>
                <h2>{latest.type === 'watchlist_match' ? `Possible watchlist match · ${latest.risk}%` : `Crowd risk ${latest.risk} at ${placeName(latest)}`}</h2>
                <p className="muted">{placeName(latest)} · {clock(latest)} · {km(HQ, place).toFixed(1)} km away</p>
                <div className="ph-map">
                  <MapContainer key={latest.id} center={place} zoom={15} zoomControl={false} dragging={false} scrollWheelZoom={false} doubleClickZoom={false} attributionControl={false} style={{ height: '100%' }}>
                    <TileLayer url="https://tile.openstreetmap.org/{z}/{x}/{y}.png" />
                    <CircleMarker center={place} radius={22} pathOptions={{ color: '#e5484d', fillColor: '#e5484d', fillOpacity: 0.25, weight: 2 }} />
                  </MapContainer>
                </div>
                <p className={`ph-check ${check[latest.id] === false ? 'fail' : ''}`}>
                  {check[latest.id] == null ? 'Checking evidence…' : check[latest.id] ? `✓ Evidence hash matches · ${short(latest.evidence_hash)}` : '✗ Evidence hash does not match'}
                </p>
                <div className="ph-actions">
                  <button className="ph-btn reject" onClick={() => decide(latest, 'reject')}>Reject</button>
                  <button className="ph-btn confirm" onClick={() => decide(latest, 'confirm')}>Confirm</button>
                </div>
                <a className="ph-maps" href={`https://www.google.com/maps/dir/?api=1&destination=${latest.lat},${latest.lng}`} target="_blank" rel="noreferrer">Navigate in Google Maps ↗</a>
              </article>
            )
          })()}
          {Object.keys(decisions).length > 0 && (
            <ul className="ph-history" aria-label="Decided alerts">
              {alerts.filter(a => decisions[a.id]).slice(-5).reverse().map(a => (
                <li key={a.id}><span>{LABEL[a.type]} · {placeName(a)}</span><span className="mono">{decisions[a.id] ? (decisions[a.id].d === 'confirm' ? '✓' : '✕') : clock(a)}</span></li>
              ))}
            </ul>
          )}
        </div>
      </div>
    </>
  )
}

/* ---------------- Analytics ---------------- */
function Chart({ file, title, caption }) {
  const [missing, setMissing] = useState(false)
  return (
    <figure className="an-chart card">
      {missing
        ? <div className="snap-empty"><p>Chart file not added yet</p><p className="mono muted">public/data/{file}</p></div>
        : <img src={`/data/${file}`} alt={title} loading="lazy" onError={() => setMissing(true)} />}
      <figcaption><strong>{title}</strong>{caption && <span>{caption}</span>}</figcaption>
    </figure>
  )
}

export function AnalyticsPage() {
  const [d, setD] = useState(null)
  const [err, setErr] = useState(false)
  useEffect(() => {
    fetch('/data/accuracy.json').then(r => (r.ok ? r.json() : Promise.reject())).then(setD).catch(() => setErr(true))
  }, [])
  if (err) return <><Intro title="Accuracy">The accuracy data file is missing.</Intro><p className="muted">Expected at public/data/accuracy.json.</p></>
  if (!d) return <Intro title="Accuracy">Loading accuracy data…</Intro>
  const h = d.headline
  return (
    <>
      <Intro title={d.title}>{h.plain_english}</Intro>
      <div className="an-head">
        <div className="metric-big card"><span className="pixel">{Math.round(h.median_detected_over_true * 100)}%</span><strong>of people found</strong><small>median detected ÷ true, {h.frames} frames</small></div>
        <div className="metric-big card"><span className="pixel">{Math.round(h.mean_absolute_error_pct)}%</span><strong>typical error</strong><small>mean absolute error, {h.mean_absolute_error_people} people</small></div>
        <div className="metric-big card"><span className="pixel">{h.rank_correlation_counts.toFixed(2)}</span><strong>rank correlation</strong><small>orders scenes by crowd size (density {h.rank_correlation_density.toFixed(2)})</small></div>
      </div>
      <p className="an-dataset mono">Dataset · {h.dataset} · {h.sequences} sequences</p>

      <div className="an-grid">
        <section className="card an-block">
          <h2>By crowd size</h2>
          <p className="muted">Detected ÷ true people. 1.00 means a perfect count; below it undercounts.</p>
          {d.by_density.map(b => (
            <div key={b.true_people} className="an-bar">
              <span>{b.true_people} <small className="muted">· {b.frames} frames</small></span>
              <div className="an-track"><b style={{ width: `${Math.min(100, (b.detected_over_true / 1.3) * 100)}%` }} /><i style={{ left: `${100 / 1.3}%` }} /></div>
              <span className="mono">{b.detected_over_true.toFixed(2)}</span>
            </div>
          ))}
        </section>
        <section className="card an-block">
          <h2>Settings compared</h2>
          <table className="an-table">
            <thead><tr><th>Setting</th><th>Error</th><th>MAE</th><th>Found</th><th>Rank</th></tr></thead>
            <tbody>
              {d.settings_compared.map(s => (
                <tr key={s.setting} className={s.best ? 'best' : ''}>
                  <td>{s.setting}{s.note && <small className="muted"> · {s.note}</small>}</td>
                  <td>{s.error_pct}%</td><td>{s.mae}</td><td>{Math.round(s.median_detected_over_true * 100)}%</td><td>{s.rank_correlation}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </section>
      </div>

      <h2 className="an-h2">Validation runs</h2>
      <div className="an-runs">
        {d.validation_runs.map(v => (
          <article key={v.name} className="card an-run">
            <h3>{v.name}</h3>
            <p className="mono caps muted">{v.detector}</p>
            <div className="an-run-stats">
              <span>Correlation <b>{v.correlation ?? 'n/a'}</b></span>
              <span>MAE <b>{v.mean_absolute_error_people} people</b></span>
              <span>Found <b>{Math.round(v.detected_over_true * 100)}%</b></span>
            </div>
            <p className="muted">{v.note}</p>
            <Chart file={v.chart} title="Detected vs ground truth" />
          </article>
        ))}
      </div>

      <h2 className="an-h2">Charts</h2>
      <div className="an-charts">{d.charts.map(c => <Chart key={c.file} {...c} />)}</div>

      <div className="an-grid">
        <section className="card an-block">
          <h2>Limits</h2>
          <ul className="an-list">{d.limits.map(l => <li key={l}>{l}</li>)}</ul>
        </section>
        <section className="card an-block">
          <h2>Sources and credits</h2>
          <ul className="an-list">
            {d.sources.map(s => (
              <li key={s.name}>
                <a href={s.url} target="_blank" rel="noreferrer">{s.name} ↗</a>
                {s.by && <span className="muted"> · {s.by}</span>}
                <br /><small className="muted">Licence: {s.licence}</small>
              </li>
            ))}
          </ul>
        </section>
      </div>
    </>
  )
}

/* ---------------- Privacy & legal ---------------- */
const KEPT = [
  ['Alert events', 'type, hotspot coordinates, risk score, time, hashes'],
  ['Annotated snapshots', 'only the frames behind an alert, for review'],
  ['Watchlist matches', 'event, confidence and the officer’s decision'],
  ['Officer decisions', 'confirm or reject, with the time'],
]
const NEVER = [
  ['Faces of non-matches', 'blurred before display, never stored'],
  ['Identity of the crowd', 'crowd risk uses person counts, not identities'],
  ['Continuous raw video', 'the demo uses recorded public clips only'],
  ['Location of individuals', 'only the hotspot is recorded, not people'],
]

export function PrivacyPage() {
  return (
    <>
      <Intro title="Privacy and Legal">
        How Kavrix limits what it sees, keeps and does. This describes the prototype as built and the rules we propose before any real deployment.
      </Intro>
      <div className="pv-grid">
        <section className="card pv-block">
          <h2>Kept</h2>
          <dl className="kv">{KEPT.map(([k, v]) => <div key={k}><dt>{k}</dt><dd>{v}</dd></div>)}</dl>
        </section>
        <section className="card pv-block">
          <h2>Never kept</h2>
          <dl className="kv">{NEVER.map(([k, v]) => <div key={k}><dt>{k}</dt><dd>{v}</dd></div>)}</dl>
        </section>
      </div>
      <div className="pv-cols">
        <section>
          <span className="pixel pv-n">01</span>
          <h3>Consent</h3>
          <p>The demo watchlist uses only consented photos of our own team. In deployment, a watchlist would hold only people named in a lawful order, such as missing persons or wanted offenders.</p>
        </section>
        <section>
          <span className="pixel pv-n">02</span>
          <h3>Data minimisation</h3>
          <p>Crowd risk needs counts, not identities, so it never looks at who people are. Face matching is limited to the watchlist and everyone else is blurred.</p>
        </section>
        <section>
          <span className="pixel pv-n">03</span>
          <h3>Retention</h3>
          <p>This website keeps events only in the browser’s memory; they are gone on reload. For deployment we propose a fixed retention period for alert evidence, set with police legal advisers, with automatic deletion after it.</p>
        </section>
        <section>
          <span className="pixel pv-n">04</span>
          <h3>Human oversight</h3>
          <p>Kavrix never acts on its own. Every alert needs an officer’s Confirm or Reject, and that decision is logged with its time. Adding decisions to the hash chain is planned.</p>
        </section>
        <section>
          <span className="pixel pv-n">05</span>
          <h3>Evidence integrity</h3>
          <p>Events are SHA-256 hashed and chained, so any later edit is detectable. Digital signatures on top of the chain are the next step before evidence is used in court.</p>
        </section>
        <section>
          <span className="pixel pv-n">06</span>
          <h3>Legal review</h3>
          <p>Before a field trial, the design needs review against India’s Digital Personal Data Protection Act, 2023, drone rules and evidence law, with the police department’s legal team.</p>
        </section>
      </div>
      <p className="pv-note mono caps muted">Prototype statement · Team Haven · CipherCop 2026 · not legal advice</p>
    </>
  )
}
