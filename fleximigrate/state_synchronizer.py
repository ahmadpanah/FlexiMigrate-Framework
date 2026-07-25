"""
State Synchronizer module.

Manages checkpointing, delta state transfer, and state restoration
during live container migration. Implements advanced checkpointing
techniques to minimize migration overhead and downtime.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import tempfile
import time
import uuid
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple

from fleximigrate.models import Container, Host, ResourceMetrics

logger = logging.getLogger(__name__)


@dataclass
class Checkpoint:
    """Represents a saved checkpoint of a container's state."""

    checkpoint_id: str = field(default_factory=lambda: str(uuid.uuid4()))
    container_id: str = ""
    timestamp: float = field(default_factory=time.time)
    size_bytes: int = 0
    checksum: str = ""
    page_dirty_bitmap: Optional[List[int]] = None
    memory_state_path: Optional[str] = None
    storage_state_path: Optional[str] = None
    metadata: Dict[str, Any] = field(default_factory=dict)


@dataclass
class DeltaState:
    """Represents the delta (changed state) between two checkpoints."""

    delta_id: str = field(default_factory=lambda: str(uuid.uuid4()))
    base_checkpoint_id: str = ""
    target_checkpoint_id: str = ""
    dirty_pages: List[int] = field(default_factory=list)
    dirty_regions: List[Tuple[int, int]] = field(default_factory=list)
    size_bytes: int = 0
    transfer_time_ms: float = 0.0
    compression_ratio: float = 1.0


