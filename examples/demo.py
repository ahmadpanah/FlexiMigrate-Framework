#!/usr/bin/env python3
"""
FlexiMigrate Demo Script.

Demonstrates the full capabilities of the FlexiMigrate framework
for live container migration in heterogeneous environments.
"""

import time
import sys
import os

# Add parent directory to path for direct execution
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from fleximigrate import FlexiMigrate
from fleximigrate.models import (
    Container,
    ContainerRuntime,
    Host,
    MigrationStrategy,
    Policy,
)


def create_sample_policies():
    """Create migration policies for the demo."""
    return [
        {
            'policy_name': 'adaptive_load_balancing',
            'CONTEXT': [
                'source_cpu_utilization',
                'destination_cpu_utilization',
                'time_of_day',
                'network_congestion_prob',
                'service_type',
            ],
            'CONDITIONS': (
                '(source_cpu_utilization > 80 and destination_cpu_utilization < 50) or '
                '(time_of_day >= 18 and time_of_day <= 22 and service_type == "critical") or '
                '(network_congestion_prob < 0.2)'
            ),
            'ACTIONS': [
                'allow_migration',
                'set_priority("high")',
                'trigger_load_balancer_reconfiguration',
            ],
            'CONSTRAINTS': {'max_concurrent_migrations': 5, 'migration_duration': 300},
            'PRIORITY': 2,
        },
        {
            'policy_name': 'critical_service_protection',
            'CONTEXT': ['service_type', 'time_of_day'],
            'CONDITIONS': (
                'service_type == "critical" and time_of_day < 6 or time_of_day > 22'
            ),
            'ACTIONS': ['block_migration', 'alert_admin'],
            'CONSTRAINTS': {},
            'PRIORITY': 10,
        },
        {
            'policy_name': 'low_load_threshold',
            'CONTEXT': ['source_cpu_utilization', 'destination_cpu_utilization'],
            'CONDITIONS': 'source_cpu_utilization < 30',
            'ACTIONS': ['defer_migration', 'set_priority("low")'],
            'CONSTRAINTS': {},
            'PRIORITY': 1,
        },
    ]


def create_infrastructure(flexi):
    """Create hosts and containers for the demo."""
    print("\n" + "=" * 60)
    print("  Creating Infrastructure")
    print("=" * 60)

    # Create hosts
    hosts = [
        Host(
            host_id="cloud-host-1",
            total_cpu=32,
            total_memory=65536,
            total_storage=2000,
            ip_address="10.0.1.10",
            region="us-east-1",
            zone="us-east-1a",
            cpu_architecture="x86_64",
        ),
        Host(
            host_id="cloud-host-2",
            total_cpu=32,
            total_memory=65536,
            total_storage=2000,
            ip_address="10.0.1.11",
            region="us-east-1",
            zone="us-east-1b",
            cpu_architecture="x86_64",
        ),
        Host(
            host_id="edge-host-1",
            total_cpu=8,
            total_memory=16384,
            total_storage=500,
            ip_address="192.168.1.10",
            region="us-east-1",
            zone="edge-1",
            cpu_architecture="arm64",
        ),
        Host(
            host_id="edge-host-2",
            total_cpu=4,
            total_memory=8192,
            total_storage=250,
            ip_address="192.168.1.11",
            region="us-east-1",
            zone="edge-2",
            cpu_architecture="arm64",
        ),
    ]

    for host in hosts:
        flexi.add_host(host)
        print(f"  + Host '{host.host_id}': {host.total_cpu} CPUs, {host.total_memory} MB RAM "
              f"({host.region}/{host.zone}, {host.cpu_architecture})")

    # Create containers
    containers = [
        Container(
            container_id="web-app-1",
            image="nginx:alpine",
            cpu_limit=2.0,
            memory_limit=1024,
            storage_limit=20,
            runtime=ContainerRuntime.DOCKER,
        ),
        Container(
            container_id="api-server-1",
            image="node:18-alpine",
            cpu_limit=4.0,
            memory_limit=2048,
            storage_limit=50,
            runtime=ContainerRuntime.DOCKER,
        ),
        Container(
            container_id="database-1",
            image="postgres:15",
            cpu_limit=4.0,
            memory_limit=4096,
            storage_limit=100,
            runtime=ContainerRuntime.DOCKER,
        ),
        Container(
            container_id="cache-1",
            image="redis:7-alpine",
            cpu_limit=1.0,
            memory_limit=512,
            storage_limit=10,
            runtime=ContainerRuntime.DOCKER,
        ),
        Container(
            container_id="worker-1",
            image="python:3.10-slim",
            cpu_limit=2.0,
            memory_limit=1024,
            storage_limit=30,
            runtime=ContainerRuntime.CONTAINERD,
        ),
    ]

    # Place containers on cloud-host-1
    for container in containers:
        flexi.add_container(container, host_id="cloud-host-1")
        print(f"  + Container '{container.container_id}': {container.image} "
              f"(CPU={container.cpu_limit}, MEM={container.memory_limit} MB) -> cloud-host-1")

    return hosts, containers


