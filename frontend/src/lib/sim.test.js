import { test } from 'node:test'
import assert from 'node:assert/strict'
import { FLEET, ON_SCENE_S, assignFleet, faceView, haversineKm } from './sim.js'

const call = (lat, lng, t) => ({ id: String(t), type: 'dispatch', lat, lng, risk: 60, timestamp: new Date(t).toISOString() })

test('haversine: Charminar to Tank Bund is about 7 km', () => {
  assert.ok(Math.abs(haversineKm([17.3616, 78.4747], [17.4239, 78.4738]) - 6.93) < 0.1)
})

test('nearest free drone is assigned, and a busy one is skipped', () => {
  const t0 = Date.UTC(2026, 10, 20, 10)
  const gachibowli = [17.441, 78.35] // right next to D-02's base
  const { rows } = assignFleet([call(...gachibowli, t0), call(...gachibowli, t0 + 1000)], t0 + 2000)
  assert.equal(rows[0].drone, 'D-02')
  assert.notEqual(rows[1].drone, 'D-02') // D-02 is still busy, so the next nearest goes
  assert.equal(rows[1].queued, false)
})

test('drone status moves through en route, on scene, returning, at base', () => {
  const t0 = Date.UTC(2026, 10, 20, 10)
  const target = [17.4239, 78.4738] // Tank Bund, nearest to D-01
  const { rows } = assignFleet([call(...target, t0)], t0)
  const { etaS } = rows[0]
  const at = ms => assignFleet([call(...target, t0)], t0 + ms).status.find(d => d.id === 'D-01').state
  assert.equal(at(etaS * 500), 'En route')
  assert.equal(at(etaS * 1000 + 1000), 'On scene')
  assert.equal(at((etaS + ON_SCENE_S) * 1000 + 1000), 'Returning')
  assert.equal(at((etaS * 2 + ON_SCENE_S) * 1000 + 1000), 'At base')
  assert.equal(FLEET.length, 3)
})

test('face size shrinks with altitude; straight overhead shows no face', () => {
  const near = faceView({ altitude: 5, distance: 5 })
  const far = faceView({ altitude: 60, distance: 40 })
  assert.ok(near.px > far.px)
  assert.equal(faceView({ altitude: 30, distance: 0 }).tier.key, 'none')
  assert.equal(faceView({ altitude: 2, distance: 3, fovDeg: 30, widthPx: 3840 }).tier.key, 'match')
})

test('fleet positions: on scene sits on the incident, halfway en route is between', async () => {
  const { fleetPositions } = await import('./sim.js')
  const t0 = Date.UTC(2026, 10, 20, 10), target = [17.4239, 78.4738]
  const { rows, status } = assignFleet([call(...target, t0)], t0 + 1)
  const etaMs = rows[0].etaS * 1000
  const mid = fleetPositions(assignFleet([call(...target, t0)], t0 + etaMs / 2).status, t0 + etaMs / 2).find(d => d.id === 'D-01')
  const onScene = fleetPositions(assignFleet([call(...target, t0)], t0 + etaMs + 1000).status, t0 + etaMs + 1000).find(d => d.id === 'D-01')
  assert.deepEqual(onScene.pos, target)
  assert.ok(mid.pos[0] > FLEET[0].at[0] && mid.pos[0] < target[0])
  assert.equal(status.length, 3)
})
