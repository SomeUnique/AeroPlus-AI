"""Agent service: consumes detection events, emits the reasoning trace and actions.

The service owns an asyncio queue and a background task. ``observe()`` is called
by the telemetry loop with the payloads the detector just produced; the task then
paces out ``agent_thinking`` / ``agent_action`` frames (and a ``report_ready``
when the subsystem recovers) through the shared publisher.

Pacing is configurable so the frontend sees a live reasoning trace, while tests
set the delays to zero and await :meth:`AgentService.idle`.
"""

from __future__ import annotations

import asyncio
import inspect
import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Callable, Dict, List, Optional

from agent.agent import (
    IncidentState,
    build_actions,
    build_report,
    build_thinking,
    incident_key,
)
from agent.playbooks import select_playbook
from models.schemas import (
    ActionStatus,
    AgentActionMessage,
    AgentThinkingMessage,
    AgentPhase,
    AnomalyMessage,
    ReportReadyMessage,
    StatusSource,
    StatusUpdateMessage,
)

logger = logging.getLogger("aeropulse.agent")

_NOMINAL_SUFFIX = "_NOMINAL"


@dataclass
class _Event:
    kind: str  # "anomaly" | "recovery"
    anomaly: Optional[AnomalyMessage] = None
    subsystem: Optional[str] = None
    context: Dict[str, Any] = field(default_factory=dict)


