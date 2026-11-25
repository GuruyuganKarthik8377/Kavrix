import { useEffect, useState } from 'react'
import DroneCanvas from './components/Drone'
import CommandView from './components/CommandView'
import { CardViz, CountUp, Marquee, Pipeline, useReveal } from './components/Motion'
import { CommandPalette, ScrollRail, StatusBar, ThemeToggle, useActiveSection } from './components/Chrome'
import { MenuButton, MobileMenu, PillNav, useNavScroll } from './components/Nav'
import Spec from './components/Spec'
import Modules from './components/Modules'
import Field from './components/Field'
import { PAGES, PageShell, AnalyticsPage, EvidencePage, IncidentPage, OfficerPage, PrivacyPage } from './components/Pages'
import { CommandPage, DispatchPage, PrivacyLabPage, ResultsPage } from './components/OpsPages'
import { RecordedPage } from './components/Recorded'
import CampusPage from './components/Campus'
import BackendChip from './components/Backend'

const PAGE_VIEWS = {
  '#/command': CommandPage, '#/dispatch': DispatchPage, '#/officer': OfficerPage, '#/incident': IncidentPage, '#/incidents': RecordedPage, '#/evidence': EvidencePage,
  '#/privacy-lab': PrivacyLabPage, '#/campus': CampusPage, '#/analytics': AnalyticsPage, '#/results': ResultsPage, '#/privacy': PrivacyPage,
}

// Order matches the reference fan: outer-left, inner-left, [center], inner-right, outer-right.
const FEATURES = [
  { n: '03', title: 'Chained Evidence', body: 'Each event is hashed with SHA-256 and chained to the one before, so any later edit breaks the chain.', pos: 'outer-left', kind: 'evidence' },
  { n: '01', title: 'Crowd-Crush Warning', body: 'YOLOv8 person counts give a 0–100 risk score per zone, before a stampede starts.', pos: 'inner-left', kind: 'crowd' },
  { n: '02', title: 'Privacy-First Matching', body: 'Faces are checked only against a lawful watchlist. Everyone else is blurred.', pos: 'inner-right', kind: 'privacy' },
  { n: '04', title: 'Officer Alert Loop', body: 'Risk alerts reach the nearest patrol, and an officer confirms every match.', pos: 'outer-right', kind: 'alert' },
]

const MARQUEE = ['112 auto-dispatch', 'Crowd-crush warning', 'Watchlist-only matching', 'Non-matches blurred', 'Hash-chained evidence', 'Officer in the loop', 'Live command map']

const GLANCE = [
  [6, '', 'modules from the 112 call to the courtroom'],
  [100, '%', 'of events hashed and chained to the one before'],
  [4, '', 'AI and trust layers in one pipeline'],
  [1, '', 'tap for an officer to confirm or reject'],
]

const CHIPS = [
  { pos: 'c1', k: 'Watchlist', v: 'Non-matches blurred' },
  { pos: 'c2', k: 'Crowd AI', v: 'YOLOv8 person counts' },
  { pos: 'c3', k: 'Evidence', v: 'SHA-256 hash chain' },
  { pos: 'c4', k: 'Dispatch', v: '112 → nearest drone' },
]

const PRINCIPLES = [
  { tag: 'Privacy', text: 'Only faces on a lawful watchlist are ever matched. Everyone else is blurred and never stored.', by: 'Data minimisation by default' },
  { tag: 'Oversight', text: 'Kavrix suggests and an officer decides. No match leads to action until a human confirms it.', by: 'Human in the loop' },
  { tag: 'Evidence', text: 'Every event is hashed and chained to the one before it, so any later change breaks the chain and shows.', by: 'Chain of custody' },
  { tag: 'Safety', text: 'Crowd risk is scored before a crush, not after, so police have time to open exits and divert the flow.', by: 'Prevention first' },
]

