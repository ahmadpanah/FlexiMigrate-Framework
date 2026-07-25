"""Tests for core data models."""

import time
import pytest
from fleximigrate.models import (
    Container,
    ContainerRuntime,
    Host,
    MigrationRequest,
    MigrationStatus,
    MigrationStrategy,
    Policy,
    ResourceMetrics,
    MigrationPlan,
)


class TestResourceMetrics:
    def test_default_values(self):
        metrics = ResourceMetrics()
        assert metrics.cpu_utilization == 0.0
        assert metrics.memory_utilization == 0.0
        assert metrics.network_congestion_prob == 0.0
        assert metrics.timestamp > 0

    def test_to_dict(self):
        metrics = ResourceMetrics(
            cpu_utilization=50.0,
            memory_utilization=60.0,
            network_bandwidth_mbps=100.0,
        )
        d = metrics.to_dict()
        assert d["cpu_utilization"] == 50.0
        assert d["memory_utilization"] == 60.0
        assert d["network_bandwidth_mbps"] == 100.0


class TestHost:
    def test_create_host(self):
        host = Host(
            host_id="test-host",
            total_cpu=16,
            total_memory=32768,
            total_storage=1000,
            ip_address="10.0.0.1",
        )
        assert host.host_id == "test-host"
        assert host.total_cpu == 16
        assert host.total_memory == 32768
        assert host.is_active is True

    def test_cpu_available(self):
        host = Host(host_id="h1", total_cpu=16, total_memory=32768, total_storage=1000)
        host.metrics.cpu_utilization = 50.0
        assert host.cpu_available == 8.0  # 50% of 16

    def test_memory_available_mb(self):
        host = Host(host_id="h1", total_cpu=16, total_memory=32768, total_storage=1000)
        host.metrics.memory_utilization = 25.0
        assert host.memory_available_mb == 24576.0  # 75% of 32768


class TestContainer:
    def test_create_container(self):
        container = Container(
            container_id="c1",
            image="nginx:latest",
            cpu_limit=2.0,
            memory_limit=1024,
            storage_limit=50,
        )
        assert container.container_id == "c1"
        assert container.image == "nginx:latest"
        assert container.runtime == ContainerRuntime.DOCKER
        assert container.status == "running"

    def test_nested_containers(self):
        parent = Container(
            container_id="parent", image="ubuntu", cpu_limit=4, memory_limit=4096, storage_limit=100
        )
        nested = Container(
            container_id="nested-1", image="alpine", cpu_limit=0.5, memory_limit=256, storage_limit=10
        )
        parent.nested_containers.append(nested)
        assert len(parent.nested_containers) == 1
        assert parent.nested_containers[0].container_id == "nested-1"


class TestMigrationRequest:
    def test_create_request(self):
        host1 = Host(host_id="src", total_cpu=16, total_memory=32768, total_storage=1000)
        host2 = Host(host_id="dst", total_cpu=16, total_memory=32768, total_storage=1000)
        request = MigrationRequest(
            container_id="c1",
            source_host=host1,
            destination_host=host2,
        )
        assert request.status == MigrationStatus.PENDING
        assert request.migration_type == MigrationStrategy.LIVE_MIGRATION
        assert request.request_id is not None
        assert request.created_at > 0

    def test_to_dict(self):
        host1 = Host(host_id="src", total_cpu=16, total_memory=32768, total_storage=1000)
        host2 = Host(host_id="dst", total_cpu=16, total_memory=32768, total_storage=1000)
        request = MigrationRequest(
            container_id="c1",
            source_host=host1,
            destination_host=host2,
            migration_type=MigrationStrategy.PRE_COPY,
        )
        d = request.to_dict()
        assert d["container_id"] == "c1"
        assert d["source_host"] == "src"
        assert d["destination_host"] == "dst"
        assert d["migration_type"] == "pre_copy"
        assert d["status"] == "pending"

    def test_duration_seconds(self):
        host1 = Host(host_id="src", total_cpu=16, total_memory=32768, total_storage=1000)
        host2 = Host(host_id="dst", total_cpu=16, total_memory=32768, total_storage=1000)
        request = MigrationRequest(
            container_id="c1",
            source_host=host1,
            destination_host=host2,
        )
        assert request.duration_seconds is None
        request.started_at = 100.0
        request.completed_at = 115.0
        assert request.duration_seconds == 15.0


class TestPolicy:
    def test_create_policy(self):
        policy = Policy(
            policy_name="test_policy",
            context=["cpu", "memory"],
            conditions="cpu > 80",
            actions=["allow"],
            constraints={"max_migrations": 5},
            priority=1,
        )
        assert policy.policy_name == "test_policy"
        assert policy.is_active is True
        assert policy.priority == 1


class TestMigrationPlan:
    def test_create_plan(self):
        host1 = Host(host_id="src", total_cpu=16, total_memory=32768, total_storage=1000)
        host2 = Host(host_id="dst", total_cpu=16, total_memory=32768, total_storage=1000)
        request = MigrationRequest(
            container_id="c1",
            source_host=host1,
            destination_host=host2,
        )
        plan = MigrationPlan(request=request, strategy=MigrationStrategy.LIVE_MIGRATION)
        assert plan.request == request
        assert plan.strategy == MigrationStrategy.LIVE_MIGRATION
        assert plan.pre_copy_rounds == 3
        assert "health_check" in plan.verification_checks
