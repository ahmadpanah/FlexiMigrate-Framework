"""
Integration and component tests for the FlexiMigrate framework.

Tests cover the full lifecycle: component interactions, migration workflows,
policy enforcement, state management, and error handling.
"""

import time
import pytest
from fleximigrate import FlexiMigrate
from fleximigrate.models import (
    Container,
    Host,
    MigrationRequest,
    MigrationStatus,
    MigrationStrategy,
    Policy,
)
from fleximigrate.resource_monitor import (
    PerformanceMetricsCollector,
    ResourceUtilizationAnalyzer,
)
from fleximigrate.decision_engine import (
    PolicyEngine,
    ResourceOptimizer,
    MigrationPlanner,
    WorkloadAnalyzer,
)
from fleximigrate.container_manager import (
    RuntimeController,
    NestedContainerManager,
    ImageManager,
)
from fleximigrate.network_manager import (
    SDNControllerInterface,
    DNSManager,
    TrafficRedirector,
)
from fleximigrate.state_synchronizer import (
    CheckpointingModule,
    DeltaTransfer,
    StateRestorationModule,
)
from fleximigrate.state_machine import MigrationStateMachine


# ============================================================================
# Fixtures
# ============================================================================


@pytest.fixture
def hosts():
    return [
        Host(host_id="host-1", total_cpu=16, total_memory=32768, total_storage=1000,
             ip_address="10.0.0.1"),
        Host(host_id="host-2", total_cpu=16, total_memory=32768, total_storage=1000,
             ip_address="10.0.0.2"),
        Host(host_id="host-3", total_cpu=8, total_memory=16384, total_storage=500,
             ip_address="10.0.0.3"),
    ]


@pytest.fixture
def containers():
    return [
        Container(container_id="web-1", image="nginx:alpine", cpu_limit=2, memory_limit=1024, storage_limit=20),
        Container(container_id="db-1", image="postgres:15", cpu_limit=4, memory_limit=4096, storage_limit=100),
        Container(container_id="cache-1", image="redis:7", cpu_limit=1, memory_limit=512, storage_limit=10),
    ]


@pytest.fixture
def flexi():
    return FlexiMigrate(log_level=30)


# ============================================================================
# Resource Monitor Tests
# ============================================================================


class TestPerformanceMetricsCollector:
    def test_simulate_metrics(self):
        collector = PerformanceMetricsCollector()
        host = Host(host_id="h1", total_cpu=16, total_memory=32768, total_storage=1000)
        metrics = collector.collect_host_metrics(host)

        assert metrics.cpu_utilization >= 0
        assert metrics.memory_utilization >= 0
        assert metrics.network_bandwidth_mbps >= 0
        assert metrics.timestamp > 0

    def test_metric_history(self):
        collector = PerformanceMetricsCollector()
        container = Container(
            container_id="c1", image="nginx", cpu_limit=2, memory_limit=1024, storage_limit=50
        )

        for _ in range(5):
            collector.collect_container_metrics(container)

        history = collector.get_container_metric_history("c1")
        assert len(history) == 5


class TestResourceUtilizationAnalyzer:
    def test_detect_resource_pressure(self):
        analyzer = ResourceUtilizationAnalyzer()
        metrics = type("Metrics", (), {
            "cpu_utilization": 85.0,
            "memory_utilization": 45.0,
            "disk_io_bytes_per_sec": 10e3,
            "network_congestion_prob": 0.1,
        })()

        pressures = analyzer.detect_resource_pressure(metrics)
        assert "cpu" in pressures
        assert "memory" not in pressures

    def test_get_host_score(self):
        analyzer = ResourceUtilizationAnalyzer()
        host = Host(host_id="h1", total_cpu=16, total_memory=32768, total_storage=1000)
        host.metrics.cpu_utilization = 50.0
        host.metrics.memory_utilization = 50.0

        score = analyzer.get_host_score(host, {
            "cpu_weight": 0.5,
            "memory_weight": 0.3,
            "network_weight": 0.2,
        })
        assert 0 <= score <= 100


