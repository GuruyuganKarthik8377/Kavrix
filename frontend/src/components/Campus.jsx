import { useEffect, useRef, useState } from 'react'
import { Intro, SourceTag } from './Pages'
import { api } from '../lib/events'
import { clock, useEvents } from '../lib/store'

const BASE = '/face-recorded/campus/'
const pct = v => `${Math.round(v * 100)}%`

const STEPS = [
  ['Consent first', 'The volunteer signs a consent form. Enrolment needs a legal basis, a consent reference and an expiry, and it rejects any photo with more than one face.'],
  ['Learn the face', 'Two photos give a first template. The system then finds the volunteer in video by similarity to the photos and adds those extra views (angles, distance, light). Only one averaged embedding is kept, encrypted by the backend.'],
  ['Detect and track', 'Every face in every frame is found and tracked, so a face that the detector misses for a moment stays covered.'],
  ['Quality gate', 'Only clear faces are checked: detector score 0.6 or more and at least 40 px. Each face is checked at most 3 times.'],
  ['Match one embedding', 'The worker sends one embedding to the backend. A non-match leaves nothing behind: no crop, no embedding, no record.'],
  ['Pixelate everyone else', 'Every face that is not a watchlist match is reduced to an 8×8 block grid, in the output video and in the evidence segments.'],
  ['Officer decides', 'A match only creates a pending review. An alert and an officer task exist only after an officer confirms it.'],
]