def simulate_host_load(flexi, host_id, cpu_load, mem_load):
    """Simulate resource load on a host."""
    host = flexi.get_host(host_id)
    if host:
        host.metrics.cpu_utilization = cpu_load
        host.metrics.memory_utilization = mem_load
        print(f"\n  [Simulation] {host_id} CPU={cpu_load}%, MEM={mem_load}%")


def demo_basic_migration(flexi):
    """Demonstrate a basic live migration."""
    print("\n\n" + "=" * 60)
    print("  Demo 1: Basic Live Migration")
    print("=" * 60)

    # Simulate high load on source host
    simulate_host_load(flexi, "cloud-host-1", 85.0, 72.0)
    simulate_host_load(flexi, "cloud-host-2", 35.0, 40.0)

    print("\n  📋 Migration: web-app-1 -> cloud-host-2")

    status = flexi.request_and_execute(
        container_id="web-app-1",
        destination_host_id="cloud-host-2",
        migration_type=MigrationStrategy.LIVE_MIGRATION,
        service_type="web",
    )

    print(f"\n  ✅ Migration result: {status.value}")
    return status


def demo_policy_enforcement(flexi):
    """Demonstrate policy-based migration control."""
    print("\n\n" + "=" * 60)
    print("  Demo 2: Policy Enforcement")
    print("=" * 60)

    # Try a migration that violates a policy
    simulate_host_load(flexi, "edge-host-1", 25.0, 30.0)

    print("\n  📋 Trying to migrate database-1 -> edge-host-1 (should be blocked by policy)")
    print("     Reason: database service is 'critical', edge host has limited resources")

    status = flexi.request_and_execute(
        container_id="database-1",
        destination_host_id="edge-host-1",
        migration_type=MigrationStrategy.LIVE_MIGRATION,
        service_type="critical",
    )

    print(f"\n  ✅ Policy enforcement result: {status.value}")

    # Now try a non-critical container
    print("\n  📋 Trying to migrate cache-1 -> edge-host-1 (should be allowed)")
    status = flexi.request_and_execute(
        container_id="cache-1",
        destination_host_id="edge-host-1",
        migration_type=MigrationStrategy.LIVE_MIGRATION,
        service_type="cache",
    )

    print(f"\n  ✅ Policy enforcement result: {status.value}")
    return status


def demo_strategy_selection(flexi):
    """Demonstrate automatic strategy selection."""
    print("\n\n" + "=" * 60)
    print("  Demo 3: Automatic Strategy Selection")
    print("=" * 60)

    # Different workloads get different strategies
    containers_to_migrate = [
        ("web-app-1", "standard", "CPU-bound workload"),
        ("api-server-1", "api", "Memory-intensive workload"),
        ("worker-1", "batch", "I/O-bound workload"),
    ]

    for container_id, service_type, desc in containers_to_migrate:
        container = flexi.get_container(container_id)
        source = flexi.get_host(container.host) if container and container.host else None
        dest = flexi.get_host("edge-host-2")

        if source and dest:
            strategy = flexi.decision_engine.get_strategy(container, source, dest)
            print(f"\n  📋 {container_id} ({desc})")
            print(f"     Selected strategy: {strategy.value}")

    return True


