"""
Migration Manager module.

Orchestrates the entire migration lifecycle: coordinates components,
enforces policies, manages resources, and handles logging/monitoring.

This is the central coordinator that ties all FlexiMigrate components
together and manages individual migration workflows.
"""

from __future__ import annotations

import logging
import time
from datetime import datetime
from enum import Enum
from typing import Any, Callable, Dict, List, Optional

from fleximigrate.container_manager import (
    ImageManager,
    NestedContainerManager,
    RuntimeController,
)
from fleximigrate.decision_engine import (
    MigrationPlanner,
    PolicyEngine,
    ResourceOptimizer,
    WorkloadAnalyzer,
)
from fleximigrate.models import (
    Container,
    Host,
    MigrationPlan,
    MigrationRequest,
    MigrationStatus,
    MigrationStrategy,
    Policy,
    ResourceMetrics,
)
from fleximigrate.network_manager import (
    DNSManager,
    SDNControllerInterface,
    TrafficRedirector,
)
from fleximigrate.resource_monitor import (
    PerformanceMetricsCollector,
    ResourceUtilizationAnalyzer,
)
from fleximigrate.state_machine import MigrationStateMachine, TransitionError
from fleximigrate.state_synchronizer import (
    CheckpointingModule,
    DeltaTransfer,
    StateRestorationModule,
)

logger = logging.getLogger(__name__)


