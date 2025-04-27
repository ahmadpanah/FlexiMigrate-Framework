# FlexiMigrate Framework

FlexiMigrate is a sophisticated framework designed to facilitate efficient and seamless live container migration across diverse cloud and edge computing environments. It addresses the limitations of existing migration schemes by introducing a modular, orchestrator-agnostic architecture, enhanced state management, adaptive migration strategies, and robust network orchestration.

## Overview

This implementation closely follows the architecture and design principles outlined in the research paper "FlexiMigrate: Enhancing Live Container Migration in Heterogeneous Computing Environments" ensuring fidelity to the proposed structure and functionalities.

## Component Architecture

Below is a high-level diagram representing the architecture of the FlexiMigrate framework, showcasing the interactions between various components:

- Migration Manager
  - Migration Coordinator
  - Policy Enforcer
  - Resource Allocator
  - Network Orchestrator
  - State Management Interface
  - Logging & Monitoring
  - Migration Strategy Selector
- Resource Monitor
  - Performance Metrics Collector
  - Resource Utilization Analyzer
- Decision Engine
  - Workload Analyzer
  - Resource Optimizer
  - Migration Planner
- Container Manager
  - Runtime Controller
  - Nested Container Manager
  - Image Manager
- Network Manager
  - SDN Controller Interface
  - DNS Manager
  - Traffic Redirector
- State Synchronizer
  - Checkpointing Module
  - Delta Transfer
  - State Restoration Module

Each component is meticulously implemented to fulfill its designated role within the framework, ensuring seamless integration and efficient operation during container migrations.

## Running the Framework

To run the FlexiMigrate framework, follow these steps:

1. **Install Required Dependencies**:
   ```
   pip install elasticsearch os-ken docker prometheus_client bsdiff4 networkx grpcio tensorflow
   ```

2. **Configure Container Runtimes and SDN Controllers**:
   - Docker: Ensure Docker is installed and running on all host machines.
   - SDN Controllers: Deploy and configure SDN controllers like os_ken.

3. **Define Migration Policies**:
   ```python
   policies = [
     {
       'policy_name': 'adaptive_load_balancing',
       'CONTEXT': ['source_cpu_utilization', 'destination_cpu_utilization', 'time_of_day', 'network_congestion_prob', 'service_type'],
       'CONDITIONS': '(source_cpu_utilization > 80 and destination_cpu_utilization < 50) or '
                     '(time_of_day >= 18 and time_of_day <= 22 and service_type == "critical") or '
                     '(network_congestion_prob < 0.2)',
       'ACTIONS': ['allow_migration', 'set_priority("high")', 'trigger_load_balancer_reconfiguration'],
       'CONSTRAINTS': {'max_concurrent_migrations': 5, 'migration_duration': 300},
       'PRIORITY': 2
     }
   ]
   ```

4. **Initialize and Run FlexiMigrate**:
   ```python
   if __name__ == '__main__':
       flexi_migrate = FlexiMigrate(policies=policies)
       flexi_migrate.run()
   ```

5. **Interact with the Framework**:
   
   - Adding Hosts and Containers:
     ```python
     host1 = Host(host_id='host1', total_cpu=16, total_memory=32768, total_storage=1000)
     host2 = Host(host_id='host2', total_cpu=16, total_memory=32768, total_storage=1000)
     flexi_migrate.migration_engine.resource_optimizer.hosts = {
         host1.host_id: host1,
         host2.host_id: host2
     }

     container1 = Container(container_id='container1', image='nginx:latest', cpu_limit=4, memory_limit=2048, storage_limit=50)
     container1.host = 'host1'
     host1.containers.append(container1)
     flexi_migrate.migration_engine.resource_optimizer.containers = {
         container1.container_id: container1
     }
     ```

   - Requesting Migrations:
     ```python
     migration_request = MigrationRequest(
         container_id='container1',
         source_host=flexi_migrate.resource_optimizer.hosts['host1'],
         destination_host=flexi_migrate.resource_optimizer.hosts['host2'],
         migration_type=MigrationStrategy.LIVE_MIGRATION
     )
     if flexi_migrate.decision_engine.enforce_policies(migration_request):
         flexi_migrate.migration_manager.add_migration_request(migration_request)
     ```

# FlexiMigrate Migration State Machine

The FlexiMigrate framework utilizes a Finite State Machine (FSM) to manage the lifecycle of live container migration requests. This ensures a structured, traceable, and robust process, handling both successful executions and potential failures. The states and transitions are defined in Table 2 and visualized in Figure 4 of the reference paper.

