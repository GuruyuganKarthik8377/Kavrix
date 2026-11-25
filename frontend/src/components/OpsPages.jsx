import { useEffect, useRef, useState } from 'react'
import { MapContainer, TileLayer, CircleMarker, Marker, Tooltip, useMapEvents } from 'react-leaflet'
import L from 'leaflet'
import 'leaflet/dist/leaflet.css'
import CommandView from './CommandView'
import { Intro, SourceTag, verifyChain } from './Pages'
import { api, placeName } from '../lib/events'
import { FLEET, ON_SCENE_S, SPEED_MS, TIERS, assignFleet, faceView, fleetPositions } from '../lib/sim'
import { clock, decisionOf, logDecision, raiseCall, useEvents } from '../lib/store'

const mmss = s => `${Math.floor(s / 60)}:${String(Math.round(s % 60)).padStart(2, '0')}`
const ICONS = {}
const iconFor = id => (ICONS[id] ||= L.divIcon({ className: 'drone-pin', html: `<span>${id}</span>`, iconSize: [46, 22] }))
// Live mode: the backend owns the fleet (docks, speed, which drone is nearest), so the page shows exactly what it reports.
const LIVE_SPEED_MS = 18 // backend/fleet.py CRUISE_MPS
const LIVE_STATE = { idle: 'At base', on_station: 'On station', en_route: 'En route', on_scene: 'On scene', returning: 'Returning' }
const LIVE_CALL_STATE = { en_route: 'En route', on_scene: 'On scene', closed: 'Closed' }
function useBackendFleet(active) {
  const [data, setData] = useState(null)
  useEffect(() => {
    if (!active) { setData(null); return undefined }
    let stop = false
    const pull = () => Promise.all([api('/drones'), api('/incidents')])
      .then(([drones, incidents]) => { if (!stop) setData({ drones, incidents: [...incidents].sort((a, b) => a.created_at.localeCompare(b.created_at)) }) })
      .catch(() => {})
    pull()
    const id = setInterval(pull, 1000)
    return () => { stop = true; clearInterval(id) }
  }, [active])
  return data
}
const useTick = ms => { const [, set] = useState(0); useEffect(() => { const id = setInterval(() => set(n => n + 1), ms); return () => clearInterval(id) }, [ms]) }

/* ---------------- Command centre ---------------- */
export function CommandPage() {
  return (
    <>
      <Intro title="Command Centre">
        The live map on its own: switch layers, filter by crowd risk, watch the fleet move, and scrub the timeline to replay any moment. The drone feed sits in the corner.
      </Intro>
      <CommandView full />
    </>
  )
}

/* ---------------- Dispatch ---------------- */
function ClickToCall({ onCall }) {
  useMapEvents({ click: e => onCall(e.latlng) })
  return null
}