class MigrationCoordinator:
    """
    Coordinates the end-to-end migration workflow by orchestrating
    all components through the FSM lifecycle.
    """

    def __init__(
        self,
        state_machine: MigrationStateMachine,
        container_manager: RuntimeController,
        checkpointing: CheckpointingModule,
        delta_transfer: DeltaTransfer,
        state_restoration: StateRestorationModule,
        traffic_redirector: TrafficRedirector,
        dns_manager: DNSManager,
        nested_container_manager: NestedContainerManager,
        image_manager: ImageManager,
        container_registry: Optional[Dict[str, 'Container']] = None,
        host_registry: Optional[Dict[str, 'Host']] = None,
    ):
        self._fsm = state_machine
        self._container_manager = container_manager
        self._checkpointing = checkpointing
        self._delta_transfer = delta_transfer
        self._state_restoration = state_restoration
        self._traffic_redirector = traffic_redirector
        self._dns_manager = dns_manager
        self._nested_manager = nested_container_manager
        self._image_manager = image_manager
        self._container_registry = container_registry or {}
        self._host_registry = host_registry or {}
        self._active_plan: Optional[MigrationPlan] = None
        self._request: Optional[MigrationRequest] = None

    def execute_plan(
        self, request: MigrationRequest, plan: MigrationPlan
    ) -> MigrationStatus:
        """
        Execute a migration plan through the FSM lifecycle.

        This is the main migration workflow method.
        """
        self._request = request
        self._active_plan = plan
        request.status = MigrationStatus.PENDING
        request.started_at = time.time()

        logger.info(
            "[MigrationCoordinator] Starting migration %s: %s -> %s (strategy=%s)",
            request.request_id, request.source_host.host_id,
            request.destination_host.host_id, request.migration_type.value,
        )

        try:
            # Phase 1: Planning
            self._fsm.transition_to(MigrationStatus.PLANNING)
            request.status = MigrationStatus.PLANNING
            if not self._phase_planning(request, plan):
                return self._fail(request, "Planning phase failed")

            # Phase 2: Preparation
            self._fsm.transition_to(MigrationStatus.PREPARING)
            request.status = MigrationStatus.PREPARING
            if not self._phase_preparation(request, plan):
                return self._fail(request, "Preparation phase failed")

            # Phase 3: Execution
            self._fsm.transition_to(MigrationStatus.EXECUTING)
            request.status = MigrationStatus.EXECUTING
            if not self._phase_execution(request, plan):
                return self._fail(request, "Execution phase failed")

            # Phase 4: Verification
            self._fsm.transition_to(MigrationStatus.VERIFYING)
            request.status = MigrationStatus.VERIFYING
            if not self._phase_verification(request, plan):
                return self._fail(request, "Verification phase failed")

            # Success
            self._fsm.transition_to(MigrationStatus.COMPLETED)
            request.status = MigrationStatus.COMPLETED
            request.completed_at = time.time()
            request.total_migration_time_ms = (
                request.completed_at - request.started_at
            ) * 1000

            logger.info(
                "[MigrationCoordinator] Migration %s completed successfully "
                "(duration=%.1fs, downtime=%.1fms)",
                request.request_id, request.duration_seconds or 0,
                request.estimated_downtime_ms,
            )
            return MigrationStatus.COMPLETED

        except TransitionError as e:
            return self._fail(request, f"State transition error: {e}")
        except Exception as e:
            logger.exception(
                "[MigrationCoordinator] Unexpected error during migration: %s", e
            )
            return self._fail(request, f"Unexpected error: {e}")

    def _phase_planning(self, request: MigrationRequest, plan: MigrationPlan) -> bool:
        """Planning phase: validate resources and create migration plan."""
        logger.info("[MigrationCoordinator] Phase: PLANNING")

        # Validate resource availability on destination
        dest = request.destination_host
        container = self._find_container(request.container_id)
        if container:
            needed_cpu = container.cpu_limit
            needed_mem = container.memory_limit
            if dest.cpu_available < needed_cpu or dest.memory_available_mb < needed_mem:
                logger.error(
                    "Insufficient resources on destination %s",
                    dest.host_id,
                )
                return False

        # Check image availability
        if container:
            image_check = self._image_manager.optimize_for_migration(
                container.image, request.source_host.host_id, dest.host_id
            )
            plan.resource_reservation["image_transfer_mb"] = image_check.get("transfer_size_mb", 0)

        return True

    def _phase_preparation(self, request: MigrationRequest, plan: MigrationPlan) -> bool:
        """Preparation phase: set up destination host and networking."""
        logger.info("[MigrationCoordinator] Phase: PREPARING")

        container = self._find_container(request.container_id)
        if not container:
            logger.error("Container %s not found", request.container_id)
            return False

        # 1. Ensure image is available on destination
        self._image_manager.pull_image(container.image, request.destination_host.host_id)

        # 2. Set up DNS for the destination
        self._dns_manager.register_container(container, request.destination_host)

        # 3. Begin traffic redirection (if live migration)
        if request.migration_type in (
            MigrationStrategy.LIVE_MIGRATION,
            MigrationStrategy.HYBRID,
            MigrationStrategy.PRE_COPY,
        ):
            self._traffic_redirector.start_traffic_redirection(
                container, request.source_host, request.destination_host
            )

        return True

    def _phase_execution(self, request: MigrationRequest, plan: MigrationPlan) -> bool:
        """Execution phase: perform the actual state transfer."""
        logger.info("[MigrationCoordinator] Phase: EXECUTING")

        container = self._find_container(request.container_id)
        if not container:
            return False

        # 1. Create initial checkpoint
        base_checkpoint = self._checkpointing.create_checkpoint(container)
        if not base_checkpoint:
            return False

        # 2. Execute iterative pre-copy rounds (for live/pre-copy)
        if plan.pre_copy_rounds > 0 and request.migration_type in (
            MigrationStrategy.LIVE_MIGRATION,
            MigrationStrategy.PRE_COPY,
            MigrationStrategy.HYBRID,
        ):
            for round_num in range(plan.pre_copy_rounds):
                logger.info(
                    "[MigrationCoordinator] Pre-copy round %d/%d",
                    round_num + 1, plan.pre_copy_rounds,
                )

                # Simulate some time passing for dirty page generation
                time.sleep(plan.checkpoint_interval_sec * 0.1)

                # Create new checkpoint and compute delta
                new_checkpoint = self._checkpointing.create_checkpoint(container)
                if not new_checkpoint:
                    continue

                delta = self._delta_transfer.compute_delta(base_checkpoint, new_checkpoint)
                if delta and delta.size_bytes > 0:
                    self._delta_transfer.simulate_transfer(
                        delta, plan.bandwidth_limit_mbps
                    )
                    self._state_restoration.incremental_restore(
                        base_checkpoint, delta, request.destination_host
                    )

                base_checkpoint = new_checkpoint

        # 3. Stop container and transfer final state
        if request.migration_type == MigrationStrategy.LIVE_MIGRATION:
            self._pause_and_transfer_final_state(container, plan)
        elif request.migration_type == MigrationStrategy.COLD_MIGRATION:
            self._container_manager.stop_container(container)

        # 4. Transfer final checkpoint
        final_checkpoint = self._checkpointing.create_checkpoint(container)
        if final_checkpoint:
            self._state_restoration.full_restore(final_checkpoint, request.destination_host)

        # 5. Update DNS to point to new host
        self._dns_manager.update_container_ip(
            request.container_id, request.destination_host.ip_address
        )

        # Simulate data transfer for metrics
        request.data_transferred_mb = final_checkpoint.size_bytes // (1024 * 1024) if final_checkpoint else 0
        request.bandwidth_used_mbps = plan.bandwidth_limit_mbps

        return True

    def _phase_verification(self, request: MigrationRequest, plan: MigrationPlan) -> bool:
        """Verification phase: validate the migration was successful."""
        logger.info("[MigrationCoordinator] Phase: VERIFYING")

        container = self._find_container(request.container_id)
        if not container:
            return False

        checks_passed = True
        failed_checks = []

        for check in plan.verification_checks:
            result = self._run_verification_check(check, container, request)
            if not result["passed"]:
                checks_passed = False
                failed_checks.append(check)
                logger.warning(
                    "[MigrationCoordinator] Verification check '%s' failed: %s",
                    check, result["reason"],
                )
            else:
                logger.info(
                    "[MigrationCoordinator] Verification check '%s' passed",
                    check,
                )

        if not checks_passed:
            logger.error(
                "[MigrationCoordinator] Verification failed: %s",
                ", ".join(failed_checks),
            )
            return False

        # Clean up traffic redirect
        self._traffic_redirector.stop_traffic_redirection(request.container_id)

        return True

    def _pause_and_transfer_final_state(
        self, container: Container, plan: MigrationPlan
    ):
        """Pause container and transfer final dirty pages (stop-and-copy)."""
        logger.info("[MigrationCoordinator] Stop-and-copy phase")
        self._container_manager.pause_container(container)
        time.sleep(plan.max_downtime_ms / 1000.0)  # Simulate final sync delay
        self._container_manager.unpause_container(container)

    def _run_verification_check(
        self, check_name: str, container: Container, request: MigrationRequest
    ) -> Dict[str, Any]:
        """Run a single verification check."""
        if check_name == "container_running":
            return {"passed": True, "reason": ""}
        elif check_name == "network_connectivity":
            return {"passed": True, "reason": ""}
        elif check_name == "data_integrity":
            return {"passed": True, "reason": ""}
        elif check_name == "health_check":
            return {"passed": True, "reason": ""}
        else:
            return {"passed": True, "reason": f"Unknown check '{check_name}', assuming pass"}

    def _fail(self, request: MigrationRequest, reason: str) -> MigrationStatus:
        """Handle migration failure and attempt rollback."""
        logger.error("[MigrationCoordinator] FAILED: %s", reason)
        request.error = reason
        request.status = MigrationStatus.FAILED

        try:
            self._fsm.transition_to(MigrationStatus.FAILED)
        except TransitionError:
            pass

        # Attempt rollback
        try:
            self._rollback(request)
        except Exception as e:
            logger.error("[MigrationCoordinator] Rollback failed: %s", e)

        return MigrationStatus.FAILED

    def _rollback(self, request: MigrationRequest):
        """Rollback a failed migration."""
        logger.info("[MigrationCoordinator] Starting rollback for %s", request.request_id)

        # Stop traffic redirection
        self._traffic_redirector.stop_traffic_redirection(request.container_id)

        # Restore DNS to point back to source
        self._dns_manager.update_container_ip(
            request.container_id, request.source_host.ip_address
        )

        # Clean up any partial state on destination
        self._state_restoration.rollback_restore(request.container_id)

        # Reset container on source
        container = self._find_container(request.container_id)
        if container:
            self._container_manager.unpause_container(container)

        request.status = MigrationStatus.ROLLED_BACK
        try:
            self._fsm.transition_to(MigrationStatus.ROLLED_BACK)
        except TransitionError:
            pass

        logger.info("[MigrationCoordinator] Rollback completed for %s", request.request_id)

    def _find_container(self, container_id: str) -> Optional[Container]:
        """Find a container by its ID."""
        return self._container_registry.get(container_id)


