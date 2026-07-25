"""
Finite State Machine for managing migration lifecycle.

Implements the state transition model from the FlexiMigrate paper:
- Normal path: PENDING -> PLANNING -> PREPARING -> EXECUTING -> VERIFYING -> COMPLETED
- Failure paths from each state -> FAILED
- Rollback from FAILED -> ROLLED_BACK
- Early cancellation from PENDING/PLANNING -> CANCELLED
"""

from __future__ import annotations

import logging
from enum import Enum
from typing import Callable, Dict, List, Optional, Set

from fleximigrate.models import MigrationStatus

logger = logging.getLogger(__name__)


class TransitionError(Exception):
    """Raised when an invalid state transition is attempted."""
    pass


class MigrationStateMachine:
    """
    Manages the lifecycle state transitions for a migration request.

    Uses a Finite State Machine (FSM) model to ensure structured,
    traceable, and robust handling of migration workflows.
    """

    # FSM transition table: current_state -> set of allowed next states
    ALLOWED_TRANSITIONS: Dict[MigrationStatus, Set[MigrationStatus]] = {
        MigrationStatus.PENDING: {
            MigrationStatus.PLANNING,
            MigrationStatus.FAILED,
            MigrationStatus.CANCELLED,
        },
        MigrationStatus.PLANNING: {
            MigrationStatus.PREPARING,
            MigrationStatus.FAILED,
            MigrationStatus.CANCELLED,
        },
        MigrationStatus.PREPARING: {
            MigrationStatus.EXECUTING,
            MigrationStatus.FAILED,
        },
        MigrationStatus.EXECUTING: {
            MigrationStatus.VERIFYING,
            MigrationStatus.FAILED,
        },
        MigrationStatus.VERIFYING: {
            MigrationStatus.COMPLETED,
            MigrationStatus.FAILED,
        },
        MigrationStatus.FAILED: {
            MigrationStatus.ROLLED_BACK,
        },
        MigrationStatus.COMPLETED: set(),
        MigrationStatus.ROLLED_BACK: set(),
        MigrationStatus.CANCELLED: set(),
    }

    def __init__(self, initial_state: MigrationStatus = MigrationStatus.PENDING):
        self._current_state = initial_state
        self._listeners: Dict[MigrationStatus, List[Callable]] = {
            state: [] for state in MigrationStatus
        }
        self._any_listeners: List[Callable] = []
        self._transition_history: List[Dict] = []

    @property
    def current_state(self) -> MigrationStatus:
        return self._current_state

    @property
    def allowed_transitions(self) -> Set[MigrationStatus]:
        """Returns the set of allowed next states from the current state."""
        return self.ALLOWED_TRANSITIONS.get(self._current_state, set())

    def can_transition_to(self, target: MigrationStatus) -> bool:
        """Check if a transition to the target state is allowed."""
        return target in self.allowed_transitions

    def transition_to(self, target: MigrationStatus, metadata: Optional[Dict] = None) -> bool:
        """
        Attempt a state transition to the target state.

        Args:
            target: The target state to transition to.
            metadata: Optional metadata to attach to the transition record.

        Returns:
            True if the transition succeeded.

        Raises:
            TransitionError: If the transition is not allowed.
        """
        if not self.can_transition_to(target):
            raise TransitionError(
                f"Transition from {self._current_state.value} to {target.value} is not allowed. "
                f"Allowed transitions: {[s.value for s in self.allowed_transitions]}"
            )

        previous = self._current_state
        self._current_state = target

        record = {
            "from": previous.value,
            "to": target.value,
            "timestamp": __import__("time").time(),
            "metadata": metadata or {},
        }
        self._transition_history.append(record)

        logger.info(
            "[StateMachine] Transition: %s -> %s",
            previous.value,
            target.value,
        )

        # Fire listeners
        for listener in self._listeners.get(target, []):
            try:
                listener(previous, target)
            except Exception as e:
                logger.error("Listener error on transition to %s: %s", target.value, e)

        for listener in self._any_listeners:
            try:
                listener(previous, target)
            except Exception as e:
                logger.error("Global listener error: %s", e)

        return True

    def on_transition_to(self, state: MigrationStatus, callback: Callable):
        """
        Register a callback that fires when transitioning to the given state.

        Args:
            state: The state to listen for.
            callback: Callable accepting (previous_state, new_state).
        """
        self._listeners[state].append(callback)

    def on_any_transition(self, callback: Callable):
        """Register a callback that fires on any state transition."""
        self._any_listeners.append(callback)

    @property
    def transition_history(self) -> List[Dict]:
        """Returns the full history of state transitions."""
        return list(self._transition_history)

    def is_terminal(self) -> bool:
        """Check if the current state is terminal (no further transitions)."""
        return len(self.allowed_transitions) == 0

    def is_active(self) -> bool:
        """Check if the migration is still in progress."""
        return not self.is_terminal() and self._current_state not in (
            MigrationStatus.FAILED,
            MigrationStatus.ROLLED_BACK,
            MigrationStatus.CANCELLED,
        )

    def reset(self, initial_state: MigrationStatus = MigrationStatus.PENDING):
        """Reset the state machine to initial state, clearing history."""
        self._current_state = initial_state
        self._transition_history.clear()
        logger.info("[StateMachine] Reset to initial state: %s", initial_state.value)
