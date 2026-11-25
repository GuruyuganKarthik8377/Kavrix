// Event contract shared by all roles:
// { id, type: 'crowd_risk' | 'watchlist_match' | 'dispatch', lat, lng, risk: 0-100, timestamp, evidence_hash, prev_hash }
// evidence_hash = SHA-256(prev_hash + canonical(event)), so events form a hash chain.
// Crowd events from the AI pipeline carry their own chain: event_hash = SHA-256(canonical JSON), see verifyEvents.mjs.
import { eventDigest, GENESIS_HASH } from './verifyEvents.mjs'

// Backend: VITE_API_URL, or the page's own origin when the Haven backend serves the built app (port 8000).
export const API_URL = import.meta.env?.VITE_API_URL ||
  (typeof location !== 'undefined' && location.port === '8000' ? location.origin : undefined)
const WS_URL = import.meta.env?.VITE_WS_URL || (API_URL ? `${API_URL.replace(/^http/, 'ws')}/ws/events` : undefined)

// ---- backend session (test logins from backend/.env)
const ls = {
  get: k => { try { return localStorage.getItem(k) } catch { return null } },
  set: (k, v) => { try { v == null ? localStorage.removeItem(k) : localStorage.setItem(k, v) } catch { /* private mode */ } },
}
export const session = () => ({ token: ls.get('haven.token'), role: ls.get('haven.role'), email: ls.get('haven.email') })
export async function login(email, password) {
  const r = await fetch(`${API_URL}/auth/login`, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ email, password }) })
  if (!r.ok) throw new Error(r.status === 401 ? 'Wrong email or password' : `Login failed (${r.status})`)
  const j = await r.json()
  ls.set('haven.token', j.access_token); ls.set('haven.role', j.role); ls.set('haven.email', j.email)
  return j
}
export function logout() { ['token', 'role', 'email'].forEach(k => ls.set(`haven.${k}`, null)) }
export async function api(path, { method = 'GET', json } = {}) {
  const { token } = session()
  const r = await fetch(`${API_URL}${path}`, {
    method, headers: { ...(token ? { Authorization: `Bearer ${token}` } : {}), ...(json ? { 'Content-Type': 'application/json' } : {}) },
    body: json ? JSON.stringify(json) : undefined,
  })
  if (r.status === 401) { logout(); throw new Error('Session expired: sign in again') }
  const body = await r.json().catch(() => ({}))
  if (!r.ok) throw new Error(body.detail || `${r.status}`)
  return body
}
export const withToken = url => `${API_URL || ''}${url}${url.includes('?') ? '&' : '?'}token=${encodeURIComponent(session().token || '')}`

export const HQ = [17.4007, 78.4747] // Hyderabad Police Commissionerate, Basheer Bagh

export const PLACES = [
  { name: 'Tank Bund', lat: 17.4239, lng: 78.4738 },
  { name: 'Charminar', lat: 17.3616, lng: 78.4747 },
  { name: 'Secunderabad Stn', lat: 17.4337, lng: 78.5016 },
  { name: 'HITEC City', lat: 17.4435, lng: 78.3772 },
  { name: 'Ameerpet Metro', lat: 17.4375, lng: 78.4482 },
]

export function placeName({ lat, lng }) {
  const near = PLACES.find(p => Math.hypot(p.lat - lat, p.lng - lng) < 0.012) // ~1.3 km
  return near ? near.name : `${lat.toFixed(4)}, ${lng.toFixed(4)}`
}

export const toDate = ts => new Date(typeof ts === 'number' && ts < 1e12 ? ts * 1000 : ts)

const jitter = () => (Math.random() - 0.5) * 0.004

export const GENESIS = '0'.repeat(64)
// Fixed field order, so the same event always hashes the same way.
export const canonical = e => JSON.stringify([e.id, e.type, e.lat, e.lng, e.risk, e.timestamp])
// crypto.subtle only exists on https or localhost; open the demo via localhost, not a LAN IP.
export async function sha256(text) {
  const buf = await crypto.subtle.digest('SHA-256', new TextEncoder().encode(text))
  return Array.from(new Uint8Array(buf), b => b.toString(16).padStart(2, '0')).join('')
}
export async function seal(e, prevHash) {
  return { ...e, prev_hash: prevHash, evidence_hash: await sha256(prevHash + canonical(e)) }
}