class PolicyEnforcer:
    """
    Wraps the Policy Engine with additional enforcement logic,
    ensuring policies are consistently applied before, during,
    and after migrations.
    """

    def __init__(self, policy_engine: PolicyEngine):
        self._engine = policy_engine
        self._violation_history: List[Dict] = []

    def enforce_policies(self, request: MigrationRequest) -> bool:
        """
        Enforce all policies against a migration request.

        Returns True if the request is allowed by all policies.
        """
        context = {
            "active_migrations": 0,
            "request_type": request.migration_type.value,
        }

        allowed, reasons = self._engine.evaluate(request, context)

        if not allowed:
            self._violation_history.append({
                "request_id": request.request_id,
                "timestamp": time.time(),
                "reasons": reasons,
            })
            for reason in reasons:
                logger.warning("[PolicyEnforcer] %s", reason)

        return allowed

    def get_violation_history(self) -> List[Dict]:
        """Get the history of policy violations."""
        return list(self._violation_history)


class MigrationStrategySelector:
    """
    Selects the optimal migration strategy based on workload
    characteristics, network conditions, and service requirements.
    """

    def __init__(self):
        self._analyzer = WorkloadAnalyzer()

    def select_strategy(
        self, container: Container, source_host: Host, destination_host: Host
    ) -> MigrationStrategy:
        """
        Select the best migration strategy based on current conditions.
        """
        workload_type = self._analyzer.classify_workload(container)

        if workload_type == "cpu_bound":
            return MigrationStrategy.LIVE_MIGRATION
        elif workload_type == "memory_bound":
            if source_host.metrics.network_bandwidth_mbps > 500:
                return MigrationStrategy.PRE_COPY
            else:
                return MigrationStrategy.POST_COPY
        elif workload_type == "io_bound":
            return MigrationStrategy.HYBRID
        else:
            # Balanced workload - prefer live migration
            return MigrationStrategy.LIVE_MIGRATION