export function DispatchPage() {
  const { events, source } = useEvents()
  useTick(1000)
  const backend = useBackendFleet(source === 'live')
  const now = Date.now()
  const calls = events.filter(e => e.type === 'dispatch')
  const { rows, status } = assignFleet(calls, now)
  const fleet = fleetPositions(status, now)
  const stateOf = r => (now < r.start ? 'Queued' : now < r.arrive ? 'En route' : now < r.arrive + ON_SCENE_S * 1000 ? 'On scene' : 'Closed')
  const fromBackend = Boolean(backend) // live and the first /drones answer has arrived

  // One shape for both sources, so the markup below is written once.
  const bases = fromBackend ? backend.drones.map(d => ({ id: d.id, base: d.dock, at: [d.dock_lat, d.dock_lng] })) : FLEET
  const callMarks = fromBackend
    ? backend.incidents.slice(-8).map(i => ({ key: i.id, pos: [i.lat, i.lng], label: `112 call · ${clock({ timestamp: i.created_at })} · ${i.drone_id} · ${LIVE_CALL_STATE[i.status] || i.status}`, closed: i.status === 'closed' }))
    : rows.slice(-8).map(r => ({ key: r.e.id, pos: [r.e.lat, r.e.lng], label: `112 call · ${clock(r.e)} · ${r.drone} · ${stateOf(r)}`, closed: stateOf(r) === 'Closed' }))
  const droneMarks = fromBackend
    ? backend.drones.map(d => ({ id: d.id, pos: [d.lat, d.lng], state: LIVE_STATE[d.status] || d.status }))
    : fleet.map(d => ({ id: d.id, pos: d.pos, state: d.state }))
  const cards = fromBackend
    ? backend.drones.map(d => {
        const inc = d.incident_id ? backend.incidents.find(i => i.id === d.incident_id) : null
        const state = LIVE_STATE[d.status] || d.status
        return { id: d.id, base: d.dock, state, detail: inc && d.status !== 'idle' ? `${placeName(inc)} · ${(inc.distance_m / 1000).toFixed(1)} km · ETA ${mmss(inc.eta_s)}${d.status === 'en_route' && d.eta_s != null ? ` · ${mmss(d.eta_s)} left` : ''}` : (d.crowd_level ? `Streaming crowd feed · risk ${Math.round(d.crowd_risk)} ${d.crowd_level}` : '') }
      })
    : fleet.map(d => ({ id: d.id, base: d.base, state: d.state, detail: d.job && d.state !== 'At base' ? `${placeName(d.job.e)} · ${d.job.km.toFixed(1)} km · ETA ${mmss(d.job.etaS)}${d.state === 'En route' ? ` · ${mmss(Math.max(0, (d.job.arrive - now) / 1000))} left` : ''}` : '' }))
  const logRows = fromBackend
    ? [...backend.incidents].reverse().slice(0, 12).map(i => ({ key: i.id, time: clock({ timestamp: i.created_at }), where: placeName(i), drone: i.drone_id, km: i.distance_m / 1000, etaS: i.eta_s, state: LIVE_CALL_STATE[i.status] || i.status }))
    : rows.slice(-12).reverse().map(r => ({ key: r.e.id, time: clock(r.e), where: placeName(r.e), drone: `${r.drone}${r.queued ? ' (queued)' : ''}`, km: r.km, etaS: r.etaS, state: stateOf(r) }))

  return (
    <>
      <Intro title="Dispatch">
        Every 112 call is assigned to the nearest free drone, with a straight-line ETA. Click anywhere on the map to raise a call and watch the assignment. Dispatch is simulated until real dispatch events arrive from the backend.
      </Intro>
      <div className="sim-banner mono caps">
        {fromBackend
          ? `Live: calls go to the Haven backend, which picks the nearest free drone · simulated fleet, no hardware · straight-line flight at ${LIVE_SPEED_MS} m/s (${Math.round(LIVE_SPEED_MS * 3.6)} km/h) · ${backend.drones.length}-drone fleet`
          : `Simulated dispatch · straight-line ETA at ${SPEED_MS} m/s (${Math.round(SPEED_MS * 3.6)} km/h) · ${ON_SCENE_S / 60} min on scene · ${FLEET.length}-drone demo fleet`}
      </div>
      <div className="dp-grid">
        <div className="card dp-map">
          <MapContainer center={[17.405, 78.44]} zoom={12} scrollWheelZoom={false} style={{ height: '100%', cursor: 'crosshair' }}>
            <TileLayer url="https://tile.openstreetmap.org/{z}/{x}/{y}.png" attribution="&copy; OpenStreetMap contributors" />
            <ClickToCall onCall={({ lat, lng }) => raiseCall(lat, lng)} />
            {bases.map(d => (
              <CircleMarker key={`b${d.id}`} center={d.at} radius={5} pathOptions={{ color: '#888', fillColor: '#888', fillOpacity: 1, weight: 1 }}>
                <Tooltip>{d.id} base · {d.base}</Tooltip>
              </CircleMarker>
            ))}
            {callMarks.map(c => (
              <CircleMarker key={c.key} center={c.pos} radius={11} pathOptions={{ color: '#e5484d', dashArray: '4 4', fillOpacity: c.closed ? 0 : 0.15, weight: 2, opacity: c.closed ? 0.35 : 1 }}>
                <Tooltip>{c.label}</Tooltip>
              </CircleMarker>
            ))}
            {droneMarks.map(d => (
              <Marker key={d.id} position={d.pos} icon={iconFor(d.id)}><Tooltip>{d.id} · {d.state}</Tooltip></Marker>
            ))}
          </MapContainer>
          <p className="dp-hint mono caps">Click the map to raise a 112 call</p>
        </div>
        <div className="dp-side">
          {cards.map(d => (
            <div key={d.id} className={`card dp-drone s-${d.state.replace(' ', '-').toLowerCase()}`}>
              <div><strong>{d.id}</strong> <span className="muted">· {d.base}</span></div>
              <span className="dp-state">{d.state}</span>
              {d.detail && <small className="muted">{d.detail}</small>}
            </div>
          ))}
        </div>
      </div>
      <div className="card dp-log">
        <div className="inc-head"><strong>112 call log</strong><SourceTag source={source} /></div>
        <table className="an-table">
          <thead><tr><th>Time</th><th>Location</th><th>Drone</th><th>Distance</th><th>ETA</th><th>Status</th></tr></thead>
          <tbody>
            {logRows.map(r => (
              <tr key={r.key}>
                <td className="mono">{r.time}</td><td>{r.where}</td><td>{r.drone}</td>
                <td>{r.km.toFixed(1)} km</td><td>{mmss(r.etaS)}</td><td>{r.state}</td>
              </tr>
            ))}
          </tbody>
        </table>
        {logRows.length === 0 && <p className="muted">No calls yet. Click the map to raise one.</p>}
      </div>
    </>
  )
}

