#!/bin/bash

# Raft Chat Cluster Startup Script
# Starts all 3 nodes in the cluster

echo "=========================================="
echo "Starting Raft Chat Cluster"
echo "=========================================="

# Kill any existing servers
echo "Stopping any existing servers..."
pkill -f "raft_server.py"
sleep 2

# Create logs directory
mkdir -p logs

# Start node1
echo "Starting node1..."
python backend/raft_server.py --node-id node1 > logs/node1.log 2>&1 &
NODE1_PID=$!
echo "  Node1 PID: $NODE1_PID"

# Wait a bit before starting next node
sleep 2

# Start node2
echo "Starting node2..."
python backend/raft_server.py --node-id node2 > logs/node2.log 2>&1 &
NODE2_PID=$!
echo "  Node2 PID: $NODE2_PID"

# Wait a bit before starting next node
sleep 2

# Start node3
echo "Starting node3..."
python backend/raft_server.py --node-id node3 > logs/node3.log 2>&1 &
NODE3_PID=$!
echo "  Node3 PID: $NODE3_PID"

echo ""
echo "=========================================="
echo "Cluster started successfully!"
echo "=========================================="
echo "Node1: localhost:50051 (PID: $NODE1_PID)"
echo "Node2: localhost:50052 (PID: $NODE2_PID)"
echo "Node3: localhost:50053 (PID: $NODE3_PID)"
echo ""
echo "Logs are available in the logs/ directory:"
echo "  - logs/node1.log"
echo "  - logs/node2.log"
echo "  - logs/node3.log"
echo ""
echo "To stop the cluster, run:"
echo "  ./stop_cluster.sh"
echo "  or: pkill -f raft_server.py"
echo "=========================================="

# Save PIDs to file for easy cleanup
echo $NODE1_PID > logs/node1.pid
echo $NODE2_PID > logs/node2.pid
echo $NODE3_PID > logs/node3.pid

# Optional: tail logs
echo ""
echo "Press Ctrl+C to stop tailing logs (nodes will continue running)"
echo ""
sleep 2
tail -f logs/node1.log logs/node2.log logs/node3.log