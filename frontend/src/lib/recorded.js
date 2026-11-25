// The AI pipeline's recorded runs (public/data/incidents). Files there are hash-chained: never edit them.
import { parseJsonl, verifyRun } from './verifyEvents.mjs'

const DATA = '/data/'
const get = (p, as) => fetch(DATA + p).then(r => (r.ok ? r[as]() : Promise.reject(new Error(`${p}: HTTP ${r.status}`))))

export const loadIndex = () => get('incidents/index.json', 'json').then(d => d.incidents)

export async function loadRun(inc) {
  const base = DATA + inc.snapshots // e.g. /data/incidents/concert/
  const [text, summary] = await Promise.all([get(inc.events, 'text'), get(inc.summary, 'json')])
  return { events: parseJsonl(text), summary, base, fetchBytes: p => fetch(base + p).then(r => (r.ok ? r.arrayBuffer() : null)) }
}

// Full check: hashes, links, snapshot images, and summary.json's count and head.
export async function checkRun(run, events = run.events) {
  const t = performance.now()
  const res = await verifyRun({ events, summary: run.summary, fetchBytes: run.fetchBytes })
  return { ...res, ms: performance.now() - t }
}

export const LEVEL_COLOR = { LOW: '#30a46c', MEDIUM: '#f5a524', HIGH: '#e5484d' }
