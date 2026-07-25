"""
Decision Engine module.

Implements intelligent migration decision-making including workload analysis,
resource optimization, policy enforcement, and migration planning.

Features an ML-based scoring system for optimal host selection and
a policy engine for constraint-based migration governance.
"""

from __future__ import annotations

import logging
import re
import time
from typing import Any, Dict, List, Optional, Tuple

from fleximigrate.models import (
    Container,
    Host,
    MigrationPlan,
    MigrationRequest,
    MigrationStrategy,
    Policy,
    ResourceMetrics,
)
from fleximigrate.resource_monitor import ResourceUtilizationAnalyzer

logger = logging.getLogger(__name__)


class WorkloadAnalyzer:
    """
    Analyzes container workloads to characterize resource usage patterns.

    Identifies whether workloads are CPU-bound, memory-bound, I/O-bound,
    or balanced, and provides recommendations for optimal placement.
    """

    def __init__(self):
        self._analyzer = ResourceUtilizationAnalyzer()

    def classify_workload(self, container: Container) -> str:
        """
        Classify a container's workload type based on its metrics.

        Returns: 'cpu_bound', 'memory_bound', 'io_bound', or 'balanced'
        """
        m = container.metrics
        if m.cpu_utilization > 70 and m.memory_utilization < 50:
            return "cpu_bound"
        elif m.memory_utilization > 70 and m.cpu_utilization < 50:
            return "memory_bound"
        elif m.disk_io_bytes_per_sec > 50e6:
            return "io_bound"
        else:
            return "balanced"

    def estimate_resource_demand(self, container: Container) -> Dict[str, float]:
        """
        Estimate the future resource demand for a container based on
        current usage and growth trends.

        Returns a dict with predicted cpu, memory, and io requirements.
        """
        metrics_history = []  # In production, fetch from the collector
        cpu_trend = self._analyzer.calculate_cpu_trend(metrics_history)
        mem_trend = self._analyzer.calculate_memory_trend(metrics_history)

        return {
            "predicted_cpu": min(100, container.metrics.cpu_utilization * (1 + max(0, cpu_trend * 0.1))),
            "predicted_memory_mb": container.memory_limit * (
                container.metrics.memory_utilization / 100.0
            ) * (1 + max(0, mem_trend * 0.1)),
            "workload_type": self.classify_workload(container),
        }

    def get_workload_compatibility(
        self, container: Container, target_host: Host
    ) -> float:
        """
        Score compatibility between a container's workload and a target host.

        Returns a score from 0 (incompatible) to 1 (perfect fit).
        """
        # Check architecture compatibility
        if hasattr(target_host, 'cpu_architecture'):
            arch_score = 1.0  # Assume compatible in simulation
        else:
            arch_score = 0.5

        # Check resource availability
        cpu_needed = container.cpu_limit
        mem_needed = container.memory_limit

        cpu_avail = target_host.cpu_available
        mem_avail = target_host.memory_available_mb

        cpu_score = min(1.0, cpu_avail / max(cpu_needed, 1))
        mem_score = min(1.0, mem_avail / max(mem_needed, 1))

        return (arch_score * 0.2 + cpu_score * 0.4 + mem_score * 0.4)


