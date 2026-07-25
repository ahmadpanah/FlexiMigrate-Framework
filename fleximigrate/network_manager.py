"""
Network Manager module.

Provides Software-Defined Networking (SDN) integration for maintaining
continuous network connectivity during container migration, DNS management,
and traffic redirection.
"""

from __future__ import annotations

import logging
import socket
import time
import uuid
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional

from fleximigrate.models import Container, Host

logger = logging.getLogger(__name__)


@dataclass
class FlowRule:
    """An SDN flow rule for traffic management."""

    flow_id: str = field(default_factory=lambda: str(uuid.uuid4()))
    priority: int = 100
    match_criteria: Dict[str, Any] = field(default_factory=dict)
    actions: List[str] = field(default_factory=list)
    source_ip: Optional[str] = None
    destination_ip: Optional[str] = None
    source_port: Optional[int] = None
    destination_port: Optional[int] = None
    protocol: str = "tcp"
    is_active: bool = True
    created_at: float = field(default_factory=time.time)


@dataclass
class DNSEntry:
    """A DNS record for service discovery."""

    record_id: str = field(default_factory=lambda: str(uuid.uuid4()))
    hostname: str = ""
    ip_addresses: List[str] = field(default_factory=list)
    ttl_seconds: int = 60
    service_name: Optional[str] = None
    container_id: Optional[str] = None


class SDNControllerInterface:
    """
    Interface to Software-Defined Networking controllers.

    Manages flow rules for traffic steering during migrations.
    In production, this interfaces with OpenFlow controllers like
    os-ken, OpenDaylight, or Ryu.
    """

    def __init__(self, controller_endpoint: str = "localhost:6633"):
        self._endpoint = controller_endpoint
        self._flow_rules: Dict[str, FlowRule] = {}
        self._connected = False

    def connect(self) -> bool:
        """Connect to the SDN controller."""
        try:
            host, port = self._endpoint.split(":")
            sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            sock.settimeout(2.0)
            sock.connect((host, int(port)))
            sock.close()
            self._connected = True
            logger.info("[SDNController] Connected to controller at %s", self._endpoint)
        except (socket.timeout, ConnectionRefusedError, OSError):
            logger.warning(
                "[SDNController] Could not connect to %s (simulating controller)",
                self._endpoint,
            )
            self._connected = True  # Simulate connection for demo purposes
        return self._connected

    def disconnect(self):
        """Disconnect from the SDN controller."""
        self._connected = False
        logger.info("[SDNController] Disconnected from controller")

    def add_flow_rule(self, rule: FlowRule) -> bool:
        """Add a flow rule to the SDN controller."""
        self._flow_rules[rule.flow_id] = rule
        logger.info(
            "[SDNController] Added flow rule %s: %s -> %s",
            rule.flow_id, rule.match_criteria, rule.actions,
        )
        return True

    def remove_flow_rule(self, flow_id: str) -> bool:
        """Remove a flow rule from the SDN controller."""
        if flow_id in self._flow_rules:
            del self._flow_rules[flow_id]
            logger.info("[SDNController] Removed flow rule %s", flow_id)
            return True
        return False

    def update_flow_rule(self, flow_id: str, updates: Dict[str, Any]) -> bool:
        """Update an existing flow rule."""
        if flow_id in self._flow_rules:
            for key, value in updates.items():
                setattr(self._flow_rules[flow_id], key, value)
            logger.info("[SDNController] Updated flow rule %s", flow_id)
            return True
        return False

    def create_migration_flows(
        self, container: Container, source_host: Host, dest_host: Host
    ) -> List[str]:
        """
        Create flow rules to redirect traffic during migration.

        Returns list of flow rule IDs created.
        """
        flow_ids = []

        # Rule 1: Allow state synchronization traffic between hosts
        sync_rule = FlowRule(
            priority=200,
            match_criteria={
                "source_ip": source_host.ip_address,
                "destination_ip": dest_host.ip_address,
                "protocol": "tcp",
            },
            actions=[f"forward:{dest_host.ip_address}:{dest_host.port}"],
            source_ip=source_host.ip_address,
            destination_ip=dest_host.ip_address,
        )
        self.add_flow_rule(sync_rule)
        flow_ids.append(sync_rule.flow_id)

        # Rule 2: Buffer traffic to container during final sync
        buffer_rule = FlowRule(
            priority=150,
            match_criteria={
                "destination_ip": container.metadata.get("ip", ""),
                "protocol": "tcp",
            },
            actions=["buffer", "monitor"],
            destination_ip=container.metadata.get("ip", ""),
        )
        self.add_flow_rule(buffer_rule)
        flow_ids.append(buffer_rule.flow_id)

        # Rule 3: Redirect traffic to new host
        redirect_rule = FlowRule(
            priority=300,
            match_criteria={
                "destination_ip": container.metadata.get("ip", ""),
                "protocol": "tcp",
            },
            actions=[f"redirect:{dest_host.ip_address}"],
            destination_ip=container.metadata.get("ip", ""),
        )
        self.add_flow_rule(redirect_rule)
        flow_ids.append(redirect_rule.flow_id)

        logger.info(
            "[SDNController] Created %d migration flows for %s",
            len(flow_ids), container.container_id,
        )
        return flow_ids

    def remove_migration_flows(self, flow_ids: List[str]):
        """Remove all flow rules associated with a migration."""
        for fid in flow_ids:
            self.remove_flow_rule(fid)

    def get_active_flows(self) -> List[FlowRule]:
        """Get all active flow rules."""
        return [r for r in self._flow_rules.values() if r.is_active]


