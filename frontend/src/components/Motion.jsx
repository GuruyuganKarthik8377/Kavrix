import { useEffect, useRef, useState } from 'react'

// Adds .in to every .reveal element the first time it scrolls into view (CSS does the animation).
// Re-run when `dep` changes: returning from a page mounts fresh .reveal elements.
export function useReveal(dep) {
  useEffect(() => {
    const io = new IntersectionObserver(entries => entries.forEach(e => {
      if (!e.isIntersecting) return
      e.target.classList.add('in')
      io.unobserve(e.target)
    }), { threshold: 0.12 })
    document.querySelectorAll('.reveal').forEach(el => io.observe(el))
    return () => io.disconnect()
  }, [dep])
}

export function CountUp({ to, suffix = '', ms = 1400 }) {
  const ref = useRef()
  const [v, setV] = useState(0)
  useEffect(() => {
    let raf
    const io = new IntersectionObserver(([e]) => {
      if (!e.isIntersecting) return
      io.disconnect()
      const t0 = performance.now()
      const tick = now => {
        const k = Math.min((now - t0) / ms, 1)
        setV(Math.round(to * (1 - (1 - k) ** 3)))
        if (k < 1) raf = requestAnimationFrame(tick)
      }
      raf = requestAnimationFrame(tick)
    })
    io.observe(ref.current)
    return () => { io.disconnect(); cancelAnimationFrame(raf) }
  }, [to, ms])
  return <span ref={ref}>{v}{suffix}</span>
}

export function Marquee({ items }) {
  const row = items.map(t => <span key={t} className="mq-item">{t}<i>✦</i></span>)
  return (
    <div className="marquee" aria-hidden="true">
      <div className="mq-track"><div className="mq-row">{row}</div><div className="mq-row">{row}</div></div>
    </div>
  )
}

const HASHES = ['9f2c…a41e', '07bd…c3f9', 'e81a…5d20', '3c6f…b7a2', 'a4d9…0e6c', '5b17…f8d3']

// Small looping visual inside each feature card.
export function CardViz({ kind }) {
  if (kind === 'crowd') {
    return (
      <div className="viz viz-crowd" aria-hidden="true">
        {Array.from({ length: 14 }, (_, i) => <span key={i} style={{ '--i': i }} />)}
        <b>Risk 84</b>
      </div>
    )
  }
  if (kind === 'privacy') {
    return (
      <div className="viz viz-privacy" aria-hidden="true">
        {Array.from({ length: 15 }, (_, i) => <span key={i} className={i === 7 ? 'hit' : ''} style={{ '--i': i }} />)}
      </div>
    )
  }
  if (kind === 'evidence') {
    return (
      <div className="viz viz-evidence" aria-hidden="true">
        <div className="hash-roll">{[...HASHES, ...HASHES].map((h, i) => <code key={i}>sha256 {h}</code>)}</div>
        <b>✓ Verified</b>
      </div>
    )
  }
  return (
    <div className="viz viz-alert" aria-hidden="true">
      <div className="notif"><i /><span><strong>Crowd risk 84</strong>Tank Bund · act now</span></div>
      <div className="notif n2"><i className="ok" /><span><strong>Officer confirmed</strong>Unit 14 en route</span></div>
    </div>
  )
}

const PIPE = [
  { k: 'Capture', t: 'Drone footage', d: 'Recorded clips: VisDrone / DroneCrowd, Pexels, Jülich' },
  { k: 'Perceive', t: 'Crowd + face AI', d: 'YOLOv8 person detection · watchlist match' },
  { k: 'Protect', t: 'Blur + hash', d: 'Non-matches blurred · SHA-256 hash chain' },
  { k: 'Command', t: 'Live map', d: 'Events stream to the control room' },
  { k: 'Act', t: 'Officer', d: 'Confirms, dispatches, closes the loop' },
]

export function Pipeline() {
  return (
    <ol className="pipe">
      {PIPE.map((p, i) => (
        <li key={p.k} className="pipe-node reveal" style={{ '--d': `${i * 90}ms` }}>
          <span className="pipe-k">0{i + 1} · {p.k}</span>
          <strong>{p.t}</strong>
          <span className="pipe-d">{p.d}</span>
          {i < PIPE.length - 1 && <span className="pipe-link" aria-hidden="true"><i style={{ animationDelay: `${i * 0.35}s` }} /></span>}
        </li>
      ))}
    </ol>
  )
}
