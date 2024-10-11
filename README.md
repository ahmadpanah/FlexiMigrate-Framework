![FlexiMigrate](https://raw.githubusercontent.com/ahmadpanah/FlexiMigrate-Framework/refs/heads/master/logo/fleximigrate.svg?token=GHSAT0AAAAAACTLFHIIKZBR5I5U5Q2XPRO6ZYJPVYA)

# FlexiMigrate Framework

FlexiMigrate is a sophisticated framework designed to facilitate efficient and seamless live container migration across diverse cloud and edge computing environments. It addresses the limitations of existing migration schemes by introducing a modular, orchestrator-agnostic architecture, enhanced state management, adaptive migration strategies, and robust network orchestration.

## Overview

This implementation closely follows the architecture and design principles outlined in the research paper "Enhancing Cloud and Edge Computing with FlexiMigrate: A Seamless Approach to Live Container Migration," ensuring fidelity to the proposed structure and functionalities.

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


## Contributing

Contributions to the FlexiMigrate framework are welcome. Please feel free to submit pull requests or open issues to discuss potential improvements or report bugs.

## License

FlexiMigrate is released under the [MIT License](https://github.com/eli64s/readme-ai/blob/main/LICENSE).

