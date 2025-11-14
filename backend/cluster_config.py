"""
Cluster Configuration for Raft-based Chat System
Defines the cluster nodes and their connection details.

SAVE THIS FILE AS: backend/cluster_config.py
"""

import os

# ----------------------------------------------------------------
# Cluster Node Configuration
# ----------------------------------------------------------------
# Each node has:
# - client_port: Port for client connections (ChatService)
# - raft_port: Port for Raft peer-to-peer communication (RaftService)
# - host: Hostname or IP address
CLUSTER_NODES = {
    "node1": {
        "host": "localhost",
        "client_port": 50051,
        "raft_port": 50061
    },
    "node2": {
        "host": "localhost",
        "client_port": 50052,
        "raft_port": 50062
    },
    "node3": {
        "host": "localhost",
        "client_port": 50053,
        "raft_port": 50063
    },
}

# ----------------------------------------------------------------
# Raft Timing Configuration (in milliseconds)
# ----------------------------------------------------------------
# Election timeout: randomized between min and max
ELECTION_TIMEOUT_MIN = 150  # ms
ELECTION_TIMEOUT_MAX = 300  # ms

# Heartbeat interval: should be much less than election timeout
HEARTBEAT_INTERVAL = 50  # ms

# RPC timeout: how long to wait for RPC responses
RPC_TIMEOUT = 100  # ms

# ----------------------------------------------------------------
# Storage Configuration
# ----------------------------------------------------------------
def get_storage_dir(node_id):
    """Get the storage directory for a specific node."""
    base_dir = os.path.join(os.path.dirname(__file__), f"{node_id}_storage")
    return base_dir

def get_raft_state_file(node_id):
    """Get the persistent state file path for a node."""
    storage_dir = get_storage_dir(node_id)
    return os.path.join(storage_dir, "raft_state.json")

def get_raft_log_file(node_id):
    """Get the log file path for a node."""
    storage_dir = get_storage_dir(node_id)
    return os.path.join(storage_dir, "raft_log.json")

# ----------------------------------------------------------------
# Cluster Utilities
# ----------------------------------------------------------------
def get_quorum_size(cluster_size=None):
    """
    Calculate the quorum size (majority) needed for consensus.
    Quorum = floor(N/2) + 1
    """
    if cluster_size is None:
        cluster_size = len(CLUSTER_NODES)
    return (cluster_size // 2) + 1

def get_all_node_ids():
    """Get a list of all node IDs in the cluster."""
    return list(CLUSTER_NODES.keys())

def get_node_config(node_id):
    """Get configuration for a specific node."""
    return CLUSTER_NODES.get(node_id)

def get_peer_node_ids(node_id):
    """Get all peer node IDs (excluding the given node)."""
    return [nid for nid in CLUSTER_NODES.keys() if nid != node_id]

def get_raft_address(node_id):
    """Get the Raft service address for a node."""
    config = CLUSTER_NODES.get(node_id)
    if config:
        return f"{config['host']}:{config['raft_port']}"
    return None

def get_client_address(node_id):
    """Get the client service address for a node."""
    config = CLUSTER_NODES.get(node_id)
    if config:
        return f"{config['host']}:{config['client_port']}"
    return None

# ----------------------------------------------------------------
# Validation
# ----------------------------------------------------------------
def validate_node_id(node_id):
    """Check if a node ID is valid."""
    if node_id not in CLUSTER_NODES:
        raise ValueError(f"Invalid node_id: {node_id}. Must be one of {list(CLUSTER_NODES.keys())}")
    return True

# ----------------------------------------------------------------
# Display Configuration
# ----------------------------------------------------------------
def print_cluster_config():
    """Print the cluster configuration for debugging."""
    print("="*60)
    print("RAFT CLUSTER CONFIGURATION")
    print("="*60)
    print(f"Cluster Size: {len(CLUSTER_NODES)}")
    print(f"Quorum Size: {get_quorum_size()}")
    print(f"Election Timeout: {ELECTION_TIMEOUT_MIN}-{ELECTION_TIMEOUT_MAX}ms")
    print(f"Heartbeat Interval: {HEARTBEAT_INTERVAL}ms")
    print("\nNodes:")
    for node_id, config in CLUSTER_NODES.items():
        print(f"  {node_id}:")
        print(f"    Client:  {config['host']}:{config['client_port']}")
        print(f"    Raft:    {config['host']}:{config['raft_port']}")
    print("="*60)

if __name__ == "__main__":
    # Test the configuration
    print_cluster_config()
    print(f"\nQuorum size for {len(CLUSTER_NODES)} nodes: {get_quorum_size()}")
    print(f"Node IDs: {get_all_node_ids()}")
    print(f"Peers of node1: {get_peer_node_ids('node1')}")