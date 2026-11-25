import { useEffect, useRef, useState } from 'react'
import { Canvas, useFrame } from '@react-three/fiber'
import { ContactShadows, Environment, Lightformer, OrbitControls, RoundedBox } from '@react-three/drei'
import {
  CanvasTexture, CatmullRomCurve3, DoubleSide, ExtrudeGeometry, MathUtils, Path, Shape, TubeGeometry, Vector3,
} from 'three'

// Procedural engineering-frame quadcopter (X plate frame, outrunner motors, compute stack, GPS mast, gimbal camera).
// Built in code: no model file to download, works offline at the venue.
const PI = Math.PI
const C = {
  alu: '#b7b7b2', aluLight: '#cfcfca', arm: '#9a9a95', motor: '#2b2b2d', cap: '#c6c6c1', copper: '#8f5d3c',
  dark: '#161618', rubber: '#3a3a3c', prop: '#e3e3de', glass: '#060608',
}
const mat = (color, metalness = 0.35, roughness = 0.42, extra = {}) => (
  <meshStandardMaterial color={color} metalness={metalness} roughness={roughness} {...extra} />
)
const MODEL_SCALE = 1.32
const DIAG = 0.9 // motor offset on each axis (X configuration)
const MOTORS = [[1, 1], [-1, 1], [1, -1], [-1, -1]].map(([sx, sz]) => [sx * DIAG, sz * DIAG])

/* ---------- shared geometry (built once, reused by every canvas) ---------- */

// X-shaped frame plate: four rounded lobes joined by concave waists, with lightening holes.
function xPlate(R, lobe, waist) {
  const s = new Shape()
  const lobeAt = k => PI / 4 + (k * PI) / 2
  for (let k = 0; k < 4; k++) {
    const a = lobeAt(k), cx = Math.cos(a) * R, cy = Math.sin(a) * R
    const a0 = a - PI / 2
    if (k === 0) s.moveTo(cx + Math.cos(a0) * lobe, cy + Math.sin(a0) * lobe)
    else {
      const m = a - PI / 4
      s.quadraticCurveTo(Math.cos(m) * waist, Math.sin(m) * waist, cx + Math.cos(a0) * lobe, cy + Math.sin(a0) * lobe)
    }
    s.absarc(cx, cy, lobe, a0, a + PI / 2, false)
  }
  const a0 = lobeAt(0) - PI / 2, m = lobeAt(0) - PI / 4
  s.quadraticCurveTo(Math.cos(m) * waist, Math.sin(m) * waist,
    Math.cos(lobeAt(0)) * R + Math.cos(a0) * lobe, Math.sin(lobeAt(0)) * R + Math.sin(a0) * lobe)
  const hole = (x, y, r) => { const h = new Path(); h.absarc(x, y, r, 0, PI * 2, true); s.holes.push(h) }
  const slot = (x, y0, y1, w) => {
    const h = new Path()
    h.moveTo(x - w, y0); h.lineTo(x - w, y1); h.absarc(x, y1, w, PI, 0, true)
    h.lineTo(x + w, y0); h.absarc(x, y0, w, 0, PI, true)
    s.holes.push(h)
  }
  for (let k = 0; k < 4; k++) hole(Math.cos(lobeAt(k)) * R, Math.sin(lobeAt(k)) * R, lobe * 0.3)
  slot(-0.1, -0.16, 0.16, 0.022); slot(0.1, -0.16, 0.16, 0.022)
  hole(0, 0.27, 0.035); hole(0, -0.27, 0.035)
  return s
}
const plateGeo = (R, lobe, waist) => new ExtrudeGeometry(xPlate(R, lobe, waist), {
  depth: 0.02, bevelEnabled: true, bevelThickness: 0.005, bevelSize: 0.005, bevelSegments: 2, curveSegments: 28,
})
const TOP_PLATE = plateGeo(0.44, 0.15, 0.2)
const BOTTOM_PLATE = plateGeo(0.48, 0.16, 0.22)

// Slim two-blade prop: tapered planform, twisted along its length (steeper pitch at the root).
const BLADE = (() => {
  const s = new Shape()
  s.moveTo(0.03, -0.034)
  s.bezierCurveTo(0.15, -0.06, 0.36, -0.05, 0.49, -0.022)
  s.quadraticCurveTo(0.53, 0, 0.49, 0.02)
  s.bezierCurveTo(0.36, 0.036, 0.15, 0.042, 0.03, 0.03)
  s.closePath()
  const g = new ExtrudeGeometry(s, { depth: 0.006, bevelEnabled: true, bevelThickness: 0.002, bevelSize: 0.002, bevelSegments: 1, curveSegments: 48 })
  const p = g.attributes.position
  for (let i = 0; i < p.count; i++) {
    const x = p.getX(i), y = p.getY(i), z = p.getZ(i), a = 0.45 - x * 0.75
    p.setY(i, y * Math.cos(a) - z * Math.sin(a))
    p.setZ(i, y * Math.sin(a) + z * Math.cos(a))
  }
  g.computeVertexNormals()
  return g
})()

