"""
FlexiMigrate Command-Line Interface.

Provides a complete CLI for managing container migrations via the
FlexiMigrate framework. Supports interactive and scripted usage.
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
import time
from typing import Any, Dict, List, Optional

from fleximigrate import __version__
from fleximigrate.fleximigrate import FlexiMigrate
from fleximigrate.models import Container, ContainerRuntime, Host, MigrationStrategy

logger = logging.getLogger(__name__)


def create_argparser() -> argparse.ArgumentParser:
    """Create the CLI argument parser."""
    parser = argparse.ArgumentParser(
        prog="fleximigrate",
        description="FlexiMigrate: Live Container Migration Framework",
        epilog="For more information, see the documentation.",
    )

    parser.add_argument(
        "--version",
        action="version",
        version=f"FlexiMigrate v{__version__}",
    )

    parser.add_argument(
        "--verbose", "-v",
        action="store_true",
        help="Enable verbose logging",
    )

    parser.add_argument(
        "--sdn-endpoint",
        default="localhost:6633",
        help="SDN controller endpoint (default: localhost:6633)",
    )

    subparsers = parser.add_subparsers(dest="command", help="Available commands")

    # ---- host command ----
    host_parser = subparsers.add_parser("host", help="Manage hosts")
    host_sub = host_parser.add_subparsers(dest="host_action", help="Host actions")

    host_add = host_sub.add_parser("add", help="Add a new host")
    host_add.add_argument("host_id", help="Host identifier")
    host_add.add_argument("--cpu", type=int, default=16, help="Number of CPU cores")
    host_add.add_argument("--memory", type=int, default=32768, help="Memory in MB")
    host_add.add_argument("--storage", type=int, default=1000, help="Storage in GB")
    host_add.add_argument("--ip", default="127.0.0.1", help="IP address")
    host_add.add_argument("--region", default="default", help="Region")
    host_add.add_argument("--zone", default="default", help="Availability zone")

    host_rm = host_sub.add_parser("remove", help="Remove a host")
    host_rm.add_argument("host_id", help="Host identifier")

    host_list = host_sub.add_parser("list", help="List all hosts")

    # ---- container command ----
    container_parser = subparsers.add_parser("container", help="Manage containers")
    container_sub = container_parser.add_subparsers(
        dest="container_action", help="Container actions"
    )

    container_add = container_sub.add_parser("add", help="Add a new container")
    container_add.add_argument("container_id", help="Container identifier")
    container_add.add_argument("--image", default="nginx:latest", help="Container image")
    container_add.add_argument("--cpu-limit", type=float, default=2.0, help="CPU limit (cores)")
    container_add.add_argument("--memory-limit", type=int, default=2048, help="Memory limit (MB)")
    container_add.add_argument("--storage-limit", type=int, default=50, help="Storage limit (GB)")
    container_add.add_argument("--host", help="Host to run container on")
    container_add.add_argument("--runtime", choices=["docker", "containerd", "cri_o", "podman"],
                               default="docker", help="Container runtime")

    container_rm = container_sub.add_parser("remove", help="Remove a container")
    container_rm.add_argument("container_id", help="Container identifier")

    container_list = container_sub.add_parser("list", help="List all containers")

    # ---- migrate command ----
    migrate_parser = subparsers.add_parser("migrate", help="Migrate a container")
    migrate_parser.add_argument("container_id", help="Container to migrate")
    migrate_parser.add_argument("--destination", "-d", help="Destination host ID")
    migrate_parser.add_argument(
        "--strategy", "-s",
        choices=["live", "pre_copy", "post_copy", "hybrid", "cold"],
        default=None,
        help="Migration strategy",
    )
    migrate_parser.add_argument(
        "--non-blocking", "-n",
        action="store_true",
        help="Don't wait for migration to complete",
    )
    migrate_parser.add_argument("--service-type", default="standard",
                                help="Service type for policy evaluation")

    # ---- policy command ----
    policy_parser = subparsers.add_parser("policy", help="Manage migration policies")
    policy_sub = policy_parser.add_subparsers(dest="policy_action", help="Policy actions")

    policy_add = policy_sub.add_parser("add", help="Add a new policy")
    policy_add.add_argument("name", help="Policy name")
    policy_add.add_argument("--conditions", required=True, help="Policy conditions expression")
    policy_add.add_argument("--actions", nargs="+", default=["allow_migration"],
                            help="Policy actions")
    policy_add.add_argument("--priority", type=int, default=0, help="Policy priority")
    policy_add.add_argument("--max-concurrent", type=int, default=5,
                            help="Max concurrent migrations constraint")

    policy_list = policy_sub.add_parser("list", help="List all policies")
    policy_rm = policy_sub.add_parser("remove", help="Remove a policy")
    policy_rm.add_argument("name", help="Policy name")

    # ---- status command ----
    status_parser = subparsers.add_parser("status", help="Show framework status")
    status_parser.add_argument("--watch", "-w", action="store_true",
                               help="Watch status changes")

    # ---- balance command ----
    subparsers.add_parser("balance", help="Balance cluster load")

    # ---- execute command (from JSON config) ----
    execute_parser = subparsers.add_parser("execute", help="Execute from configuration file")
    execute_parser.add_argument("config_file", help="JSON configuration file path")
    execute_parser.add_argument("--scenario", "-s", default="demo",
                                help="Scenario to execute (default: demo)")

    # ---- benchmark command ----
    benchmark_parser = subparsers.add_parser("benchmark", help="Run migration benchmark")
    benchmark_parser.add_argument("--containers", type=int, default=5,
                                  help="Number of containers to migrate")
    benchmark_parser.add_argument("--iterations", type=int, default=3,
                                  help="Iterations per container")

    return parser


def _strategy_from_str(s: str) -> Optional[MigrationStrategy]:
    """Convert strategy string to enum."""
    mapping = {
        "live": MigrationStrategy.LIVE_MIGRATION,
        "pre_copy": MigrationStrategy.PRE_COPY,
        "post_copy": MigrationStrategy.POST_COPY,
        "hybrid": MigrationStrategy.HYBRID,
        "cold": MigrationStrategy.COLD_MIGRATION,
    }
    return mapping.get(s)


def cmd_host(args: argparse.Namespace, flexi: FlexiMigrate):
    """Handle host subcommands."""
    if args.host_action == "add":
        host = Host(
            host_id=args.host_id,
            total_cpu=args.cpu,
            total_memory=args.memory,
            total_storage=args.storage,
            ip_address=args.ip,
            region=args.region,
            zone=args.zone,
        )
        flexi.add_host(host)
        print(f"✓ Host '{args.host_id}' added ({args.cpu} CPUs, {args.memory} MB RAM)")

    elif args.host_action == "remove":
        try:
            flexi.remove_host(args.host_id)
            print(f"✓ Host '{args.host_id}' removed")
        except KeyError:
            print(f"✗ Host '{args.host_id}' not found", file=sys.stderr)
            sys.exit(1)

    elif args.host_action == "list" or not args.host_action:
        hosts = flexi.list_hosts()
        if not hosts:
            print("No hosts registered.")
            return
        print(f"{'ID':<20} {'CPU':<12} {'Memory':<12} {'Containers':<12} {'Active':<8}")
        print("-" * 64)
        for h in hosts:
            print(f"{h['id']:<20} {h['cpu']:<12} {h['memory']:<12} {h['containers']:<12} {h['active']!s:<8}")


def cmd_container(args: argparse.Namespace, flexi: FlexiMigrate):
    """Handle container subcommands."""
    if args.container_action == "add":
        runtime_map = {
            "docker": ContainerRuntime.DOCKER,
            "containerd": ContainerRuntime.CONTAINERD,
            "cri_o": ContainerRuntime.CRI_O,
            "podman": ContainerRuntime.PODMAN,
        }
        container = Container(
            container_id=args.container_id,
            image=args.image,
            cpu_limit=args.cpu_limit,
            memory_limit=args.memory_limit,
            storage_limit=args.storage_limit,
            runtime=runtime_map.get(args.runtime, ContainerRuntime.DOCKER),
        )
        flexi.add_container(container, host_id=args.host)
        host_info = f" on host '{args.host}'" if args.host else ""
        print(f"✓ Container '{args.container_id}' added (image={args.image}{host_info})")

    elif args.container_action == "remove":
        try:
            flexi.remove_container(args.container_id)
            print(f"✓ Container '{args.container_id}' removed")
        except KeyError:
            print(f"✗ Container '{args.container_id}' not found", file=sys.stderr)
            sys.exit(1)

    elif args.container_action == "list" or not args.container_action:
        containers = flexi.list_containers()
        if not containers:
            print("No containers registered.")
            return
        print(f"{'ID':<24} {'Image':<24} {'Host':<16} {'Status':<12} {'CPU':<8} {'Memory':<8}")
        print("-" * 92)
        for c in containers:
            print(f"{c['id']:<24} {c['image']:<24} {c['host'] or '-':<16} {c['status']:<12} {c['cpu_limit']:<8} {c['memory_mb']:<8}")


def cmd_migrate(args: argparse.Namespace, flexi: FlexiMigrate):
    """Handle migration command."""
    strategy = _strategy_from_str(args.strategy) if args.strategy else None

    print(f"🚀 Starting migration of container '{args.container_id}'...")

    status = flexi.request_and_execute(
        container_id=args.container_id,
        destination_host_id=args.destination,
        migration_type=strategy,
        blocking=not args.non_blocking,
        service_type=args.service_type,
    )

    print(f"\n{'=' * 50}")
    print(f"Migration Result: {status.value}")
    print(f"{'=' * 50}")

    # Show request details if available
    for req in flexi.list_migrations():
        if req["container_id"] == args.container_id:
            print(f"  Request ID:    {req['request_id']}")
            print(f"  Source:        {req['source_host']}")
            print(f"  Destination:   {req['destination_host']}")
            print(f"  Strategy:      {req['migration_type']}")
            print(f"  Duration:      {req['total_migration_time_ms']:.0f} ms")
            print(f"  Data Transfer: {req['data_transferred_mb']:.1f} MB")
            if req.get("error"):
                print(f"  Error:         {req['error']}")
            break


def cmd_policy(args: argparse.Namespace, flexi: FlexiMigrate):
    """Handle policy subcommands."""
    if args.policy_action == "add":
        policy = {
            'policy_name': args.name,
            'CONTEXT': ['source_cpu_utilization', 'destination_cpu_utilization'],
            'CONDITIONS': args.conditions,
            'ACTIONS': args.actions,
            'CONSTRAINTS': {'max_concurrent_migrations': args.max_concurrent},
            'PRIORITY': args.priority,
        }
        flexi.policy_engine.add_policy(__import__("fleximigrate.models").models.Policy(**policy))
        print(f"✓ Policy '{args.name}' added")

    elif args.policy_action == "remove":
        flexi.policy_engine.remove_policy(args.name)
        print(f"✓ Policy '{args.name}' removed")

    elif args.policy_action == "list" or not args.policy_action:
        print(f"{'Name':<30} {'Priority':<10} {'Active':<8} {'Conditions':<40}")
        print("-" * 88)
        for p in flexi.policy_engine._policies:
            cond = p.conditions[:37] + "..." if len(p.conditions) > 40 else p.conditions
            print(f"{p.policy_name:<30} {p.priority:<10} {p.is_active!s:<8} {cond:<40}")


def cmd_status(args: argparse.Namespace, flexi: FlexiMigrate):
    """Show framework status."""
    if args.watch:
        try:
            while True:
                _print_status(flexi)
                time.sleep(2)
        except KeyboardInterrupt:
            print("\nStopped watching.")
    else:
        _print_status(flexi)


def _print_status(flexi: FlexiMigrate):
    """Print formatted status."""
    status = flexi.get_status()
    hosts = flexi.list_hosts()
    containers = flexi.list_containers()

    print(f"\n{'=' * 60}")
    print(f"  FlexiMigrate Framework Status")
    print(f"{'=' * 60}")
    print(f"  Hosts:               {status['hosts']}")
    print(f"  Containers:          {status['containers']}")
    print(f"  Active Migrations:   {status['active_migrations']}")
    print(f"  Completed:           {status['completed_migrations']}")
    print(f"  Failed:              {status['failed_migrations']}")
    print(f"  Pending:             {status['pending_requests']}")
    print(f"  SDN Connected:       {status['sdn_connected']}")
    print(f"  Checkpoints:         {status['checkpoints_created']}")
    print(f"  DNS Records:         {status['dns_records']}")
    print(f"{'=' * 60}")

    if hosts:
        print(f"\n{'Hosts':-^60}")
        for h in hosts:
            print(f"  {h['id']}: CPU={h['cpu']}, Mem={h['memory']}, "
                  f"Containers={h['containers']}, Active={h['active']}")

    if containers:
        print(f"\n{'Containers':-^60}")
        for c in containers[:10]:  # Limit display
            print(f"  {c['id']}: {c['image']} on {c['host']} ({c['status']})")
        if len(containers) > 10:
            print(f"  ... and {len(containers) - 10} more")


def cmd_balance(args: argparse.Namespace, flexi: FlexiMigrate):
    """Balance cluster load."""
    print("⚖️  Analyzing cluster balance...")
    requests = flexi.balance_cluster()

    if not requests:
        print("✓ Cluster is balanced. No migrations needed.")
        return

    print(f"📋 {len(requests)} migration(s) recommended:")
    for req in requests:
        print(f"  • {req.container_id}: {req.source_host.host_id} -> {req.destination_host.host_id}")


def cmd_execute(args: argparse.Namespace, flexi: FlexiMigrate):
    """Execute a pre-configured scenario from a JSON file."""
    try:
        with open(args.config_file, "r") as f:
            config = json.load(f)
    except (FileNotFoundError, json.JSONDecodeError) as e:
        print(f"✗ Failed to load config: {e}", file=sys.stderr)
        sys.exit(1)

    scenario = config.get("scenarios", {}).get(args.scenario, config)
    print(f"📄 Executing scenario: {args.scenario}")

    # Add hosts
    for host_cfg in scenario.get("hosts", []):
        host = Host(**host_cfg)
        flexi.add_host(host)
        print(f"  + Host: {host.host_id}")

    # Add containers
    for cont_cfg in scenario.get("containers", []):
        container = Container(**cont_cfg)
        host_id = cont_cfg.pop("host", None)
        flexi.add_container(container, host_id=host_id)
        print(f"  + Container: {container.container_id} ({container.image})")

    # Execute migrations
    for mig_cfg in scenario.get("migrations", []):
        strategy = _strategy_from_str(mig_cfg.get("strategy", "live"))
        print(f"\n  → Migrating {mig_cfg['container_id']}...")
        status = flexi.request_and_execute(
            container_id=mig_cfg["container_id"],
            destination_host_id=mig_cfg.get("destination"),
            migration_type=strategy,
            service_type=mig_cfg.get("service_type", "standard"),
        )
        print(f"  ✓ Result: {status.value}")


def cmd_benchmark(args: argparse.Namespace, flexi: FlexiMigrate):
    """Run migration benchmarks."""
    import random
    import statistics

    print(f"🏋️  Running benchmark: {args.containers} containers x {args.iterations} iterations")

    # Ensure we have hosts and containers
    if len(flexi.hosts) < 2:
        print("✗ Need at least 2 hosts for benchmarking", file=sys.stderr)
        sys.exit(1)

    results = []

    for i in range(args.containers):
        container_id = f"bench-container-{i}"
        container = Container(
            container_id=container_id,
            image=f"bench-image-{random.choice(['web', 'db', 'cache', 'worker'])}",
            cpu_limit=random.uniform(0.5, 4.0),
            memory_limit=random.randint(256, 4096),
            storage_limit=random.randint(10, 100),
        )

        # Add to a random host
        host_ids = list(flexi.hosts.keys())
        flexi.add_container(container, host_id=random.choice(host_ids))

        for j in range(args.iterations):
            dest_id = random.choice([h for h in host_ids if h != container.host])
            start = time.time()
            status = flexi.request_and_execute(
                container_id=container_id,
                destination_host_id=dest_id,
                migration_type=random.choice(list(MigrationStrategy)),
            )
            elapsed = time.time() - start

            results.append({
                "container": container_id,
                "iteration": j + 1,
                "status": status.value,
                "duration_ms": elapsed * 1000,
            })

            print(f"  [{i + 1}/{args.containers}, iter {j + 1}] {container_id} -> {dest_id}: "
                  f"{status.value} ({elapsed:.2f}s)")

    # Summary
    durations = [r["duration_ms"] for r in results if r["status"] == "completed"]
    if durations:
        print(f"\n{'=' * 50}")
        print(f"Benchmark Summary:")
        print(f"  Total migrations:  {len(results)}")
        print(f"  Successful:        {sum(1 for r in results if r['status'] == 'completed')}")
        print(f"  Failed:            {sum(1 for r in results if r['status'] == 'failed')}")
        print(f"  Avg duration:      {statistics.mean(durations):.1f} ms")
        print(f"  Min duration:      {min(durations):.1f} ms")
        print(f"  Max duration:      {max(durations):.1f} ms")
        if len(durations) > 1:
            print(f"  Std deviation:     {statistics.stdev(durations):.1f} ms")


def main(argv: Optional[List[str]] = None):
    """Main entry point for the FlexiMigrate CLI."""
    parser = create_argparser()
    args = parser.parse_args(argv)

    # Set up logging
    log_level = logging.DEBUG if args.verbose else logging.INFO

    # Initialize FlexiMigrate framework
    flexi = FlexiMigrate(log_level=log_level, sdn_controller_endpoint=args.sdn_endpoint)

    if not args.command:
        parser.print_help()
        return

    # Route to appropriate command handler
    commands = {
        "host": cmd_host,
        "container": cmd_container,
        "migrate": cmd_migrate,
        "policy": cmd_policy,
        "status": cmd_status,
        "balance": cmd_balance,
        "execute": cmd_execute,
        "benchmark": cmd_benchmark,
    }

    handler = commands.get(args.command)
    if handler:
        handler(args, flexi)
    else:
        parser.print_help()


if __name__ == "__main__":
    main()