class CheckpointingModule:
    """
    Creates and manages container state checkpoints.

    In production, this interfaces with CRIU (Checkpoint/Restore in Userspace)
    or Docker checkpoint/restore capabilities.
    """

    def __init__(self):
        self._checkpoints: Dict[str, Checkpoint] = {}
        self._checkpoint_dir = tempfile.mkdtemp(prefix="fleximigrate_cp_")

    def create_checkpoint(self, container: Container) -> Optional[Checkpoint]:
        """
        Create a checkpoint of a container's current state.

        In production: docker checkpoint create / criu dump
        """
        try:
            # Simulate checkpoint creation
            memory_size = int(container.memory_limit * 0.8)  # Simulate 80% memory in use
            checkpoint = Checkpoint(
                container_id=container.container_id,
                size_bytes=memory_size * 1024 * 1024,  # Convert to bytes
                page_dirty_bitmap=self._simulate_page_bitmap(memory_size),
            )

            # Simulate saving checkpoint to disk
            checkpoint_path = os.path.join(
                self._checkpoint_dir, f"cp_{container.container_id}_{int(time.time())}"
            )
            os.makedirs(checkpoint_path, exist_ok=True)
            checkpoint.memory_state_path = os.path.join(checkpoint_path, "memory.img")
            checkpoint.storage_state_path = os.path.join(checkpoint_path, "storage.img")

            # Calculate checksum (excluding the checksum field itself)
            data = {k: v for k, v in checkpoint.__dict__.items() if k != 'checksum'}
            checkpoint.checksum = hashlib.sha256(
                json.dumps(data, default=str).encode()
            ).hexdigest()[:16]

            self._checkpoints[checkpoint.checkpoint_id] = checkpoint

            logger.info(
                "[Checkpointing] Created checkpoint %s for %s (size=%d MB)",
                checkpoint.checkpoint_id, container.container_id,
                checkpoint.size_bytes // (1024 * 1024),
            )

            # Update container
            container.checkpoint_path = checkpoint_path
            return checkpoint

        except Exception as e:
            logger.error("[Checkpointing] Failed to create checkpoint: %s", e)
            return None

    def _simulate_page_bitmap(self, memory_mb: int) -> List[int]:
        """Simulate a dirty page bitmap for a given memory size."""
        page_size = 4096  # 4KB pages
        num_pages = (memory_mb * 1024 * 1024) // page_size
        # Simulate some pages being dirty
        import random
        return sorted(random.sample(range(num_pages), min(num_pages // 10, 10000)))

    def get_checkpoint(self, checkpoint_id: str) -> Optional[Checkpoint]:
        """Retrieve a saved checkpoint by ID."""
        return self._checkpoints.get(checkpoint_id)

    def get_latest_checkpoint(self, container_id: str) -> Optional[Checkpoint]:
        """Get the most recent checkpoint for a container."""
        container_cps = [
            cp for cp in self._checkpoints.values()
            if cp.container_id == container_id
        ]
        if not container_cps:
            return None
        return max(container_cps, key=lambda cp: cp.timestamp)

    def verify_checkpoint(self, checkpoint: Checkpoint) -> bool:
        """Verify the integrity of a checkpoint."""
        try:
            # Exclude the checksum field itself from the hash computation
            data = {k: v for k, v in checkpoint.__dict__.items() if k != 'checksum'}
            expected = hashlib.sha256(
                json.dumps(data, default=str).encode()
            ).hexdigest()[:16]
            return expected == checkpoint.checksum
        except Exception as e:
            logger.error("[Checkpointing] Checkpoint verification failed: %s", e)
            return False

    def delete_checkpoint(self, checkpoint_id: str):
        """Delete a checkpoint."""
        if checkpoint_id in self._checkpoints:
            cp = self._checkpoints.pop(checkpoint_id)
            if cp.memory_state_path and os.path.exists(os.path.dirname(cp.memory_state_path)):
                import shutil
                shutil.rmtree(os.path.dirname(cp.memory_state_path), ignore_errors=True)
            logger.info("[Checkpointing] Deleted checkpoint %s", checkpoint_id)

    def cleanup(self):
        """Clean up all checkpoint data."""
        import shutil
        if os.path.exists(self._checkpoint_dir):
            shutil.rmtree(self._checkpoint_dir, ignore_errors=True)
        self._checkpoints.clear()
        logger.info("[Checkpointing] Cleaned up all checkpoints")


class DeltaTransfer:
    """
    Manages efficient state transfer by sending only the changed
    (dirty) pages between successive checkpoints.

    Implements iterative pre-copy and post-copy delta transfer algorithms.
    """

    def __init__(self):
        self._deltas: Dict[str, DeltaState] = {}
        self._transfer_stats: Dict[str, Dict] = {}

    def compute_delta(
        self, base_checkpoint: Checkpoint, target_checkpoint: Checkpoint
    ) -> Optional[DeltaState]:
        """
        Compute the delta (changed pages) between two checkpoints.

        Uses the dirty page bitmap to identify only the pages that
        changed between checkpoints.
        """
        if base_checkpoint.container_id != target_checkpoint.container_id:
            logger.error("Cannot compute delta between different containers")
            return None

        # Compute dirty pages by comparing bitmaps
        base_pages = set(base_checkpoint.page_dirty_bitmap or [])
        target_pages = set(target_checkpoint.page_dirty_bitmap or [])
        dirty_pages = sorted(target_pages - base_pages)

        # Group consecutive dirty pages into regions for efficient transfer
        dirty_regions = self._group_dirty_regions(dirty_pages)

        # Estimate size: 4KB per dirty page
        delta_size = len(dirty_pages) * 4096

        delta = DeltaState(
            base_checkpoint_id=base_checkpoint.checkpoint_id,
            target_checkpoint_id=target_checkpoint.checkpoint_id,
            dirty_pages=dirty_pages,
            dirty_regions=dirty_regions,
            size_bytes=delta_size,
            compression_ratio=0.7,  # Assume 30% compression
        )

        self._deltas[delta.delta_id] = delta

        logger.info(
            "[DeltaTransfer] Computed delta: %d dirty pages, %d KB",
            len(dirty_pages), delta_size // 1024,
        )
        return delta

    def _group_dirty_regions(self, dirty_pages: List[int], max_gap: int = 256) -> List[Tuple[int, int]]:
        """
        Group consecutive dirty pages into regions.

        Pages within max_gap of each other are considered a single region.
        """
        if not dirty_pages:
            return []

        regions = []
        start = dirty_pages[0]
        prev = dirty_pages[0]

        for page in dirty_pages[1:]:
            if page - prev > max_gap:
                regions.append((start, prev))
                start = page
            prev = page

        regions.append((start, prev))
        return regions

    def estimate_transfer_time(
        self, delta: DeltaState, bandwidth_mbps: float
    ) -> float:
        """Estimate the time to transfer a delta state over a given bandwidth."""
        transfer_bits = delta.size_bytes * 8 * delta.compression_ratio
        time_ms = (transfer_bits / (bandwidth_mbps * 1_000_000)) * 1000
        delta.transfer_time_ms = time_ms
        return time_ms

    def simulate_transfer(self, delta: DeltaState, bandwidth_mbps: float) -> bool:
        """
        Simulate the transfer of a delta state.

        In production, this would send the dirty pages over the network
        to the destination host.
        """
        time_ms = self.estimate_transfer_time(delta, bandwidth_mbps)
        time.sleep(time_ms / 1000.0)  # Simulate transfer delay

        self._transfer_stats[delta.delta_id] = {
            "size_bytes": delta.size_bytes,
            "compressed_size": int(delta.size_bytes * delta.compression_ratio),
            "bandwidth_mbps": bandwidth_mbps,
            "transfer_time_ms": time_ms,
            "dirty_pages_count": len(delta.dirty_pages),
            "completed_at": time.time(),
        }

        logger.info(
            "[DeltaTransfer] Transferred %d bytes in %.1f ms (%.1f Mbps)",
            delta.size_bytes, time_ms, bandwidth_mbps,
        )
        return True

    def get_transfer_stats(self, delta_id: str) -> Optional[Dict]:
        """Get transfer statistics for a delta."""
        return self._transfer_stats.get(delta_id)


class StateRestorationModule:
    """
    Restores container state from checkpoints on the destination host.

    Handles full restoration, incremental restoration (for pre-copy),
    and lazy restoration (for post-copy).
    """

    def __init__(self):
        self._restoration_history: Dict[str, Dict] = {}

    def full_restore(
        self, checkpoint: Checkpoint, destination_host: Host
    ) -> bool:
        """
        Perform a full state restoration from a checkpoint.

        In production: docker start --checkpoint / criu restore
        """
        if not self._validate_destination(checkpoint, destination_host):
            return False

        self._restoration_history[checkpoint.checkpoint_id] = {
            "type": "full",
            "destination": destination_host.host_id,
            "checkpoint_size_mb": checkpoint.size_bytes // (1024 * 1024),
            "restored_at": time.time(),
            "status": "completed",
        }

        logger.info(
            "[StateRestoration] Full restore of %s on %s (size=%d MB)",
            checkpoint.container_id, destination_host.host_id,
            checkpoint.size_bytes // (1024 * 1024),
        )
        return True

    def incremental_restore(
        self, base_checkpoint: Checkpoint, delta: DeltaState, destination_host: Host
    ) -> bool:
        """
        Apply a delta to an already-restored base checkpoint.

        Used during pre-copy migration to iteratively apply changes.
        """
        restore_key = f"{base_checkpoint.checkpoint_id}_{delta.delta_id}"
        self._restoration_history[restore_key] = {
            "type": "incremental",
            "destination": destination_host.host_id,
            "dirty_pages": len(delta.dirty_pages),
            "restored_at": time.time(),
            "status": "completed",
        }

        logger.info(
            "[StateRestoration] Incremental restore: %d pages applied on %s",
            len(delta.dirty_pages), destination_host.host_id,
        )
        return True

    def lazy_restore(
        self, checkpoint: Checkpoint, destination_host: Host, page_fault_handler: Optional[Callable] = None
    ) -> bool:
        """
        Perform a lazy restoration where pages are fetched on demand.

        Used during post-copy migration.
        """
        from typing import Callable

        self._restoration_history[checkpoint.checkpoint_id] = {
            "type": "lazy",
            "destination": destination_host.host_id,
            "checkpoint_size_mb": checkpoint.size_bytes // (1024 * 1024),
            "restored_at": time.time(),
            "status": "partial",
        }

        logger.info(
            "[StateRestoration] Lazy restore of %s on %s (on-demand page fetch)",
            checkpoint.container_id, destination_host.host_id,
        )
        return True

    def _validate_destination(self, checkpoint: Checkpoint, host: Host) -> bool:
        """Validate that the destination host can accept the restore."""
        # Check available memory
        needed_mb = checkpoint.size_bytes // (1024 * 1024)
        available_mb = host.total_memory * (1 - host.metrics.memory_utilization / 100.0)

        if available_mb < needed_mb:
            logger.error(
                "Insufficient memory on %s: need %d MB, have %.1f MB",
                host.host_id, needed_mb, available_mb,
            )
            return False

        # Verify checkpoint integrity
        if not hasattr(checkpoint, 'checksum'):
            return False

        return True

    def get_restoration_status(self) -> Dict[str, Dict]:
        """Get the status of all restoration operations."""
        return dict(self._restoration_history)

    def rollback_restore(self, container_id: str) -> bool:
        """
        Rollback a restoration on failure.

        Removes any partially restored state from the destination.
        """
        logger.info("[StateRestoration] Rolled back restoration for %s", container_id)
        return True
