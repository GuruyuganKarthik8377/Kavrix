import { useEffect, useRef, useState } from 'react'

// 4x4 Bayer matrix for ordered dithering.
const BAYER = [0, 8, 2, 10, 12, 4, 14, 6, 3, 11, 1, 9, 15, 7, 13, 5]
const rng = seed => () => (seed = (seed * 16807) % 2147483647) / 2147483647
const gauss = r => (r() + r() + r() - 1.5) / 1.5

// Scenes are drawn in grayscale at low resolution, then dithered to two tones.
const SCENES = {
  crowd(x, w, h) {
    const g = x.createLinearGradient(0, 0, 0, h)
    g.addColorStop(0, '#c8c8c8'); g.addColorStop(1, '#5a5a5a')
    x.fillStyle = g; x.fillRect(0, 0, w, h)
    x.strokeStyle = '#eee'; x.lineWidth = 6
    x.beginPath(); x.moveTo(0, h * 0.82); x.lineTo(w, h * 0.7); x.stroke()
    const r = rng(7)
    for (let i = 0; i < 1100; i++) {
      x.fillStyle = r() > 0.45 ? '#141414' : '#f4f4f4'
      x.beginPath(); x.arc(w * 0.55 + gauss(r) * w * 0.55, h * 0.5 + gauss(r) * h * 0.6, 1 + r() * 1.3, 0, 7); x.fill()
    }
  },
  city(x, w, h) {
    x.fillStyle = '#7a7a7a'; x.fillRect(0, 0, w, h)
    const r = rng(3)
    for (let by = 2; by < h; by += 22) for (let bx = 2; bx < w; bx += 26) {
      const v = 60 + Math.floor(r() * 170)
      x.fillStyle = `rgb(${v},${v},${v})`; x.fillRect(bx, by, 20 + r() * 4, 16 + r() * 4)
    }
    x.strokeStyle = '#1c1c1c'; x.lineWidth = 12
    x.beginPath(); x.moveTo(-10, h * 0.2); x.bezierCurveTo(w * 0.3, h * 0.6, w * 0.6, h * 0.1, w + 10, h * 0.7); x.stroke()
    x.fillStyle = '#fff'; x.beginPath(); x.arc(w * 0.62, h * 0.42, 9, 0, 7); x.fill()
  },
  drone(x, w, h) {
    const g = x.createRadialGradient(w * 0.5, h * 0.35, 4, w * 0.5, h * 0.5, w * 0.75)
    g.addColorStop(0, '#f6f6f6'); g.addColorStop(1, '#6a6a6a')
    x.fillStyle = g; x.fillRect(0, 0, w, h)
    const cx = w / 2, cy = h / 2
    x.strokeStyle = '#1a1a1a'; x.lineWidth = 5
    for (const [dx, dy] of [[-1, -1], [1, -1], [-1, 1], [1, 1]]) {
      x.beginPath(); x.moveTo(cx, cy); x.lineTo(cx + dx * w * 0.28, cy + dy * h * 0.3); x.stroke()
      x.fillStyle = 'rgba(40,40,40,.35)'; x.beginPath(); x.ellipse(cx + dx * w * 0.28, cy + dy * h * 0.3, w * 0.13, h * 0.06, 0, 0, 7); x.fill()
    }
    x.fillStyle = '#2a2a2a'; x.beginPath(); x.ellipse(cx, cy, w * 0.1, h * 0.17, 0, 0, 7); x.fill()
    x.fillStyle = '#ddd'; x.beginPath(); x.arc(cx, cy + h * 0.12, 4, 0, 7); x.fill()
  },
  heat(x, w, h) {
    x.fillStyle = '#1e1e1e'; x.fillRect(0, 0, w, h)
    const r = rng(11)
    for (let i = 0; i < 7; i++) {
      const px = r() * w, py = r() * h, rad = 14 + r() * 34
      const g = x.createRadialGradient(px, py, 1, px, py, rad)
      g.addColorStop(0, 'rgba(255,255,255,.95)'); g.addColorStop(1, 'rgba(255,255,255,0)')
      x.fillStyle = g; x.fillRect(0, 0, w, h)
    }
  },
  screens(x, w, h) {
    x.fillStyle = '#161616'; x.fillRect(0, 0, w, h)
    const r = rng(5)
    for (let sy = 6; sy < h - 10; sy += 30) for (let sx = 6; sx < w - 10; sx += 38) {
      const v = 70 + Math.floor(r() * 180)
      x.fillStyle = `rgb(${v},${v},${v})`; x.fillRect(sx, sy, 32, 24)
      x.fillStyle = '#111'
      for (let l = 0; l < 3; l++) x.fillRect(sx + 3, sy + 4 + l * 6, 8 + r() * 20, 2)
    }
  },
  faces(x, w, h) {
    x.fillStyle = '#9a9a9a'; x.fillRect(0, 0, w, h)
    const r = rng(9)
    for (let fy = 14; fy < h; fy += 34) for (let fx = 12; fx < w; fx += 28) {
      const g = x.createRadialGradient(fx - 3, fy - 4, 1, fx, fy, 14)
      g.addColorStop(0, '#f2f2f2'); g.addColorStop(1, '#3a3a3a')
      x.fillStyle = g; x.beginPath(); x.ellipse(fx + gauss(r) * 2, fy, 9, 12, 0, 0, 7); x.fill()
    }
    x.strokeStyle = '#000'; x.lineWidth = 3; x.strokeRect(w * 0.45, h * 0.3, 30, 36)
  },
}

