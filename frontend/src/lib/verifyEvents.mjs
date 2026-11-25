// verifyEvents.mjs: check a crowd_risk run in the browser (or Node) with no backend.
//
// Same checks as verify_events.py, same hash rules as crowd_risk.canonical_json:
//   1. each event's event_hash matches its contents      (edited values)
//   2. each prev_hash equals the previous event_hash      (deleted / inserted / re-ordered events)
//   3. each snapshot image hashes to evidence_hash        (edited or missing images)
//   4. summary.json's event count and chain_head match    (events cut off the end)
//
// Limit, stated honestly: a chain proves the log is consistent with itself. Someone who can rewrite
// every event AND the summary can forge a new chain. Pass `expectHead` (a chain_head you got from a
// source the attacker cannot edit, e.g. the backend's signed store) to catch that.
//
// Usage in the browser:
//   const text = await (await fetch('/data/incidents/concert/events.jsonl')).text()
//   const result = await verifyRun({
//     events: parseJsonl(text),
//     fetchBytes: p => fetch('/data/incidents/concert/' + p).then(r => (r.ok ? r.arrayBuffer() : null)),
//     summary: await (await fetch('/data/incidents/concert/summary.json')).json(),
//   })
//   result.ok, result.problems, result.head

export const GENESIS_HASH = '0'.repeat(64)

// Canonical JSON: keys sorted, no spaces. JSON.stringify already prints 64.0 as 64, which is exactly
// what the Python side does on purpose, so the two languages produce identical text.
export function canonicalJson(v) {
  if (Array.isArray(v)) return '[' + v.map(canonicalJson).join(',') + ']'
  if (v && typeof v === 'object') {
    return '{' + Object.keys(v).sort().map(k => JSON.stringify(k) + ':' + canonicalJson(v[k])).join(',') + '}'
  }
  if (typeof v === 'number' && !Number.isFinite(v)) throw new Error('NaN/Infinity cannot be hashed')
  return JSON.stringify(v)
}

export async function sha256Hex(bytes) {
  const data = typeof bytes === 'string' ? new TextEncoder().encode(bytes) : bytes
  const digest = await globalThis.crypto.subtle.digest('SHA-256', data)
  return Array.from(new Uint8Array(digest), b => b.toString(16).padStart(2, '0')).join('')
}

export async function eventDigest(event) {
  const { event_hash, ...body } = event // eslint-disable-line no-unused-vars
  return sha256Hex(canonicalJson(body))
}

export function parseJsonl(text) {
  return text.split('\n').filter(l => l.trim()).map(l => JSON.parse(l))
}

/**
 * Verify a whole run.
 * @param {object[]} events        parsed events.jsonl, in file order
 * @param {(path:string)=>Promise<ArrayBuffer|Uint8Array|null>} [fetchBytes]
 *        returns the snapshot bytes for an event's `heatmap` path, or null if unavailable.
 *        Omit it to skip snapshot checks (then `snapshotsChecked` is 0).
 * @param {object} [summary]       parsed summary.json (checks chain_events / chain_head)
 * @param {string} [expectHead]    trusted final hash
 * @returns {Promise<{ok:boolean, events:number, head:string|null, snapshotsChecked:number, problems:{index:number,id:string,kind:string,message:string}[]}>}
 */
export async function verifyRun({ events, fetchBytes, summary, expectHead }) {
  const problems = []
  const add = (index, e, kind, message) => problems.push({ index, id: e?.id ?? '?', kind, message })
  let prev = GENESIS_HASH
  let head = null
  let snapshotsChecked = 0

  for (let i = 0; i < events.length; i++) {
    const e = events[i]
    const where = `event ${i + 1} (${e.id}, t=${e.video_t})`
    if (!e.event_hash || !e.prev_hash) {
      add(i, e, 'no-chain', `${where}: no chain fields (log made by an older version)`)
      continue
    }
    if ((await eventDigest(e)) !== e.event_hash) add(i, e, 'edited', `${where}: contents do not match its hash (the event was edited)`)
    if (e.prev_hash !== prev) add(i, e, 'chain-broken', `${where}: chain broken here (an event was deleted, inserted or reordered)`)
    prev = e.event_hash // continue from what the log claims, so one break is reported once
    head = e.event_hash

    if (fetchBytes && e.heatmap) {
      const bytes = await fetchBytes(e.heatmap)
      if (!bytes) add(i, e, 'snapshot-missing', `${where}: snapshot ${e.heatmap} is missing`)
      else {
        snapshotsChecked++
        if ((await sha256Hex(bytes)) !== e.evidence_hash) add(i, e, 'snapshot-modified', `${where}: snapshot ${e.heatmap} was modified (hash differs from evidence_hash)`)
      }
    }
  }

  if (summary) {
    if (summary.chain_events !== undefined && summary.chain_events !== events.length) {
      add(events.length, null, 'truncated', `summary says ${summary.chain_events} events but the log has ${events.length} (events were removed from or added to the end)`)
    }
    if (summary.chain_head && head && summary.chain_head !== head) add(events.length, null, 'head-mismatch', 'final hash differs from summary.json chain_head')
  }
  if (expectHead && head !== expectHead) add(events.length, null, 'head-mismatch', `final hash ${String(head).slice(0, 16)}... does not match the expected head ${expectHead.slice(0, 16)}...`)
  return { ok: problems.length === 0, events: events.length, head, snapshotsChecked, problems }
}