const tube = (pts, r = 0.011) => new TubeGeometry(new CatmullRomCurve3(pts.map(p => new Vector3(...p))), 48, r, 8, false)
const CABLES = [
  tube([[-0.15, -0.09, 0.12], [-0.34, -0.3, 0.22], [-0.27, -0.44, 0.4], [-0.1, -0.3, 0.42]]),
  tube([[0.18, -0.09, -0.05], [0.36, -0.36, 0.06], [0.3, -0.46, 0.3], [0.11, -0.3, 0.42]]),
  tube([[-0.12, -0.09, -0.22], [-0.06, -0.4, -0.26], [0.16, -0.38, -0.16], [0.13, -0.09, -0.1]], 0.009),
]
const SPRING = tube(Array.from({ length: 70 }, (_, i) => {
  const t = i / 69, a = t * 7 * PI * 2
  return [Math.cos(a) * 0.024, t * 0.12, Math.sin(a) * 0.024]
}), 0.0045)

// Soft radial texture for the spinning-prop blur disc.
const BLUR = (() => {
  if (typeof document === 'undefined') return null
  const c = document.createElement('canvas')
  c.width = c.height = 128
  const x = c.getContext('2d'), g = x.createRadialGradient(64, 64, 5, 64, 64, 64)
  g.addColorStop(0, 'rgba(255,255,255,0)'); g.addColorStop(0.18, 'rgba(255,255,255,.6)')
  g.addColorStop(0.8, 'rgba(255,255,255,.4)'); g.addColorStop(1, 'rgba(255,255,255,0)')
  x.fillStyle = g; x.fillRect(0, 0, 128, 128)
  return new CanvasTexture(c)
})()

const spinSpeed = (mode, t) => {
  if (mode === 'spin') return 22
  if (mode === 'spec') return 16
  // props start fast during the fly-in, settle, then spin up again as you scroll
  if (mode === 'hero') return 2.5 + Math.min(window.scrollY / window.innerHeight, 1) * 40 + Math.max(0, 1 - t / 2.5) * 45
  return 3
}

/* ---------- parts ---------- */

function Motor({ position: [x, z], dir, mode, front }) {
  const prop = useRef(), disc = useRef()
  useFrame(({ clock }, dt) => {
    const speed = spinSpeed(mode, clock.elapsedTime)
    prop.current.rotation.y += dir * dt * speed
    disc.current.opacity = MathUtils.clamp((speed - 8) / 60, 0, 0.55)
  })
  return (
    <group position={[x, 0.016, z]}>
      <mesh position={[0, 0.008, 0]}><cylinderGeometry args={[0.1, 0.1, 0.016, 28]} />{mat(C.alu, 0.6, 0.35)}</mesh>
      <mesh position={[0, 0.03, 0]}><cylinderGeometry args={[0.128, 0.128, 0.02, 36]} />{mat(C.motor, 0.6, 0.35)}</mesh>
      {/* stator windings, visible through the bell vents */}
      <mesh position={[0, 0.095, 0]}><cylinderGeometry args={[0.104, 0.104, 0.1, 28]} />{mat(C.copper, 0.75, 0.35)}</mesh>
      {Array.from({ length: 14 }, (_, k) => {
        const a = (k / 14) * PI * 2
        return (
          <mesh key={k} position={[Math.cos(a) * 0.118, 0.095, Math.sin(a) * 0.118]} rotation={[0, -a, 0]} castShadow>
            <boxGeometry args={[0.014, 0.11, 0.042]} />{mat(C.motor, 0.6, 0.32)}
          </mesh>
        )
      })}
      <mesh position={[0, 0.158, 0]} castShadow><cylinderGeometry args={[0.122, 0.128, 0.026, 36]} />{mat(C.cap, 0.75, 0.28)}</mesh>
      {Array.from({ length: 6 }, (_, k) => {
        const a = (k / 6) * PI * 2
        return <mesh key={k} position={[Math.cos(a) * 0.072, 0.172, Math.sin(a) * 0.072]}><cylinderGeometry args={[0.017, 0.017, 0.004, 12]} />{mat(C.dark)}</mesh>
      })}
      <mesh position={[0, 0.2, 0]}><cylinderGeometry args={[0.016, 0.016, 0.07, 12]} />{mat(C.aluLight, 0.8, 0.25)}</mesh>
      {/* status LED under the mount: white front, red rear */}
      <mesh position={[0, -0.002, 0]}>
        <sphereGeometry args={[0.016, 12, 12]} />
        <meshStandardMaterial color={front ? '#ffffff' : '#ff3b3b'} emissive={front ? '#ffffff' : '#ff3b3b'} emissiveIntensity={1.6} />
      </mesh>
      <group ref={prop} position={[0, 0.205, 0]}>
        <mesh><cylinderGeometry args={[0.036, 0.04, 0.026, 20]} />{mat(C.prop, 0.2, 0.5)}</mesh>
        <mesh position={[0, 0.026, 0]}><cylinderGeometry args={[0.026, 0.026, 0.026, 6]} />{mat(C.aluLight, 0.85, 0.22)}</mesh>
        {[0, PI].map(r => (
          // Euler XYZ applies Z first: spin the blade about what becomes vertical, then lay it flat
          <mesh key={r} geometry={BLADE} rotation={[-PI / 2, 0, r]} scale={[1, 1, dir]} castShadow>
            {mat(C.prop, 0.15, 0.48, { side: DoubleSide })}
          </mesh>
        ))}
      </group>
      <mesh position={[0, 0.21, 0]} rotation={[-PI / 2, 0, 0]}>
        <circleGeometry args={[0.54, 56]} />
        <meshBasicMaterial ref={disc} map={BLUR} color="#b8b8b3" transparent opacity={0} depthWrite={false} />
      </mesh>
    </group>
  )
}

