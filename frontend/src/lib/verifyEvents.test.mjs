// Run from this folder:  node --test verifyEvents.test.mjs
import { test } from 'node:test'
import assert from 'node:assert/strict'
import fs from 'node:fs'
import path from 'node:path'
import { fileURLToPath } from 'node:url'
import { GENESIS_HASH, canonicalJson, eventDigest, parseJsonl, sha256Hex, verifyRun } from './verifyEvents.mjs'

const root = path.join(path.dirname(fileURLToPath(import.meta.url)), '../../public/data/incidents')
const load = id => {
  const dir = path.join(root, id)
  return {
    events: parseJsonl(fs.readFileSync(path.join(dir, 'events.jsonl'), 'utf8')),
    summary: JSON.parse(fs.readFileSync(path.join(dir, 'summary.json'), 'utf8')),
    fetchBytes: async p => { const f = path.join(dir, p); return fs.existsSync(f) ? fs.readFileSync(f) : null },
  }
}
const clone = x => JSON.parse(JSON.stringify(x))
const rehash = async events => { // what a skilled forger does: recompute the chain after an edit
  let prev = GENESIS_HASH
  for (const e of events) { e.prev_hash = prev; e.event_hash = await eventDigest(e); prev = e.event_hash }
  return prev
}

test('canonical JSON gives the same text and hash as the Python side', async () => {
  const sample = { id: 'abc', type: 'crowd_risk', risk: 64.0, motion: 0.0, density_peak: 6.41, count: 75,
    hotspot: { x: 0.21, y: 0.558 }, lat: 17.4239, heatmap: null, ok: true, list: [1.0, 2.5, 'é'] }
  assert.equal(canonicalJson(sample), '{"count":75,"density_peak":6.41,"heatmap":null,"hotspot":{"x":0.21,"y":0.558},"id":"abc","lat":17.4239,"list":[1,2.5,"é"],"motion":0,"ok":true,"risk":64,"type":"crowd_risk"}')
  assert.equal(await eventDigest(sample), 'd4476d4c4a29d6d02fdb1d4355d2163253af5b026fde56c0b4021a9461be2cd7')
  assert.throws(() => canonicalJson({ x: NaN }))
})

for (const id of ['concert', 'jl030']) {
  test(`${id}: the untouched run verifies, snapshots included`, async () => {
    const r = await verifyRun(load(id))
    assert.deepEqual(r.problems, [])
    assert.equal(r.ok, true)
    assert.equal(r.snapshotsChecked, r.events)   // every event has its snapshot in the bundle
    assert.equal(r.head, load(id).summary.chain_head)
  })
}

test('every kind of tampering is caught', async () => {
  const base = load('concert')
  const kinds = async mutate => {
    const run = { ...base, events: clone(base.events), summary: clone(base.summary) }
    const out = (await mutate(run)) || run
    return (await verifyRun(out)).problems.map(p => p.kind)
  }
  assert.ok((await kinds(r => { r.events[3].risk = 5 })).includes('edited'))
  assert.ok((await kinds(r => { r.events.splice(4, 1) })).includes('chain-broken'))
  assert.ok((await kinds(r => { [r.events[2], r.events[3]] = [r.events[3], r.events[2]] })).includes('chain-broken'))
  assert.ok((await kinds(async r => {
    const f = { ...r.events[2], id: 'forged000000', risk: 99, level: 'HIGH' }
    f.event_hash = await eventDigest(f) // the forger even recomputes the event's own hash
    r.events.splice(3, 0, f)
  })).includes('chain-broken'))
  assert.ok((await kinds(r => { r.fetchBytes = async p => { const b = Buffer.from(base.fetchBytes ? fs.readFileSync(path.join(root, 'concert', p)) : []); b[10] ^= 1; return b } })).includes('snapshot-modified'))
  assert.ok((await kinds(r => { r.fetchBytes = async () => null })).includes('snapshot-missing'))
  assert.ok((await kinds(r => { r.events = r.events.slice(0, -3) })).includes('truncated'))
})

test('a full chain rewrite passes alone but is caught by a trusted head', async () => {
  const base = load('concert')
  const events = clone(base.events)
  events[3].risk = 5
  const forgedHead = await rehash(events)
  const summary = { ...base.summary, chain_head: forgedHead }
  const plain = await verifyRun({ events, summary })          // no snapshots, no anchor: looks consistent
  assert.equal(plain.ok, true, 'a rewritten chain is self-consistent: this is the known limit')
  const anchored = await verifyRun({ events, summary, expectHead: base.summary.chain_head })
  assert.equal(anchored.ok, false)
  assert.ok(anchored.problems.some(p => p.kind === 'head-mismatch'))
})

test('sha256Hex matches a known value', async () => {
  assert.equal(await sha256Hex('abc'), 'ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad')
})
