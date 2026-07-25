"""
Resource Monitor module.

Collects performance metrics from hosts and containers, and analyzes
resource utilization patterns to inform migration decisions.
"""

from __future__ import annotations

import logging
import random
import statistics
import time
from collections import defaultdict, deque
from threading import Lock, Thread
from typing import Callable, Dict, List, Optional, Tuple

from fleximigrate.models import Container, Host, ResourceMetrics

logger = logging.getLogger(__name__)


class PerformanceMetricsCollector:
    """
    Collects real-time performance metrics from hosts and containers.

    In a production deployment, this would interface with monitoring tools
    like Prometheus, cAdvisor, or the Docker API. This implementation
    provides a simulation layer with realistic metric generation.
    """

    def __init__(self, collection_interval_sec: float = 5.0):
        self._interval = collection_interval_sec
        self._collectors: Dict[str, Callable] = {}
        self._running = False
        self._thread: Optional[Thread] = None
        self._lock = Lock()
        self._host_metrics: Dict[str, deque] = defaultdict(
            lambda: deque(maxlen=100)  # Keep last 100 samples
        )
        self._container_metrics: Dict[str, deque] = defaultdict(
            lambda: deque(maxlen=100)
        )

    def register_host_metric_source(self, host_id: str, collector_fn: Callable):
        """Register a custom metric collector for a host."""
        self._collectors[f"host_{host_id}"] = collector_fn

    def register_container_metric_source(self, container_id: str, collector_fn: Callable):
        """Register a custom metric collector for a container."""
        self._collectors[f"container_{container_id}"] = collector_fn

    def collect_host_metrics(self, host: Host) -> ResourceMetrics:
        """
        Collect current resource metrics for a host.

        Uses registered collectors if available, otherwise simulates metrics.
        """
        collector_key = f"host_{host.host_id}"
        if collector_key in self._collectors:
            try:
                metrics = self._collectors[collector_key](host)
                self._host_metrics[host.host_id].append(metrics)
                return metrics
            except Exception as e:
                logger.warning("Host metric collector failed for %s: %s", host.host_id, e)

        # Simulate realistic metrics based on host capacity and load
        base_cpu = random.uniform(10, 40)
        cpu_spike = random.uniform(-5, 5) if random.random() > 0.1 else random.uniform(15, 40)
        metrics = ResourceMetrics(
            cpu_utilization=max(0, min(100, base_cpu + cpu_spike + host.metrics.cpu_utilization * 0.1)),
            memory_utilization=max(0, min(100, random.uniform(30, 75))),
            memory_used_mb=host.total_memory * random.uniform(0.3, 0.75),
            disk_io_bytes_per_sec=random.uniform(10e3, 100e6),
            network_bandwidth_mbps=random.uniform(10, 1000),
            network_latency_ms=random.uniform(0.5, 5),
            network_congestion_prob=random.uniform(0, 0.3),
            temperature_celsius=random.uniform(35, 75),
            power_consumption_watts=random.uniform(50, 300),
        )
        host.metrics = metrics
        self._host_metrics[host.host_id].append(metrics)
        return metrics

    def collect_container_metrics(self, container: Container) -> ResourceMetrics:
        """
        Collect current resource metrics for a container.
        """
        collector_key = f"container_{container.container_id}"
        if collector_key in self._collectors:
            try:
                metrics = self._collectors[collector_key](container)
                self._container_metrics[container.container_id].append(metrics)
                return metrics
            except Exception as e:
                logger.warning("Container metric collector failed for %s: %s",
                               container.container_id, e)

        # Simulate container-level metrics
        metrics = ResourceMetrics(
            cpu_utilization=max(0, min(100, random.uniform(5, 60))),
            memory_utilization=max(0, min(100, random.uniform(10, 80))),
            memory_used_mb=container.memory_limit * random.uniform(0.1, 0.8),
            disk_io_bytes_per_sec=random.uniform(1e3, 10e6),
            network_bandwidth_mbps=random.uniform(1, 100),
            network_latency_ms=random.uniform(0.5, 3),
            network_congestion_prob=random.uniform(0, 0.2),
        )
        container.metrics = metrics
        self._container_metrics[container.container_id].append(metrics)
        return metrics

    def get_host_metric_history(self, host_id: str) -> List[ResourceMetrics]:
        """Get the metric history for a host."""
        return list(self._host_metrics.get(host_id, []))

    def get_container_metric_history(self, container_id: str) -> List[ResourceMetrics]:
        """Get the metric history for a container."""
        return list(self._container_metrics.get(container_id, []))

    def start_background_collection(
        self, hosts: Dict[str, Host], containers: Dict[str, Container]
    ):
        """Start a background thread for periodic metric collection."""
        if self._running:
            return
        self._running = True

        def _collect_loop():
            while self._running:
                try:
                    for host in hosts.values():
                        self.collect_host_metrics(host)
                    for container in containers.values():
                        self.collect_container_metrics(container)
                except Exception as e:
                    logger.error("Background collection error: %s", e)
                time.sleep(self._interval)

        self._thread = Thread(target=_collect_loop, daemon=True, name="metric-collector")
        self._thread.start()
        logger.info("Background metric collection started (interval=%ss)", self._interval)

    def stop_background_collection(self):
        """Stop the background metric collection thread."""
        self._running = False
        if self._thread:
            self._thread.join(timeout=10)
            logger.info("Background metric collection stopped")


