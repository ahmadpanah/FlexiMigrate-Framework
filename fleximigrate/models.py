"""
Core data models for the FlexiMigrate framework.

Defines the fundamental entities used throughout the migration lifecycle:
Hosts, Containers, Migration Requests, Policies, and Metrics.
"""

from __future__ import annotations

import enum
import time
import uuid
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional


class MigrationStrategy(enum.Enum):
    """Supported migration strategies."""

    LIVE_MIGRATION = "live_migration"
    PRE_COPY = "pre_copy"
    POST_COPY = "post_copy"
    HYBRID = "hybrid"
    COLD_MIGRATION = "cold_migration"


class MigrationStatus(enum.Enum):
    """Possible states for a migration request in its lifecycle."""

    PENDING = "pending"
    PLANNING = "planning"
    PREPARING = "preparing"
    EXECUTING = "executing"
    VERIFYING = "verifying"
    COMPLETED = "completed"
    FAILED = "failed"
    ROLLED_BACK = "rolled_back"
    CANCELLED = "cancelled"


class ContainerRuntime(enum.Enum):
    """Supported container runtimes."""

    DOCKER = "docker"
    CONTAINERD = "containerd"
    CRI_O = "cri_o"
    PODMAN = "podman"


@dataclass
class ResourceMetrics:
    """Resource utilization metrics for a host or container."""

    cpu_utilization: float = 0.0  # Percentage (0-100)
    memory_utilization: float = 0.0  # Percentage (0-100)
    memory_used_mb: float = 0.0
    disk_io_bytes_per_sec: float = 0.0
    network_bandwidth_mbps: float = 0.0
    network_latency_ms: float = 0.0
    network_congestion_prob: float = 0.0  # 0.0 to 1.0
    temperature_celsius: float = 0.0
    power_consumption_watts: float = 0.0
    timestamp: float = field(default_factory=time.time)

    def to_dict(self) -> Dict[str, float]:
        return {
            "cpu_utilization": self.cpu_utilization,
            "memory_utilization": self.memory_utilization,
            "memory_used_mb": self.memory_used_mb,
            "disk_io_bytes_per_sec": self.disk_io_bytes_per_sec,
            "network_bandwidth_mbps": self.network_bandwidth_mbps,
            "network_latency_ms": self.network_latency_ms,
            "network_congestion_prob": self.network_congestion_prob,
            "temperature_celsius": self.temperature_celsius,
            "power_consumption_watts": self.power_consumption_watts,
        }


@dataclass
class Host:
    """Represents a physical or virtual host machine in the cluster."""

    host_id: str
    total_cpu: int  # Number of cores
    total_memory: int  # MB
    total_storage: int  # GB
    cpu_architecture: str = "x86_64"
    os_type: str = "linux"
    ip_address: str = "127.0.0.1"
    port: int = 2375
    region: str = "default"
    zone: str = "default"
    containers: List[Container] = field(default_factory=list)
    metrics: ResourceMetrics = field(default_factory=ResourceMetrics)
    labels: Dict[str, str] = field(default_factory=dict)
    is_active: bool = True

    @property
    def cpu_available(self) -> float:
        """Available CPU cores based on utilization."""
        return self.total_cpu * (1 - self.metrics.cpu_utilization / 100.0)

    @property
    def memory_available_mb(self) -> float:
        """Available memory in MB based on utilization."""
        return self.total_memory * (1 - self.metrics.memory_utilization / 100.0)


@dataclass
class Container:
    """Represents a container running on a host."""

    container_id: str
    image: str
    cpu_limit: float  # Number of CPU cores
    memory_limit: int  # MB
    storage_limit: int  # GB
    host: Optional[str] = None  # host_id of the host running this container
    runtime: ContainerRuntime = ContainerRuntime.DOCKER
    status: str = "running"
    labels: Dict[str, str] = field(default_factory=dict)
    environment: Dict[str, str] = field(default_factory=dict)
    ports: List[int] = field(default_factory=list)
    volumes: List[str] = field(default_factory=list)
    metrics: ResourceMetrics = field(default_factory=ResourceMetrics)
    metadata: Dict[str, Any] = field(default_factory=dict)
    created_at: float = field(default_factory=time.time)
    checkpoint_path: Optional[str] = None
    nested_containers: List[Container] = field(default_factory=list)


@dataclass
class Policy:
    """
    Migration policy defining conditions and actions.

    Policies are evaluated by the Decision Engine to determine whether
    a migration should proceed and how it should be prioritized.
    """

    policy_name: str
    context: List[str]
    conditions: str  # Expression string evaluated against context
    actions: List[str]
    constraints: Dict[str, Any] = field(default_factory=dict)
    priority: int = 0  # Higher = higher priority
    is_active: bool = True


@dataclass
class MigrationRequest:
    """
    Represents a request to migrate a container from one host to another.

    Tracks the full lifecycle including timing, strategy, and final outcome.
    """

    container_id: str
    source_host: Host
    destination_host: Host
    migration_type: MigrationStrategy = MigrationStrategy.LIVE_MIGRATION
    request_id: str = field(default_factory=lambda: str(uuid.uuid4()))
    status: MigrationStatus = MigrationStatus.PENDING
    created_at: float = field(default_factory=time.time)
    started_at: Optional[float] = None
    completed_at: Optional[float] = None
    estimated_downtime_ms: float = 0.0
    total_migration_time_ms: float = 0.0
    data_transferred_mb: float = 0.0
    bandwidth_used_mbps: float = 0.0
    priority: int = 0
    error: Optional[str] = None
    metadata: Dict[str, Any] = field(default_factory=dict)

    @property
    def duration_seconds(self) -> Optional[float]:
        """Total duration of the migration in seconds."""
        if self.started_at and self.completed_at:
            return self.completed_at - self.started_at
        return None

    def to_dict(self) -> Dict[str, Any]:
        return {
            "request_id": self.request_id,
            "container_id": self.container_id,
            "source_host": self.source_host.host_id,
            "destination_host": self.destination_host.host_id,
            "migration_type": self.migration_type.value,
            "status": self.status.value,
            "created_at": self.created_at,
            "started_at": self.started_at,
            "completed_at": self.completed_at,
            "estimated_downtime_ms": self.estimated_downtime_ms,
            "total_migration_time_ms": self.total_migration_time_ms,
            "data_transferred_mb": self.data_transferred_mb,
            "bandwidth_used_mbps": self.bandwidth_used_mbps,
            "error": self.error,
        }


@dataclass
class MigrationPlan:
    """Detailed plan for a migration, produced by the Decision Engine."""

    request: MigrationRequest
    strategy: MigrationStrategy
    checkpoint_interval_sec: int = 5
    max_downtime_ms: float = 100.0
    bandwidth_limit_mbps: float = 1000.0
    pre_copy_rounds: int = 3
    verification_checks: List[str] = field(default_factory=lambda: [
        "container_running",
        "network_connectivity",
        "data_integrity",
        "health_check",
    ])
    rollback_steps: List[str] = field(default_factory=list)
    estimated_total_time_ms: float = 0.0
    estimated_downtime_ms: float = 0.0
    resource_reservation: Dict[str, float] = field(default_factory=dict)