export function mockEvent(type, place, risk) {
  return {
    id: crypto.randomUUID(),
    type,
    lat: place.lat + jitter(),
    lng: place.lng + jitter(),
    risk: Math.round(risk),
    timestamp: new Date().toISOString(),
  }
}

// Scripted demo: a 112 call at Charminar, then a crowd building up at Tank Bund until a drone is dispatched.
// One chain per session. Hashing is async; the promise queue keeps events (and their links) in order.
export function createChain(onEvent) {
  let prev = GENESIS, tail = Promise.resolve()
  return { emit: e => { tail = tail.then(async () => { const s = await seal(e, prev); prev = s.evidence_hash; onEvent(s) }) } }
}

export function mockStream(onEvent, ms = 2200, chain = createChain(onEvent)) {
  let tick = 0
  const emit = chain.emit
  emit(mockEvent('dispatch', PLACES[1], 60))
  const id = setInterval(() => {
    tick = (tick + 1) % 40
    const surge = Math.min(96, 30 + tick * 3.2)
    if (tick % 9 === 4) emit(mockEvent('watchlist_match', PLACES[2 + (tick % 3)], 82 + Math.random() * 14))
    else if (tick % 2) emit(mockEvent('crowd_risk', PLACES[0], surge))
    else emit(mockEvent('crowd_risk', PLACES[1 + Math.floor(Math.random() * 4)], 10 + Math.random() * 50))
    if (surge >= 80 && tick % 6 === 0) emit(mockEvent('dispatch', PLACES[0], surge))
  }, ms)
  return () => clearInterval(id)
}

const valid = e => e && typeof e.type === 'string' && Number.isFinite(e.lat) && Number.isFinite(e.lng)

// Live backend when signed in (reconnects with backoff, so a backend restart mid-demo recovers);
// otherwise the scripted simulation. onSource: 'live' | 'simulated' | 'reconnecting'.
export function subscribe(onEvent, onSource, chain) {
  let stopMock, ws, closed = false, tries = 0, everLive = false
  const startMock = () => {
    if (stopMock) return
    onSource('simulated')
    stopMock = mockStream(onEvent, undefined, chain)
  }
  const { token } = session()
  if (!WS_URL || !token) {
    startMock()
    return () => stopMock?.()
  }
  const open = () => {
    ws = new WebSocket(`${WS_URL}?token=${encodeURIComponent(token)}`)
    ws.onopen = () => { tries = 0; everLive = true; onSource('live') }
    ws.onmessage = m => {
      try {
        const e = JSON.parse(m.data)
        if (valid(e)) onEvent(e)
      } catch { /* ignore malformed frames */ }
    }
    ws.onclose = ev => {
      if (closed) return
      if (ev.code === 4401) { logout(); startMock(); return } // token expired
      if (!everLive && tries >= 2) { startMock(); return } // backend never reachable: demo still works
      onSource('reconnecting')
      setTimeout(open, Math.min(8000, 500 * 2 ** tries++))
    }
  }
  open()
  return () => { closed = true; ws?.close(); stopMock?.() }
}

// Recompute the event's own hash. AI crowd events use event_hash (canonical JSON); the others use evidence_hash.
export async function verifyHash(e) {
  if (e.event_hash) return (await eventDigest(e)) === e.event_hash
  return Boolean(e.evidence_hash && e.prev_hash) && (await sha256(e.prev_hash + canonical(e))) === e.evidence_hash
}
const ownHash = e => e.event_hash || e.evidence_hash
// Two chains share the stream: each AI run (run_id) and the ops chain (dispatch / watchlist). Link within the chain.
export function verifyLinkAt(list, i) {
  const e = list[i]
  const same = x => (e.run_id ? x.run_id === e.run_id : !x.run_id)
  for (let j = i - 1; j >= 0; j--) if (same(list[j])) return e.prev_hash === ownHash(list[j])
  const first = e.run_id ? e.seq === 0 : false
  return e.prev_hash === (e.run_id ? GENESIS_HASH : GENESIS) && (first || !e.run_id) ? true : null // null: earlier events not loaded
}
export const verifyLink = (e, prevEvent) => (prevEvent ? e.prev_hash === ownHash(prevEvent) : e.prev_hash === GENESIS)

export async function verifyEvidence(e) {
  if (!API_URL || !session().token) return verifyHash(e)
  try { return (await api(`/verify/${encodeURIComponent(e.id)}`)).valid === true } catch { return false }
}