/* ---------------- Privacy lab ---------------- */
const LENSES = [['Wide (82°)', 82], ['2× zoom (47°)', 47], ['7× zoom (15°)', 15]]
const RESOLUTIONS = [['1080p', 1920], ['4K', 3840]]

function FaceCrop({ w, h }) {
  const ref = useRef()
  useEffect(() => {
    const c = ref.current, x = c.getContext('2d')
    x.fillStyle = '#d9d8d2'; x.fillRect(0, 0, 112, 112)
    const fw = Math.min(112, w), fh = Math.min(112, h)
    if (fw >= 1 && fh >= 1) {
      x.fillStyle = '#2a2a2c'
      x.beginPath(); x.ellipse(56, 56, fw / 2, fh / 2, 0, 0, Math.PI * 2); x.fill()
    }
  }, [w, h])
  return <canvas ref={ref} width={112} height={112} className="face-crop" aria-label={`A face covers about ${Math.round(w)} by ${Math.round(h)} pixels`} />
}

export function PrivacyLabPage() {
  const { events } = useEvents()
  const [alt, setAlt] = useState(40)
  const [dist, setDist] = useState(30)
  const [fov, setFov] = useState(82)
  const [res, setRes] = useState(1920)
  const [thr, setThr] = useState(85)
  const v = faceView({ altitude: alt, distance: dist, fovDeg: fov, widthPx: res })
  const matches = events.filter(e => e.type === 'watchlist_match').slice(-6).reverse()

  return (
    <>
      <Intro title="Privacy Lab">
        Try the limits yourself: how big a face is from a drone, when matching is even possible, and how the watchlist threshold decides what an officer ever sees.
      </Intro>

      <section className="card pl-block">
        <div className="inc-head"><h2>What can a drone see?</h2><span className="mono caps muted">Pinhole camera model · rule-of-thumb tiers</span></div>
        <div className="pl-grid">
          <div className="pl-controls">
            <label>Altitude <b>{alt} m</b><input type="range" min={2} max={120} value={alt} onChange={e => setAlt(+e.target.value)} /></label>
            <label>Horizontal distance <b>{dist} m</b><input type="range" min={0} max={200} value={dist} onChange={e => setDist(+e.target.value)} /></label>
            <div className="pl-seg" role="radiogroup" aria-label="Lens">{LENSES.map(([l, f]) => <button key={f} role="radio" aria-checked={fov === f} className={fov === f ? 'on' : ''} onClick={() => setFov(f)}>{l}</button>)}</div>
            <div className="pl-seg" role="radiogroup" aria-label="Resolution">{RESOLUTIONS.map(([l, w]) => <button key={w} role="radio" aria-checked={res === w} className={res === w ? 'on' : ''} onClick={() => setRes(w)}>{l}</button>)}</div>
          </div>
          <div className="pl-out">
            <div className="pl-numbers">
              <span>Distance to face <b>{v.slant.toFixed(0)} m</b></span>
              <span>Looking down <b>{v.depressionDeg.toFixed(0)}°</b></span>
              <span>Face size <b>{v.wPx.toFixed(0)} × {v.hPx.toFixed(0)} px</b></span>
            </div>
            <div className={`pl-verdict t-${v.tier.key}`}>{v.tier.label}</div>
            <div className="pl-crop">
              <FaceCrop w={v.wPx} h={v.hPx} />
              <p className="muted">The face drawn at its real pixel size inside a 112 × 112 crop, the input size face-recognition models such as ArcFace use.</p>
            </div>
          </div>
        </div>
        <ol className="pl-tiers">{TIERS.map(t => <li key={t.key} className={t.key === v.tier.key ? 'on' : ''}><b>{t.min}+ px</b> {t.label}</li>)}</ol>
        <p className="muted small">Assumes a 15 × 22 cm face. Faces are vertical, so looking down shortens them; straight overhead you see the top of a head, not a face. Tiers are a rule of thumb, not validated.</p>
      </section>

      <section className="card pl-block">
        <div className="inc-head"><h2>Before / after blur</h2><span className="mono caps muted">Consented footage</span></div>
        <div className="snap-empty"><p>The before/after view uses consented footage from the face-blur pipeline.</p><p className="muted">It appears here once that footage is added to the site.</p></div>
      </section>

      <section className="card pl-block">
        <div className="inc-head"><h2>Watchlist flow</h2><span className="mono caps muted">Demo threshold · adjustable</span></div>
        <label className="pl-thr">Match threshold <b>{thr}%</b><input type="range" min={50} max={99} value={thr} onChange={e => setThr(+e.target.value)} /></label>
        <p className="muted small">Matches below the threshold are discarded and never reach an officer. Above it, a person decides; nothing happens automatically.</p>
        {matches.length === 0 && <p className="muted">Waiting for watchlist matches from the stream…</p>}
        <ul className="pl-matches">
          {matches.map(m => {
            const pass = m.risk >= thr, dec = decisionOf(m.id)
            return (
              <li key={m.id} className={pass ? '' : 'below'}>
                <span className="mono">{clock(m)}</span>
                <span>{placeName(m)}</span>
                <span className="pl-conf"><i style={{ width: `${m.risk}%` }} /><em style={{ left: `${thr}%` }} /></span>
                <b>{m.risk}%</b>
                {!pass ? <span className="muted">Discarded · not shown</span>
                  : dec ? <span>{dec.d === 'confirm' ? '✓ Confirmed' : '✕ Rejected'}</span>
                    : <span className="pl-act">
                        <button className="chip dark" onClick={() => logDecision(m, 'confirm', 'Privacy lab reviewer')}>Confirm</button>
                        <button className="chip" onClick={() => logDecision(m, 'reject', 'Privacy lab reviewer')}>Reject</button>
                      </span>}
              </li>
            )
          })}
        </ul>
      </section>
    </>
  )
}