const FAQ = [
  ['Does Kavrix identify everyone in a crowd?', 'No. Faces are matched only against a lawful watchlist (missing persons, wanted offenders). Everyone else is blurred, and no data about them is kept.'],
  ['Who makes the final call on a face match?', 'An officer. Kavrix only suggests a match with a confidence score, and nothing happens until a human confirms or rejects it.'],
  ['How is the evidence court-ready?', 'Each event gets a SHA-256 hash that also covers the previous event’s hash, so the events form a chain. Changing any event breaks its hash and every link after it, and the verify page shows exactly where. Digital signatures on top of the chain are the next step.'],
  ['How does crowd-crush warning work?', 'YOLOv8 detects and counts people per zone in drone footage. The count and how fast it rises give a 0–100 risk score. From 65 (HIGH), officers are alerted.'],
  ['Can it use existing CCTV as well as drones?', 'Yes. The video pipeline works on any stream, and drones add coverage where fixed cameras can’t reach.'],
  ['Is the drone feed in this demo real?', 'No. There is no drone hardware: the AI runs on recorded drone footage on a laptop. Dispatch is simulated, and this website plays a scripted event stream until it is connected to the backend’s live events.'],
]

// Phase 1 is what this demo shows; later phases are plans, worded as such.
const ROADMAP = [
  { status: 'Built · this demo', title: 'Prototype', body: 'Command view, simulated 112 dispatch, crowd-risk and watchlist pipeline on recorded drone footage, hash-chained evidence.' },
  { status: 'Next', title: 'Field trial', body: 'A live drone feed at one Hyderabad hotspot with one patrol unit, measuring detection accuracy and response time.' },
  { status: 'Planned', title: 'City pilot', body: 'Integration with 112 dispatch and the city command centre, multi-drone routing and officer mobile alerts.' },
  { status: 'Planned', title: 'Scale', body: 'Wider rollout after an independent privacy and data-protection review, with published retention rules.' },
]

const NAV = [
  ['#features', 'Features', 'Why Kavrix stands out'], ['#spec', 'Drone', 'KAVRIX-D01 airframe spec'],
  ['#modules', 'Modules', 'Six-part case study'], ['#live', 'Live', 'Map, alerts and replay'],
  ['#principles', 'Trust', 'Privacy, oversight, evidence'], ['#faq', 'FAQ', 'Common questions'],
]
const NAV_IDS = NAV.map(([h]) => h.slice(1))
const PALETTE = [
  ['#top', 'Home', 'Hero'], ['#features', 'Features', 'Why Kavrix stands out'], ['#spec', 'Drone spec', 'KAVRIX-D01'],
  ['#how', 'How it works', '112 to command map'], ['#pipeline', 'Pipeline', 'From pixels to proof'], ['#modules', 'Modules', 'Six-part case study'],
  ['#live', 'Live command view', 'Map, alerts, replay'], ['#principles', 'Principles', 'Trusted by design'],
  ['#roadmap', 'Roadmap', 'From demo to deployment'], ['#faq', 'FAQ', 'Questions'],
  ['#field', 'Field studies', 'Team Haven'],
  ...PAGES,
]