class DNSManager:
    """
    Manages DNS records for container service discovery.

    Ensures that service hostnames resolve to the correct IP addresses
    during and after migration, with minimal TTL for fast updates.
    """

    def __init__(self):
        self._records: Dict[str, DNSEntry] = {}

    def register_container(self, container: Container, host: Host, service_name: Optional[str] = None):
        """Register DNS record for a container."""
        record = DNSEntry(
            hostname=f"{container.container_id}.fleximigrate.local",
            ip_addresses=[host.ip_address],
            ttl_seconds=5,  # Low TTL for fast migration updates
            service_name=service_name or f"svc-{container.image.split(':')[0]}",
            container_id=container.container_id,
        )
        self._records[container.container_id] = record
        logger.info(
            "[DNSManager] Registered %s -> %s", record.hostname, host.ip_address
        )

    def update_container_ip(self, container_id: str, new_ip: str):
        """Update the IP address for a container's DNS record."""
        if container_id in self._records:
            old_ips = list(self._records[container_id].ip_addresses)
            self._records[container_id].ip_addresses = [new_ip]
            logger.info(
                "[DNSManager] Updated %s IP: %s -> %s",
                container_id, old_ips, new_ip,
            )

    def resolve(self, hostname: str) -> Optional[List[str]]:
        """Resolve a hostname to IP addresses."""
        for record in self._records.values():
            if record.hostname == hostname:
                return record.ip_addresses
        return None

    def unregister_container(self, container_id: str):
        """Remove DNS record for a container."""
        if container_id in self._records:
            del self._records[container_id]
            logger.info("[DNSManager] Unregistered container %s", container_id)


class TrafficRedirector:
    """
    Manages traffic redirection during live migration to minimize
    connection disruption and packet loss.
    """

    def __init__(self, sdn_controller: SDNControllerInterface):
        self._sdn = sdn_controller
        self._active_redirects: Dict[str, Dict[str, Any]] = {}
        self._connection_tracking: Dict[str, int] = {}

    def start_traffic_redirection(
        self, container: Container, source_host: Host, dest_host: Host
    ) -> List[str]:
        """
        Start redirecting traffic from source to destination host.
        """
        flow_ids = self._sdn.create_migration_flows(container, source_host, dest_host)
        self._active_redirects[container.container_id] = {
            "flow_ids": flow_ids,
            "source_host": source_host.host_id,
            "destination_host": dest_host.host_id,
            "started_at": time.time(),
        }

        logger.info(
            "[TrafficRedirector] Active: %s -> %s (flows: %s)",
            source_host.host_id, dest_host.host_id, flow_ids,
        )
        return flow_ids

    def stop_traffic_redirection(self, container_id: str):
        """Stop traffic redirection for a container."""
        if container_id in self._active_redirects:
            redirect = self._active_redirects.pop(container_id)
            self._sdn.remove_migration_flows(redirect["flow_ids"])
            logger.info("[TrafficRedirector] Stopped redirection for %s", container_id)

    def is_redirect_active(self, container_id: str) -> bool:
        """Check if traffic redirection is active for a container."""
        return container_id in self._active_redirects

    def get_redirect_info(self, container_id: str) -> Optional[Dict]:
        """Get information about an active redirect."""
        return self._active_redirects.get(container_id)

    def buffer_and_forward(self, container_id: str, packet: Any) -> bool:
        """
        Buffer packets during the final phase of migration and forward them
        to the destination once migration completes.
        """
        # In production, this would buffer TCP packets and replay them
        # on the destination to prevent connection drops
        self._connection_tracking[container_id] = self._connection_tracking.get(container_id, 0) + 1
        return True

    def get_buffered_packet_count(self, container_id: str) -> int:
        """Get the number of buffered packets for a container."""
        return self._connection_tracking.get(container_id, 0)

    def flush_buffer(self, container_id: str) -> bool:
        """Flush the packet buffer for a container."""
        if container_id in self._connection_tracking:
            count = self._connection_tracking.pop(container_id, 0)
            logger.info("[TrafficRedirector] Flushed %d buffered packets for %s", count, container_id)
        return True
