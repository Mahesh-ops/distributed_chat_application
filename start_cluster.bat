@echo off
echo Starting Raft Cluster...

start "Node1" cmd /k "python backend/raft_server.py --node-id node1"
timeout /t 2 /nobreak >nul

start "Node2" cmd /k "python backend/raft_server.py --node-id node2"
timeout /t 2 /nobreak >nul

start "Node3" cmd /k "python backend/raft_server.py --node-id node3"

echo All nodes started!
echo Close this window to keep nodes running.
pause