# ============================================================================
# Decision Engine Tests
# ============================================================================


class TestPolicyEngine:
    def test_add_and_evaluate_policy(self):
        engine = PolicyEngine()
        policy = Policy(
            policy_name="high_cpu",
            context=["source_cpu_utilization"],
            conditions="source_cpu_utilization > 80",
            actions=["allow_migration"],
            priority=1,
        )
        engine.add_policy(policy)
        assert len(engine._policies) == 1

    def test_evaluate_blocked_migration(self):
        engine = PolicyEngine()
        # Policy that blocks migrations when source CPU < 50 (i.e. only allow when source is busy)
        policy = Policy(
            policy_name="block_if_idle",
            context=["source_cpu_utilization"],
            conditions="source_cpu_utilization > 80",  # Only allow if CPU > 80
            actions=["allow_migration"],
            constraints={"max_concurrent_migrations": 5},
            priority=10,
        )
        engine.add_policy(policy)

        host1 = Host(host_id="src", total_cpu=16, total_memory=32768, total_storage=1000)
        host2 = Host(host_id="dst", total_cpu=16, total_memory=32768, total_storage=1000)
        host1.metrics.cpu_utilization = 30.0  # CPU is 30% - below 80 threshold

        request = MigrationRequest(container_id="c1", source_host=host1, destination_host=host2)
        allowed, reasons = engine.evaluate(request, {"active_migrations": 0})
        assert allowed is False, f"Expected blocked, got reasons: {reasons}"


class TestResourceOptimizer:
    def test_find_optimal_destination(self):
        optimizer = ResourceOptimizer()
        optimizer.hosts = {
            "h1": Host(host_id="h1", total_cpu=16, total_memory=32768, total_storage=1000),
            "h2": Host(host_id="h2", total_cpu=16, total_memory=32768, total_storage=1000),
            "h3": Host(host_id="h3", total_cpu=8, total_memory=16384, total_storage=500),
        }
        container = Container(
            container_id="c1", image="nginx", cpu_limit=2, memory_limit=1024, storage_limit=50,
            host="h1",
        )

        dest = optimizer.find_optimal_destination(container)
        assert dest is not None
        assert dest.host_id != "h1"

    def test_no_candidates(self):
        optimizer = ResourceOptimizer()
        optimizer.hosts = {
            "h1": Host(host_id="h1", total_cpu=16, total_memory=32768, total_storage=1000),
        }
        container = Container(
            container_id="c1", image="nginx", cpu_limit=2, memory_limit=1024, storage_limit=50,
            host="h1",
        )

        dest = optimizer.find_optimal_destination(container)
        assert dest is None


class TestMigrationPlanner:
    def test_plan_live_migration(self):
        planner = MigrationPlanner()
        host1 = Host(host_id="src", total_cpu=16, total_memory=32768, total_storage=1000)
        host2 = Host(host_id="dst", total_cpu=16, total_memory=32768, total_storage=1000)
        request = MigrationRequest(
            container_id="c1", source_host=host1, destination_host=host2,
            migration_type=MigrationStrategy.LIVE_MIGRATION,
        )
        plan = planner.create_plan(request)
        assert plan.strategy == MigrationStrategy.LIVE_MIGRATION
        assert plan.pre_copy_rounds == 3
        assert plan.estimated_total_time_ms > 0

    def test_plan_cold_migration(self):
        planner = MigrationPlanner()
        host1 = Host(host_id="src", total_cpu=16, total_memory=32768, total_storage=1000)
        host2 = Host(host_id="dst", total_cpu=16, total_memory=32768, total_storage=1000)
        request = MigrationRequest(
            container_id="c1", source_host=host1, destination_host=host2,
            migration_type=MigrationStrategy.COLD_MIGRATION,
        )
        plan = planner.create_plan(request)
        assert plan.strategy == MigrationStrategy.COLD_MIGRATION
        assert plan.pre_copy_rounds == 0