function Dither({ scene, w = 150, h = 110 }) {
  const ref = useRef()
  useEffect(() => {
    const c = ref.current, x = c.getContext('2d', { willReadFrequently: true })
    c.width = w; c.height = h
    SCENES[scene](x, w, h)
    const img = x.getImageData(0, 0, w, h), d = img.data
    for (let i = 0; i < d.length; i += 4) {
      const p = i / 4, px = p % w, py = (p / w) | 0
      const lum = d[i] * 0.3 + d[i + 1] * 0.59 + d[i + 2] * 0.11
      const v = lum > (BAYER[(py % 4) * 4 + (px % 4)] + 0.5) * 16 ? 232 : 18
      d[i] = d[i + 1] = d[i + 2] = v; d[i + 3] = 255
    }
    x.putImageData(img, 0, 0)
  }, [scene, w, h])
  return <canvas ref={ref} className="dither" aria-hidden="true" />
}

const TILES = [
  { scene: 'faces', href: '#principles', label: 'Watchlist only', sub: 'Everyone else blurred', size: 'tall' },
  { scene: 'screens', href: '#live', label: 'Control room', sub: 'Live command view', size: 'wide' },
  { scene: 'crowd', href: '#modules', label: 'Crowd density', sub: 'Festival-night surge', size: 'wide' },
  { scene: 'city', href: '#live', label: 'Aerial grid', sub: 'Hyderabad, from 120 m', size: 'mid' },
  { scene: 'drone', href: '#spec', label: 'D-01', sub: 'Reference airframe', size: 'mid' },
  { scene: 'heat', href: '#pipeline', label: 'Hot zones', sub: 'Risk above threshold', size: 'tall' },
]

export default function Field() {
  const [cursor, setCursor] = useState(null)
  return (
    <section id="field" className="field">
      <div
        className="field-strip"
        onMouseMove={e => setCursor(c => c && { ...c, x: e.clientX, y: e.clientY })}
        onMouseLeave={() => setCursor(null)}
      >
        {TILES.map((t, i) => (
          <a
            key={t.label} href={t.href} className={`tile ${t.size} reveal`} style={{ '--d': `${i * 70}ms` }}
            onMouseEnter={e => setCursor({ x: e.clientX, y: e.clientY })}
            aria-label={`${t.label}: ${t.sub}`}
          >
            <Dither scene={t.scene} w={t.size === 'wide' ? 200 : 140} h={t.size === 'tall' ? 190 : 120} />
            <span className="tile-cap"><strong>{t.label}</strong><span>{t.sub}</span></span>
          </a>
        ))}
      </div>
      <p className="mono caps muted field-note">Field studies · generated illustrations, dithered in the browser</p>
      {cursor && <span className="cursor-tag" style={{ left: cursor.x, top: cursor.y }} aria-hidden="true">View section</span>}

      <div className="field-statement">
        <h2 className="pixel field-name reveal">Team Haven</h2>
        <div className="field-side reveal" style={{ '--d': '120ms' }}>
          <p className="mono caps">CipherCop 2026 <i>+</i> Track 1 <i>+</i> Hyderabad, India</p>
          <p className="field-lead">We build drone first response for Indian police that is fast, private and court-ready, and ship it where you can check.</p>
          <div className="field-btns">
            <a href="#live" className="btn-shine">See it live</a>
            <a href="#faq" className="link-arrow">Read the FAQ <span aria-hidden="true">↗</span></a>
          </div>
        </div>
      </div>
    </section>
  )
}
