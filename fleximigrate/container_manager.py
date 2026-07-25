"""
Container Manager module.

Handles container runtime operations including starting, stopping,
checkpointing, restoring containers, managing nested containers,
and image lifecycle management.
"""

from __future__ import annotations

import logging
import os
import shutil
import tempfile
import time
import uuid
from typing import Dict, List, Optional

from fleximigrate.models import Container, ContainerRuntime, Host

logger = logging.getLogger(__name__)


class RuntimeController:
    """
    Controls container runtime operations.

    In production, this interfaces with Docker, containerd, or CRI-O APIs.
    This implementation provides a simulation layer with realistic behavior.
    """

    def __init__(self):
        self._running_containers: Dict[str, Dict] = {}
        self._checkpoint_dir = tempfile.mkdtemp(prefix="fleximigrate_cp_")

    def start_container(
        self, container: Container, host: Host
    ) -> bool:
        """
        Start a container on a given host.

        In production: docker run / ctr run / podman run
        """
        container.host = host.host_id
        container.status = "running"
        self._running_containers[container.container_id] = {
            "host": host.host_id,
            "started_at": time.time(),
            "pid": 12345,  # Simulated PID
        }
        logger.info(
            "[RuntimeController] Started container %s (image=%s) on host %s",
            container.container_id, container.image, host.host_id,
        )
        return True

    def stop_container(self, container: Container) -> bool:
        """
        Stop a running container.

        In production: docker stop / ctr task kill
        """
        if container.container_id not in self._running_containers:
            logger.warning("[RuntimeController] Container %s is not running", container.container_id)
            return False

        container.status = "stopped"
        del self._running_containers[container.container_id]
        logger.info(
            "[RuntimeController] Stopped container %s", container.container_id
        )
        return True

    def pause_container(self, container: Container) -> bool:
        """
        Pause a running container (freeze its processes).

        In production: docker pause / ctr task pause
        """
        if container.status != "running":
            return False
        container.status = "paused"
        logger.info(
            "[RuntimeController] Paused container %s", container.container_id
        )
        return True

    def unpause_container(self, container: Container) -> bool:
        """
        Unpause a paused container.

        In production: docker unpause / ctr task resume
        """
        if container.status != "paused":
            return False
        container.status = "running"
        logger.info(
            "[RuntimeController] Unpaused container %s", container.container_id
        )
        return True

    def get_container_pid(self, container_id: str) -> Optional[int]:
        """Get the PID of a running container (simulated)."""
        info = self._running_containers.get(container_id)
        return info.get("pid") if info else None

    def is_container_running(self, container_id: str) -> bool:
        """Check if a container is currently running."""
        return container_id in self._running_containers

    def execute_in_container(self, container_id: str, command: List[str]) -> Dict:
        """
        Execute a command inside a running container.

        Returns simulated output.
        """
        if container_id not in self._running_containers:
            return {"success": False, "error": "Container not running"}

        logger.info(
            "[RuntimeController] Exec in container %s: %s",
            container_id, " ".join(command),
        )
        return {
            "success": True,
            "exit_code": 0,
            "stdout": f"Executed: {' '.join(command)}",
            "stderr": "",
        }

    def cleanup(self):
        """Clean up temporary checkpoint directory."""
        if os.path.exists(self._checkpoint_dir):
            shutil.rmtree(self._checkpoint_dir, ignore_errors=True)


