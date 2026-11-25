import { useEffect, useRef, useState } from 'react'

const MODULES = [
  {
    name: 'Dispatch', sub: 'Nearest-drone auto launch', tag: 'AUTO DISPATCH', viz: 'route',
    head: 'A 112 call becomes an aerial view before the patrol arrives.',
    body: 'When a call comes in with a location, the nearest idle drone is assigned and launched. Its live view appears on the command map while officers are still on the way.',
    spec: [['Trigger', '112 call with location'], ['Logic', 'Nearest idle drone'], ['Output', 'dispatch event on the map'], ['Stack', 'FastAPI, WebSockets'], ['Status', 'Simulated in the demo']],
    rows: [['CALL', '112 · Tank Bund'], ['ASSIGN', 'nearest idle drone'], ['FLY', 'straight-line route'], ['STREAM', 'live view to command']],
    foot: 'D-01 · base: Commissionerate, Basheer Bagh',
  },
  {
    name: 'Crowd Risk', sub: 'Crowd-crush early warning', tag: 'CROWD SAFETY', viz: 'spike',
    head: 'Crowd density is scored before a crush, not after.',
    body: 'YOLOv8 detects and counts people per zone in drone footage. The count and how fast it rises give a 0–100 risk score, and HIGH risk (65 or more) alerts the nearest patrol.',
    spec: [['Model', 'YOLOv8 person detection'], ['Data', 'VisDrone / DroneCrowd, Pexels, Jülich'], ['Output', 'crowd_risk 0–100 per zone'], ['Threshold', 'HIGH from 65 alerts officers'], ['Stack', 'Python, PyTorch, OpenCV']],
    rows: [['BASELINE', 'normal density per zone'], ['SURGE', 'density rising fast'], ['ALERT', 'risk ≥ 65 notifies patrol'], ['DECISION', 'an officer opens exits']],
    foot: 'scored on drone video · runs per frame',
  },
  {
    name: 'Watchlist', sub: 'Privacy-first face matching', tag: 'PRIVACY', viz: 'faces',
    head: 'Only the lawful watchlist is matched. Everyone else stays anonymous.',
    body: 'Faces are compared only with a small lawful watchlist, such as missing persons or wanted offenders. Every other face is blurred and nothing about it is kept.',
    spec: [['Model', 'InsightFace (ArcFace)'], ['Watchlist', 'Consented demo photos'], ['Non-matches', 'Blurred, never stored'], ['Output', 'watchlist_match + confidence'], ['Review', 'Officer confirm / reject']],
    rows: [['DETECT', 'every face in frame'], ['MATCH', 'against the watchlist only'], ['BLUR', 'all non-matches'], ['DECISION', 'an officer confirms']],
    foot: 'no face data leaves the pipeline unless matched',
  },
  {
    name: 'Evidence', sub: 'Hash-chained custody log', tag: 'CHAIN OF CUSTODY', viz: 'chain',
    head: 'Every event is chained to the last, so tampering shows.',
    body: 'Each event is hashed with SHA-256 together with the previous event’s hash, forming a chain. The verify check recomputes every hash, so any change after recording is flagged. Signing the chain is the next step.',
    spec: [['Hash', 'SHA-256 per event'], ['Chain', 'Each hash covers the previous one'], ['Log', 'Append-only custody log'], ['Check', 'Recompute hash + link'], ['Signature', 'Planned (backend)'], ['Output', 'Audit trail for court']],
    rows: [['RECORD', 'event + metadata'], ['HASH', 'SHA-256'], ['CHAIN', 'covers previous hash'], ['VERIFY', 'any change is flagged']],
    foot: 'hash · prev-hash · timestamp',
  },
  {
    name: 'Command Map', sub: 'Live geospatial view', tag: 'GEOSPATIAL', viz: 'map',
    head: 'One map shows drones, crowds and matches as they happen.',
    body: 'Events stream to the control room over WebSockets. Commanders see drone positions, risk-coloured zones and matches waiting for review, and can replay any incident.',
    spec: [['Map', 'Leaflet + OpenStreetMap'], ['Transport', 'WebSocket event stream'], ['Replay', 'Timeline scrubber'], ['Client', 'React'], ['Fallback', 'Scripted simulation']],
    rows: [['DRONES', 'live position'], ['ZONES', 'risk-coloured'], ['MATCHES', 'waiting for review'], ['REPLAY', 'scrub any incident']],
    foot: 'Hyderabad · 5 demo hotspots',
  },
  {
    name: 'Officer Loop', sub: 'Human in the loop', tag: 'OVERSIGHT', viz: 'loop',
    head: 'The system suggests. A human makes the call.',
    body: 'Alerts reach the nearest patrol with the location and the evidence. Nothing is enforced automatically: an officer confirms or rejects, and every decision is logged.',
    spec: [['Alert', 'Risk ≥ 65 (HIGH) or watchlist match'], ['Channel', 'Patrol web page'], ['Action', 'Confirm, reject, dispatch'], ['Audit', 'Every decision logged'], ['Principle', 'No automated enforcement']],
    rows: [['SIGNAL', 'alert raised'], ['CONTEXT', 'evidence laid out'], ['REVIEW', 'officer checks'], ['DECISION', 'a human makes the call']],
    foot: 'every decision is logged with its time',
  },
]