export default function App() {
  const [palette, setPalette] = useState(false)
  const [route, setRoute] = useState(location.hash)
  useEffect(() => {
    const on = () => setRoute(location.hash)
    addEventListener('hashchange', on)
    return () => removeEventListener('hashchange', on)
  }, [])
  const Page = PAGE_VIEWS[route]
  // Pages and the landing page swap on hash change, so scroll to the right place after the swap renders.
  useEffect(() => {
    if (Page) scrollTo(0, 0)
    else if (route.length > 1) document.getElementById(route.slice(1))?.scrollIntoView()
  }, [route, Page])
  const [menu, setMenu] = useState(false)
  const active = useActiveSection(NAV_IDS)
  const { compact, hidden } = useNavScroll(palette || menu)
  useReveal(route)
  useEffect(() => {
    const onKey = e => {
      if ((e.metaKey || e.ctrlKey) && e.key.toLowerCase() === 'k') { e.preventDefault(); setPalette(p => !p) }
    }
    addEventListener('keydown', onKey)
    return () => removeEventListener('keydown', onKey)
  }, [])
  return (
    <>
      <a href="#main" className="skip">Skip to content</a>
      <StatusBar />
      <header className={`nav${compact ? ' compact' : ''}${hidden ? ' hidden' : ''}`}>
        <a href="#top" className="brand-pill"><DroneIcon size={22} /> Kavrix<span>/D-01</span></a>
        <PillNav items={NAV} active={active} onPalette={() => setPalette(true)} />
        <div className="nav-right">
          <BackendChip />
          <a href="#live" className="live-pill"><i className="live-dot" /> LIVE</a>
          <ThemeToggle />
          <MenuButton open={menu} onClick={() => setMenu(true)} />
        </div>
      </header>
      <ScrollRail />
      <CommandPalette items={PALETTE} open={palette} onClose={() => setPalette(false)} />
      <MobileMenu items={NAV} open={menu} active={active} onClose={() => setMenu(false)} />

      {Page ? <PageShell route={route}><Page /></PageShell> : (
      <main id="main">
        <section id="top" className="hero">
          <svg className="swoosh" viewBox="0 0 1440 900" preserveAspectRatio="none" aria-hidden="true">
            <path d="M-40 610 C 260 420, 520 520, 720 640 S 1180 760, 1480 420" />
          </svg>
          <h1 className="hero-word" aria-label="Kavrix">
            {[...'Kavrix'].map((c, i) => <span key={i} aria-hidden="true" style={{ '--i': i }}>{c}</span>)}
          </h1>
          <DroneCanvas mode="hero" className="hero-canvas" />
          {CHIPS.map((c, i) => (
            <div key={c.pos} className={`hero-chip ${c.pos}`} style={{ '--i': i }} aria-hidden="true">
              <span className="chip-dot" />
              <span><small>{c.k}</small>{c.v}</span>
            </div>
          ))}
          <div className="hud" aria-hidden="true">
            <span><i className="live-dot" /> D-01</span>
            <span>STATUS <b>Standby</b></span>
            <span>LINK <b>Secure</b></span>
            <span>HASH CHAIN <b>On</b></span>
          </div>
          <div className="hero-foot">
            <p>Experience a first response that is faster, safer and lawful by design.</p>
            <p className="hero-quote">
              <span className="rating"><StarIcon /> 112</span>
              <span>“First on Scene. Private by Design. Proof That Holds.”</span>
            </p>
            <a href="#features" className="icon-btn big bounce" aria-label="Scroll to features"><ArrowDown /></a>
          </div>
        </section>

        <Marquee items={MARQUEE} />

        <section id="features" className="section">
          <SectionHead title="Why Kavrix Stands Out">
            Most systems stop at a drone feed and face recognition. Kavrix makes aerial response lawful, court-ready and focused on preventing harm, for police in Indian cities.
          </SectionHead>
          <div className="fan reveal">
            {FEATURES.slice(0, 2).map(f => <FeatureCard key={f.n} f={f} />)}
            <div className="fan-center">
              <span className="orbit" aria-hidden="true"><i /></span>
              <DroneCanvas mode="spin" className="fan-canvas" />
              <h3>Drone First Response</h3>
              <p>The nearest drone is dispatched to every 112 call.</p>
            </div>
            {FEATURES.slice(2).map(f => <FeatureCard key={f.n} f={f} />)}
          </div>
          <div className="glance">
            {GLANCE.map(([n, s, label], i) => (
              <div key={label} className="glance-item reveal" style={{ '--d': `${i * 90}ms` }}>
                <strong><CountUp to={n} suffix={s} /></strong>
                <span>{label}</span>
              </div>
            ))}
          </div>
        </section>

        <Spec />

        <section id="how" className="section">
          <SectionHead title={<>Simple Dispatch,<br />Instant Clarity</>}>
            From a 112 call to a live aerial view on the command map, with no manual dispatch. Commanders see the scene while officers are still on the way.
          </SectionHead>
          <ol className="steps">
            <li className="step reveal">
              <span className="step-n">1</span>
              <p>A 112 call comes in with the caller’s location.</p>
              <div className="step-art"><PhoneArt /></div>
            </li>
            <li className="step step-flip reveal" style={{ '--d': '120ms' }}>
              <span className="step-n">2</span>
              <p>The nearest drone launches and flies to the scene.</p>
              <div className="step-art"><DispatchArt /></div>
            </li>
            <li className="step reveal" style={{ '--d': '240ms' }}>
              <span className="step-n">3</span>
              <p>Its live view and crowd risk reach the control room.</p>
              <div className="step-art"><MapArt /></div>
            </li>
          </ol>
        </section>

        <section id="pipeline" className="section">
          <SectionHead title={<>From Pixels<br />to Proof</>}>
            One pipeline from the camera to the officer. Every stage adds a safeguard, so what reaches the control room is useful, lawful and verifiable.
          </SectionHead>
          <Pipeline />
        </section>

        <section className="section modules-intro">
          <SectionHead title={<>Six Modules,<br />One System</>}>
            Scroll through each module to see what it does, how it is built and what it hands to the next stage, from the 112 call to the courtroom.
          </SectionHead>
        </section>
        <Modules />

        <section id="live" className="section">
          <SectionHead title="See Kavrix In Action">
            The live command view: drone position, crowd-risk zones, watchlist matches waiting for officer review, and hash-chained evidence. Drag the timeline to replay an incident.
          </SectionHead>
          <div className="reveal"><CommandView /></div>
          <div className="page-cards">
            {PAGES.map(([href, label, desc], i) => (
              <a key={href} href={href} className="page-card reveal" style={{ '--d': `${i * 70}ms` }}>
                <span className="pixel">{String(i + 1).padStart(2, '0')}</span>
                <strong>{label}</strong>
                <span className="muted">{desc}</span>
                <span className="page-go" aria-hidden="true">↗</span>
              </a>
            ))}
          </div>
        </section>

        <section id="principles" className="section">
          <SectionHead title={<>Trusted By<br />Design</>}>
            Built for police leaders and courts as well as the public. Each principle is enforced in the pipeline, not added in a slide.
          </SectionHead>
          <Principles />
        </section>

        <section id="roadmap" className="section">
          <SectionHead title={<>From Demo<br />to Deployment</>}>
            What is built today and what comes next. Each phase adds real-world proof before Kavrix covers more of the city.
          </SectionHead>
          <ol className="roadmap">
            {ROADMAP.map((r, i) => (
              <li key={r.title} className={`rm-step reveal ${i === 0 ? 'now' : ''}`} style={{ '--d': `${i * 110}ms` }}>
                <span className="rm-dot" aria-hidden="true" />
                <span className="pixel rm-n">0{i + 1}</span>
                <span className={`rm-status mono caps ${i === 0 ? 'on' : ''}`}>{r.status}</span>
                <h3>{r.title}</h3>
                <p>{r.body}</p>
              </li>
            ))}
          </ol>
        </section>

        <section id="faq" className="section">
          <SectionHead title={<>Frequently Asked<br />Questions</>}>
            Common questions about Kavrix’s privacy safeguards, human oversight, evidence handling and how the demo works.
          </SectionHead>
          <div className="faq-grid reveal">
            <div className="faq-art">
              <DroneCanvas mode="spin" className="faq-tile" />
              <div className="faq-tile faq-tile-dark"><DroneIcon size={64} /><span>D-01 · Standby</span></div>
            </div>
            <div className="faq">
              {FAQ.map(([q, a], i) => (
                <details key={q} open={i === 0}>
                  <summary>{q}<span className="plus" aria-hidden="true" /></summary>
                  <p>{a}</p>
                </details>
              ))}
            </div>
          </div>
        </section>

        <section className="section cta">
          <div className="section-head reveal">
            <h2>Have a Question?<br />We’re Here.</h2>
            <div className="cta-side">
              <p className="cta-quote">“Questions about the build, the data or a pilot deployment? Talk to Team Haven.”</p>
              <div className="cta-btns">
                <a href="#live" className="btn dark">Open command view <span aria-hidden="true">→</span></a>
                <a href="#principles" className="btn">Our principles</a>
              </div>
            </div>
          </div>
          <div className="cta-wrap reveal">
            <DroneCanvas mode="closeup" className="cta-canvas" />
            <span className="scan" aria-hidden="true" />
            <span className="cta-tag t1" aria-hidden="true">Lens · watchlist-only matching</span>
            <span className="cta-tag t2" aria-hidden="true">Frame · aluminium X plate, standoff stack</span>
          </div>
        </section>

        <Field />
      </main>
      )}

      <footer className="footer">
        <div className="footer-top">
          <div className="footer-brand">
            <span className="logo"><DroneIcon size={26} /> KAVRIX</span>
            <p className="muted">Drone first response for Indian police: fast, private and court-ready.</p>
          </div>
          <nav className="footer-col" aria-label="Footer: product">
            <h4>Product</h4>
            <a href="#features">Features</a><a href="#how">How it works</a><a href="#pipeline">Pipeline</a><a href="#live">Command view</a>
          </nav>
          <nav className="footer-col" aria-label="Footer: trust">
            <h4>Trust</h4>
            <a href="#principles">Principles</a><a href="#roadmap">Roadmap</a><a href="#faq">FAQ</a>
          </nav>
          <nav className="footer-col" aria-label="Footer: pages">
            <h4>Pages</h4>
            {PAGES.map(([href, label]) => <a key={href} href={href}>{label}</a>)}
          </nav>
          <div className="footer-col">
            <h4>Team Haven</h4>
            <span>Guruyugan Karthik</span><span>Harishram Balamurugan</span><span>Hariharasrinivas T</span><span>Harish Ram S</span>
          </div>
        </div>
        <div className="footer-word" aria-hidden="true">KAVRIX</div>
        <p className="footer-credits mono">
          Map data © OpenStreetMap contributors · Footage: VisDrone / DroneCrowd, Pexels, Jülich · Built with React, three.js, Leaflet, FastAPI ·
          Field-study images are generated illustrations · Drone telemetry in this demo is simulated
        </p>
        <div className="footer-bottom">
          <span className="muted small">CipherCop 2026 · Track 1: AI for Smart Public Safety</span>
          <a href="#top" className="btn">Back to top ↑</a>
        </div>
      </footer>
    </>
  )
}

