import { useEffect, useLayoutEffect, useRef, useState } from 'react'

// Nav tucks away while scrolling down, returns on scroll up or when the pointer nears the top; compacts once scrolled.
export function useNavScroll(locked) {
  const [state, setState] = useState({ compact: false, hidden: false })
  useEffect(() => {
    let last = scrollY
    const set = (compact, hidden) => setState(s => (s.compact === compact && s.hidden === hidden ? s : { compact, hidden }))
    const onScroll = () => {
      const y = scrollY, dy = y - last
      if (Math.abs(dy) < 6) return
      setState(s => {
        const compact = y > 80
        const hidden = !locked && dy > 0 && y > 500
        return s.compact === compact && s.hidden === hidden ? s : { compact, hidden }
      })
      last = y
    }
    const onMove = e => { if (e.clientY < 110) setState(s => (s.hidden ? { ...s, hidden: false } : s)) }
    if (locked) set(scrollY > 80, false)
    addEventListener('scroll', onScroll, { passive: true })
    addEventListener('mousemove', onMove, { passive: true })
    return () => { removeEventListener('scroll', onScroll); removeEventListener('mousemove', onMove) }
  }, [locked])
  return state
}

const magnet = e => {
  const el = e.currentTarget, r = el.getBoundingClientRect()
  el.style.setProperty('--mx', `${(e.clientX - r.left - r.width / 2) * 0.18}px`)
  el.style.setProperty('--my', `${(e.clientY - r.top - r.height / 2) * 0.3}px`)
}
const unmagnet = e => { e.currentTarget.style.setProperty('--mx', '0px'); e.currentTarget.style.setProperty('--my', '0px') }

export function PillNav({ items, active, onPalette }) {
  const nav = useRef(), track = useRef(), links = useRef([]), ind = useRef(), leave = useRef()
  const [hover, setHover] = useState(null)
  const [box, setBox] = useState(null)
  const [, remeasure] = useState(0)
  const activeIdx = items.findIndex(([h]) => h.slice(1) === active)
  const target = hover ?? (activeIdx >= 0 ? activeIdx : null)

  // Re-measure whenever a link's outer size changes (fonts, window) and again once any nav transition settles
  // (the compact padding/font shrink), batched to one measure per frame.
  useEffect(() => {
    let raf = 0
    const kick = () => { cancelAnimationFrame(raf); raf = requestAnimationFrame(() => remeasure(n => n + 1)) }
    const ro = new ResizeObserver(kick)
    links.current.forEach(el => el && ro.observe(el, { box: 'border-box' }))
    const el = nav.current
    el.addEventListener('transitionend', kick)
    document.fonts?.ready.then(kick)
    return () => { ro.disconnect(); el.removeEventListener('transitionend', kick); cancelAnimationFrame(raf) }
  }, [items])

  // Slide the highlight under the hovered link, or back to the current section (sub-pixel, relative to the track).
  useLayoutEffect(() => {
    const el = links.current[target]
    let next = null
    if (el && track.current) {
      const a = el.getBoundingClientRect(), t = track.current.getBoundingClientRect()
      next = { x: Math.round((a.left - t.left) * 2) / 2, w: Math.round(a.width * 2) / 2 }
    }
    setBox(b => (b === next || (b && next && b.x === next.x && b.w === next.w) ? b : next))
  })

  // Progress through the current section, drawn inside the highlight.
  useEffect(() => {
    const on = () => {
      const el = active && document.getElementById(active)
      if (!el || !ind.current) return
      const r = el.getBoundingClientRect()
      ind.current.style.setProperty('--p', Math.min(1, Math.max(0, (innerHeight * 0.5 - r.top) / r.height)))
    }
    on()
    addEventListener('scroll', on, { passive: true })
    return () => removeEventListener('scroll', on)
  }, [active])

  const enter = i => { clearTimeout(leave.current); setHover(i) }
  const exit = () => { leave.current = setTimeout(() => setHover(null), 140) }

  return (
    <nav ref={nav} className={`pill-nav ${hover != null ? 'hovering' : ''}`} aria-label="Sections" onMouseLeave={exit}>
      {/* track clips the highlight to the pill's shape, so its spring can't poke outside the nav */}
      <span ref={track} className="pill-track" aria-hidden="true">
        {box && <span ref={ind} className="pill-ind" style={{ translate: `${box.x}px 0`, width: box.w }} />}
      </span>
      {items.map(([href, label], i) => (
        <a
          key={href} href={href} ref={el => (links.current[i] = el)}
          className={i === target ? 'on' : ''} aria-current={i === activeIdx ? 'true' : undefined}
          onMouseEnter={() => enter(i)} onFocus={() => enter(i)} onBlur={exit}
          onMouseMove={magnet} onMouseOut={unmagnet}
        >
          <span className="lbl">
            <small className="pn" aria-hidden="true">0{i + 1}</small>
            {href === '#live' && <i className="live-dot" aria-hidden="true" />}
            {label}
          </span>
        </a>
      ))}
      <button className="kbd" onClick={onPalette} aria-label="Open section search (Ctrl or Cmd + K)">⌘</button>
      {hover != null && box && (
        <div className="nav-peek" style={{ '--x': `${box.x + box.w / 2}px` }} aria-hidden="true">
          <div className="peek-inner" key={hover}>
            <span className="pixel peek-n">0{hover + 1}</span>
            <span className="peek-text"><strong>{items[hover][1]}</strong><small>{items[hover][2]}</small></span>
            <span className="peek-go mono">↵</span>
          </div>
        </div>
      )}
    </nav>
  )
}

export function MenuButton({ open, onClick }) {
  return (
    <button className={`burger ${open ? 'open' : ''}`} onClick={onClick} aria-label={open ? 'Close menu' : 'Open menu'} aria-expanded={open}>
      <i /><i />
    </button>
  )
}

export function MobileMenu({ items, open, onClose, active }) {
  const ref = useRef()
  useEffect(() => {
    const d = ref.current
    if (open && !d.open) d.showModal()
    if (!open && d.open) d.close()
  }, [open])
  return (
    <dialog ref={ref} className="mmenu" aria-label="Menu" onClose={onClose}>
      <div className="mmenu-head">
        <span className="mono caps">Kavrix · Menu</span>
        <MenuButton open onClick={onClose} />
      </div>
      <nav aria-label="Sections">
        {items.map(([href, label, desc], i) => (
          <a key={href} href={href} onClick={onClose} className={active === href.slice(1) ? 'on' : ''} style={{ '--i': i }}>
            <span className="pixel">0{i + 1}</span>
            <span className="mm-text"><strong>{label}</strong><small>{desc}</small></span>
            <span aria-hidden="true">↗</span>
          </a>
        ))}
      </nav>
      <p className="mono caps muted mmenu-foot">CipherCop 2026 · Team Haven · Hyderabad</p>
    </dialog>
  )
}
