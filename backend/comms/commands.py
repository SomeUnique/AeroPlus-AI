"""Uplink command queue (ground side, human-in-the-loop).

Commands are queued on the ground and can only be transmitted during a pass, and
only after an operator approves them:

    PENDING_APPROVAL --approve--> APPROVED --AOS--> QUEUED_FOR_PASS
        --uplink--> UPLINKED --ack--> EXECUTED

The agent *recommends* actions; it never queues or uplinks commands on its own.
Every transition is re-emitted under one ``command_id`` so the UI can animate it.
Everything is SIMULATED - no command reaches real hardware.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Dict, List, Optional

from models.schemas import CommandStatus, CommandStatusMessage, new_id


class CommandError(ValueError):
    """Base class for command-queue rejections."""


class UnknownCommandError(CommandError):
    """The command name is not in the approved uplink command set."""


class InvalidTransitionError(CommandError):
    """The requested action is not legal for the command's current status."""


@dataclass(frozen=True)
class CommandSpec:
    """One allowed uplink command."""

    name: str
    description: str
    critical: bool = False


#: The complete, auditable set of commands ground may uplink.
UPLINK_COMMANDS: Dict[str, CommandSpec] = {
    "SWITCH_TO_SECONDARY_ARRAY": CommandSpec(
        name="SWITCH_TO_SECONDARY_ARRAY",
        description="Switch the power bus to the redundant solar array string.",
    ),
    "THROTTLE_PAYLOAD": CommandSpec(
        name="THROTTLE_PAYLOAD",
        description="Reduce payload duty cycle to lower the power/thermal load.",
    ),
    "ENTER_SAFE_MODE": CommandSpec(
        name="ENTER_SAFE_MODE",
        description="Put the spacecraft into safe mode: payload off, minimum load.",
        critical=True,
    ),
}


def _now() -> datetime:
    return datetime.now(timezone.utc)


class UplinkCommandQueue:
    """Ground command queue with an operator-approval gate."""

    def __init__(self, *, max_per_pass: int = 3) -> None:
        self.max_per_pass = max(1, max_per_pass)
        self._commands: Dict[str, CommandStatusMessage] = {}
        self._uplinked_this_pass = 0
        self._pass_active = False

    # -- operator actions (ground, no pass needed) ---------------------------- #
    def submit(self, command: str, params: Optional[dict] = None, *, operator: str = "operator") -> CommandStatusMessage:
        """Queue a command for approval. Starts as PENDING_APPROVAL."""
        if command not in UPLINK_COMMANDS:
            raise UnknownCommandError(
                f"unknown command '{command}'; allowed: {', '.join(sorted(UPLINK_COMMANDS))}"
            )
        record = CommandStatusMessage(
            command_id=new_id("cmd"),
            command=command,
            params=dict(params or {}),
            status=CommandStatus.PENDING_APPROVAL,
            detail=f"Queued by {operator}; awaiting operator approval.",
            submitted_by=operator,
        )
        self._commands[record.command_id] = record
        return record

    def approve(self, command_id: str, *, operator: str = "operator") -> CommandStatusMessage:
        record = self._require(command_id)
        if record.status is not CommandStatus.PENDING_APPROVAL:
            raise InvalidTransitionError(
                f"command {command_id} is {record.status.value}, not PENDING_APPROVAL"
            )
        return self._transition(
            record,
            status=CommandStatus.APPROVED,
            detail=f"Approved by {operator}; will be uplinked at the next pass.",
            approved_by=operator,
        )

    def reject(self, command_id: str) -> CommandStatusMessage:
        """Remove a pending command. Returns the final snapshot (not retained)."""
        record = self._require(command_id)
        if record.status is not CommandStatus.PENDING_APPROVAL:
            raise InvalidTransitionError(
                f"command {command_id} is {record.status.value}; only PENDING_APPROVAL can be rejected"
            )
        del self._commands[command_id]
        return record.model_copy(
            update={"status": CommandStatus.PENDING_APPROVAL, "detail": "Rejected by operator.", "updated_at": _now()}
        )

    # -- pass-driven actions (spacecraft) ------------------------------------- #
    def on_pass_start(self) -> List[CommandStatusMessage]:
        """Move every APPROVED command to QUEUED_FOR_PASS. Returns the transitions."""
        self._pass_active = True
        self._uplinked_this_pass = 0
        transitions: List[CommandStatusMessage] = []
        for record in self._commands.values():
            if record.status is CommandStatus.APPROVED:
                transitions.append(
                    self._transition(
                        record,
                        status=CommandStatus.QUEUED_FOR_PASS,
                        detail="Uplink locked to the current pass; waiting for the link.",
                    )
                )
        return transitions

    def on_pass_end(self) -> None:
        self._pass_active = False

    def promote_approved(self) -> List[CommandStatusMessage]:
        """Queue any command approved *during* the current pass (allowed while in
        contact). Returns the APPROVED -> QUEUED_FOR_PASS transitions."""
        if not self._pass_active:
            return []
        transitions: List[CommandStatusMessage] = []
        for record in self._commands.values():
            if record.status is CommandStatus.APPROVED:
                transitions.append(
                    self._transition(
                        record,
                        status=CommandStatus.QUEUED_FOR_PASS,
                        detail="Approved during the pass; uplink locked to the current contact.",
                    )
                )
        return transitions

    def next_for_uplink(self) -> Optional[CommandStatusMessage]:
        """Take the next queued command for transmission, honouring the per-pass cap."""
        if not self._pass_active or self._uplinked_this_pass >= self.max_per_pass:
            return None
        for record in self._commands.values():
            if record.status is CommandStatus.QUEUED_FOR_PASS:
                self._uplinked_this_pass += 1
                return self._transition(
                    record,
                    status=CommandStatus.UPLINKED,
                    detail="Transmitted to the spacecraft during this pass.",
                )
        return None

    def mark_executed(self, command_id: str, *, detail: Optional[str] = None) -> CommandStatusMessage:
        record = self._require(command_id)
        if record.status is not CommandStatus.UPLINKED:
            raise InvalidTransitionError(
                f"command {command_id} is {record.status.value}, not UPLINKED"
            )
        return self._transition(
            record,
            status=CommandStatus.EXECUTED,
            detail=detail or "Execution acknowledged by the spacecraft.",
        )

    # -- introspection -------------------------------------------------------- #
    def get(self, command_id: str) -> Optional[CommandStatusMessage]:
        return self._commands.get(command_id)

    def list(self) -> List[CommandStatusMessage]:
        return list(self._commands.values())

    def counts(self) -> Dict[str, int]:
        counts = {status.value: 0 for status in CommandStatus}
        for record in self._commands.values():
            counts[record.status.value] += 1
        return counts

    # -- internals ------------------------------------------------------------ #
    def _require(self, command_id: str) -> CommandStatusMessage:
        record = self._commands.get(command_id)
        if record is None:
            raise CommandError(f"unknown command id '{command_id}'")
        return record

    def _transition(
        self,
        record: CommandStatusMessage,
        *,
        status: CommandStatus,
        detail: str,
        approved_by: Optional[str] = None,
    ) -> CommandStatusMessage:
        updated = record.model_copy(
            update={
                "status": status,
                "detail": detail,
                "updated_at": _now(),
                **({"approved_by": approved_by} if approved_by is not None else {}),
            }
        )
        self._commands[record.command_id] = updated
        return updated
