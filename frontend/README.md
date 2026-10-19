# AeroPulse AI - Frontend

Mission-control UI for the AeroPulse AI backend. Next.js (App Router) +
TypeScript + Tailwind v4 + zustand + framer-motion. The centrepiece hero is a
`@react-three/fiber` 3D Earth (three.js / `@react-three/drei`) with an automatic
2D canvas ground-track fallback when WebGL is unavailable or the frame rate
drops below ~38 fps.

```bash
npm install
npm run dev        # http://localhost:3000  (start the backend on :8000 first)
npm run build      # production build
npm run lint       # eslint
```

Endpoints default to `http://127.0.0.1:8000` / `ws://127.0.0.1:8000/ws`; override
with `NEXT_PUBLIC_API_URL` / `NEXT_PUBLIC_WS_URL` (see `.env.example`).

Press **D** (or use the button, bottom-right) to open the Demo Controls drawer and
inject a fault (battery overheat, solar degradation, signal loss) or reset the
scenario. The UI honours `prefers-reduced-motion` and reflects anomalies through
panel glows, a top-bar alert sweep and an animated system-status badge.

See the repository root `README.md` for the full architecture and wire contract.