class NestedContainerManager:
    """
    Manages nested container architecture.

    Nested containers allow application containers to be decoupled from
    the orchestration platform, enabling cross-platform migration.
    """

    def __init__(self):
        self._nested_relationships: Dict[str, str] = {}  # nested_id -> parent_id

    def create_nested_container(
        self,
        parent: Container,
        image: str,
        cpu_limit: float = 0.5,
        memory_limit: int = 256,
    ) -> Optional[Container]:
        """
        Create a nested container inside a parent container.

        In production, this uses Docker-in-Docker or similar technologies.
        """
        nested = Container(
            container_id=f"nested-{uuid.uuid4().hex[:8]}",
            image=image,
            cpu_limit=cpu_limit,
            memory_limit=memory_limit,
            storage_limit=10,
            host=parent.host,
            runtime=parent.runtime,
            status="created",
        )
        parent.nested_containers.append(nested)
        self._nested_relationships[nested.container_id] = parent.container_id

        logger.info(
            "[NestedContainerManager] Created nested container %s (image=%s) inside %s",
            nested.container_id, image, parent.container_id,
        )
        return nested

    def get_parent_container(self, nested_id: str) -> Optional[str]:
        """Get the parent container ID for a nested container."""
        return self._nested_relationships.get(nested_id)

    def get_nested_containers(self, parent_id: str) -> List[str]:
        """Get all nested container IDs for a parent."""
        return [
            cid for cid, pid in self._nested_relationships.items()
            if pid == parent_id
        ]

    def evacuate_nested_containers(self, parent: Container, new_parent: Container) -> bool:
        """
        Move all nested containers from one parent to another.
        Used during migration when the parent container moves.
        """
        moved = []
        for nested in list(parent.nested_containers):
            nested.host = new_parent.host
            self._nested_relationships[nested.container_id] = new_parent.container_id
            new_parent.nested_containers.append(nested)
            moved.append(nested.container_id)

        parent.nested_containers.clear()

        if moved:
            logger.info(
                "[NestedContainerManager] Evacuated %d nested containers to %s",
                len(moved), new_parent.container_id,
            )
        return True


class ImageManager:
    """
    Manages container images across the cluster.

    Handles image pulling, caching, versioning, and layer management
    to optimize migration performance.
    """

    def __init__(self):
        self._local_images: Dict[str, Dict[str, bool]] = {}  # host_id -> {image_name: is_cached}
        self._image_layers: Dict[str, List[str]] = {}  # image_name -> list of layer hashes
        self._layer_cache: Dict[str, bool] = {}  # layer_hash -> is_cached_globally

    def pull_image(self, image: str, host_id: str) -> bool:
        """
        Pull a container image to a host.

        In production: docker pull / ctr image pull
        """
        if host_id not in self._local_images:
            self._local_images[host_id] = {}

        self._local_images[host_id][image] = True
        # Simulate layer caching
        self._image_layers.setdefault(image, [f"layer_{i}" for i in range(3)])
        for layer in self._image_layers[image]:
            self._layer_cache[layer] = True

        logger.info("[ImageManager] Pulled image %s on host %s", image, host_id)
        return True

    def is_image_cached(self, image: str, host_id: str) -> bool:
        """Check if an image is already cached on a host."""
        return self._local_images.get(host_id, {}).get(image, False)

    def get_shared_layers(self, image1: str, image2: str) -> List[str]:
        """Get common layers between two images."""
        layers1 = set(self._image_layers.get(image1, []))
        layers2 = set(self._image_layers.get(image2, []))
        return list(layers1 & layers2)

    def estimate_image_transfer_size(
        self, image: str, target_host_id: str
    ) -> int:
        """
        Estimate the size (in MB) of image data that needs to be transferred.

        Accounts for layers already cached on the target host.
        """
        if self.is_image_cached(image, target_host_id):
            return 0  # Already cached

        # Estimate 100 MB per unique layer, assume 3 layers per image
        return 300

    def optimize_for_migration(
        self, image: str, source_host: str, target_host: str
    ) -> Dict:
        """
        Optimize image transfer for migration by determining
        delta layers and transfer strategy.
        """
        if self.is_image_cached(image, target_host):
            return {"needs_transfer": False, "transfer_size_mb": 0, "strategy": "noop"}

        if self.is_image_cached(image, source_host):
            # Need to transfer from source
            layers = self._image_layers.get(image, [])
            cached_on_target = sum(
                1 for l in layers if self._layer_cache.get(l, False)
            )
            total_layers = len(layers)
            if cached_on_target > 0:
                return {
                    "needs_transfer": True,
                    "transfer_size_mb": (total_layers - cached_on_target) * 100,
                    "strategy": "delta",
                    "layers_to_transfer": total_layers - cached_on_target,
                }

        return {
            "needs_transfer": True,
            "transfer_size_mb": 300,
            "strategy": "full",
            "layers_to_transfer": 3,
        }