class TestWorkloadAnalyzer:
    def test_classify_workload(self):
        analyzer = WorkloadAnalyzer()
        container = Container(
            container_id="c1", image="nginx", cpu_limit=2, memory_limit=1024, storage_limit=50
        )
        container.metrics.cpu_utilization = 80.0
        container.metrics.memory_utilization = 30.0
        assert analyzer.classify_workload(container) == "cpu_bound"

        container.metrics.cpu_utilization = 30.0
        container.metrics.memory_utilization = 80.0
        assert analyzer.classify_workload(container) == "memory_bound"


# ============================================================================
# Container Manager Tests
# ============================================================================


class TestRuntimeController:
    def test_start_and_stop_container(self):
        controller = RuntimeController()
        host = Host(host_id="h1", total_cpu=16, total_memory=32768, total_storage=1000)
        container = Container(
            container_id="c1", image="nginx", cpu_limit=2, memory_limit=1024, storage_limit=50
        )

        assert controller.start_container(container, host) is True
        assert container.status == "running"
        assert controller.is_container_running("c1") is True

        assert controller.stop_container(container) is True
        assert container.status == "stopped"
        assert controller.is_container_running("c1") is False

    def test_pause_unpause(self):
        controller = RuntimeController()
        host = Host(host_id="h1", total_cpu=16, total_memory=32768, total_storage=1000)
        container = Container(
            container_id="c1", image="nginx", cpu_limit=2, memory_limit=1024, storage_limit=50
        )
        controller.start_container(container, host)

        assert controller.pause_container(container) is True
        assert container.status == "paused"
        assert controller.unpause_container(container) is True
        assert container.status == "running"


class TestNestedContainerManager:
    def test_create_nested(self):
        manager = NestedContainerManager()
        parent = Container(
            container_id="parent", image="ubuntu", cpu_limit=4, memory_limit=4096, storage_limit=100
        )
        nested = manager.create_nested_container(parent, "alpine:latest")
        assert nested is not None
        assert nested.container_id.startswith("nested-")
        assert len(parent.nested_containers) == 1


class TestImageManager:
    def test_pull_and_cache(self):
        manager = ImageManager()
        assert manager.pull_image("nginx:latest", "host-1") is True
        assert manager.is_image_cached("nginx:latest", "host-1") is True
        assert manager.is_image_cached("nginx:latest", "host-2") is False


# ============================================================================
# Network Manager Tests
# ============================================================================


class TestSDNControllerInterface:
    def test_connect(self):
        sdn = SDNControllerInterface("localhost:9999")
        result = sdn.connect()
        # Should simulate connection (test environment has no SDN controller)
        assert result is True

    def test_flow_rules(self):
        sdn = SDNControllerInterface()
        sdn.connect()
        from fleximigrate.network_manager import FlowRule
        rule = FlowRule(priority=100)
        assert sdn.add_flow_rule(rule) is True
        assert sdn.remove_flow_rule(rule.flow_id) is True
        assert sdn.remove_flow_rule("nonexistent") is False


class TestDNSManager:
    def test_register_and_resolve(self):
        dns = DNSManager()
        container = Container(
            container_id="web-1", image="nginx", cpu_limit=2, memory_limit=1024, storage_limit=50
        )
        host = Host(host_id="h1", total_cpu=16, total_memory=32768, total_storage=1000,
                     ip_address="10.0.0.1")

        dns.register_container(container, host)
        ips = dns.resolve("web-1.fleximigrate.local")
        assert ips == ["10.0.0.1"]

        dns.update_container_ip("web-1", "10.0.0.2")
        ips = dns.resolve("web-1.fleximigrate.local")
        assert ips == ["10.0.0.2"]


# ============================================================================
# State Synchronizer Tests
# ============================================================================


class TestCheckpointingModule:
    def test_create_checkpoint(self):
        cp = CheckpointingModule()
        container = Container(
            container_id="c1", image="nginx", cpu_limit=2, memory_limit=1024, storage_limit=50
        )

        checkpoint = cp.create_checkpoint(container)
        assert checkpoint is not None
        assert checkpoint.container_id == "c1"
        assert checkpoint.size_bytes > 0

    def test_verify_checkpoint(self):
        cp = CheckpointingModule()
        container = Container(
            container_id="c1", image="nginx", cpu_limit=2, memory_limit=1024, storage_limit=50
        )
        checkpoint = cp.create_checkpoint(container)
        assert cp.verify_checkpoint(checkpoint) is True