function SectionHead({ title, children }) {
  return (
    <div className="section-head reveal">
      <h2>{title}</h2>
      <p>{children}</p>
    </div>
  )
}

function FeatureCard({ f }) {
  return (
    <article className={`fcard ${f.pos}`}>
      <div className="fcard-top"><Seal n={f.n} /><h3>{f.title}</h3></div>
      <CardViz kind={f.kind} />
      <p>{f.body}</p>
    </article>
  )
}

function Principles() {
  const [i, setI] = useState(0)
  useEffect(() => {
    const id = setInterval(() => setI(n => (n + 1) % PRINCIPLES.length), 6000)
    return () => clearInterval(id)
  }, [i])
  const p = PRINCIPLES[i]
  return (
    <div className="principle reveal">
      <div className="principle-tag" key={`t${i}`}><SunburstIcon /> {p.tag}</div>
      <p className="principle-text" key={`p${i}`} aria-live="polite">“{p.text}”</p>
      <div className="principle-by">
        <span className="avatar"><DroneIcon size={22} /></span>
        <span><strong>Team Haven</strong><span className="muted">{p.by}</span></span>
      </div>
      <div className="dots" role="tablist" aria-label="Principles">
        {PRINCIPLES.map((x, k) => (
          <button key={x.tag} role="tab" aria-selected={k === i} aria-label={x.tag} className={k === i ? 'on' : ''} onClick={() => setI(k)} />
        ))}
      </div>
      <span className="principle-timer" key={`b${i}`} aria-hidden="true" />
    </div>
  )
}

