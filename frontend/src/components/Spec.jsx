import DroneCanvas from './Drone'
import { CountUp } from './Motion'

// Every figure here is a true property of the system as built, not a benchmark.
const METRICS = [
  { label: 'Evidence hash', sub: 'SHA-256 per event, chained to the one before', value: 256, unit: 'BIT' },
  { label: 'HIGH-risk alert threshold', sub: 'Crowd-risk score that notifies patrol', value: 65, unit: '/100' },
  { label: 'Faces stored outside the watchlist', sub: 'Non-matches are blurred, never stored', value: 0, unit: 'FACES' },
  { label: 'Hotspots in the demo', sub: 'Hyderabad, simulated event stream', value: 5, unit: 'ZONES' },
]

export default function Spec() {
  return (
    <section id="spec" className="section spec">
      <div className="spec-drone reveal">
        <DroneCanvas mode="spec" className="spec-canvas" />
        <span className="spec-hint mono">Drag to inspect</span>
      </div>
      <div className="spec-body reveal" style={{ '--d': '120ms' }}>
        <h2 className="pixel spec-title">KAVRIX-D01</h2>
        <p className="spec-sub">Concept airframe · no drone hardware yet; telemetry is simulated</p>
        <p className="spec-lead">
          Recorded drone footage runs through the pipeline on a laptop. It scores crowd risk, matches only the watchlist,
          blurs everyone else and hash-chains every event, so an officer sees the evidence behind each alert and can check it later.
        </p>
        <dl className="metrics">
          {METRICS.map(m => (
            <div key={m.label} className="metric">
              <dt><strong>{m.label}</strong><span>{m.sub}</span></dt>
              <dd><span className="pixel"><CountUp to={m.value} /></span><small>{m.unit}</small></dd>
            </div>
          ))}
        </dl>
      </div>
    </section>
  )
}