class TestDeltaTransfer:
    def test_compute_delta(self):
        dt = DeltaTransfer()
        container = Container(
            container_id="c1", image="nginx", cpu_limit=2, memory_limit=1024, storage_limit=50
        )
        cp_module = CheckpointingModule()
        base = cp_module.create_checkpoint(container)

        # Simulate some time passing
        time.sleep(0.01)
        new_cp = cp_module.create_checkpoint(container)

        delta = dt.compute_delta(base, new_cp)
        assert delta is not None
        assert delta.base_checkpoint_id == base.checkpoint_id
        assert delta.size_bytes >= 0


class TestStateRestorationModule:
    def test_full_restore(self):
        srm = StateRestorationModule()
        container = Container(
            container_id="c1", image="nginx", cpu_limit=2, memory_limit=1024, storage_limit=50
        )
        cp = CheckpointingModule().create_checkpoint(container)
        host = Host(host_id="dest", total_cpu=16, total_memory=32768, total_storage=1000)

        assert srm.full_restore(cp, host) is True


# ============================================================================
# State Machine Tests
# ============================================================================


class TestMigrationStateMachine:
    def test_normal_path(self):
        fsm = MigrationStateMachine()
        fsm.transition_to(MigrationStatus.PLANNING)
        fsm.transition_to(MigrationStatus.PREPARING)
        fsm.transition_to(MigrationStatus.EXECUTING)
        fsm.transition_to(MigrationStatus.VERIFYING)
        fsm.transition_to(MigrationStatus.COMPLETED)
        assert fsm.current_state == MigrationStatus.COMPLETED
        assert fsm.is_terminal()

    def test_failure_from_planning(self):
        fsm = MigrationStateMachine()
        fsm.transition_to(MigrationStatus.FAILED)
        assert fsm.current_state == MigrationStatus.FAILED

    def test_transition_history(self):
        fsm = MigrationStateMachine()
        fsm.transition_to(MigrationStatus.PLANNING)
        fsm.transition_to(MigrationStatus.FAILED)
        assert len(fsm.transition_history) == 2


# ============================================================================
# Integration Tests
# ============================================================================


