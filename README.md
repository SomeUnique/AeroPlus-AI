# AeroPulse AI

**An affordable AI mission-control co-pilot for university CubeSat teams and small space startups.**

> **SIMULATED DATA - SOFTWARE DEMONSTRATION ONLY.** Telemetry, RF link, orbit,
> ground station and faults are generated in software. No real spacecraft, radio
> or ground station is involved.

AeroPulse AI watches satellite telemetry, detects anomalies, explains what is
probably wrong, and recommends a fix. A human operator always decides whether to
send the command.

```
Detect (ML)  ->  Decide (AI agent)  ->  Act (human-approved command)
```

## Purpose

Most university CubeSat teams cannot afford 24/7 mission control. AeroPulse AI
gives them a low-cost co-pilot that provides early warnings, plain-language
explanations and a safe approval workflow. This matters for satellites that
support weather forecasting, communications and disaster monitoring - including
university and SUPARCO-style missions in Pakistan.

## How it works

1. **Simulated spacecraft + ground link.** The backend generates five sensor
   readings. The link alternates between 60 s `CONNECTED` and 60 s `NO_CONTACT`;
   readings are buffered during gaps and delivered as a burst at the next pass.
2. **Detect.** Three detectors run on every sample: absolute threshold, rolling
   z-score, and an Isolation Forest ML model. Three consecutive flags open an
   incident with a confidence value, evidence, sensors and detector type.
3. **Decide.** An LLM agent (Anthropic/OpenAI) explains the root cause, estimates
   time to failure and recommends a command. Without an API key, a clearly
   labelled scripted fallback runs (`OFFLINE FALLBACK`).
4. **Act.** The command waits as `PENDING_APPROVAL`. The operator must click
   **APPROVE UPLINK**. It is queued and sent only during the next contact window
   (`UPLINKED`). The agent never acts on its own.

## Features

- Live mission-control dashboard: sensor charts, thresholds, orbit view, Karachi
  ground-station pass timeline and subsystem health cards.
- Threshold + z-score + Isolation Forest detection with persistence filtering.
- Explainable incidents: severity, confidence, evidence, detector and incident ID.
- AI analysis with live LLM or offline fallback, including risk and next-pass
  deadline.
- Human-in-the-loop command queue: pending -> queued for pass -> uplinked.
- SIMULATED comms with contact gaps, onboard buffering and burst recovery.
- Three injectable faults: battery overheat, solar degradation, signal loss.
- Printable incident report at `/report`, reset control and boot animation.
- 110 backend tests plus an offline detector evaluation script.

## Tech stack

| Layer | Used |
| --- | --- |
| Main backend | **`backend/main.py`** - single-file FastAPI + WebSocket backend |
| Server | FastAPI, Uvicorn, Pydantic v2, websockets |
| Detection / ML | NumPy, scikit-learn `IsolationForest` |
| Main UI | `backend/static/index.html` - HTML, Tailwind CSS (CDN), Chart.js (CDN), vanilla JS |
| Optional UI | Next.js 15, React 19, TypeScript, Tailwind v4, zustand, Framer Motion, react-three-fiber |
| Optional modular backend | `backend/app` with `backend/detection`, `backend/agent`, `backend/comms` |
| Optional LLM | `anthropic` or `openai` Python package |

The recommended demo uses only `backend/main.py` and needs no Node.js build.

## Quick start (recommended)

Requires Python 3.11+ and a modern browser.

Windows PowerShell:

```powershell
cd backend
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
uvicorn main:app --host 127.0.0.1 --port 8000
```

macOS / Linux:

```bash
cd backend
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
uvicorn main:app --host 127.0.0.1 --port 8000
```

Then open:

- Mission control: <http://127.0.0.1:8000/>
- API docs: <http://127.0.0.1:8000/docs>
- Incident report: <http://127.0.0.1:8000/report>

If `uvicorn` is not found, run `python -m uvicorn main:app --host 127.0.0.1 --port 8000`.

## Using the app

1. Watch the charts and subsystem cards while the link is `CONNECTED`.
2. During `NO_CONTACT`, charts pause; data is buffered and arrives as a burst.
3. Press `D` to open the operator drawer and inject a fault.
4. Wait for an incident (roughly three flagged samples), then read the agent's
   explanation, evidence, confidence and recommended command.
5. Review the command and click **APPROVE UPLINK** if it makes sense.
6. Wait for the next pass to see the command become `UPLINKED`.
7. Use **Open Report** for a printable summary; **RESET MISSION** restarts the
   scenario.

## Optional: live LLM agent

```powershell
pip install anthropic
$env:ANTHROPIC_API_KEY = "your-key"
uvicorn main:app --host 127.0.0.1 --port 8000
```

Or install `openai` and set `OPENAI_API_KEY`. Optional model overrides:
`AEROPULSE_ANTHROPIC_MODEL` (default `claude-3-5-haiku-latest`) and
`AEROPULSE_OPENAI_MODEL` (default `gpt-4o-mini`). If the call fails or times out
(25 s), that incident uses the scripted fallback. Never commit API keys.

## API summary

| Method | Path | Purpose |
| --- | --- | --- |
| `GET` | `/` | Mission-control UI |
| `WS` | `/ws` | Live stream: snapshot, link, telemetry, burst, incidents, agent, commands |
| `POST` | `/inject-fault` | `{"fault": "battery_overheat" \| "solar_degradation" \| "signal_loss"}` |
| `POST` | `/reset` | Clear faults, incidents and commands; clock restarts |
| `POST` | `/approve/{command_id}` | Operator approval for a pending command |
| `GET` | `/report` | Printable SIMULATED incident report |

## Testing

```bash
cd backend
pytest                                        # last verified: 110 passed
python -m tools.detection_eval --nominal 3600 # last measured: 0 false alarms
```

The evaluation script also reports fault-detection latency (58 s, 6 s and 8 s in
the last local run). It scores the modular detection pipeline, not the
single-file demo detector.

## Optional Next.js UI

The `frontend/` app pairs with the modular backend and uses a different wire
format:

```bash
cd backend && uvicorn app.main:app --host 127.0.0.1 --port 8000
cd frontend && npm install && npm run dev     # http://localhost:3000
```

Run only one backend on port 8000 at a time, or change the port and set
`NEXT_PUBLIC_API_URL` / `NEXT_PUBLIC_WS_URL`.

## Limitations

- **False alarms are possible**; nothing has been tested on real hardware.
- Orbit and RF models are simplified simulations, not flight-grade physics.
- LLM output can be slow or wrong; the UI always shows live LLM vs fallback.
- No authentication, encryption or command signing - this is a prototype, not
  flight software.

## Troubleshooting

- **Port 8000 busy:** stop the other server or use `--port 8001`.
- **`ModuleNotFoundError` for numpy/sklearn:** run `pip install -r requirements.txt`.
- **Charts stop for about a minute:** expected `NO_CONTACT` window.
- **`OFFLINE FALLBACK` shown:** no LLM key, package missing, or provider failure.
- **Approved command not sent yet:** it waits for the next contact window.
- **PowerShell blocks venv activation:** run
  `Set-ExecutionPolicy -Scope Process -ExecutionPolicy Bypass`.