export default function CampusPage() {
  const { events, source } = useEvents()
  const [d, setD] = useState(null)
  const [err, setErr] = useState(false)
  const [note, setNote] = useState('')
  const [busy, setBusy] = useState(false)
  const [videoMissing, setVideoMissing] = useState(false) // the recording shows real people and is not in the public repository
  const [sheetMissing, setSheetMissing] = useState(false)
  const video = useRef(null)
  const live = source === 'live'

  useEffect(() => {
    fetch(`${BASE}results.json`).then(r => (r.ok ? r.json() : Promise.reject())).then(setD).catch(() => setErr(true))
  }, [])

  if (err) return <><Intro title="Campus test">The recorded campus run is missing.</Intro><p className="muted">Expected at ai/face/recorded/campus/results.json, served by the backend at {BASE}results.json.</p></>
  if (!d) return <Intro title="Campus test">Loading the recorded run…</Intro>

  const t = d.totals
  const recorded = events.filter(e => e.type === 'watchlist_match' && /recorded run/.test(e.label || '')).slice(-6).reverse()

  async function replay() {
    setBusy(true); setNote('')
    try {
      const r = await api('/demo/face-replay/campus', { method: 'POST' })
      if (video.current) { video.current.currentTime = 0; video.current.play().catch(() => {}) }
      setNote(`Sending ${r.matches} recorded matches to the backend as the video reaches them (about ${Math.round(r.seconds)} s). Each one appears in the Officer app as a pending review.`)
    } catch (e) { setNote(e.message) } finally { setBusy(false) }
  }

  return (
    <>
      <Intro title="Campus test">
        A consenting volunteer walks through a real crowd, filmed on a campus. The face worker checks every face against one consented watchlist entry and pixelates everyone else. This page shows the face team's recording, what the AI found, and how sure it was, with the limits.
      </Intro>

      <div className="card cp-video">
        {videoMissing
          ? (
            <div className="cp-missing">
              <strong>The recording is not in this copy.</strong>
              <p>It shows real people, so it is not published in the public repository. It is part of the CipherCop submission zip: put <code>campus_demo.mp4</code> into <code>ai/face/recorded/campus/</code> to play it here. The results below and the replay button work without it.</p>
            </div>
          )
          : <video ref={video} src={`${BASE}${d.video}`} controls preload="metadata" playsInline onError={() => setVideoMissing(true)} />}
        <div className="cp-actions">
          <button className="btn dark" onClick={replay} disabled={!live || busy}>Send the recorded matches to the live system</button>
          {live
            ? <a href="#/officer" className="link-arrow">Open the Officer app to confirm ↗</a>
            : <span className="muted">Sign in (top right) to send the matches through the live backend.</span>}
        </div>
        {note && <p className="cp-note">{note}</p>}
        <p className="mono caps muted">Recorded {d.recorded_on} · {d.video_seconds} s · {d.video_resolution.replace('x', '×')} · {d.location.name} · pixelated output only</p>
      </div>

      <div className="an-head">
        <div className="metric-big card"><span className="pixel">{t.matches}</span><strong>watchlist matches</strong><small>across {t.clips} clips, {t.seconds} s of footage</small></div>
        <div className="metric-big card"><span className="pixel">{pct(t.confidence_min)}–{pct(t.confidence_max)}</span><strong>match confidence</strong><small>mean {pct(t.confidence_mean)}; the match line is 50%</small></div>
        <div className="metric-big card"><span className="pixel">{t.matches_in_clip_without_volunteer}</span><strong>matches without him</strong><small>clip 4 does not show the volunteer</small></div>
      </div>

      <h2 className="an-h2">The workflow, in the order it runs</h2>
      <ol className="card cp-steps">
        {STEPS.map(([title, body], i) => <li key={title}><b>{i + 1}. {title}</b><span>{body}</span></li>)}
      </ol>

      <div className="an-grid">
        <section className="card an-block">
          <h2>The four clips</h2>
          <table className="an-table">
            <thead><tr><th>Clip</th><th>Length</th><th>Match</th></tr></thead>
            <tbody>
              {d.clips.map((c, i) => (
                <tr key={c.id}>
                  <td>{i + 1} · {c.id}{!c.volunteer_in_clip && <small className="muted"> · volunteer not in clip</small>}</td>
                  <td>{c.duration_s} s</td>
                  <td>{c.matches.length ? c.matches.map(m => `${pct(m.confidence)} at ${m.video_t.toFixed(0)} s`).join(', ') : 'none'}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </section>

        <section className="card an-block">
          <h2>Does learning from video help?</h2>
          <p className="muted">{d.held_out_test.what}</p>
          <table className="an-table">
            <thead><tr><th>Template</th><th>His faces found</th><th>Mean similarity</th></tr></thead>
            <tbody>
              {d.held_out_test.rows.map((r, i) => (
                <tr key={i} className={i === d.held_out_test.rows.length - 1 ? 'best' : ''}>
                  <td>{r.template}</td><td>{pct(r.recall)}</td><td>{r.mean_similarity.toFixed(2)}</td>
                </tr>
              ))}
            </tbody>
          </table>
          <ul className="an-list">{d.held_out_test.notes.map(n => <li key={n}>{n}</li>)}</ul>
          <p className="muted">{d.held_out_test.source}</p>
        </section>
      </div>

      <div className="an-grid">
        <section className="card an-block cp-sheet">
          <h2>What the template learned</h2>
          {sheetMissing
            ? <p className="cp-missing">The face sheet is not in this copy: it shows the volunteer's face, so it is not published in the public repository.</p>
            : <img src={`${BASE}${d.enrolled_sheet}`} alt={`${d.enrolment.faces_learned_from_video} face crops of the consenting volunteer found in video`} loading="lazy" onError={() => setSheetMissing(true)} />}
          <p className="muted">{d.enrolment.faces_learned_from_video} faces of the volunteer found in video, checked by eye by the face team: all are him. Consent reference {d.enrolment.consent_ref} ({d.enrolment.legal_basis}). Kept by the backend: {d.enrolment.stored}.</p>
        </section>

        <section className="card an-block">
          <h2>Limits</h2>
          <ul className="an-list">{d.limits.map(l => <li key={l}>{l}</li>)}</ul>
          <p className="muted"><b>For comparison:</b> the stock sample clip matches at {pct(d.reference.stock_sample.confidence)}. {d.reference.stock_sample.note}</p>
        </section>
      </div>

      <div className="card cp-live">
        <div className="inc-head"><strong>Recorded matches in the live system</strong><SourceTag source={source} /></div>
        {recorded.length === 0
          ? <p className="muted">None yet. Press the button above while signed in.</p>
          : <ul className="an-list">{recorded.map(e => <li key={e.id}>{pct(e.risk / 100)} match · {clock(e)} · <a href="#/officer">review in the Officer app ↗</a></li>)}</ul>}
      </div>
    </>
  )
}
