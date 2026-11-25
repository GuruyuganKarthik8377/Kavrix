import { useEffect, useReducer } from 'react'
import { api, createChain, mockEvent, session, subscribe, toDate } from './events'

// One event stream and one audit log for the whole app, so every page sees the same hash chain and decisions.
let events = [], source = 'connecting', started = false, audit = []
const subs = new Set()
const notify = () => subs.forEach(f => f())
// Dedupe by id: the backend re-sends recent history on every (re)connect.
const push = e => {
  const i = events.findIndex(x => x.id === e.id)
  events = i >= 0 ? events.map((x, j) => (j === i ? e : x)) : [...events.slice(-299), e]
  notify()
}
const chain = createChain(push)

export function useEvents() {
  const [, force] = useReducer(n => n + 1, 0)
  useEffect(() => {
    if (!started) {
      started = true
      subscribe(push, s => { source = s; notify() }, chain)
    }
    subs.add(force)
    return () => subs.delete(force)
  }, [])
  return { events, source, audit }
}

const live = () => source === 'live' && Boolean(session().token)

// A 112 call: live -> POST /dispatch/112 (backend picks the nearest drone and seals a dispatch event);
// simulated -> joins the in-browser chain.
export const canRaiseCall = () => true
export async function raiseCall(lat, lng, kind = 'emergency', description = '112 call raised from the dispatch map') {
  if (live()) {
    try { await api('/dispatch/112', { method: 'POST', json: { lat, lng, kind, description } }) } catch (err) { alert(`Dispatch failed: ${err.message}`) }
    return
  }
  chain.emit({ ...mockEvent('dispatch', { lat, lng }, 60), lat, lng })
}

// Officer decision. For a live watchlist match it is sent to the backend (human-in-the-loop review), which raises
// the officer task on Confirm and writes the decision to its tamper-evident audit log.
export function logDecision(e, d, who) {
  audit = [...audit, { id: e.id, type: e.type, risk: e.risk, lat: e.lat, lng: e.lng, hash: e.evidence_hash || e.event_hash, d, who, at: new Date() }]
  notify()
  if (live() && e.type === 'watchlist_match' && e.review_id) {
    api(`/reviews/${e.review_id}`, { method: 'POST', json: { decision: d === 'confirm' ? 'confirm' : 'reject', note: who } })
      .catch(err => console.warn('review not sent:', err.message))
  }
}
export const decisionOf = id => audit.findLast(a => a.id === id)

export const ALERT_AT = 65 // the AI's HIGH level starts here
export const riskColor = r => (r >= 80 ? '#e5484d' : r >= 50 ? '#f5a524' : '#30a46c')
export const LABEL = { crowd_risk: 'Crowd risk', watchlist_match: 'Watchlist match', dispatch: 'Drone dispatched', crowd_alert: 'Crowd alert' }
export const clock = e => toDate(e.timestamp).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit', second: '2-digit' })
// crowd_alert events are the AI's officer alerts; a crowd_risk reading alone no longer floods the queue.
export const isAlert = e => e.type === 'watchlist_match' || e.type === 'crowd_alert' || (e.type === 'crowd_risk' && e.risk >= ALERT_AT && !e.run_id)