export default function Modules() {
  const ref = useRef()
  const [i, setI] = useState(0)
  const [prog, setProg] = useState(0)
  const [pinned, setPinned] = useState(true)

  useEffect(() => {
    const mq = matchMedia('(min-width: 1101px)')
    const on = () => {
      if (!mq.matches) return
      const r = ref.current.getBoundingClientRect()
      const p = Math.min(1, Math.max(0, -r.top / (r.height - innerHeight)))
      setProg(p)
      setI(Math.min(MODULES.length - 1, Math.floor(p * MODULES.length)))
    }
    const onMq = () => { setPinned(mq.matches); on() }
    onMq()
    addEventListener('scroll', on, { passive: true })
    mq.addEventListener('change', onMq)
    return () => { removeEventListener('scroll', on); mq.removeEventListener('change', onMq) }
  }, [])

  // Pinned (desktop): scroll to the module's slice of the section. Stacked (mobile): just switch.
  const pick = k => {
    if (!pinned) return setI(k)
    const el = ref.current
    scrollTo({ top: el.offsetTop + ((el.offsetHeight - innerHeight) * (k + 0.5)) / MODULES.length, behavior: 'smooth' })
  }
  const m = MODULES[i]

  return (
    <section id="modules" ref={ref} className={`modules ${pinned ? 'pinned' : ''}`} style={pinned ? { height: `${MODULES.length * 75 + 40}vh` } : undefined}>
      <div className="modules-sticky">
        <div className="mod-left" key={`l${i}`}>
          <p className="mono caps muted">Module {String(i + 1).padStart(2, '0')} · {m.sub}</p>
          <h3>{m.head}</h3>
          <p className="mod-body">{m.body}</p>
          <dl className="kv">{m.spec.map(([k, v]) => <div key={k}><dt>{k}</dt><dd>{v}</dd></div>)}</dl>
          <a href="#live" className="btn">See it live <span aria-hidden="true">↗</span></a>
        </div>

        <div className="mod-stage" aria-hidden="true">
          <span className="mod-back b1" />
          <span className="mod-back b2" />
          <article className="mod-card" key={`c${i}`}>
            <header><h4>{m.name}</h4><span className="mono caps">{m.tag}</span></header>
            <div className="mod-viz"><Viz kind={m.viz} /></div>
            <table className="mod-table">
              <tbody>
                {m.rows.map(([k, v], j) => (
                  <tr key={k} className={j === m.rows.length - 1 ? 'on' : ''}><th>{k}</th><td>{v}</td></tr>
                ))}
              </tbody>
            </table>
            <footer className="mono">{m.foot}</footer>
          </article>
        </div>

        <div className="mod-right">
          <ol className="mod-list">
            {MODULES.map((x, k) => (
              <li key={x.name} style={{ '--dist': Math.abs(k - i) }}>
                <button className={k === i ? 'on' : ''} aria-current={k === i} onClick={() => pick(k)}>
                  {x.name}
                  {k === i && <small>{x.sub}</small>}
                </button>
              </li>
            ))}
          </ol>
          <div className="mod-count" aria-hidden="true">
            <span className="pixel">{String(i + 1).padStart(2, '0')}</span>
            <small className="mono">/{String(MODULES.length).padStart(2, '0')}</small>
            {pinned && <small className="mono mod-prog">{Math.round(prog * 100)}%</small>}
          </div>
        </div>
      </div>
    </section>
  )
}