This part provides example log snippets demonstrating the state transitions logged by the `Migration Manager` and other interacting components during various migration scenarios. These examples help illustrate the FSM's behavior in practice.

## Log Format

Log entries in the examples generally follow this format:

`LEVEL [ComponentName]: Message - [Optional State Transition Info]`

Where:

*   `LEVEL`: Log severity (e.g., INFO, ERROR, WARN).
*   `ComponentName`: The FlexiMigrate component logging the message (e.g., MigrationManager, StateSynchronizer, NetworkManager).
*   `Message`: Description of the event or action.
*   `[Optional State Transition Info]`: Explicit mentions like `Transitioning: STATE_A -> STATE_B` or `Final State: STATE_C` are included to highlight FSM changes.

---

## Migration Log Scenarios



### Scenario 1: Successful Live Migration [Link](https://github.com/ahmadpanah/FlexiMigrate-Framework/blob/master/Migration-State-Machine-Log-Examples/Successful-Live-Migration.log).

**Explanation:** This demonstrates the ideal workflow where a migration request is received, planned, prepared, executed, verified, and completed without errors. This follows the path 1N -> 2N -> 3N -> 4N -> 5N -> 6N in Figure 4.

### Scenario 2: Failure in Pending State [Link](https://github.com/ahmadpanah/FlexiMigrate-Framework/blob/master/Migration-State-Machine-Log-Examples/Failure-During-Initialization.log).

**Explanation**: The migration request is received (State: PENDING - 1N), but an immediate error occurs during initial validation or setup before transitioning to the PLANNING state. This could be due to issues like an invalid request format, inability to find the source container immediately, lack of basic permissions, or a critical internal error within the FlexiMigrate framework preventing it from processing any new requests. The FSM transitions directly to FAILED (Path: 1F), potentially triggering a minimal rollback/cleanup if any preliminary steps were taken. This is distinct from Scenario 4 (Planning Failure), which occurs after the system has started evaluating policies and potential resources.

### Scenario 3: Failure in Planning State [Link](https://github.com/ahmadpanah/FlexiMigrate-Framework/blob/master/Migration-State-Machine-Log-Examples/Migration-Fails-during-Planning.log).

**Explanation:** The migration request is received but is immediately blocked during the planning phase because it violates a predefined migration policy. The FSM transitions directly to FAILED (Path: 1N -> 2F -> 8N). No technical preparation or execution occurs.

### Scenario 4: Failure in Preparation State [Link](https://github.com/ahmadpanah/FlexiMigrate-Framework/blob/master/Migration-State-Machine-Log-Examples/Migration-Fails-during-Preparation.log).

**Explanation:** The migration proceeds past planning, but an error occurs during the preparation phase (e.g., network configuration fails). The FSM transitions to FAILED (Path: 1N -> 2N -> 3F -> 8N), and rollback procedures are initiated.

### Scenario 5: Failure in Execution State [Link](https://github.com/ahmadpanah/FlexiMigrate-Framework/blob/master/Migration-State-Machine-Log-Examples/Migration-Fails-during-Execution.log).

**Explanation:** Preparation completes, but an error occurs during the actual state transfer (e.g., network corruption). The FSM transitions to FAILED (Path: 1N -> 2N -> 3N -> 4F -> 8N), triggering rollback.


### Scenario 6: Failure in Verification State [Link](https://github.com/ahmadpanah/FlexiMigrate-Framework/blob/master/Migration-State-Machine-Log-Examples/Migration-Fails-during-Verification.log).

**Explanation:** The container state is successfully transferred and restored on the destination, but post-migration checks fail (e.g., the container is unresponsive, health checks fail, or network connectivity to dependencies is impaired). The FSM transitions to FAILED (Path: 1N -> 2N -> 3N -> 4N -> 5F -> 8N), triggering rollback to revert the container to the source host.





## Contributing

Contributions to the FlexiMigrate framework are welcome. Please feel free to submit pull requests or open issues to discuss potential improvements or report bugs.

## License

This project is licensed under the MIT License.

## Cite
```bibtex
@article{ahmadpanahfleximigrate24,
  title={FlexiMigrate: Enhancing Live Container Migration in Heterogeneous Computing Environments},
  author={Seyed Hossein Ahmadpanah and Meghdad Mirabi and Amir Sahafi and Seyed Hossein Erfani},
  journal={Cluster Computing},
  year={2025}
}
```

The article is in the reviewing process and will be available after publication.
```