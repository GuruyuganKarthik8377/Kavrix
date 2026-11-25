# Frontend: Kavrix

The Kavrix website: the landing page and the 11 pages of the police command view. React 19, Vite, Leaflet (map) and three.js (the 3D drone). There is no server code here: `setup_haven.py` builds it into `frontend/dist/`, and the backend serves that folder.

## Run it

Normally you do not need to: `python3 setup_haven.py` builds it and `python3 start_haven.py` serves it at http://localhost:8000.

For frontend work (Node 22 or newer):

```bash
cd frontend
npm ci              # or npm install
npm run dev         # http://localhost:5173, hot reload
npm run build       # static build in dist/
npm test            # 16 tests: event contract, dispatch model, browser verifier
```

With no backend the site runs a **simulated event stream** (a 112 call at Charminar, then a crowd building at Tank Bund). The simulated events form a real SHA-256 hash chain built in the browser, so verification on the Evidence page is genuine. Use `localhost`, not a LAN IP: browsers only allow the hashing API on localhost or HTTPS.

## Connecting to the backend

When the page is served from port 8000 it talks to that origin automatically. Otherwise create `frontend/.env.local`:

```
VITE_API_URL=http://localhost:8000
VITE_WS_URL=ws://localhost:8000/ws/events     # optional, derived from VITE_API_URL
VITE_FEED_URL=/feed/concert.mp4               # optional, the drone-feed clip
```

Signing in (the chip at the top right) stores a login token in the browser and switches the pages from the simulation to the live backend. The socket reconnects by itself if the backend restarts; if the token has expired you are signed out and the simulation takes over.

## Pages

| Route | Page | File |
|---|---|---|
| `#/command` | Command centre | `components/OpsPages.jsx`, `components/CommandView.jsx` |
| `#/dispatch` | Dispatch | `components/OpsPages.jsx`, model in `lib/sim.js` |
| `#/officer` | Officer app | `components/Pages.jsx` |
| `#/incident` | Incident | `components/Pages.jsx` |
| `#/incidents` | Recorded incidents | `components/Recorded.jsx`, `lib/recorded.js` |
| `#/evidence` | Evidence | `components/Pages.jsx` |
| `#/privacy-lab` | Privacy lab | `components/OpsPages.jsx`, optics model in `lib/sim.js` |
| `#/campus` | Campus test | `components/Campus.jsx` |
| `#/analytics` | Accuracy | `components/Pages.jsx` |
| `#/results` | Results | `components/OpsPages.jsx` |
| `#/privacy` | Privacy & legal | `components/Pages.jsx` |

The routes and the list of pages are in `src/App.jsx` and `PAGES` in `components/Pages.jsx`. The landing page sections are in `App.jsx` (3D drone in `components/Drone.jsx`).

## Source layout

```
src/
├── App.jsx                 landing page and the hash router
├── components/             pages and widgets (see the table above)
│   ├── Backend.jsx         the sign-in / "Backend live" chip and the replay buttons
│   └── Chrome.jsx, Nav.jsx, Motion.jsx, Field.jsx, Spec.jsx, Modules.jsx   layout and visuals
└── lib/
    ├── events.js           WebSocket client, simulated stream, hash and link checks
    ├── store.js            one shared event stream and audit log for every page
    ├── sim.js              dispatch fleet model and face-size optics model
    ├── recorded.js         loads and checks the recorded runs
    └── verifyEvents.mjs    browser verifier for the crowd AI's hash chain (same rules as the Python one)
public/
├── data/                   accuracy.json, charts, recorded incidents with their snapshots
└── feed/concert.mp4        the drone-feed clip
```

## Event contract

One JSON object per WebSocket message. Crowd events come from the AI unchanged; dispatch and watchlist events are sealed by the backend.

```json
{ "id": "…", "type": "crowd_risk | crowd_alert | watchlist_match | dispatch",
  "lat": 17.4239, "lng": 78.4738, "risk": 0, "timestamp": "ISO-8601",
  "prev_hash": "…", "event_hash": "…  (crowd events)", "evidence_hash": "…  (dispatch / watchlist events)" }
```

The crowd AI's full event format is in `ai/crowd/contract/event.schema.json`. `lib/verifyEvents.mjs` and the Python `ai/crowd/code/verify_events.py` implement the same canonical-JSON hash rule and are tested against each other.

**Do not edit anything under `public/data/incidents/`.** Each event there is hash-chained and each snapshot's SHA-256 is stored in its event, so changing a single byte makes verification fail (correctly).