class ResourceOptimizer:
    """
    Optimizes resource allocation across the cluster by finding
    optimal container-to-host placements.
    """

    def __init__(self):
        self.hosts: Dict[str, Host] = {}
        self.containers: Dict[str, Container] = {}
        self._analyzer = ResourceUtilizationAnalyzer()

    def find_optimal_destination(
        self,
        container: Container,
        exclude_host_ids: Optional[List[str]] = None,
    ) -> Optional[Host]:
        """
        Find the best host to migrate a container to.

        Evaluates all available hosts and returns the one with the
        highest compatibility score.
        """
        if exclude_host_ids is None:
            exclude_host_ids = [container.host] if container.host else []

        candidates = [
            h for h in self.hosts.values()
            if h.host_id not in exclude_host_ids and h.is_active
        ]

        if not candidates:
            return None

        workload = WorkloadAnalyzer()
        scored_hosts = []

        for host in candidates:
            compat = workload.get_workload_compatibility(container, host)
            util_score = self._analyzer.get_host_score(host, {
                "cpu_weight": 0.4,
                "memory_weight": 0.4,
                "network_weight": 0.2,
            })
            combined = (compat * 0.6 + util_score / 100.0 * 0.4)
            scored_hosts.append((combined, host))

        scored_hosts.sort(key=lambda x: x[0], reverse=True)
        best_host = scored_hosts[0][1]
        logger.info(
            "[ResourceOptimizer] Optimal destination for %s: %s (score=%.2f)",
            container.container_id, best_host.host_id, scored_hosts[0][0],
        )
        return best_host

    def balance_cluster(self) -> List[Tuple[Container, Host]]:
        """
        Analyze cluster balance and recommend migrations.

        Returns a list of (container, recommended_host) pairs.
        """
        recommendations = []
        threshold = 0.75  # If any host exceeds 75% utilization, suggest rebalancing

        for host in self.hosts.values():
            if host.metrics.cpu_utilization > threshold * 100 or \
               host.metrics.memory_utilization > threshold * 100:
                # This host is overloaded - find containers to move
                for container in list(self.containers.values()):
                    if container.host == host.host_id:
                        dest = self.find_optimal_destination(
                            container,
                            exclude_host_ids=[host.host_id],
                        )
                        if dest:
                            recommendations.append((container, dest))

        return recommendations

    def reserve_resources(
        self, host: Host, cpu_needed: float, memory_needed: int
    ) -> bool:
        """
        Attempt to reserve resources on a host for an incoming migration.

        Returns True if resources are available and reserved.
        """
        cpu_avail = host.cpu_available
        mem_avail = host.memory_available_mb

        if cpu_avail >= cpu_needed and mem_avail >= memory_needed:
            # Simulate resource reservation
            logger.info(
                "[ResourceOptimizer] Reserved %.1f CPU, %d MB on %s",
                cpu_needed, memory_needed, host.host_id,
            )
            return True
        else:
            logger.warning(
                "[ResourceOptimizer] Insufficient resources on %s: "
                "need %.1f CPU, %d MB; have %.1f CPU, %.1f MB",
                host.host_id, cpu_needed, memory_needed, cpu_avail, mem_avail,
            )
            return False