function Arm({ to: [x, z] }) {
  const len = Math.hypot(x, z) - 0.1, mid = (len + 0.2) / 2
  const yaw = -Math.atan2(z, x)
  return (
    <group rotation={[0, yaw, 0]}>
      <mesh position={[mid, -0.011, 0]} castShadow><boxGeometry args={[len, 0.055, 0.078]} />{mat(C.arm, 0.55, 0.38)}</mesh>
      {/* lightening slots on the arm side */}
      {[0.4, 0.62, 0.84].map(f => (
        <mesh key={f} position={[0.1 + len * f, -0.011, 0.04]}><boxGeometry args={[0.08, 0.022, 0.004]} />{mat(C.dark, 0.2, 0.7)}</mesh>
      ))}
      {/* motor lead running along the arm, held by zip ties */}
      <mesh position={[mid, 0.022, 0.016]}><boxGeometry args={[len * 0.82, 0.011, 0.011]} />{mat(C.dark, 0.1, 0.6)}</mesh>
      {[0.45, 0.72].map(f => (
        <mesh key={f} position={[0.1 + len * f, -0.008, 0]}><boxGeometry args={[0.014, 0.07, 0.09]} />{mat(C.dark, 0.1, 0.55)}</mesh>
      ))}
    </group>
  )
}

