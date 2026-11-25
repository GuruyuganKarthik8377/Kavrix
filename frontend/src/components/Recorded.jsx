import { useEffect, useRef, useState } from 'react'
import { Intro } from './Pages'
import { ALERT_AT } from '../lib/store'
import { GENESIS_HASH, eventDigest } from '../lib/verifyEvents.mjs'
import { LEVEL_COLOR, checkRun, loadIndex, loadRun } from '../lib/recorded'

const short = h => (h ? `${h.slice(0, 8)}…${h.slice(-4)}` : '—')
const KIND = { surge: 'Surge', sustained_high: 'Sustained high' }

// Risk over the clip, with each interval tinted by the pipeline's own level and the alerts marked.
function RunChart({ events, sel, onPick, bad }) {
  const W = 720, H = 230, pad = 30
  const T = Math.max(1, events.at(-1).video_t)
  const x = t => pad + (t / T) * (W - pad * 2)
  const y = r => H - pad - (r / 100) * (H - pad * 2)
  const pts = events.filter(e => e.type === 'crowd_risk')
  return (
    <svg viewBox={`0 0 ${W} ${H}`} className="risk-chart rc-chart" role="img" aria-label="Crowd risk over the recorded clip">
      {events.slice(0, -1).map((e, k) => (
        <rect key={e.id} x={x(e.video_t)} width={Math.max(0, x(events[k + 1].video_t) - x(e.video_t))} y={pad} height={H - pad * 2} fill={LEVEL_COLOR[e.level]} opacity=".1" />
      ))}
      {[0, 25, 50, 75, 100].map(r => <g key={r}><line x1={pad} x2={W - pad} y1={y(r)} y2={y(r)} className="grid" /><text x={4} y={y(r) + 4} className="svg-mono dim">{r}</text></g>)}
      <line x1={pad} x2={W - pad} y1={y(ALERT_AT)} y2={y(ALERT_AT)} className="thresh" />
      <text x={W - pad} y={y(ALERT_AT) - 6} textAnchor="end" className="svg-mono thresh-label">HIGH ≥ {ALERT_AT}</text>
      {events.map((e, k) => [e, k]).filter(([e]) => e.type === 'crowd_alert').map(([e, k], j) => (
        <g key={e.id} className="rc-alert" onClick={() => onPick(k)}>
          <line x1={x(e.video_t)} x2={x(e.video_t)} y1={pad - 8 - (j % 2) * 12} y2={H - pad} />
          {/* stagger labels so alerts close in time don't overlap */}
          <text x={x(e.video_t) + 4} y={pad - 10 - (j % 2) * 12} className="svg-mono">{KIND[e.alert_kind] ?? 'Alert'}</text>
        </g>
      ))}
      <polyline points={pts.map(e => `${x(e.video_t)},${y(e.risk)}`).join(' ')} className="line" />
      <line x1={x(events[sel].video_t)} x2={x(events[sel].video_t)} y1={pad} y2={H - pad} className="rc-cursor" />
      {events.map((e, k) => (
        <circle key={e.id} cx={x(e.video_t)} cy={y(e.risk)} r={k === sel ? 6 : e.type === 'crowd_alert' ? 5 : 3.4}
          fill={bad.has(k) ? '#e5484d' : LEVEL_COLOR[e.level]} stroke={k === sel ? 'var(--ink)' : 'none'} strokeWidth="2"
          className="rc-pt" onClick={() => onPick(k)}><title>{`${e.video_t}s · risk ${e.risk} · ${e.level}`}</title></circle>
      ))}
      <text x={W - pad} y={H - 8} textAnchor="end" className="svg-mono dim">{T.toFixed(0)} s</text>
      <text x={pad} y={H - 8} className="svg-mono dim">0 s</text>
    </svg>
  )
}

// What an attacker might do to a copy. The files on the server are never touched.
async function tamper(events, mode) {
  const list = events.map(e => structuredClone(e))
  if (mode === 'edit' || mode === 'forge') {
    const i = list.reduce((b, e, k) => (k > 0 && e.risk > list[b].risk ? k : b), 1)
    const before = list[i].risk
    list[i].risk = Math.round((before - 40) * 10) / 10
    let note = `Event ${i + 1}'s risk was lowered from ${before} to ${list[i].risk}, as if someone downplayed the alert.`
    if (mode === 'forge') { // a skilled forger recomputes every hash after the edit
      let prev = GENESIS_HASH
      for (const e of list) { e.prev_hash = prev; e.event_hash = await eventDigest(e); prev = e.event_hash }
      note += ' Then every hash in the log was recomputed, so the chain is consistent with itself again.'
    }
    return { list, note }
  }
  const i = list.findIndex(e => e.type === 'crowd_alert')
  const [gone] = list.splice(i, 1)
  return { list, note: `The ${KIND[gone.alert_kind]?.toLowerCase() ?? ''} alert at ${gone.video_t} s (event ${i + 1}) was deleted, as if it never fired.` }
}