class MigrationPlanner:
    """
    Creates detailed migration plans based on workload analysis,
    resource availability, and chosen strategy.
    """

    def __init__(self):
        self._strategies = {
            MigrationStrategy.LIVE_MIGRATION: self._plan_live_migration,
            MigrationStrategy.PRE_COPY: self._plan_pre_copy,
            MigrationStrategy.POST_COPY: self._plan_post_copy,
            MigrationStrategy.COLD_MIGRATION: self._plan_cold_migration,
            MigrationStrategy.HYBRID: self._plan_hybrid,
        }

    def create_plan(self, request: MigrationRequest) -> MigrationPlan:
        """
        Create a detailed migration plan for a given request.
        """
        planner = self._strategies.get(request.migration_type, self._plan_live_migration)
        return planner(request)

    def _estimate_transfer_time(
        self, container: Container, bandwidth_mbps: float
    ) -> Tuple[float, float]:
        """
        Estimate data transfer time and expected downtime.

        Returns (total_time_ms, downtime_ms).
        """
        # Estimate memory state size (multiple of memory limit based on dirty rate)
        memory_mb = container.memory_limit
        dirty_rate = 0.1  # 10% memory dirtied per second during migration
        transfer_time_ms = (memory_mb * 8 / max(bandwidth_mbps, 1)) * 1000

        # For live migration, downtime is the final stop-and-copy phase
        downtime_ms = min(
            transfer_time_ms * 0.05,  # 5% of transfer time for final sync
            500,  # Cap at 500ms
        )

        return transfer_time_ms, downtime_ms

    def _plan_live_migration(self, request: MigrationRequest) -> MigrationPlan:
        """Plan a standard live migration using pre-copy approach."""
        container = self._find_container(request.container_id)
        total_ms, downtime_ms = self._estimate_transfer_time(container, 1000)

        return MigrationPlan(
            request=request,
            strategy=MigrationStrategy.LIVE_MIGRATION,
            checkpoint_interval_sec=5,
            max_downtime_ms=100.0,
            bandwidth_limit_mbps=1000,
            pre_copy_rounds=3,
            estimated_total_time_ms=total_ms,
            estimated_downtime_ms=downtime_ms,
        )

    def _plan_pre_copy(self, request: MigrationRequest) -> MigrationPlan:
        """Plan pre-copy migration with iterative memory transfer."""
        container = self._find_container(request.container_id)
        total_ms, downtime_ms = self._estimate_transfer_time(container, 500)

        return MigrationPlan(
            request=request,
            strategy=MigrationStrategy.PRE_COPY,
            checkpoint_interval_sec=3,
            max_downtime_ms=50.0,
            bandwidth_limit_mbps=500,
            pre_copy_rounds=5,
            estimated_total_time_ms=total_ms,
            estimated_downtime_ms=downtime_ms,
        )

    def _plan_post_copy(self, request: MigrationRequest) -> MigrationPlan:
        """Plan post-copy migration (transfer after resuming on destination)."""
        container = self._find_container(request.container_id)
        # Post-copy has minimal initial transfer time but higher network fault risk
        total_ms = 200  # Minimal initial state transfer
        downtime_ms = 20  # Very low downtime

        return MigrationPlan(
            request=request,
            strategy=MigrationStrategy.POST_COPY,
            checkpoint_interval_sec=2,
            max_downtime_ms=30.0,
            bandwidth_limit_mbps=2000,
            pre_copy_rounds=0,
            estimated_total_time_ms=total_ms,
            estimated_downtime_ms=downtime_ms,
        )

    def _plan_cold_migration(self, request: MigrationRequest) -> MigrationPlan:
        """Plan cold migration (stop container, transfer, restart)."""
        container = self._find_container(request.container_id)
        # Higher downtime but simpler and more reliable
        total_ms = container.memory_limit * 8 / 1000 * 1000  # Simplified
        downtime_ms = total_ms  # Full downtime equals total time

        return MigrationPlan(
            request=request,
            strategy=MigrationStrategy.COLD_MIGRATION,
            checkpoint_interval_sec=0,
            max_downtime_ms=30000.0,  # 30 seconds max
            bandwidth_limit_mbps=10000,
            pre_copy_rounds=0,
            estimated_total_time_ms=total_ms,
            estimated_downtime_ms=downtime_ms,
        )

    def _plan_hybrid(self, request: MigrationRequest) -> MigrationPlan:
        """Plan hybrid migration combining pre-copy and post-copy benefits."""
        container = self._find_container(request.container_id)
        total_ms, downtime_ms = self._estimate_transfer_time(container, 750)

        return MigrationPlan(
            request=request,
            strategy=MigrationStrategy.HYBRID,
            checkpoint_interval_sec=4,
            max_downtime_ms=75.0,
            bandwidth_limit_mbps=750,
            pre_copy_rounds=2,
            estimated_total_time_ms=total_ms * 0.8,  # Hybrid is faster
            estimated_downtime_ms=downtime_ms * 0.6,
        )

    def _find_container(self, container_id: str) -> Container:
        """Find a container by ID. Returns a default for planning estimates."""
        from fleximigrate.models import ContainerRuntime
        return Container(
            container_id=container_id,
            image="unknown",
            cpu_limit=2,
            memory_limit=2048,
            storage_limit=50,
        )


