import { useEffect, useState } from 'react'
import { API_URL, api, login, logout, session } from '../lib/events'
import { useEvents } from '../lib/store'

const REPLAY_LABEL = { concert: 'Concert', jl030: 'Jülich entrance' }

// Nav chip: shows whether the page is on the live Haven backend or the simulation, and signs in / out.
export default function BackendChip() {
  const { source } = useEvents()
  const [open, setOpen] = useState(false)
  const [err, setErr] = useState('')
  const [busy, setBusy] = useState(false)
  const [recs, setRecs] = useState([{ name: 'concert' }, { name: 'jl030' }]) // replaced by the backend's list when it answers
  const s = session()
  // Every recorded run the backend can replay: the two samples plus anything saved under runs/.
  useEffect(() => {
    if (open && s.token && source === 'live') api('/demo/recordings').then(list => list.length && setRecs(list)).catch(() => {})
  }, [open, source, s.token])
  if (!API_URL) return null
  const label = source === 'live' ? 'Backend live' : source === 'reconnecting' ? 'Reconnecting…' : s.token ? 'Connecting…' : 'Simulated · sign in'

  async function submit(ev) {
    ev.preventDefault()
    const f = new FormData(ev.target)
    setBusy(true); setErr('')
    try { await login(f.get('email'), f.get('password')); location.reload() } catch (e) { setErr(e.message) } finally { setBusy(false) }
  }
  async function replay(name) {
    try { await api(`/demo/replay/${name}`, { method: 'POST' }); setOpen(false); location.hash = '#/command' } catch (e) { setErr(e.message) }
  }

  return (
    <div className="be-wrap">
      <button className={`be-chip ${source === 'live' ? 'on' : ''}`} onClick={() => setOpen(o => !o)} aria-expanded={open}>
        <i /> {label}
      </button>
      {open && (
        <div className="be-pop card" role="dialog" aria-label="Backend connection">
          {s.token ? (
            <>
              <p className="small"><b>{s.email}</b> · {s.role}</p>
              <p className="small muted">{source === 'live' ? 'Receiving live events from the Haven backend: crowd AI, watchlist matches, 112 dispatches.' : 'Connecting to the backend…'}</p>
              {s.role === 'commander' && (
                <>
                  <p className="small muted" style={{ marginBottom: 4 }}>Replay a real crowd-AI run through the live pipeline:</p>
                  <div className="be-row" style={{ flexWrap: 'wrap' }}>
                    {recs.map(r => (
                      <button key={r.name} className="chip dark" onClick={() => replay(r.name)}>▶ {REPLAY_LABEL[r.name] || r.name}</button>
                    ))}
                  </div>
                </>
              )}
              {err && <p className="small" style={{ color: '#e5484d' }}>{err}</p>}
              <div className="be-row"><button className="chip" onClick={() => { logout(); location.reload() }}>Sign out</button></div>
            </>
          ) : (
            <form onSubmit={submit}>
              <p className="small muted">Sign in to switch from the simulation to the live backend. Test logins are in <code>backend/.env</code>.</p>
              <input name="email" type="email" required defaultValue="commander@haven.test" autoComplete="username" aria-label="Email" />
              <input name="password" type="password" required placeholder="Password" autoComplete="current-password" aria-label="Password" />
              {err && <p className="small" style={{ color: '#e5484d' }}>{err}</p>}
              <button className="chip dark" disabled={busy}>{busy ? 'Signing in…' : 'Sign in'}</button>
            </form>
          )}
        </div>
      )}
    </div>
  )
}