def demo_workflow(flexi):
    """Demonstrate a complete migration workflow."""
    print("\n\n" + "=" * 60)
    print("  Demo 4: Complete Migration Workflow")
    print("=" * 60)

    # 1. Show initial state
    status = flexi.get_status()
    print(f"\n  📊 Initial state: {status['hosts']} hosts, {status['containers']} containers")

    # 2. Perform migrations across cluster
    migrations = [
        ("api-server-1", "cloud-host-2", "api"),
        ("database-1", "cloud-host-2", "critical"),  # Won't work due to policies
        ("worker-1", "edge-host-2", "batch"),
    ]

    for container_id, dest, svc_type in migrations:
        if flexi.get_container(container_id):
            print(f"\n  📋 Attempting: {container_id} -> {dest}")
            status = flexi.request_and_execute(
                container_id=container_id,
                destination_host_id=dest,
                service_type=svc_type,
            )
            print(f"     Result: {status.value}")

    # 3. Show final state
    print(f"\n  📊 Final cluster state:")
    for h_info in flexi.list_hosts():
        print(f"     {h_info['id']}: {h_info['containers']} containers")

    return True


def demo_detailed_report(flexi):
    """Generate and display detailed migration reports."""
    print("\n\n" + "=" * 60)
    print("  Demo 5: Migration Reports")
    print("=" * 60)

    migrations = flexi.list_migrations()
    for req in migrations:
        print(f"\n  📄 Migration Report: {req['request_id']}")
        print(f"     Container:   {req['container_id']}")
        print(f"     Source:      {req['source_host']}")
        print(f"     Destination: {req['destination_host']}")
        print(f"     Strategy:    {req['migration_type']}")
        print(f"     Status:      {req['status']}")
        print(f"     Duration:    {req['total_migration_time_ms']:.0f} ms")
        print(f"     Data:        {req['data_transferred_mb']:.1f} MB")
        if req.get("error"):
            print(f"     Error:       {req['error']}")


def main():
    """Run the FlexiMigrate demo."""
    print("=" * 60)
    print("  FlexiMigrate Framework - Demo")
    print("  Enhancing Live Container Migration in Heterogeneous Environments")
    print("=" * 60)

    # Initialize framework with policies
    policies = create_sample_policies()

    # Create FlexiMigrate instance
    flexi = FlexiMigrate(
        policies=policies,
        log_level=20,  # INFO level
        sdn_controller_endpoint="localhost:6633",
    )

    print("\n  ✓ FlexiMigrate initialized with {} policies".format(len(policies)))
    for p in policies:
        print(f"    - Policy: '{p['policy_name']}' (priority={p['PRIORITY']})")

    # Create infrastructure
    hosts, containers = create_infrastructure(flexi)

    # Run demos
    demo_basic_migration(flexi)
    demo_policy_enforcement(flexi)
    demo_strategy_selection(flexi)
    demo_workflow(flexi)
    demo_detailed_report(flexi)

    # Final summary
    print("\n\n" + "=" * 60)
    print("  Demo Complete!")
    print("=" * 60)
    final_status = flexi.get_status()
    print(f"  Hosts:       {final_status['hosts']}")
    print(f"  Containers:  {final_status['containers']}")
    print(f"  Migrations:  {final_status['completed_migrations']} completed, "
          f"{final_status['failed_migrations']} failed")
    print(f"  Active:      {final_status['active_migrations']}")
    print(f"  Checkpoints: {final_status['checkpoints_created']}")
    print("=" * 60)

    # Shutdown
    flexi.shutdown()
    print("\n  ✓ Framework shutdown complete")
    print("\n  📚 Paper Reference:")
    print("     Ahmadpanah et al. (2025)")
    print("     'FlexiMigrate: Enhancing Live Container Migration")
    print("      in Heterogeneous Computing Environments'")
    print("     Cluster Computing, 28(13), 847")
    print()


if __name__ == "__main__":
    main()