class ResourceUtilizationAnalyzer:
    """
    Analyzes resource utilization patterns to detect trends and anomalies.

    Provides insights used by the Decision Engine to determine when and
    where migrations should occur.
    """

    def __init__(self, history_window: int = 10):
        self._window = history_window

    def calculate_cpu_trend(self, metrics_history: List[ResourceMetrics]) -> float:
        """
        Calculate the CPU utilization trend (positive = increasing load).
        Returns slope over the history window.
        """
        if len(metrics_history) < 2:
            return 0.0
        recent = metrics_history[-self._window:]
        values = [m.cpu_utilization for m in recent]
        return self._linear_trend(values)

    def calculate_memory_trend(self, metrics_history: List[ResourceMetrics]) -> float:
        """Calculate the memory utilization trend."""
        if len(metrics_history) < 2:
            return 0.0
        recent = metrics_history[-self._window:]
        values = [m.memory_utilization for m in recent]
        return self._linear_trend(values)

    def detect_cpu_spike(self, metrics_history: List[ResourceMetrics], threshold: float = 15.0) -> bool:
        """Detect if there's a sudden CPU spike."""
        if len(metrics_history) < 3:
            return False
        recent = metrics_history[-3:]
        values = [m.cpu_utilization for m in recent]
        # Check if the latest value is significantly higher than the mean
        if len(values) >= 2:
            mean = statistics.mean(values[:-1])
            return abs(values[-1] - mean) > threshold
        return False

    def detect_resource_pressure(self, metrics: ResourceMetrics, thresholds: Optional[Dict] = None) -> List[str]:
        """
        Detect if any resource is under pressure.

        Returns a list of resource names that exceed their thresholds.
        """
        if thresholds is None:
            thresholds = {
                "cpu": 80.0,
                "memory": 80.0,
                "disk_io": 50e6,
                "network_congestion": 0.5,
            }

        pressures = []
        if metrics.cpu_utilization > thresholds["cpu"]:
            pressures.append("cpu")
        if metrics.memory_utilization > thresholds["memory"]:
            pressures.append("memory")
        if metrics.disk_io_bytes_per_sec > thresholds["disk_io"]:
            pressures.append("disk_io")
        if metrics.network_congestion_prob > thresholds["network_congestion"]:
            pressures.append("network")
        return pressures

    def estimate_migration_impact(
        self, source_metrics: ResourceMetrics, dest_metrics: ResourceMetrics
    ) -> Dict[str, float]:
        """
        Estimate the impact of moving a workload from source to destination.

        Returns a dict with estimated improvements/declines in key metrics.
        """
        return {
            "cpu_improvement": source_metrics.cpu_utilization - dest_metrics.cpu_utilization,
            "memory_improvement": source_metrics.memory_utilization - dest_metrics.memory_utilization,
            "network_congestion_reduction": source_metrics.network_congestion_prob - dest_metrics.network_congestion_prob,
        }

    def get_host_score(self, host: Host, workload_requirements: Dict[str, float]) -> float:
        """
        Score a host's suitability for a given workload.

        Higher score = more suitable. Considers available resources and current load.
        """
        cpu_score = max(0, 1 - (host.metrics.cpu_utilization / 100.0))
        memory_score = max(0, 1 - (host.metrics.memory_utilization / 100.0))
        network_score = max(0, 1 - host.metrics.network_congestion_prob)

        # Weight scores based on workload requirements
        weights = {
            "cpu_weight": workload_requirements.get("cpu_weight", 0.4),
            "memory_weight": workload_requirements.get("memory_weight", 0.4),
            "network_weight": workload_requirements.get("network_weight", 0.2),
        }

        score = (
            cpu_score * weights["cpu_weight"]
            + memory_score * weights["memory_weight"]
            + network_score * weights["network_weight"]
        ) * 100.0

        return score

    @staticmethod
    def _linear_trend(values: List[float]) -> float:
        """Calculate linear trend (slope) of a series using linear regression."""
        n = len(values)
        if n < 2:
            return 0.0
        x_avg = (n - 1) / 2.0
        y_avg = statistics.mean(values)

        numerator = sum((i - x_avg) * (v - y_avg) for i, v in enumerate(values))
        denominator = sum((i - x_avg) ** 2 for i in range(n))

        if denominator == 0:
            return 0.0
        return numerator / denominator