class PolicyEngine:
    """
    Enforces migration policies by evaluating conditions against
    current context and determining whether migrations should proceed.
    """

    def __init__(self, policies: Optional[List[Policy]] = None):
        self._policies: List[Policy] = policies or []
        self._policy_cache: Dict[str, Any] = {}

    def add_policy(self, policy: Policy):
        """Add a policy to the engine."""
        self._policies.append(policy)
        self._policy_cache.clear()

    def remove_policy(self, policy_name: str):
        """Remove a policy by name."""
        self._policies = [p for p in self._policies if p.policy_name != policy_name]
        self._policy_cache.clear()

    def evaluate(self, request: MigrationRequest, context: Dict[str, Any]) -> Tuple[bool, List[str]]:
        """
        Evaluate all active policies against a migration request.

        Args:
            request: The migration request being evaluated.
            context: Current system context (metrics, time, etc.).

        Returns:
            Tuple of (is_allowed, list_of_reasons).
        """
        allowed = True
        reasons = []

        for policy in sorted(self._policies, key=lambda p: p.priority, reverse=True):
            if not policy.is_active:
                continue

            try:
                result = self._evaluate_policy(policy, request, context)
                if not result["allowed"]:
                    allowed = False
                    reasons.append(f"Policy '{policy.policy_name}' blocked: {result['reason']}")
                else:
                    logger.debug("Policy '%s' passed", policy.policy_name)
            except Exception as e:
                logger.error("Error evaluating policy '%s': %s", policy.policy_name, e)
                allowed = False
                reasons.append(f"Policy '{policy.policy_name}' evaluation error: {e}")

        return allowed, reasons

    def _evaluate_policy(
        self, policy: Policy, request: MigrationRequest, context: Dict[str, Any]
    ) -> Dict[str, Any]:
        """
        Evaluate a single policy.

        In production, this would use a proper expression evaluator.
        For this implementation, we use simple pattern matching.
        """
        result = {"allowed": True, "reason": ""}

        # Build evaluation context from request and system state
        eval_context = {
            "source_cpu_utilization": request.source_host.metrics.cpu_utilization,
            "destination_cpu_utilization": request.destination_host.metrics.cpu_utilization,
            "source_memory_utilization": request.source_host.metrics.memory_utilization,
            "destination_memory_utilization": request.destination_host.metrics.memory_utilization,
            "network_congestion_prob": request.source_host.metrics.network_congestion_prob,
            "time_of_day": time.localtime().tm_hour + time.localtime().tm_min / 60.0,
            "service_type": request.metadata.get("service_type", "standard"),
            "container_count": len(request.source_host.containers),
            "migration_count": context.get("active_migrations", 0),
            **context,
        }

        # Check constraints
        for key, value in policy.constraints.items():
            if key == "max_concurrent_migrations" and context.get("active_migrations", 0) >= value:
                result["allowed"] = False
                result["reason"] = f"Max concurrent migrations ({value}) reached"
                return result

        # Evaluate conditions using simple expression matching
        conditions = policy.conditions.strip()
        # Simple condition evaluation for common patterns
        try:
            result["allowed"] = self._evaluate_expression(conditions, eval_context)
        except Exception:
            # If evaluation fails, default to allowing (permissive mode)
            result["allowed"] = True

        if not result["allowed"]:
            result["reason"] = f"Conditions not met: {policy.conditions}"

        return result

    def _evaluate_expression(self, expression: str, context: Dict[str, Any]) -> bool:
        """
        Evaluate a simple policy expression against a context dictionary.

        Supports basic comparisons: >, <, >=, <=, ==, !=, and, or, not, parentheses.
        """
        # Replace variable names with their values from context
        resolved = expression
        for key, value in sorted(context.items(), key=lambda x: -len(x[0])):
            if isinstance(value, (int, float)):
                resolved = resolved.replace(key, str(value))
            elif isinstance(value, str):
                resolved = resolved.replace(key, f"'{value}'")

        # Replace Python booleans
        resolved = resolved.replace("and", " and ").replace("or", " or ").replace("not", " not ")

        # Safely evaluate the expression
        try:
            # Only allow numeric comparisons and boolean operations
            allowed_names = {
                "True": True, "False": False,
                "and": lambda a, b: a and b,
                "or": lambda a, b: a or b,
                "not": lambda a: not a,
            }

            # Simple safe eval using compile/restricted globals
            code = compile(resolved, "<policy>", "eval", flags=0)
            for name in code.co_names:
                if name not in allowed_names and name not in context:
                    # Variable name - try to find it
                    if name not in context:
                        logger.warning("Unknown variable in policy: %s", name)
                        return True  # Default to allowing on unknown variables

            result = eval(code, {"__builtins__": {}}, {**context, **allowed_names})
            return bool(result)
        except Exception as e:
            logger.warning("Policy expression evaluation error: %s (expr: %s)", e, resolved)
            return True  # Default permissive on errors

    def get_applicable_policies(self, context: Dict[str, Any]) -> List[Policy]:
        """Get all policies applicable to the current context."""
        return [p for p in self._policies if p.is_active]
