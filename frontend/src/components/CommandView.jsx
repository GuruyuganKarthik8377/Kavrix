import { useEffect, useState } from 'react'
import { MapContainer, TileLayer, CircleMarker, Marker, Tooltip } from 'react-leaflet'
import L from 'leaflet'
import 'leaflet/dist/leaflet.css'
import { verifyEvidence, placeName, HQ, toDate } from '../lib/events'
import { FLEET, assignFleet, fleetPositions } from '../lib/sim'
import { useEvents, ALERT_AT, riskColor, LABEL, clock as time, decisionOf, logDecision } from '../lib/store'
import DroneCanvas from './Drone'

const FEED_URL = import.meta.env.VITE_FEED_URL || '/feed/concert.mp4'
const droneIcon = L.divIcon({ className: 'drone-pin', html: '<span>D-01</span>', iconSize: [46, 22] })
const hqIcon = L.divIcon({ className: 'hq-pin', html: '<span>HQ</span>', iconSize: [34, 22] })
const FLEET_ICONS = Object.fromEntries(FLEET.map(d => [d.id, L.divIcon({ className: 'drone-pin', html: `<span>${d.id}</span>`, iconSize: [46, 22] })]))
const LAYERS = [['zones', 'Crowd zones'], ['drones', 'Drones'], ['matches', 'Watchlist matches'], ['incidents', '112 incidents']]