class AgentService:
    def __init__(
        self,
        *,
        enabled: bool = True,
        step_delay_s: float = 0.35,
        action_delay_s: float = 0.6,
        report_delay_s: float = 0.4,
    ) -> None:
        self.step_delay_s = step_delay_s
        self.action_delay_s = action_delay_s
        self.report_delay_s = report_delay_s

        self._queue: "asyncio.Queue[_Event]" = asyncio.Queue()
        self._task: Optional[asyncio.Task] = None
        self._publisher: Optional[Callable[[Any], Any]] = None

        self._incidents: Dict[str, IncidentState] = {}
        self._by_subsystem: Dict[str, str] = {}
        self._enabled = enabled

        # health counters
        self.incidents_handled = 0
        self.thinking_steps = 0
        self.actions_taken = 0
        self.reports_generated = 0

    # -- lifecycle ------------------------------------------------------------ #
    def set_publisher(self, publisher: Callable[[Any], Any]) -> None:
        self._publisher = publisher

    async def start(self) -> None:
        if self._task is None or self._task.done():
            self._task = asyncio.create_task(self._run(), name="agent")

    async def stop(self) -> None:
        if self._task is not None:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
            self._task = None

    async def idle(self) -> None:
        """Block until every queued event has been fully handled (tests)."""
        await self._queue.join()

    @property
    def active_incidents(self) -> int:
        return len(self._incidents)

    # -- input ---------------------------------------------------------------- #
    def observe(self, payloads: List[Any], context: Any = None) -> None:
        """Enqueue detection payloads. Non-blocking; ignored when disabled."""
        if not self._enabled:
            return
        ctx = self._context_snapshot(context)
        for payload in payloads:
            if isinstance(payload, AnomalyMessage):
                self._queue.put_nowait(
                    _Event(kind="anomaly", anomaly=payload,
                           subsystem=payload.subsystem, context=ctx)
                )
            elif (
                isinstance(payload, StatusUpdateMessage)
                and payload.source is StatusSource.DETECTION
                and payload.status.endswith(_NOMINAL_SUFFIX)
            ):
                subsystem = payload.status[: -len(_NOMINAL_SUFFIX)]
                self._queue.put_nowait(_Event(kind="recovery", subsystem=subsystem))

    @staticmethod
    def _context_snapshot(reading: Any) -> Dict[str, Any]:
        if reading is None:
            return {}
        return {
            "payload_mode": getattr(getattr(reading, "payload_mode", None), "value", "unknown"),
            "sun_fraction": float(getattr(reading, "sun_fraction", 1.0)),
            "in_eclipse": bool(getattr(reading, "in_eclipse", False)),
        }

    def reset(self) -> None:
        self._incidents.clear()
        self._by_subsystem.clear()
        self.incidents_handled = 0
        self.thinking_steps = 0
        self.actions_taken = 0
        self.reports_generated = 0

    # -- run loop ------------------------------------------------------------- #
    async def _run(self) -> None:
        while True:
            event = await self._queue.get()
            try:
                if event.kind == "anomaly" and event.anomaly is not None:
                    await self._handle_anomaly(event)
                elif event.kind == "recovery" and event.subsystem:
                    await self._handle_recovery(event.subsystem)
            except asyncio.CancelledError:
                raise
            except Exception:  # noqa: BLE001 - an agent bug must not kill the loop
                logger.exception("agent failed to handle event %s", event.kind)
            finally:
                self._queue.task_done()

    async def _handle_anomaly(self, event: _Event) -> None:
        anomaly = event.anomaly
        assert anomaly is not None
        key = incident_key(anomaly)
        existing = self._incidents.get(key)
        if existing is not None:
            escalated = existing.note_anomaly(anomaly)
            if escalated:
                existing.thinking_steps += 1
                await self._publish(self._thinking(
                    existing.thinking_steps,
                    AgentPhase.REASON,
                    f"Escalation: {anomaly.subsystem} severity raised to "
                    f"{anomaly.severity.value} ({anomaly.sensor.value}={anomaly.value:.3f}).",
                    anomaly,
                ))
                await asyncio.sleep(self.step_delay_s)
            return

        playbook = select_playbook(anomaly.subsystem)
        state = IncidentState(
            incident_id=key,
            subsystem=anomaly.subsystem or "Unknown",
            playbook=playbook,
            severity=anomaly.severity.value,
            context=dict(event.context),
        )
        state.note_anomaly(anomaly)
        self._incidents[key] = state
        self._by_subsystem[state.subsystem] = key
        self.incidents_handled += 1

        for step in build_thinking(anomaly, event.context, playbook):
            state.thinking_steps = step.step
            await self._publish(step)
            self.thinking_steps += 1
            await asyncio.sleep(self.step_delay_s)

        actions = build_actions(anomaly, event.context, playbook)
        for action in actions:
            await self._cycle_action(action)
            state.actions.append(action)

        state.thinking_steps += 1
        await self._publish(self._thinking(
            state.thinking_steps,
            AgentPhase.VERIFY,
            f"Actions issued for {state.subsystem}; monitoring subsystem status for recovery.",
            anomaly,
        ))
        await asyncio.sleep(self.step_delay_s)

    async def _cycle_action(self, action: AgentActionMessage) -> None:
        """Emit the action lifecycle as distinct snapshots (proposed -> executing -> succeeded)."""
        await self._publish(action.model_copy(update={"status": ActionStatus.PROPOSED}))
        await asyncio.sleep(self.action_delay_s)
        await self._publish(action.model_copy(update={"status": ActionStatus.EXECUTING}))
        await asyncio.sleep(self.action_delay_s)
        action.status = ActionStatus.SUCCEEDED
        await self._publish(action.model_copy())
        self.actions_taken += 1

    async def _handle_recovery(self, subsystem: str) -> None:
        key = self._by_subsystem.pop(subsystem, None)
        if key is None:
            return
        state = self._incidents.pop(key, None)
        if state is None:
            return
        report = build_report(state, ended_at=datetime.now(timezone.utc))
        await asyncio.sleep(self.report_delay_s)
        await self._publish(report)
        self.reports_generated += 1
        logger.info("incident %s closed with report %s", state.incident_id, report.report_id)

    # -- helpers -------------------------------------------------------------- #
    @staticmethod
    def _thinking(step: int, phase: AgentPhase, content: str,
                  anomaly: AnomalyMessage) -> AgentThinkingMessage:
        return AgentThinkingMessage(
            step=step,
            phase=phase,
            content=content,
            inputs={"incident_id": anomaly.incident_id, "sensor": anomaly.sensor.value},
        )

    async def _publish(self, payload: Any) -> None:
        if self._publisher is None:
            return
        result = self._publisher(payload)
        if inspect.isawaitable(result):
            await result
