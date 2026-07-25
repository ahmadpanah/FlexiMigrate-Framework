"""
FlexiMigrate: Main orchestrator class.

Ties together all components of the FlexiMigrate framework and provides
the primary API for managing live container migrations across heterogeneous
cloud and edge computing environments.
"""

from __future__ import annotations

import logging
import sys
from typing import Any, Dict, List, Optional

from fleximigrate.container_manager import (
    ImageManager,
    NestedContainerManager,
    RuntimeController,
)
from fleximigrate.decision_engine import (
    MigrationPlanner,
    PolicyEngine,
    ResourceOptimizer,
)
from fleximigrate.migration_manager import (
    LoggingAndMonitoring,
    MigrationCoordinator,
    MigrationStrategySelector,
    PolicyEnforcer,
)
from fleximigrate.models import (
    Container,
    Host,
    MigrationPlan,
    MigrationRequest,
    MigrationStatus,
    MigrationStrategy,
    Policy,
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
from fleximigrate.state_machine import MigrationStateMachine
from fleximigrate.state_synchronizer import (
    CheckpointingModule,
    DeltaTransfer,
    StateRestorationModule,
)

logger = logging.getLogger(__name__)


class FlexiMigrate:
    """
    Main orchestrator class for the FlexiMigrate framework.

    Provides a unified API for:
    - Managing hosts and containers in the cluster
    - Defining and enforcing migration policies
    - Planning and executing live container migrations
    - Monitoring system state and migration progress
    - Generating reports and logs

    Usage:
        flexi = FlexiMigrate(policies=[...])
        flexi.add_host(Host(host_id='host1', ...))
        flexi.add_container(Container(container_id='c1', ...))
        flexi.request_migration(container_id='c1', target_host_id='host2')
        flexi.run()
    """

    def __init__(
        self,
        policies: Optional[List[Dict[str, Any]]] = None,
        log_level: int = logging.INFO,
        sdn_controller_endpoint: str = "localhost:6633",
    ):
        self._setup_logging(log_level)

        # Initialize all sub-components
        self.resource_monitor = PerformanceMetricsCollector()
        self.utilization_analyzer = ResourceUtilizationAnalyzer()
        self.policy_engine = PolicyEngine()
        self.resource_optimizer = ResourceOptimizer()
        self.migration_planner = MigrationPlanner()
        self.container_manager = RuntimeController()
        self.nested_container_manager = NestedContainerManager()
        self.image_manager = ImageManager()
        self.sdn_controller = SDNControllerInterface(sdn_controller_endpoint)
        self.dns_manager = DNSManager()
        self.traffic_redirector = TrafficRedirector(self.sdn_controller)
        self.checkpointing = CheckpointingModule()
        self.delta_transfer = DeltaTransfer()
        self.state_restoration = StateRestorationModule()
        self.logging_monitoring = LoggingAndMonitoring()
        self.strategy_selector = MigrationStrategySelector()

        # Higher-level managers
        self.policy_enforcer = PolicyEnforcer(self.policy_engine)
        self.migration_coordinator: Optional[MigrationCoordinator] = None

        # Track migration requests and state machines
        self._migration_requests: Dict[str, MigrationRequest] = {}
        self._state_machines: Dict[str, MigrationStateMachine] = {}
        self._active_migrations: int = 0
        self._is_running = False

        # Register policies
        if policies:
            for p in policies:
                self.policy_engine.add_policy(Policy(**p))

        logger.info("FlexiMigrate framework initialized")

    def _setup_logging(self, level: int):
        """Configure logging for the framework."""
        handler = logging.StreamHandler(sys.stdout)
        handler.setFormatter(
            logging.Formatter(
                "%(levelname)s [%(name)s] %(message)s",
                datefmt="%H:%M:%S",
            )
        )
        root = logging.getLogger("fleximigrate")
        root.setLevel(level)
        root.addHandler(handler)

    @property
    def hosts(self) -> Dict[str, Host]:
        """Get all registered hosts."""
        return self.resource_optimizer.hosts

    @property
    def containers(self) -> Dict[str, Container]:
        """Get all registered containers."""
        return self.resource_optimizer.containers

    @property
    def requests(self) -> Dict[str, MigrationRequest]:
        """Get all migration requests."""
        return dict(self._migration_requests)

    @property
    def migration_engine(self) -> ResourceOptimizer:
        """Access the resource optimizer (backward compatibility)."""
        return self.resource_optimizer

    @property
    def decision_engine(self) -> "FlexiMigrateDecisionEngine":
        """Access the decision engine."""
        return FlexiMigrateDecisionEngine(
            self.policy_enforcer,
            self.migration_planner,
            self.strategy_selector,
            self.resource_optimizer,
        )

    # ---------- Host Management ----------

    def add_host(self, host: Host):
        """Register a host in the cluster."""
        self.resource_optimizer.hosts[host.host_id] = host
        logger.info("[FlexiMigrate] Added host: %s (CPU=%d, MEM=%dMB)", 
                     host.host_id, host.total_cpu, host.total_memory)

    def remove_host(self, host_id: str):
        """Remove a host from the cluster."""
        if host_id in self.resource_optimizer.hosts:
            host = self.resource_optimizer.hosts.pop(host_id)
            host.is_active = False
            logger.info("[FlexiMigrate] Removed host: %s", host_id)

    def get_host(self, host_id: str) -> Optional[Host]:
        """Get a host by ID."""
        return self.resource_optimizer.hosts.get(host_id)

    # ---------- Container Management ----------

    def add_container(self, container: Container, host_id: Optional[str] = None):
        """Register a container in the cluster."""
        if host_id and host_id in self.resource_optimizer.hosts:
            container.host = host_id
            host = self.resource_optimizer.hosts[host_id]
            host.containers.append(container)

        self.resource_optimizer.containers[container.container_id] = container
        logger.info("[FlexiMigrate] Added container: %s (image=%s, host=%s)",
                     container.container_id, container.image, container.host or "unassigned")

    def remove_container(self, container_id: str):
        """Remove a container from the cluster."""
        if container_id in self.resource_optimizer.containers:
            container = self.resource_optimizer.containers.pop(container_id)
            if container.host and container.host in self.resource_optimizer.hosts:
                host = self.resource_optimizer.hosts[container.host]
                host.containers = [c for c in host.containers if c.container_id != container_id]
            logger.info("[FlexiMigrate] Removed container: %s", container_id)

    def get_container(self, container_id: str) -> Optional[Container]:
        """Get a container by ID."""
        return self.resource_optimizer.containers.get(container_id)

    # ---------- Migration Operations ----------

    def request_migration(
        self,
        container_id: str,
        destination_host_id: Optional[str] = None,
        migration_type: Optional[MigrationStrategy] = None,
        **metadata,
    ) -> Optional[MigrationRequest]:
        """
        Request a migration for a container.

        Args:
            container_id: ID of the container to migrate.
            destination_host_id: Target host ID (auto-selected if None).
            migration_type: Migration strategy (auto-selected if None).
            **metadata: Additional metadata for the request.

        Returns:
            The created MigrationRequest, or None if validation fails.
        """
        # Validate container
        container = self.resource_optimizer.containers.get(container_id)
        if not container:
            logger.error("Container %s not found", container_id)
            return None

        # Get source host
        source_host = self.resource_optimizer.hosts.get(container.host) if container.host else None
        if not source_host:
            logger.error("Container %s has no assigned host", container_id)
            return None

        # Get or auto-select destination host
        if destination_host_id:
            dest_host = self.resource_optimizer.hosts.get(destination_host_id)
            if not dest_host:
                logger.error("Destination host %s not found", destination_host_id)
                return None
        else:
            dest_host = self.resource_optimizer.find_optimal_destination(container)
            if not dest_host:
                logger.error("No suitable destination host found")
                return None

        # Auto-select migration strategy if not specified
        if migration_type is None:
            migration_type = self.strategy_selector.select_strategy(
                container, source_host, dest_host
            )

        # Create migration request
        request = MigrationRequest(
            container_id=container_id,
            source_host=source_host,
            destination_host=dest_host,
            migration_type=migration_type,
            metadata=metadata,
        )

        # Enforce policies
        if not self.policy_enforcer.enforce_policies(request):
            logger.warning(
                "Migration request %s blocked by policy enforcement",
                request.request_id,
            )
            return None

        self._migration_requests[request.request_id] = request
        self._state_machines[request.request_id] = MigrationStateMachine()

        logger.info(
            "[FlexiMigrate] Migration request created: %s (%s -> %s, strategy=%s)",
            request.request_id, source_host.host_id, dest_host.host_id,
            migration_type.value,
        )
        return request

    def execute_migration(self, request: MigrationRequest, blocking: bool = True) -> MigrationStatus:
        """
        Execute a migration request.

        Args:
            request: The migration request to execute.
            blocking: If True, wait for migration to complete.

        Returns:
            The final migration status.
        """
        state_machine = self._state_machines.get(request.request_id)
        if not state_machine:
            logger.error("No state machine found for request %s", request.request_id)
            return MigrationStatus.FAILED

        self._active_migrations += 1

        # Create coordinator for this migration
        coordinator = MigrationCoordinator(
            state_machine=state_machine,
            container_manager=self.container_manager,
            checkpointing=self.checkpointing,
            delta_transfer=self.delta_transfer,
            state_restoration=self.state_restoration,
            traffic_redirector=self.traffic_redirector,
            dns_manager=self.dns_manager,
            nested_container_manager=self.nested_container_manager,
            image_manager=self.image_manager,
            container_registry=self.resource_optimizer.containers,
            host_registry=self.resource_optimizer.hosts,
        )

        self.migration_coordinator = coordinator

        # Create and execute migration plan
        plan = self.migration_planner.create_plan(request)
        status = coordinator.execute_plan(request, plan)

        self._active_migrations -= 1
        request.status = status

        # Log completion
        self.logging_monitoring.log_event(
            "FlexiMigrate",
            "migration_complete",
            f"Migration {request.request_id}: {status.value}",
            {"duration_ms": request.total_migration_time_ms},
        )

        # Generate report
        report = self.logging_monitoring.generate_migration_report(request)
        logger.info("\n%s", report)

        return status

    def request_and_execute(
        self,
        container_id: str,
        destination_host_id: Optional[str] = None,
        migration_type: Optional[MigrationStrategy] = None,
        blocking: bool = True,
        **metadata,
    ) -> MigrationStatus:
        """
        Convenience method: request and execute a migration in one step.

        Args:
            container_id: ID of the container to migrate.
            destination_host_id: Target host ID (auto-selected if None).
            migration_type: Migration strategy (auto-selected if None).
            blocking: If True, wait for migration to complete.
            **metadata: Additional metadata for the request.

        Returns:
            The final migration status.
        """
        request = self.request_migration(
            container_id, destination_host_id, migration_type, **metadata
        )
        if not request:
            return MigrationStatus.FAILED
        return self.execute_migration(request, blocking=blocking)

    def cancel_migration(self, request_id: str) -> bool:
        """Cancel a pending migration request."""
        state_machine = self._state_machines.get(request_id)
        if state_machine and state_machine.current_state == MigrationStatus.PENDING:
            try:
                state_machine.transition_to(MigrationStatus.CANCELLED)
                if request_id in self._migration_requests:
                    self._migration_requests[request_id].status = MigrationStatus.CANCELLED
                logger.info("[FlexiMigrate] Cancelled migration %s", request_id)
                return True
            except Exception as e:
                logger.error("Failed to cancel migration %s: %s", request_id, e)
        return False

    # ---------- Cluster Management ----------

    def balance_cluster(self) -> List[MigrationRequest]:
        """
        Analyze the cluster and automatically migrate containers
        to balance resource utilization.

        Returns:
            List of migration requests created for rebalancing.
        """
        recommendations = self.resource_optimizer.balance_cluster()
        requests = []

        for container, dest_host in recommendations:
            request = self.request_migration(
                container.container_id,
                dest_host.host_id,
                metadata={"reason": "cluster_balancing", "auto_initiated": True},
            )
            if request:
                requests.append(request)

        logger.info(
            "[FlexiMigrate] Cluster balancing: %d migrations recommended",
            len(requests),
        )
        return requests

    # ---------- Status and Reporting ----------

    def get_status(self) -> Dict[str, Any]:
        """Get the current status of the FlexiMigrate framework."""
        return {
            "hosts": len(self.resource_optimizer.hosts),
            "containers": len(self.resource_optimizer.containers),
            "active_migrations": self._active_migrations,
            "completed_migrations": sum(
                1 for r in self._migration_requests.values()
                if r.status == MigrationStatus.COMPLETED
            ),
            "failed_migrations": sum(
                1 for r in self._migration_requests.values()
                if r.status == MigrationStatus.FAILED
            ),
            "pending_requests": sum(
                1 for r in self._migration_requests.values()
                if r.status == MigrationStatus.PENDING
            ),
            "sdn_connected": self.sdn_controller._connected,
            "checkpoints_created": len(self.checkpointing._checkpoints),
            "dns_records": len(self.dns_manager._records),
        }

    def list_hosts(self) -> List[Dict[str, Any]]:
        """Get a formatted list of all hosts."""
        return [
            {
                "id": h.host_id,
                "cpu": f"{h.metrics.cpu_utilization:.1f}%",
                "memory": f"{h.metrics.memory_utilization:.1f}%",
                "containers": len(h.containers),
                "active": h.is_active,
            }
            for h in self.resource_optimizer.hosts.values()
        ]

    def list_containers(self) -> List[Dict[str, Any]]:
        """Get a formatted list of all containers."""
        return [
            {
                "id": c.container_id,
                "image": c.image,
                "host": c.host,
                "status": c.status,
                "cpu_limit": c.cpu_limit,
                "memory_mb": c.memory_limit,
            }
            for c in self.resource_optimizer.containers.values()
        ]

    def list_migrations(self) -> List[Dict[str, Any]]:
        """Get a formatted list of all migration requests."""
        return [r.to_dict() for r in self._migration_requests.values()]

    # ---------- Lifecycle ----------

    def run(self, interval_sec: float = 5.0):
        """
        Start the FlexiMigrate framework main loop.

        In this mode, the framework continuously monitors the cluster,
        processes migration requests, and maintains system health.
        """
        import time

        self._is_running = True
        logger.info("[FlexiMigrate] Framework started (monitoring interval=%ss)", interval_sec)

        # Connect to SDN controller
        self.sdn_controller.connect()

        # Start background metric collection
        self.resource_monitor.start_background_collection(
            self.resource_optimizer.hosts,
            self.resource_optimizer.containers,
        )

        try:
            while self._is_running:
                self._monitoring_tick()
                time.sleep(interval_sec)
        except KeyboardInterrupt:
            logger.info("[FlexiMigrate] Shutting down...")
        finally:
            self.shutdown()

    def _monitoring_tick(self):
        """Perform one monitoring cycle."""
        # Check for pending migrations that need processing
        for request_id, request in list(self._migration_requests.items()):
            if request.status == MigrationStatus.PENDING and self._active_migrations < 5:
                logger.info(
                    "[FlexiMigrate] Auto-executing pending migration %s",
                    request_id,
                )
                self.execute_migration(request, blocking=False)

    def shutdown(self):
        """Gracefully shut down the framework."""
        logger.info("[FlexiMigrate] Shutting down...")
        self._is_running = False
        self.resource_monitor.stop_background_collection()
        self.checkpointing.cleanup()
        self.container_manager.cleanup()
        self.sdn_controller.disconnect()
        logger.info("[FlexiMigrate] Shutdown complete")


class FlexiMigrateDecisionEngine:
    """
    Decision engine wrapper providing convenient access to
    policy enforcement, migration planning, and strategy selection.
    """

    def __init__(
        self,
        policy_enforcer: PolicyEnforcer,
        migration_planner: MigrationPlanner,
        strategy_selector: MigrationStrategySelector,
        resource_optimizer: ResourceOptimizer,
    ):
        self.policy_enforcer = policy_enforcer
        self.migration_planner = migration_planner
        self.strategy_selector = strategy_selector
        self.resource_optimizer = resource_optimizer

    def get_strategy(self, container: Container, source: Host, dest: Host) -> MigrationStrategy:
        return self.strategy_selector.select_strategy(container, source, dest)

    def create_plan(self, request: MigrationRequest) -> MigrationPlan:
        return self.migration_planner.create_plan(request)

    def enforce_policies(self, request: MigrationRequest) -> bool:
        return self.policy_enforcer.enforce_policies(request)