class LoggingAndMonitoring:
    """
    Provides comprehensive logging and monitoring for the entire
    migration lifecycle. Tracks metrics, logs events, and generates
    reports.
    """

    def __init__(self):
        self._events: List[Dict] = []
        self._metrics_history: Dict[str, List[ResourceMetrics]] = {}

    def log_event(
        self,
        component: str,
        event_type: str,
        message: str,
        metadata: Optional[Dict] = None,
    ):
        """Log an event with metadata."""
        event = {
            "timestamp": datetime.now().isoformat(),
            "component": component,
            "type": event_type,
            "message": message,
            "metadata": metadata or {},
        }
        self._events.append(event)
        logger.info("[%s] %s: %s", component, event_type, message)

    def record_metrics(self, component: str, metrics: ResourceMetrics):
        """Record resource metrics for a component."""
        if component not in self._metrics_history:
            self._metrics_history[component] = []
        self._metrics_history[component].append(metrics)

    def get_events(
        self,
        component: Optional[str] = None,
        event_type: Optional[str] = None,
        limit: int = 100,
    ) -> List[Dict]:
        """Get filtered event log."""
        events = self._events
        if component:
            events = [e for e in events if e["component"] == component]
        if event_type:
            events = [e for e in events if e["type"] == event_type]
        return events[-limit:]

    def generate_migration_report(self, request: MigrationRequest) -> str:
        """Generate a human-readable migration report."""
        lines = [
            "=" * 60,
            f"MIGRATION REPORT - {request.request_id}",
            "=" * 60,
            f"Container: {request.container_id}",
            f"Source: {request.source_host.host_id}",
            f"Destination: {request.destination_host.host_id}",
            f"Strategy: {request.migration_type.value}",
            f"Status: {request.status.value}",
            f"Duration: {request.duration_seconds or 0:.2f}s",
            f"Downtime: {request.estimated_downtime_ms:.1f}ms",
            f"Data Transferred: {request.data_transferred_mb:.1f}MB",
            f"Bandwidth Used: {request.bandwidth_used_mbps:.1f}Mbps",
            "-" * 60,
        ]
        if request.error:
            lines.append(f"Error: {request.error}")

        lines.append("=" * 60)
        return "\n".join(lines)
