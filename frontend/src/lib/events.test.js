// Run: npm test
import { test } from 'node:test'
import assert from 'node:assert/strict'
import { GENESIS, mockEvent, mockStream, placeName, PLACES, seal, toDate, verifyHash, verifyLink } from './events.js'

test('sealed mock events follow the shared contract', async () => {
  const e = await seal(mockEvent('crowd_risk', PLACES[0], 87.4), GENESIS)
  assert.deepEqual(Object.keys(e).sort(), ['evidence_hash', 'id', 'lat', 'lng', 'prev_hash', 'risk', 'timestamp', 'type'])
  assert.equal(e.risk, 87)
  assert.match(e.evidence_hash, /^[0-9a-f]{64}$/)
})

test('hash chain verifies, and any edit or broken link is caught', async () => {
  const a = await seal(mockEvent('dispatch', PLACES[1], 60), GENESIS)
  const b = await seal(mockEvent('crowd_risk', PLACES[0], 70), a.evidence_hash)
  assert.equal(await verifyHash(a), true)
  assert.equal(await verifyHash(b), true)
  assert.equal(verifyLink(a, null), true)
  assert.equal(verifyLink(b, a), true)
  assert.equal(await verifyHash({ ...b, risk: 20 }), false) // edited field
  assert.equal(verifyLink(b, { ...a, evidence_hash: GENESIS }), false) // previous event swapped
})

test('placeName snaps to known hotspots, else prints coords', () => {
  assert.equal(placeName({ lat: 17.424, lng: 78.474 }), 'Tank Bund')
  assert.equal(placeName({ lat: 10, lng: 10 }), '10.0000, 10.0000')
})

test('toDate accepts epoch seconds and ISO strings', () => {
  assert.equal(toDate(1700000000).getTime(), 1700000000000)
  assert.equal(toDate('2026-11-20T10:00:00Z').getUTCHours(), 10)
})

test('mockStream starts with a chained dispatch and can be stopped', async () => {
  const seen = []
  const stop = mockStream(e => seen.push(e), 1000)
  stop()
  await new Promise(r => setTimeout(r, 50))
  assert.equal(seen[0].type, 'dispatch')
  assert.equal(seen[0].prev_hash, GENESIS)
})