/* ---------- step illustrations ---------- */
function PhoneArt() {
  return (
    <div className="phone">
      <span className="phone-notch" />
      <span className="phone-label">Incoming · 112</span>
      <strong>Emergency call</strong>
      <span className="phone-loc"><PinIcon /> Tank Bund, Hyderabad</span>
      <span className="phone-btns"><i className="red" /><i className="green" /></span>
    </div>
  )
}
function DispatchArt() {
  return (
    <div className="dispatch">
      <span className="ring r1" /><span className="ring r2" /><span className="ring r3" />
      <DroneIcon size={84} />
      <span className="dispatch-tag">D-01 · en route · 0.8 km</span>
    </div>
  )
}
function MapArt() {
  return (
    <div className="mini-map">
      <span className="road a" /><span className="road b" /><span className="road c" />
      <span className="heat" />
      <span className="mini-pin" />
      <span className="mini-chip">Crowd risk 84</span>
    </div>
  )
}

/* ---------- icons ---------- */
const SEAL = Array.from({ length: 24 }, (_, i) => {
  const r = i % 2 ? 16 : 19, a = (i / 24) * Math.PI * 2
  return `${i ? 'L' : 'M'}${(20 + r * Math.cos(a)).toFixed(2)} ${(20 + r * Math.sin(a)).toFixed(2)}`
}).join('') + 'Z'