export function RecordedPage() {
  const [index, setIndex] = useState(null)
  const [err, setErr] = useState(null)
  const [id, setId] = useState(null)
  const [run, setRun] = useState(null)
  const [sel, setSel] = useState(0)
  const [playing, setPlaying] = useState(false)
  const [check, setCheck] = useState(null) // { mode, note, ok, problems, ... }
  const [busy, setBusy] = useState(false)
  const video = useRef()

  useEffect(() => { loadIndex().then(l => { setIndex(l); setId(l[0].id) }).catch(e => setErr(e.message)) }, [])
  const inc = index?.find(x => x.id === id)
  useEffect(() => {
    if (!inc) return
    let live = true
    setRun(null); setSel(0); setPlaying(false); setCheck(null)
    loadRun(inc).then(r => live && setRun(r)).catch(e => live && setErr(e.message))
    return () => { live = false }
  }, [inc])

  const events = run?.events ?? []
  const e = events[sel]
  // No video: step one event per second. With video: the video drives the selection.
  useEffect(() => {
    if (!playing || inc?.video) return
    const t = setInterval(() => setSel(s => (s + 1 < events.length ? s + 1 : (setPlaying(false), s))), 1000)
    return () => clearInterval(t)
  }, [playing, inc, events.length])
  useEffect(() => {
    const v = video.current
    if (!v) return
    if (playing) v.play().catch(() => setPlaying(false))
    else v.pause()
  }, [playing])
  const pick = k => {
    setPlaying(false); setSel(k)
    if (video.current) video.current.currentTime = events[k].video_t
  }
  const onTime = () => {
    if (!playing) return
    const t = video.current.currentTime
    setSel(Math.max(0, events.findLastIndex(x => x.video_t <= t + 0.05)))
  }

  const verify = async mode => {
    setBusy(true)
    const t = mode === 'original' ? { list: events, note: null } : await tamper(events, mode)
    const res = await checkRun(run, t.list)
    setCheck({ mode, note: t.note, ...res, shown: t.list.length === events.length })
    setBusy(false)
  }
  const bad = new Set(check?.shown ? check.problems.filter(p => p.index < events.length).map(p => p.index) : [])

  if (err) return <><Intro title="Recorded Incidents">The recorded incident files could not be loaded.</Intro><p className="fail mono">{err}</p></>
  if (!index) return <Intro title="Recorded Incidents">Loading recorded runs…</Intro>

  return (
    <>
      <Intro title="Recorded Incidents">
        Two runs of the real AI pipeline on real footage, exactly as it wrote them. Scrub through each one, open the annotated snapshot behind every reading, and check the evidence chain, snapshots included, right here in your browser.
      </Intro>
      <div className="inc-spots" role="tablist" aria-label="Recorded runs">
        {index.map(x => (
          <button key={x.id} role="tab" aria-selected={x.id === id} className={x.id === id ? 'on' : ''} onClick={() => setId(x.id)}>
            {x.id === 'concert' ? 'Concert clip' : 'Jülich entrance'} <small>{x.kind}</small>
          </button>
        ))}
      </div>

      <section className="card rc-story">
        <h2>{inc.title}</h2>
        <p>{inc.story}</p>
        <ul className="rc-caveats">{inc.caveats.map(c => <li key={c}>{c}</li>)}</ul>
        {run && (
          <p className="mono caps muted rc-meta">
            {run.summary.run_id} · {run.summary.duration_s} s · {run.summary.frames} frames · {run.summary.chain_events} events · {run.summary.alerts} alerts · peak risk {run.summary.max_risk} · processed at {run.summary.processing_fps} fps on a laptop · {inc.location.name}
          </p>
        )}
      </section>

      {!run ? <p className="muted">Loading {inc.id} events…</p> : (<>
        <div className="rc-grid">
          <figure className="card rc-snap">
            <img key={e.heatmap} src={run.base + e.heatmap} alt={`Annotated snapshot at ${e.video_t} s: ${e.count} people detected, risk ${e.risk}, ${e.level}`} />
            <figcaption className="mono caps muted">Snapshot {sel + 1}/{events.length} · {e.heatmap} · SHA-256 {short(e.evidence_hash)}</figcaption>
          </figure>
          <aside className="card rc-facts">
            <span className="rc-level" style={{ background: LEVEL_COLOR[e.level] }}>{e.type === 'crowd_alert' ? `ALERT · ${KIND[e.alert_kind] ?? e.alert_kind}` : e.level}</span>
            {e.message && <p className="rc-msg">{e.message}</p>}
            <dl className="kv">
              <div><dt>Time in clip</dt><dd>{e.video_t} s</dd></div>
              <div><dt>Risk</dt><dd><b>{e.risk}</b> / 100 · {e.level}</dd></div>
              <div><dt>People detected</dt><dd>{e.count}</dd></div>
              <div><dt>Peak density</dt><dd>{e.density_peak}</dd></div>
              <div><dt>Motion</dt><dd>{e.motion}</dd></div>
              <div><dt>Risk rate</dt><dd>{e.risk_rate > 0 ? '+' : ''}{e.risk_rate} / s</dd></div>
              <div><dt>Time to HIGH</dt><dd>{e.time_to_high == null ? '—' : `≈ ${e.time_to_high} s (estimate)`}</dd></div>
              <div><dt>Hotspot</dt><dd>{e.hotspot ? `${Math.round(e.hotspot.x * 100)}% across, ${Math.round(e.hotspot.y * 100)}% down` : '—'}</dd></div>
              <div><dt>Event</dt><dd className="mono">#{e.seq} · {e.id}</dd></div>
            </dl>
            {inc.video && (
              <video ref={video} src={inc.video} muted playsInline preload="metadata" onTimeUpdate={onTime} onEnded={() => setPlaying(false)}
                onLoadedMetadata={() => { video.current.currentTime = e.video_t }} aria-label="Original clip, synced to the selected event" />
            )}
          </aside>
        </div>

        <div className="card rc-scrub">
          <button className="chip dark" onClick={() => { if (!playing && sel === events.length - 1) pick(0); setPlaying(p => !p) }}>{playing ? '❚❚ Pause' : '▶ Play'}</button>
          <input type="range" min={0} max={events.length - 1} value={sel} onChange={x => pick(+x.target.value)} aria-label="Scrub through the recorded events" />
          <span className="mono muted">{e.video_t.toFixed(1)} s</span>
        </div>

        <div className="card rc-chartcard">
          <div className="inc-head"><strong>Risk over the clip</strong><span className="mono caps muted">shaded by the pipeline's level · click a point</span></div>
          <RunChart events={events} sel={sel} onPick={pick} bad={bad} />
          <div className="rc-legend mono caps muted">{Object.entries(LEVEL_COLOR).map(([k, c]) => <span key={k}><i style={{ background: c }} />{k}</span>)}<span><i className="rc-alert-key" />Alert</span></div>
        </div>

        <div className="card rc-chaincard">
          <div className="inc-head"><strong>Evidence chain</strong><span className="mono caps muted">each block holds the hash of the one before</span></div>
          <ol className="rc-chain" aria-label="Events in chain order">
            {events.map((x, k) => (
              <li key={x.id}>
                <button className={`${k === sel ? 'sel' : ''} ${x.type === 'crowd_alert' ? 'alert' : ''} ${bad.has(k) ? 'bad' : ''}`}
                  style={{ '--c': LEVEL_COLOR[x.level] }} onClick={() => pick(k)}
                  title={`#${x.seq} · ${x.video_t}s · prev ${short(x.prev_hash)} → ${short(x.event_hash)}`}>{x.seq}</button>
              </li>
            ))}
          </ol>
          <p className="mono small muted">#{e.seq} · prev_hash {short(e.prev_hash)} → event_hash {short(e.event_hash)} · summary.json chain head {short(run.summary.chain_head)}</p>
        </div>

        <section className="card rc-verify">
          <div className="inc-head"><h2>Verify this run</h2><span className="mono caps muted">Web Crypto · no backend · the AI team's verifier</span></div>
          <div className="ev-actions">
            <button className="btn dark" disabled={busy} onClick={() => verify('original')}>Verify this incident</button>
            <button className="btn" disabled={busy} onClick={() => verify('edit')}>Tamper with a copy</button>
            <button className="btn" disabled={busy} onClick={() => verify('delete')}>Delete an alert from a copy</button>
            <button className="btn" disabled={busy} onClick={() => verify('forge')}>Rewrite the whole chain</button>
          </div>
          {busy && <p className="muted">Checking {events.length} events and their snapshots…</p>}
          {check && !busy && (
            <div className={`ev-verdict ${check.ok ? 'ok' : 'fail'}`} role="status">
              {check.ok
                ? <>✓ VERIFIED. {check.events} events, every hash and link matches, {check.snapshotsChecked} snapshots match their evidence hashes, and the head matches summary.json. {check.ms.toFixed(0)} ms.</>
                : <>✗ {check.problems.length} problem{check.problems.length > 1 ? 's' : ''} found in {check.ms.toFixed(0)} ms.</>}
              {check.note && <span className="ev-why">{check.note}</span>}
              {!check.ok && <ul className="rc-problems">{check.problems.slice(0, 6).map((p, k) => <li key={k}><b>{p.kind}</b> · {p.message}</li>)}</ul>}
              {check.mode === 'forge' && <span className="ev-why">This rewrite is caught only because summary.json still holds the original chain head. Someone who also rewrites summary.json would pass, so in deployment the head must come from a store the attacker cannot edit (the backend's signed store).</span>}
            </div>
          )}
          <p className="muted small">What this proves: the log and its snapshots are unchanged since the pipeline wrote them, and no event was edited, removed or reordered. What it does not prove: that the footage itself is genuine, or that nobody rewrote everything, including the summary. Events are hashed and chained, not digitally signed.</p>
        </section>
      </>)}
    </>
  )
}