/* ---------- card visuals (SVG, theme-aware via currentColor) ---------- */
const yAt = i => 150 + Math.sin(i * 1.7) * 14 + Math.sin(i * 0.53) * 9 + (i === 44 ? -95 : i === 43 || i === 45 ? -30 : 0)

function Viz({ kind }) {
  if (kind === 'spike') {
    const pts = Array.from({ length: 57 }, (_, i) => `${4 + i * 7},${yAt(i).toFixed(1)}`).join(' ')
    const sx = 4 + 44 * 7, sy = yAt(44)
    return (
      <svg viewBox="0 0 400 230">
        <text x="12" y="20" className="svg-mono">CROWD DENSITY, PER ZONE</text>
        <path className="band" d="M0 125 C 80 110, 160 140, 240 120 S 360 110, 400 128 L 400 178 C 330 168, 260 190, 180 176 S 60 170, 0 182 Z" />
        <polyline className="line draw" points={pts} />
        <path d="M2 6 V 226 H 398" className="axis" />
        <circle cx={sx} cy={sy} r="9" className="ring pulse" />
        <path d={`M${sx + 7} ${sy - 6} L ${sx + 38} ${sy - 24}`} className="axis" />
        <text x={sx + 40} y={sy - 24} className="svg-mono">SURGE</text>
      </svg>
    )
  }
  if (kind === 'route') {
    const base = [23, 205], targets = [[300, 44], [250, 124], [118, 84]]
    return (
      <svg viewBox="0 0 400 230">
        <defs><pattern id="mod-grid" width="20" height="20" patternUnits="userSpaceOnUse"><path d="M20 0H0V20" className="gridline" /></pattern></defs>
        <rect width="400" height="230" fill="url(#mod-grid)" />
        {[[70, 44], [48, 28], [26, 14]].map(([rx, ry]) => <ellipse key={rx} cx="270" cy="105" rx={rx} ry={ry} className="contour" />)}
        <polyline points="60,30 360,30 360,70 60,70 60,110 360,110 360,150 60,150 60,190 360,190" className="line thin draw" />
        {targets.map(([x, y]) => (
          <g key={x}>
            <path d={`M${base[0]} ${base[1]} L ${x} ${y}`} className="dash" />
            <rect x={x - 8} y={y - 8} width="16" height="16" className="target" />
          </g>
        ))}
        <rect x="14" y="196" width="18" height="18" className="fill-ink" />
        <text x="38" y="210" className="svg-mono">BASE</text>
      </svg>
    )
  }
  if (kind === 'faces') {
    const cells = Array.from({ length: 12 }, (_, k) => [10 + (k % 4) * 97, 10 + Math.floor(k / 4) * 72, k])
    const br = (x, y, w, h) => `M${x} ${y + 10}V${y}H${x + 10}M${x + w - 10} ${y}H${x + w}V${y + 10}M${x + w} ${y + h - 10}V${y + h}H${x + w - 10}M${x + 10} ${y + h}H${x}V${y + h - 10}`
    return (
      <svg viewBox="0 0 400 230">
        <defs><pattern id="mod-dots" width="5" height="5" patternUnits="userSpaceOnUse"><circle cx="2.5" cy="2.5" r="1.2" className="fill-ink" /></pattern></defs>
        {cells.map(([x, y, k]) => (
          <g key={k}>
            <rect x={x} y={y} width="88" height="62" className="cell" />
            {k === 6
              ? <><rect x={x + 30} y={y + 14} width="28" height="30" className="fill-ink pulse" /><text x={x + 6} y={y + 57} className="svg-mono">MATCH</text></>
              : k % 3 === 0
                ? <rect x={x + 4} y={y + 4} width="80" height="54" fill="url(#mod-dots)" opacity=".55" />
                : <><path d={br(x + 20, y + 12, 22, 26)} className="axis" /><path d={br(x + 50, y + 18, 20, 24)} className="axis" /></>}
          </g>
        ))}
      </svg>
    )
  }
  if (kind === 'chain') {
    const blocks = [['0410', '07bd…c3f9', 'e81a…5d20'], ['0411', '9f2c…a41e', '07bd…c3f9'], ['0412', '3c6f…b7a2', '9f2c…a41e']]
    return (
      <svg viewBox="0 0 400 230">
        {blocks.map(([n, h, prev], k) => {
          const x = 10 + k * 132, last = k === 2
          return (
            <g key={n}>
              <rect x={x} y="45" width="116" height="140" className={last ? 'fill-ink' : 'cell'} />
              <text x={x + 10} y="68" className={`svg-mono ${last ? 'on-ink' : ''}`}>BLOCK {n}</text>
              <text x={x + 10} y="100" className={`svg-mono dim ${last ? 'on-ink' : ''}`}>hash</text>
              <text x={x + 10} y="114" className={`svg-mono ${last ? 'on-ink' : ''}`}>{h}</text>
              <text x={x + 10} y="140" className={`svg-mono dim ${last ? 'on-ink' : ''}`}>prev</text>
              <text x={x + 10} y="154" className={`svg-mono ${last ? 'on-ink' : ''}`}>{prev}</text>
              {last && <text x={x + 10} y="176" className="svg-mono on-ink">✓ VERIFIED</text>}
              {k < 2 && <path d={`M${x + 118} 115 h 12 m -5 -5 l 5 5 l -5 5`} className="axis" />}
            </g>
          )
        })}
      </svg>
    )
  }
  if (kind === 'map') {
    const dots = Array.from({ length: 46 }, (_, k) => [20 + ((k * 89) % 360), 20 + ((k * 53) % 190)])
    return (
      <svg viewBox="0 0 400 230">
        <path d="M-10 160 C 90 120, 180 190, 410 90" className="road" />
        <path d="M120 -10 C 140 80, 110 160, 160 240" className="road" />
        {[[80, 52], [56, 36], [32, 20]].map(([rx, ry]) => <ellipse key={rx} cx="250" cy="98" rx={rx} ry={ry} className="contour" />)}
        {dots.map(([x, y], k) => <circle key={k} cx={x} cy={y} r="2" className="fill-ink" opacity=".35" />)}
        <circle cx="250" cy="98" r="10" className="ring pulse" />
        <rect x="244" y="92" width="12" height="12" className="fill-ink" />
        <text x="266" y="80" className="svg-mono">RISK 84</text>
        <text x="12" y="220" className="svg-mono dim">17.4239°N 78.4738°E</text>
      </svg>
    )
  }
  const steps = ['SIGNAL', 'CONTEXT', 'REVIEW', 'DECISION']
  return (
    <svg viewBox="0 0 400 230">
      <path d="M40 100 H 360" className="axis" />
      <path d="M360 128 C 360 200, 40 200, 40 128" className="dash" />
      <text x="168" y="196" className="svg-mono dim">logged · hash-chained</text>
      {steps.map((s, k) => {
        const x = 40 + k * 106.7, last = k === 3
        return (
          <g key={s}>
            <circle cx={x} cy="100" r={last ? 16 : 11} className={last ? 'fill-ink pulse' : 'cell'} />
            <text x={x} y="64" textAnchor="middle" className="svg-mono">{s}</text>
          </g>
        )
      })}
    </svg>
  )
}
