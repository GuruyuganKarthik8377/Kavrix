// Pure models behind the Dispatch page and the Privacy lab. Every constant is an assumption, shown on the page.
import { toDate } from './events.js'

/* ---------- dispatch ---------- */
export const SPEED_MS = 15 // assumed cruise speed, about 54 km/h
export const ON_SCENE_S = 240 // assumed time a drone stays over an incident
export const FLEET = [
  { id: 'D-01', base: 'Basheer Bagh HQ', at: [17.4007, 78.4747] },
  { id: 'D-02', base: 'Gachibowli', at: [17.4401, 78.3489] },
  { id: 'D-03', base: 'Secunderabad', at: [17.4399, 78.4983] },
]

export function haversineKm(a, b) {
  const R = 6371, rad = x => (x * Math.PI) / 180
  const dLat = rad(b[0] - a[0]), dLng = rad(b[1] - a[1])
  const h = Math.sin(dLat / 2) ** 2 + Math.cos(rad(a[0])) * Math.cos(rad(b[0])) * Math.sin(dLng / 2) ** 2
  return 2 * R * Math.asin(Math.sqrt(h))
}

// Replay dispatch events in time order; each goes to the nearest drone that is free at that moment.
// A drone is busy for the flight out, its time on scene and the flight back to base.
export function assignFleet(dispatches, now = Date.now()) {
  const busyUntil = Object.fromEntries(FLEET.map(d => [d.id, 0]))
  const rows = dispatches.map(e => {
    const t = toDate(e.timestamp).getTime()
    const free = FLEET.filter(d => busyUntil[d.id] <= t)
    const pool = free.length ? free : FLEET // all busy: queue on the nearest one
    const best = pool.map(d => ({ d, km: haversineKm(d.at, [e.lat, e.lng]) })).sort((a, b) => a.km - b.km)[0]
    const etaS = (best.km * 1000) / SPEED_MS
    const start = Math.max(t, busyUntil[best.d.id])
    busyUntil[best.d.id] = start + (etaS * 2 + ON_SCENE_S) * 1000
    return { e, drone: best.d.id, km: best.km, etaS, start, arrive: start + etaS * 1000, queued: !free.length }
  })
  const status = FLEET.map(d => {
    const last = rows.findLast(r => r.drone === d.id)
    if (!last) return { ...d, state: 'At base' }
    const leave = last.arrive + ON_SCENE_S * 1000, back = leave + last.etaS * 1000
    const state = now < last.start ? 'Queued' : now < last.arrive ? 'En route' : now < leave ? 'On scene' : now < back ? 'Returning' : 'At base'
    return { ...d, state, job: last }
  })
  return { rows, status }
}

/* ---------- what can a drone see ---------- */
// Pinhole camera: pixels per metre at the face = image width / (2 · slant distance · tan(FOV / 2)).
// Faces are vertical, so looking down at them foreshortens their height by cos(depression angle).
export const FACE_W = 0.15, FACE_H = 0.22 // typical adult face, metres
export const TIERS = [ // rule of thumb, not validated; face-recognition models such as ArcFace work on 112 px crops
  { min: 0, key: 'none', label: 'Face not detectable' },
  { min: 12, key: 'detect', label: 'Detectable and blurrable, not identifiable' },
  { min: 40, key: 'weak', label: 'Matching unreliable' },
  { min: 80, key: 'match', label: 'Matching feasible' },
]

export function faceView({ altitude, distance, fovDeg = 82, widthPx = 1920 }) {
  const slant = Math.hypot(altitude, distance)
  const pxPerM = widthPx / (2 * Math.max(slant, 0.01) * Math.tan((fovDeg * Math.PI) / 360))
  const depression = Math.atan2(altitude, distance)
  const wPx = FACE_W * pxPerM
  const hPx = FACE_H * pxPerM * Math.cos(depression)
  const px = Math.min(wPx, hPx)
  const tier = TIERS.findLast(t => px >= t.min)
  return { slant, wPx, hPx, px, depressionDeg: (depression * 180) / Math.PI, tier }
}

// Where each drone is now: at base, on scene, or interpolated along its straight-line route.
export function fleetPositions(status, now = Date.now()) {
  return status.map(d => {
    const j = d.job
    if (!j || d.state === 'At base' || d.state === 'Queued') return { ...d, pos: d.at }
    const target = [j.e.lat, j.e.lng]
    if (d.state === 'On scene') return { ...d, pos: target }
    const f = d.state === 'En route'
      ? (now - j.start) / (j.arrive - j.start)
      : 1 - (now - j.arrive - ON_SCENE_S * 1000) / (j.etaS * 1000)
    const k = Math.min(1, Math.max(0, f))
    return { ...d, pos: [d.at[0] + (target[0] - d.at[0]) * k, d.at[1] + (target[1] - d.at[1]) * k] }
  })
}
