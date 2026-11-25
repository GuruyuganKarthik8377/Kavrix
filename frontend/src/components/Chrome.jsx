import { useEffect, useRef, useState } from 'react'

// Top status strip: live IST clock, measured FPS and measured LCP (where the browser reports it).
export function StatusBar() {
  const [now, setNow] = useState(() => new Date())
  const [fps, setFps] = useState(60)
  const [lcp, setLcp] = useState(null)
  useEffect(() => {
    const id = setInterval(() => setNow(new Date()), 1000)
    let frames = 0, t0 = performance.now(), raf
    const loop = t => {
      frames++
      if (t - t0 >= 1000) { setFps(Math.round((frames * 1000) / (t - t0))); frames = 0; t0 = t }
      raf = requestAnimationFrame(loop)
    }
    raf = requestAnimationFrame(loop)
    let po
    try {
      po = new PerformanceObserver(list => { const e = list.getEntries().at(-1); if (e) setLcp(e.startTime) })
      po.observe({ type: 'largest-contentful-paint', buffered: true })
    } catch { /* LCP not supported in this browser */ }
    const freeze = setTimeout(() => po?.disconnect(), 5000) // initial-load LCP only; later paints aren't load time
    return () => { clearInterval(id); clearTimeout(freeze); cancelAnimationFrame(raf); po?.disconnect() }
  }, [])
  const time = now.toLocaleTimeString('en-GB', { timeZone: 'Asia/Kolkata', hour12: false })
  return (
    <div className="status" aria-hidden="true">
      <span>HYDERABAD <b>{time} IST</b></span>
      <span>FPS <b>{fps}</b></span>
      {lcp != null && <span className="hide-sm">LCP <b>{(lcp / 1000).toFixed(2)}s</b></span>}
      <span className="status-right hide-sm">CIPHERCOP <b>2026</b> · TEAM <b>HAVEN</b></span>
      <span className="progress" />
    </div>
  )
}

export function useActiveSection(ids) {
  const [active, setActive] = useState(null)
  useEffect(() => {
    const io = new IntersectionObserver(
      entries => entries.forEach(e => e.isIntersecting && setActive(e.target.id)),
      { rootMargin: '-45% 0px -50% 0px' },
    )
    ids.forEach(id => { const el = document.getElementById(id); if (el) io.observe(el) })
    return () => io.disconnect()
  }, [ids])
  return active
}

export function CommandPalette({ items, open, onClose }) {
  const ref = useRef(), input = useRef()
  const [q, setQ] = useState('')
  const [sel, setSel] = useState(0)
  useEffect(() => {
    const d = ref.current
    if (open) {
      if (!d.open) d.showModal()
      input.current.focus()
    } else {
      if (d.open) d.close()
      setQ(''); setSel(0) // always start fresh next time
    }
  }, [open])
  const list = items.filter(([, label]) => label.toLowerCase().includes(q.trim().toLowerCase()))
  const go = href => { onClose(); location.hash = href }
  const onKey = e => {
    if (e.key === 'ArrowDown') { e.preventDefault(); setSel(s => Math.min(s + 1, list.length - 1)) }
    if (e.key === 'ArrowUp') { e.preventDefault(); setSel(s => Math.max(s - 1, 0)) }
    // preventDefault: otherwise the same Enter lands on whatever regains focus after close (e.g. the theme button)
    if (e.key === 'Enter' && list[sel]) { e.preventDefault(); go(list[sel][0]) }
  }
  return (
    <dialog ref={ref} className="palette" aria-label="Jump to section" onClose={onClose} onClick={e => e.target === ref.current && onClose()}>
      <div className="palette-box">
        <input
          ref={input} value={q} placeholder="Jump to…" aria-label="Search sections"
          onChange={e => { setQ(e.target.value); setSel(0) }} onKeyDown={onKey}
        />
        <ul role="listbox" aria-label="Sections">
          {list.map(([href, label, hint], i) => (
            <li key={href} role="option" aria-selected={i === sel}>
              <button className={i === sel ? 'on' : ''} onMouseEnter={() => setSel(i)} onClick={() => go(href)}>
                <span>{label}</span><small>{hint}</small>
              </button>
            </li>
          ))}
          {list.length === 0 && <li className="palette-empty">No section matches “{q}”.</li>}
        </ul>
        <p className="palette-foot"><kbd>↑</kbd><kbd>↓</kbd> move <kbd>↵</kbd> open <kbd>esc</kbd> close</p>
      </div>
    </dialog>
  )
}

export function ThemeToggle() {
  const [dark, setDark] = useState(() => document.documentElement.dataset.theme === 'dark')
  useEffect(() => {
    document.documentElement.dataset.theme = dark ? 'dark' : 'light'
    try { localStorage.setItem('kavrix-theme', dark ? 'dark' : 'light') } catch { /* storage blocked */ }
  }, [dark])
  return (
    <button className="icon-btn" aria-label={dark ? 'Switch to light theme' : 'Switch to dark theme'} aria-pressed={dark} onClick={() => setDark(d => !d)}>
      {dark
        ? <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" aria-hidden="true"><circle cx="12" cy="12" r="4.5" /><path d="M12 2v2M12 20v2M4.9 4.9l1.4 1.4M17.7 17.7l1.4 1.4M2 12h2M20 12h2M4.9 19.1l1.4-1.4M17.7 6.3l1.4-1.4" /></svg>
        : <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true"><path d="M20 14.5A8 8 0 0 1 9.5 4a8 8 0 1 0 10.5 10.5z" /></svg>}
    </button>
  )
}

// Right-edge scroll rail with a percentage readout.
export function ScrollRail() {
  const [p, setP] = useState(0)
  useEffect(() => {
    const on = () => setP(scrollY / Math.max(1, document.documentElement.scrollHeight - innerHeight))
    on()
    addEventListener('scroll', on, { passive: true })
    return () => removeEventListener('scroll', on)
  }, [])
  return (
    <div className="rail" aria-hidden="true">
      <span>{Math.round(p * 100)}%</span>
      <i><b style={{ scale: `1 ${p}` }} /></i>
    </div>
  )
}