function Seal({ n }) {
  return (
    <span className="seal">
      <svg viewBox="0 0 40 40" aria-hidden="true"><path d={SEAL} /></svg>
      <b>{n}</b>
    </span>
  )
}
function DroneIcon({ size = 24 }) {
  return (
    <svg width={size} height={size * 0.6} viewBox="0 0 40 24" fill="none" stroke="currentColor" strokeWidth="2.4" strokeLinecap="round" aria-hidden="true">
      <path d="M3 4h10M27 4h10M8 4v4l6 5h12l6-5V4M15 13l-2 7M25 13l2 7" />
      <rect x="15" y="10" width="10" height="7" rx="2" fill="currentColor" />
    </svg>
  )
}
const Svg = ({ children, size = 18, sw = 1.8 }) => (
  <svg width={size} height={size} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth={sw} strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">{children}</svg>
)
const ArrowDown = () => <Svg size={20}><path d="M12 5v14M6 13l6 6 6-6" /></Svg>
const PinIcon = () => <Svg size={12} sw={2.2}><path d="M12 21s-7-6.2-7-11a7 7 0 1 1 14 0c0 4.8-7 11-7 11z" /><circle cx="12" cy="10" r="2.5" /></Svg>
const StarIcon = () => <svg width="12" height="12" viewBox="0 0 24 24" fill="currentColor" aria-hidden="true"><path d="M12 2l3 7 7 .6-5.3 4.7 1.6 7.2L12 17.8 5.7 21.5l1.6-7.2L2 9.6 9 9z" /></svg>
const SunburstIcon = () => (
  <svg width="30" height="30" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.2" strokeLinecap="round" aria-hidden="true">
    {Array.from({ length: 12 }, (_, i) => {
      const a = (i / 12) * Math.PI * 2
      return <line key={i} x1={12 + 4 * Math.cos(a)} y1={12 + 4 * Math.sin(a)} x2={12 + 10 * Math.cos(a)} y2={12 + 10 * Math.sin(a)} />
    })}
  </svg>
)