/* ---------------- Results ---------------- */
export function ResultsPage() {
  const { events } = useEvents()
  const [acc, setAcc] = useState(null)
  const [verifyRun, setVerifyRun] = useState(null)
  const [campus, setCampus] = useState(null)
  const [speed, setSpeed] = useState({})
  const today = new Date().toLocaleDateString([], { day: 'numeric', month: 'short', year: 'numeric' })

  useEffect(() => { fetch('/data/accuracy.json').then(r => r.json()).then(setAcc).catch(() => setAcc(false)) }, [])
  useEffect(() => { fetch('/face-recorded/campus/results.json').then(r => (r.ok ? r.json() : null)).then(setCampus).catch(() => setCampus(null)) }, [])
  useEffect(() => {
    const nav = performance.getEntriesByType('navigation')[0]
    let po
    try {
      // initial load only: later paints come from moving between pages, not from loading the site
      po = new PerformanceObserver(l => { const e = l.getEntries().filter(x => x.startTime < 5000).at(-1); if (e) setSpeed(s => ({ ...s, lcp: Math.max(s.lcp ?? 0, e.startTime) })) })
      po.observe({ type: 'largest-contentful-paint', buffered: true })
    } catch { /* LCP not supported */ }
    if (nav) setSpeed(s => ({ ...s, dcl: nav.domContentLoadedEventEnd, load: nav.loadEventEnd }))
    return () => po?.disconnect()
  }, [])
  const measure = async () => {
    const list = events.slice()
    const t = performance.now()
    const rows = await verifyChain(list)
    setVerifyRun({ ms: performance.now() - t, n: list.length, ok: rows.every(r => r.hash && r.link !== false) })
  }
  useEffect(() => { if (!verifyRun && events.length >= 5) measure() }, [events.length]) // eslint-disable-line react-hooks/exhaustive-deps

  const h = acc?.headline
  const fromTeam = 'reported by the AI pipeline, 20 Nov 2026'
  const ROWS = [
    ['Crowd counting', h ? `${Math.round(h.median_detected_over_true * 100)}% of people found` : '…', 'accuracy.json · median detected ÷ true', h ? `${h.frames} frames, ${h.sequences} sequences (DroneCrowd)` : '', fromTeam],
    ['Crowd counting', h ? `${h.mean_absolute_error_people} people (${Math.round(h.mean_absolute_error_pct)}%)` : '…', 'accuracy.json · mean absolute error', h ? `${h.frames} frames` : '', fromTeam],
    ['Crowd counting', h ? h.rank_correlation_counts.toFixed(2) : '…', 'accuracy.json · rank correlation of counts', h ? `${h.frames} frames` : '', fromTeam],
    ...(acc ? acc.validation_runs.map(r => ['Validation run', `${Math.round(r.detected_over_true * 100)}% found${r.detected_over_true > 1 ? ' (over-counts)' : ''} · MAE ${r.mean_absolute_error_people}`, `${r.name} · ${r.detector}`, r.correlation ? `correlation ${r.correlation}` : 'single sequence', fromTeam]) : []),
    ['Alert level', 'HIGH from risk 65', 'AI pipeline settings', 'threshold, not a measurement', fromTeam],
    ...(campus ? [
      ['Face matching', `${campus.totals.matches} matches at ${Math.round(campus.totals.confidence_min * 100)}–${Math.round(campus.totals.confidence_max * 100)}% confidence`, 'campus/results.json · cosine similarity, not a probability', `${campus.totals.clips} real campus clips, one consenting volunteer`, 'recorded 20 Nov 2026'],
      ['Face matching', `${Math.round(campus.held_out_test.rows[0].recall * 100)}% of his faces found from photos alone, ${Math.round(campus.held_out_test.rows[1].recall * 100)}% after learning from video`, 'campus/results.json · held-out clip, measured by the face team', 'one clip', 'recorded 20 Nov 2026'],
      ['Face matching', `${campus.totals.matches_in_clip_without_volunteer} matches in the clip without him`, 'campus/results.json · negative clip', 'one clip', 'recorded 20 Nov 2026'],
    ] : [['Face blur & matching', 'Not reported yet', 'Face pipeline', '—', '—']]),
    ['Face privacy', 'Small or partly hidden faces cannot be pixelated', 'campus/results.json · limits from the face team', 'see the Campus test page', 'recorded 20 Nov 2026'],
    ['Dispatch time', 'Not measured', 'Dispatch is simulated (straight-line ETA model)', '—', '—'],
    ['Evidence verify', verifyRun ? `${verifyRun.ms.toFixed(1)} ms ${verifyRun.ok ? '· all valid' : '· chain broken'}` : 'Measuring…', 'This browser, SHA-256 via Web Crypto', verifyRun ? `${verifyRun.n} events` : '', `${today}, live`],
    ['Page speed', speed.lcp ? `LCP ${(speed.lcp / 1000).toFixed(2)} s` : 'Not available in this browser', 'This browser, initial page load', 'one load', `${today}, live`],
    ['Page speed', speed.load ? `Loaded in ${(speed.load / 1000).toFixed(2)} s` : '…', 'Navigation timing', 'one load', `${today}, live`],
  ]

  return (
    <>
      <Intro title="Results">
        Every headline number in one place, with where it comes from, how much data is behind it and when. Anything not yet measured says so instead of showing a guess.
      </Intro>
      <div className="card res-table">
        <table className="an-table">
          <thead><tr><th>Area</th><th>Result</th><th>Source</th><th>Sample</th><th>Date</th></tr></thead>
          <tbody>
            {ROWS.map(([area, value, src, n, date], i) => (
              <tr key={i} className={value.startsWith('Not') ? 'pending' : ''}>
                <td>{area}</td><td><b>{value}</b></td><td className="muted">{src}</td><td className="muted">{n}</td><td className="muted">{date}</td>
              </tr>
            ))}
          </tbody>
        </table>
        <div className="res-actions">
          <button className="btn" onClick={measure} disabled={!events.length}>Measure verify time again</button>
          <a href="#/analytics" className="link-arrow">Accuracy details ↗</a>
          <a href="#/campus" className="link-arrow">Campus test details ↗</a>
        </div>
      </div>
      {acc === false && <p className="fail">accuracy.json could not be loaded.</p>}
    </>
  )
}
