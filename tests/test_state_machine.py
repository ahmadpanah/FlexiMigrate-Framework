"""Tests for the migration state machine."""

import pytest
from fleximigrate.state_machine import MigrationStateMachine, TransitionError
from fleximigrate.models import MigrationStatus


class TestMigrationStateMachine:
    def test_initial_state(self):
        fsm = MigrationStateMachine()
        assert fsm.current_state == MigrationStatus.PENDING

    def test_custom_initial_state(self):
        fsm = MigrationStateMachine(MigrationStatus.PLANNING)
        assert fsm.current_state == MigrationStatus.PLANNING

    def test_allowed_transitions_from_pending(self):
        fsm = MigrationStateMachine()
        allowed = fsm.allowed_transitions
        assert MigrationStatus.PLANNING in allowed
        assert MigrationStatus.FAILED in allowed
        assert MigrationStatus.CANCELLED in allowed

    def test_successful_full_path(self):
        fsm = MigrationStateMachine()
        fsm.transition_to(MigrationStatus.PLANNING)
        assert fsm.current_state == MigrationStatus.PLANNING
        fsm.transition_to(MigrationStatus.PREPARING)
        assert fsm.current_state == MigrationStatus.PREPARING
        fsm.transition_to(MigrationStatus.EXECUTING)
        assert fsm.current_state == MigrationStatus.EXECUTING
        fsm.transition_to(MigrationStatus.VERIFYING)
        assert fsm.current_state == MigrationStatus.VERIFYING
        fsm.transition_to(MigrationStatus.COMPLETED)
        assert fsm.current_state == MigrationStatus.COMPLETED

    def test_failure_path(self):
        fsm = MigrationStateMachine()
        fsm.transition_to(MigrationStatus.FAILED)
        assert fsm.current_state == MigrationStatus.FAILED
        fsm.transition_to(MigrationStatus.ROLLED_BACK)
        assert fsm.current_state == MigrationStatus.ROLLED_BACK

    def test_invalid_transition_raises_error(self):
        fsm = MigrationStateMachine()
        with pytest.raises(TransitionError, match="not allowed"):
            fsm.transition_to(MigrationStatus.COMPLETED)

    def test_can_transition_to(self):
        fsm = MigrationStateMachine()
        assert fsm.can_transition_to(MigrationStatus.PLANNING) is True
        assert fsm.can_transition_to(MigrationStatus.COMPLETED) is False

    def test_is_terminal(self):
        fsm = MigrationStateMachine()
        assert fsm.is_terminal() is False
        fsm.transition_to(MigrationStatus.PLANNING)
        fsm.transition_to(MigrationStatus.PREPARING)
        fsm.transition_to(MigrationStatus.EXECUTING)
        fsm.transition_to(MigrationStatus.VERIFYING)
        fsm.transition_to(MigrationStatus.COMPLETED)
        assert fsm.is_terminal() is True

    def test_is_active(self):
        fsm = MigrationStateMachine()
        assert fsm.is_active() is True
        fsm.transition_to(MigrationStatus.CANCELLED)
        assert fsm.is_active() is False

    def test_transition_history(self):
        fsm = MigrationStateMachine()
        fsm.transition_to(MigrationStatus.PLANNING)
        fsm.transition_to(MigrationStatus.PREPARING)
        history = fsm.transition_history
        assert len(history) == 2
        assert history[0]["from"] == "pending"
        assert history[0]["to"] == "planning"
        assert history[1]["from"] == "planning"
        assert history[1]["to"] == "preparing"

    def test_listeners(self):
        fsm = MigrationStateMachine()
        callbacks = []

        def on_complete(prev, new):
            callbacks.append((prev, new))

        fsm.on_transition_to(MigrationStatus.COMPLETED, on_complete)
        fsm.transition_to(MigrationStatus.PLANNING)
        fsm.transition_to(MigrationStatus.PREPARING)
        fsm.transition_to(MigrationStatus.EXECUTING)
        fsm.transition_to(MigrationStatus.VERIFYING)
        fsm.transition_to(MigrationStatus.COMPLETED)

        assert len(callbacks) == 1
        assert callbacks[0][0] == MigrationStatus.VERIFYING
        assert callbacks[0][1] == MigrationStatus.COMPLETED

    def test_any_listener(self):
        fsm = MigrationStateMachine()
        callbacks = []

        def on_any(prev, new):
            callbacks.append(new)

        fsm.on_any_transition(on_any)
        fsm.transition_to(MigrationStatus.PLANNING)
        fsm.transition_to(MigrationStatus.PREPARING)

        assert len(callbacks) == 2

    def test_reset(self):
        fsm = MigrationStateMachine()
        fsm.transition_to(MigrationStatus.PLANNING)
        fsm.reset()
        assert fsm.current_state == MigrationStatus.PENDING
        assert len(fsm.transition_history) == 0

    def test_cancellation(self):
        fsm = MigrationStateMachine()
        fsm.transition_to(MigrationStatus.CANCELLED)
        assert fsm.current_state == MigrationStatus.CANCELLED
        assert fsm.is_terminal() is True