function Frame() {
  return (
    <>
      <mesh geometry={TOP_PLATE} position={[0, 0.06, 0]} rotation={[PI / 2, 0, 0]} castShadow>{mat(C.alu, 0.62, 0.32)}</mesh>
      <mesh geometry={BOTTOM_PLATE} position={[0, -0.062, 0]} rotation={[PI / 2, 0, 0]} castShadow>{mat(C.alu, 0.62, 0.32)}</mesh>
      {/* standoffs with screw heads */}
      {[[1, 1], [-1, 1], [1, -1], [-1, -1]].map(([sx, sz]) => (
        <group key={`${sx}${sz}`} position={[sx * 0.2, 0, sz * 0.22]}>
          <mesh position={[0, -0.011, 0]}><cylinderGeometry args={[0.02, 0.02, 0.09, 6]} />{mat(C.aluLight, 0.8, 0.25)}</mesh>
          <mesh position={[0, 0.071, 0]}><cylinderGeometry args={[0.026, 0.026, 0.012, 16]} />{mat(C.dark, 0.6, 0.3)}</mesh>
        </group>
      ))}
      {/* compute module: finned heatsink on a carrier board */}
      <mesh position={[0, 0.076, 0.04]}><boxGeometry args={[0.46, 0.012, 0.36]} />{mat('#2f3a33', 0.3, 0.6)}</mesh>
      <mesh position={[0, 0.09, 0.04]}><boxGeometry args={[0.4, 0.018, 0.32]} />{mat(C.aluLight, 0.75, 0.28)}</mesh>
      {Array.from({ length: 13 }, (_, k) => (
        <mesh key={k} position={[-0.18 + k * 0.03, 0.145, 0.04]} castShadow><boxGeometry args={[0.009, 0.095, 0.32]} />{mat(C.aluLight, 0.75, 0.28)}</mesh>
      ))}
      {/* GPS puck on a sprung mast */}
      <group position={[0.2, 0.066, -0.32]}>
        <mesh geometry={SPRING}>{mat(C.aluLight, 0.85, 0.22)}</mesh>
        <mesh position={[0, 0.21, 0]}><cylinderGeometry args={[0.01, 0.01, 0.18, 10]} />{mat(C.dark, 0.4, 0.4)}</mesh>
        <mesh position={[0, 0.31, 0]} castShadow><cylinderGeometry args={[0.13, 0.135, 0.03, 40]} />{mat('#d6d6d1', 0.1, 0.55)}</mesh>
        <mesh position={[0, 0.328, 0]}><cylinderGeometry args={[0.11, 0.13, 0.012, 40]} />{mat('#e2e2dd', 0.1, 0.5)}</mesh>
      </group>
      {/* antennas */}
      {[1, -1].map(s => (
        <group key={s} position={[s * 0.13, 0.07, -0.42]} rotation={[-0.55, 0, s * 0.35]}>
          <mesh position={[0, 0.11, 0]}><cylinderGeometry args={[0.007, 0.009, 0.22, 8]} />{mat(C.dark, 0.3, 0.5)}</mesh>
          <mesh position={[0, 0.23, 0]}><cylinderGeometry args={[0.014, 0.014, 0.04, 10]} />{mat(C.dark, 0.3, 0.5)}</mesh>
        </group>
      ))}
      {/* gimbal camera: bracket, rubber dampers, body, lens */}
      {[1, -1].map(s => (
        <group key={s}>
          <mesh position={[s * 0.125, -0.18, 0.42]}><boxGeometry args={[0.012, 0.2, 0.12]} />{mat(C.alu, 0.6, 0.35)}</mesh>
          <mesh position={[s * 0.125, -0.095, 0.38]}><sphereGeometry args={[0.024, 14, 14]} />{mat(C.rubber, 0, 0.8)}</mesh>
          <mesh position={[s * 0.125, -0.095, 0.46]}><sphereGeometry args={[0.024, 14, 14]} />{mat(C.rubber, 0, 0.8)}</mesh>
        </group>
      ))}
      <group position={[0, -0.25, 0.43]} rotation={[-0.18, 0, 0]}>
        <RoundedBox args={[0.22, 0.15, 0.17]} radius={0.025} castShadow>{mat('#1d1d1f', 0.4, 0.4)}</RoundedBox>
        <mesh position={[0, 0, 0.11]} rotation={[PI / 2, 0, 0]}><cylinderGeometry args={[0.058, 0.062, 0.07, 32]} />{mat('#2a2a2c', 0.6, 0.3)}</mesh>
        <mesh position={[0, 0, 0.147]} rotation={[PI / 2, 0, 0]}><cylinderGeometry args={[0.044, 0.044, 0.006, 32]} />{mat(C.glass, 0.9, 0.05)}</mesh>
        <mesh position={[0.016, 0.016, 0.151]}><sphereGeometry args={[0.006, 8, 8]} /><meshBasicMaterial color="#ffffff" /></mesh>
        <mesh position={[0.07, 0.078, 0.04]}><sphereGeometry args={[0.009, 10, 10]} /><meshStandardMaterial color="#ff3434" emissive="#ff3434" emissiveIntensity={2} /></mesh>
      </group>
      {CABLES.map((g, i) => <mesh key={i} geometry={g}>{mat(C.dark, 0.1, 0.6)}</mesh>)}
    </>
  )
}

// Screen footprint [width, height] in world units while the drone turns: the prop-tip sweep across, and how
// tall it projects. Seen from above (spin/spec) the tilted depth adds height; seen nearly level (closeup) it doesn't.
const FOOTPRINT = { spin: [4.9, 3.4], closeup: [4.9, 2.5] }

