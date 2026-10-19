# AGENTS.md — AeroPulse AI

Rules for all work on this repository. Follow them for every change, in every session.

## Project

AeroPulse AI is an affordable AI mission-control co-pilot for university CubeSat
teams and small space startups that cannot afford 24/7 mission-control engineers.

- **Pakistan relevance**: university satellite projects and SUPARCO-style missions.
- **Why it matters**: satellites support weather forecasting, communication and
  disaster monitoring — protecting them protects people.
- **Core loop**: **Detect (ML) -> Decide (LLM agent) -> Act (human-approved actions).**

Layout:

```
/backend   FastAPI + WebSocket mission-control API (telemetry, detection, agent)
/frontend  Next.js App Router + TypeScript + Tailwind mission-control UI
```

## Honesty rules (hackathon policy: do not fabricate evidence)

1. **Label everything simulated.** Any simulated data — telemetry, RF link, ground
   station, fault injection — must be visibly marked `SIMULATED` in the UI and in
   the README. Never imply it is real hardware or real ground-station data.
2. **Declare the fallback.** If the LLM is unavailable and the scripted fallback
   agent runs, the UI must clearly show `OFFLINE FALLBACK MODE`.
3. **No fake metrics.** Every metric shown in the UI or README must be computed by
   a real evaluation script (e.g. `backend/tools/detection_eval.py`). Never hand
   write, round up, extrapolate or invent a number. If it has not been measured,
   do not display it.

## Responsible AI rules

1. **Human-in-the-loop.** The agent *recommends*; it never acts on its own.
   Critical commands require explicit operator approval before execution.
2. **Confidence + explainability on every alert.** Each alert must show a
   confidence value and which sensors and which detector produced it
   (`detector_type`, `sensor`, `incident_id`).
3. **Plain language.** Explanations must be understandable by a student engineer.
   No unexplained jargon; define or avoid it.
4. **Limitations section in the README.** Must state that false alarms are
   possible and that the system has not been tested on real hardware.

## Division of AI roles

| Stage | Owner | Responsibility |
| ----- | ----- | -------------- |
| Detect | ML models — Isolation Forest, optionally LSTM autoencoder | Detect anomalies in telemetry. Statistical detectors (rule, z-score, rate) are the current implementation; ML models slot into the same `detection/` interface. |
| Decide | LLM agent | Explain root cause, estimate risk, propose actions — using tools. |
| Act | Human operator | Approves and executes commands. Agent actions go `proposed -> executing -> succeeded` only after approval. |

Keep the boundary clean: ML does not explain, the LLM does not detect raw
telemetry, and neither one executes commands without a human.

## Quality bar

- Production-quality code. No beginner or template look.
- **Typed everywhere**: full type annotations in Python (`backend/`), strict
  TypeScript in `frontend/`. Validate boundaries with pydantic models
  (`backend/models/schemas.py`) and the mirrored contract in
  `frontend/src/lib/ws.ts`.
- Small, reusable, single-responsibility components. Shared primitives live in
  `frontend/src/components/ui/`.
- Match existing style and conventions in the file you are editing; do not
  introduce new frameworks or libraries without checking they are already used
  in the repo.

## Workflow

1. **Work in phases.** One phase at a time, scoped before starting.
2. **Run and verify each phase** before moving on:
   ```bash
   cd backend && pytest                        # contract, detection, agent tests
   cd backend && python -m tools.detection_eval --nominal 3600 -v   # real metrics
   cd frontend && npm run lint && npm run build
   ```
3. **Summarize what works** at the end of each phase: what was verified, what
   was measured, and what is still unverified.
4. Keep the README's Roadmap, API and metric sections in sync with reality.