class TestFlexiMigrateIntegration:
    def test_framework_initialization(self):
        flexi = FlexiMigrate()
        assert flexi.get_status()["hosts"] == 0
        assert flexi.get_status()["containers"] == 0

    def test_add_hosts(self, flexi):
        h1 = Host(host_id="h1", total_cpu=16, total_memory=32768, total_storage=1000)
        h2 = Host(host_id="h2", total_cpu=16, total_memory=32768, total_storage=1000)
        flexi.add_host(h1)
        flexi.add_host(h2)
        assert flexi.get_status()["hosts"] == 2

    def test_add_and_list_containers(self, flexi):
        h1 = Host(host_id="h1", total_cpu=16, total_memory=32768, total_storage=1000)
        flexi.add_host(h1)
        c1 = Container(container_id="web", image="nginx", cpu_limit=2, memory_limit=1024, storage_limit=20)
        flexi.add_container(c1, host_id="h1")
        assert flexi.get_status()["containers"] == 1

        containers = flexi.list_containers()
        assert len(containers) == 1
        assert containers[0]["id"] == "web"
        assert containers[0]["host"] == "h1"

    def test_successful_migration(self, flexi):
        """Test a full migration lifecycle."""
        h1 = Host(host_id="h1", total_cpu=16, total_memory=32768, total_storage=1000)
        h2 = Host(host_id="h2", total_cpu=16, total_memory=32768, total_storage=1000)
        flexi.add_host(h1)
        flexi.add_host(h2)
        c1 = Container(container_id="web", image="nginx", cpu_limit=2, memory_limit=1024, storage_limit=20)
        flexi.add_container(c1, host_id="h1")

        status = flexi.request_and_execute(
            container_id="web",
            destination_host_id="h2",
            migration_type=MigrationStrategy.LIVE_MIGRATION,
        )
        assert status == MigrationStatus.COMPLETED, f"Migration failed with status: {status}"

    def test_cluster_balancing(self, flexi):
        """Test cluster load balancing."""
        h1 = Host(host_id="h1", total_cpu=16, total_memory=32768, total_storage=1000)
        h2 = Host(host_id="h2", total_cpu=16, total_memory=32768, total_storage=1000)
        flexi.add_host(h1)
        flexi.add_host(h2)

        # Add multiple containers to h1
        for i in range(3):
            c = Container(
                container_id=f"c{i}", image="nginx", cpu_limit=2, memory_limit=1024, storage_limit=20
            )
            flexi.add_container(c, host_id="h1")

        # Simulate high load on h1
        h1.metrics.cpu_utilization = 85.0
        h2.metrics.cpu_utilization = 25.0

        requests = flexi.balance_cluster()
        # Should recommend migrations
        assert len(requests) > 0

    def test_policy_blocked_migration(self, flexi):
        """Test that policies can block migrations."""
        h1 = Host(host_id="h1", total_cpu=16, total_memory=32768, total_storage=1000)
        h2 = Host(host_id="h2", total_cpu=16, total_memory=32768, total_storage=1000)
        flexi.add_host(h1)
        flexi.add_host(h2)
        c1 = Container(container_id="web", image="nginx", cpu_limit=2, memory_limit=1024, storage_limit=20)
        flexi.add_container(c1, host_id="h1")

        # Add a restrictive policy
        policy = Policy(
            policy_name="deny_all",
            context=[],
            conditions="False",  # Always false = always deny
            actions=["block"],
            priority=100,
        )
        flexi.policy_engine.add_policy(policy)

        status = flexi.request_and_execute(
            container_id="web",
            destination_host_id="h2",
        )
        assert status == MigrationStatus.FAILED

    def test_get_status(self, flexi):
        """Test status reporting."""
        h1 = Host(host_id="h1", total_cpu=16, total_memory=32768, total_storage=1000)
        flexi.add_host(h1)
        c1 = Container(container_id="web", image="nginx", cpu_limit=2, memory_limit=1024, storage_limit=20)
        flexi.add_container(c1, host_id="h1")

        status = flexi.get_status()
        assert "hosts" in status
        assert "containers" in status
        assert "active_migrations" in status
        assert "checkpoints_created" in status

    def test_cancel_pending_migration(self, flexi):
        """Test cancelling a pending migration."""
        h1 = Host(host_id="h1", total_cpu=16, total_memory=32768, total_storage=1000)
        h2 = Host(host_id="h2", total_cpu=16, total_memory=32768, total_storage=1000)
        flexi.add_host(h1)
        flexi.add_host(h2)
        c1 = Container(container_id="web", image="nginx", cpu_limit=2, memory_limit=1024, storage_limit=20)
        flexi.add_container(c1, host_id="h1")

        request = flexi.request_migration(
            container_id="web",
            destination_host_id="h2",
        )
        assert request is not None

        cancelled = flexi.cancel_migration(request.request_id)
        assert cancelled is True

    def test_multiple_migrations(self, flexi):
        """Test multiple sequential migrations."""
        hosts = [
            Host(host_id=f"h{i}", total_cpu=16, total_memory=32768, total_storage=1000)
            for i in range(3)
        ]
        for h in hosts:
            flexi.add_host(h)

        containers = [
            Container(
                container_id=f"c{i}", image="nginx", cpu_limit=1, memory_limit=512, storage_limit=10
            )
            for i in range(2)
        ]
        for c in containers:
            flexi.add_container(c, host_id="h0")

        for c in containers:
            status = flexi.request_and_execute(
                container_id=c.container_id,
                destination_host_id="h1",
            )
            assert status == MigrationStatus.COMPLETED

        assert flexi.get_status()["completed_migrations"] == 2