function Drone({ mode, interactive }) {
  const ref = useRef()
  useFrame(({ clock, pointer, size, viewport, camera }) => {
    const g = ref.current, t = clock.elapsedTime
    let fp = FOOTPRINT[mode], cap = 1
    if (mode === 'spec') {
      // The visitor can orbit, so size to the current view angle: looking down, the drone's depth projects as height.
      const e = Math.abs(Math.asin(camera.position.y / camera.position.length()))
      fp = [4.85, 4.9 * Math.sin(e) + 1.45 * Math.cos(e)]
      cap = 1.6
    }
    if (fp) {
      // Scale so the whole drone fits whatever box the canvas sits in (tiles, circles, panels, the CTA band).
      g.scale.setScalar(Math.min(cap, (viewport.width * 0.95) / fp[0], (viewport.height * 0.95) / fp[1]))
    }
    if (mode === 'hero') {
      g.scale.setScalar(Math.min(1, size.width / size.height / 1.7)) // shrink on portrait screens
      const p = Math.min(window.scrollY / window.innerHeight, 1.5)
      const intro = 1 - (1 - Math.min(t / 1.8, 1)) ** 3 // fly-in on load
      g.rotation.y = MathUtils.lerp(g.rotation.y, pointer.x * 0.45 + p * PI + (1 - intro) * 1.4, 0.08)
      g.rotation.x = MathUtils.lerp(g.rotation.x, 0.12 - pointer.y * 0.2, 0.08)
      g.rotation.z = MathUtils.lerp(g.rotation.z, -pointer.x * 0.1, 0.08)
      g.position.y = Math.sin(t * 1.4) * 0.06 + p * 1.1 + (1 - intro) * 2.4
    } else if (mode === 'spec' && interactive) {
      g.position.y = Math.sin(t * 1.4) * 0.04 // OrbitControls turns the camera
    } else if (mode === 'closeup') {
      g.rotation.y = Math.sin(t * 0.25) * 0.6
      g.position.y = Math.sin(t * 1.2) * 0.03
    } else {
      g.rotation.y = t * 0.45
      g.rotation.x = 0.22
      g.position.y = Math.sin(t * 1.4) * 0.05
    }
  })
  return (
    <group ref={ref}>
      <group scale={MODEL_SCALE}>
        <Frame />
        {MOTORS.map((m, i) => <Arm key={`a${i}`} to={m} />)}
        {MOTORS.map((m, i) => <Motor key={`m${i}`} position={m} dir={i === 0 || i === 3 ? 1 : -1} front={m[1] > 0} mode={mode} />)}
      </group>
    </group>
  )
}

const CAMERAS = {
  hero: { position: [0, 1.7, 4.5], fov: 31 },
  spin: { position: [0, 1.7, 4.6], fov: 34 },
  closeup: { position: [0.9, -0.5, 3.4], fov: 36 },
  spec: { position: [4.1, 1.55, 4.5], fov: 36 },
}

export default function DroneCanvas({ mode = 'hero', className }) {
  const wrap = useRef()
  const [onScreen, setOnScreen] = useState(true)
  useEffect(() => {
    // Pause offscreen canvases: several of them on one page would otherwise all render every frame.
    const io = new IntersectionObserver(([e]) => setOnScreen(e.isIntersecting))
    io.observe(wrap.current)
    return () => io.disconnect()
  }, [])
  const reduce = matchMedia('(prefers-reduced-motion: reduce)').matches
  // drag-to-orbit only with a mouse; on touch it would swallow page scrolling
  const interactive = mode === 'spec' && matchMedia('(pointer: fine)').matches
  return (
    <div ref={wrap} className={className} aria-hidden="true">
      <Canvas shadows dpr={[1, 2]} frameloop={reduce ? 'demand' : onScreen ? 'always' : 'never'} camera={CAMERAS[mode]}>
        <ambientLight intensity={0.35} />
        <directionalLight position={[2, 6, 4]} intensity={1.5} castShadow />
        {/* studio lighting rendered locally, so no HDR download */}
        <Environment resolution={256} frames={1}>
          <Lightformer intensity={2.2} position={[0, 6, 1]} scale={[10, 6, 1]} />
          <Lightformer intensity={1.2} position={[-6, 1, 2]} scale={[4, 8, 1]} />
          <Lightformer intensity={1.2} position={[6, 1, 2]} scale={[4, 8, 1]} />
          <Lightformer intensity={0.8} position={[0, 1, 7]} scale={[10, 3, 1]} />
          <Lightformer intensity={0.4} position={[0, -5, 0]} scale={[10, 10, 1]} />
        </Environment>
        <Drone mode={mode} interactive={interactive} />
        {interactive && <OrbitControls enableZoom={false} enablePan={false} autoRotate={!reduce} autoRotateSpeed={0.9} minPolarAngle={0.45} maxPolarAngle={1.65} />}
        {mode !== 'closeup' && <ContactShadows position={[0, -0.75, 0]} opacity={0.2} scale={7} blur={2.8} far={2.5} />}
      </Canvas>
    </div>
  )
}