// `full`: the stand-alone command centre page, with layer toggles, filters, the whole fleet and the feed as an overlay.
export default function CommandView({ full = false }) {
  const { events, source } = useEvents()
  const [layers, setLayers] = useState({ zones: true, drones: true, matches: true, incidents: true })
  const [minRisk, setMinRisk] = useState(0)
  const [, setTick] = useState(0)
  useEffect(() => {
    if (!full) return
    const id = setInterval(() => setTick(n => n + 1), 1000) // keep fleet positions moving between events
    return () => clearInterval(id)
  }, [full])
  const [replay, setReplay] = useState(null) // null = live, otherwise index into events
  const [drone, setDrone] = useState(HQ)
  const [verified, setVerified] = useState({})
  const [noFeed, setNoFeed] = useState(false)

  const visible = replay === null ? events : events.slice(0, replay + 1)
  const lastDispatch = visible.findLast(e => e.type === 'dispatch')
  const [tLat, tLng] = lastDispatch ? [lastDispatch.lat, lastDispatch.lng] : HQ

  // Fly the drone marker toward the latest dispatch.
  useEffect(() => {
    const id = setInterval(() => setDrone(prev => {
      const [la, ln] = prev
      if (Math.abs(tLat - la) + Math.abs(tLng - ln) < 1e-5) return prev // arrived: same ref, no re-render
      return [la + (tLat - la) * 0.06, ln + (tLng - ln) * 0.06]
    }), 60)
    return () => clearInterval(id)
  }, [tLat, tLng])

  const zones = Object.values(Object.fromEntries(
    visible.filter(e => e.type === 'crowd_risk').map(e => [placeName(e), e]),
  )).filter(z => z.risk >= minRisk)
  const matches = visible.filter(e => e.type === 'watchlist_match').slice(-5)
  const alerts = visible.filter(e => e.type === 'watchlist_match' || e.type === 'crowd_alert' || (!e.run_id && e.risk >= ALERT_AT && e.risk >= minRisk)).slice(-8).reverse()
  const now = replay === null ? Date.now() : toDate(visible.at(-1)?.timestamp ?? Date.now()).getTime()
  const fleet = full ? fleetPositions(assignFleet(visible.filter(e => e.type === 'dispatch'), now).status, now) : []
  const peak = Math.max(0, ...zones.map(z => z.risk))

  const verify = async e => {
    const ok = await verifyEvidence(e)
    setVerified(v => ({ ...v, [e.id]: ok ? 'ok' : 'fail' }))
  }

  return (
    <div className={`cmd${full ? ' full' : ''}`}>
      <div className="cmd-stats">
        <Stat label="Feed" value={source === 'live' ? 'Live backend' : source === 'simulated' ? 'Simulated' : 'Connecting…'} dot={source === 'live' ? '#30a46c' : '#f5a524'} />
        <Stat label={full ? 'Drones out' : 'Drones dispatched'} value={full ? fleet.filter(d => d.state !== 'At base').length : lastDispatch ? '1' : '0'} />
        <Stat label="Events" value={visible.length} />
        <Stat label="Peak crowd risk" value={peak} dot={riskColor(peak)} />
      </div>

      <div className="cmd-grid">
        <div className="cmd-map card">
          <MapContainer center={[17.405, 78.46]} zoom={12} scrollWheelZoom={false} style={{ height: '100%' }}>
            <TileLayer
              url="https://tile.openstreetmap.org/{z}/{x}/{y}.png"
              attribution='&copy; OpenStreetMap contributors'
            />
            <Marker position={HQ} icon={hqIcon}><Tooltip>Police Commissionerate (drone base)</Tooltip></Marker>
            {layers.zones && zones.map(z => (
              <CircleMarker key={placeName(z)} center={[z.lat, z.lng]} radius={10 + z.risk / 3}
                pathOptions={{ color: riskColor(z.risk), fillColor: riskColor(z.risk), fillOpacity: 0.28, weight: 1.5 }}>
                <Tooltip>{placeName(z)} · crowd risk {z.risk}</Tooltip>
              </CircleMarker>
            ))}
            {layers.matches && matches.map(m => (
              <CircleMarker key={m.id} center={[m.lat, m.lng]} radius={6}
                pathOptions={{ color: '#111', fillColor: '#111', fillOpacity: 0.9, weight: 2, className: 'wl-marker' }}>
                <Tooltip>Watchlist match · {m.risk}% confidence · needs officer review</Tooltip>
              </CircleMarker>
            ))}
            {layers.incidents && lastDispatch && (
              <CircleMarker center={[lastDispatch.lat, lastDispatch.lng]} radius={16}
                pathOptions={{ color: '#e5484d', dashArray: '4 4', fillOpacity: 0, weight: 2 }}>
                <Tooltip>112 incident · {placeName(lastDispatch)}</Tooltip>
              </CircleMarker>
            )}
            {layers.drones && !full && <Marker position={drone} icon={droneIcon} />}
            {layers.drones && fleet.map(d => (
              <Marker key={d.id} position={d.pos} icon={FLEET_ICONS[d.id]}>
                <Tooltip>{d.id} · {d.state}{d.job && d.state !== 'At base' ? ` · ${placeName(d.job.e)}` : ''} · base {d.base}</Tooltip>
              </Marker>
            ))}
          </MapContainer>
          {full && (
            <div className="map-tools" role="group" aria-label="Layers and filters">
              <span className="mono caps muted">Layers</span>
              {LAYERS.map(([k, label]) => (
                <label key={k}><input type="checkbox" checked={layers[k]} onChange={e => setLayers(l => ({ ...l, [k]: e.target.checked }))} /> {label}</label>
              ))}
              <label className="map-range">
                <span className="mono caps muted">Min crowd risk · {minRisk}</span>
                <input type="range" min={0} max={90} step={5} value={minRisk} onChange={e => setMinRisk(Number(e.target.value))} aria-label="Minimum crowd risk shown" />
              </label>
            </div>
          )}
          {full && (
            <div className="map-feed">
              <div className="feed-head"><span className="live-dot" /> Feed · {lastDispatch ? placeName(lastDispatch) : 'standby'}</div>
              {noFeed ? <DroneCanvas mode="spin" className="feed-placeholder" /> : <video src={FEED_URL} autoPlay muted loop playsInline onError={() => setNoFeed(true)} />}
            </div>
          )}
          <div className="map-legend" aria-label="Map legend">
            <span><i className="lg-zone" /> Crowd risk <em className="lg-scale"><b style={{ background: '#30a46c' }} /><b style={{ background: '#f5a524' }} /><b style={{ background: '#e5484d' }} /></em></span>
            <span><i className="lg-match" /> Watchlist match</span>
            <span><i className="lg-incident" /> 112 incident</span>
            <span><i className="lg-drone">{full ? 'D-0x' : 'D-01'}</i> Drone</span>
          </div>
        </div>

        <aside className="cmd-side">
          {!full && <div className="card feed">
            <div className="feed-head"><span className="live-dot" /> D-01 · {lastDispatch ? placeName(lastDispatch) : 'On standby'}</div>
            {noFeed
              ? <DroneCanvas mode="spin" className="feed-placeholder" />
              : <video src={FEED_URL} autoPlay muted loop playsInline onError={() => setNoFeed(true)} />}
            <p className="feed-note">Faces outside the watchlist are blurred.</p>
          </div>}

          <div className="card alerts">
            <h3>Alerts <span className="muted">· crowd alerts and watchlist matches go to officers</span></h3>
            {alerts.length === 0 && <p className="muted">No alerts yet.</p>}
            <ul>
              {alerts.map(a => (
                <li key={a.id} className="alert">
                  <span className="alert-bar" style={{ background: a.type === 'watchlist_match' ? 'var(--ink)' : riskColor(a.risk) }} />
                  <div className="alert-body">
                    <strong>{LABEL[a.type] ?? a.type}</strong> · {placeName(a)}
                    <div className="muted small">
                      {time(a)} · {a.type === 'watchlist_match' ? `${a.risk}% confidence` : `risk ${a.risk}`}
                      {a.drone_id ? ` · ${a.drone_id}` : ''}
                    </div>
                    {a.message && <div className="small">{a.message}</div>}
                    {a.label && <div className="small">{a.label} · pending officer review</div>}
                    <div className="alert-actions">
                      {a.evidence_hash && (
                        <button className={`chip ${verified[a.id] ?? ''}`} onClick={() => verify(a)}
                          title={a.evidence_hash}>
                          {verified[a.id] === 'ok' ? '✓ Evidence verified' : verified[a.id] === 'fail' ? '✗ Tampered' : `SHA-256 ${a.evidence_hash.slice(0, 8)}…`}
                        </button>
                      )}
                      {a.type === 'watchlist_match' && (decisionOf(a.id)
                        ? <span className="chip">{decisionOf(a.id).d === 'confirm' ? 'Confirmed by officer' : 'Rejected · discarded'}</span>
                        : <>
                            <button className="chip dark" onClick={() => logDecision(a, 'confirm', 'Command centre')}>Confirm</button>
                            <button className="chip" onClick={() => logDecision(a, 'reject', 'Command centre')}>Reject</button>
                          </>)}
                    </div>
                  </div>
                </li>
              ))}
            </ul>
          </div>
        </aside>
      </div>

      <div className="replay card">
        <button className="chip dark" onClick={() => setReplay(null)} disabled={replay === null}>
          {replay === null ? '● Live' : 'Back to live'}
        </button>
        <input
          type="range" aria-label="Replay timeline" min={0} max={Math.max(0, events.length - 1)}
          value={replay ?? events.length - 1}
          onChange={e => setReplay(Number(e.target.value))}
        />
        <span className="muted small">{visible.length ? time(visible.at(-1)) : '--:--'}</span>
      </div>

      {/* raw events exactly as the backend contract sends them, newest first */}
      <div className="stream card">
        <div className="stream-head">
          <span className="mono caps">Event stream</span>
          <span className="mono caps muted">{source === 'live' ? 'WebSocket · live' : 'WebSocket contract · simulated'}</span>
        </div>
        <ol className="stream-list" aria-label="Latest events">
          {visible.slice(-6).reverse().map(e => (
            <li key={e.id} className="mono">
              <span className="s-time">{time(e)}</span>
              <span className={`s-type t-${e.type}`}>{e.type}</span>
              <span>lat <b>{e.lat.toFixed(4)}</b></span>
              <span>lng <b>{e.lng.toFixed(4)}</b></span>
              <span>risk <b>{e.risk}</b></span>
              <span className="s-hash">sha256 <b>{e.evidence_hash ? `${e.evidence_hash.slice(0, 8)}…${e.evidence_hash.slice(-4)}` : '—'}</b></span>
            </li>
          ))}
        </ol>
      </div>
    </div>
  )
}

function Stat({ label, value, dot }) {
  return (
    <div className="stat">
      <span className="muted small">{label}</span>
      <strong>{dot && <span className="dot" style={{ background: dot }} />}{value}</strong>
    </div>
  )
}
