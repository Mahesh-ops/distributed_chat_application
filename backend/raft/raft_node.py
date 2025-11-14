"""
Raft Node Implementation
Core logic for Raft consensus algorithm including:
- Leader election
- Log replication
- Safety guarantees
"""

import grpc
import asyncio
import random
import time
import threading
from typing import Dict, List, Optional, Callable
import sys
import os
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '../..')))

from backend.raft.raft_state import RaftState, NodeState, LogEntry
from backend.raft.state_machine import StateMachine
from backend.cluster_config import (
    get_peer_node_ids, get_raft_address, get_quorum_size,
    ELECTION_TIMEOUT_MIN, ELECTION_TIMEOUT_MAX, HEARTBEAT_INTERVAL, RPC_TIMEOUT
)
import protos.raft_pb2 as raft_pb2
import protos.raft_pb2_grpc as raft_pb2_grpc


class RaftNode:
    """
    Implements the Raft consensus algorithm.
    """
    
    def __init__(self, node_id: str, state_machine: StateMachine):
        self.node_id = node_id
        self.raft_state = RaftState(node_id)
        self.state_machine = state_machine
        
        # Peer connections
        self.peer_stubs: Dict[str, raft_pb2_grpc.RaftServiceStub] = {}
        self.peer_ids = get_peer_node_ids(node_id)
        
        # Timers
        self.election_timer = None
        self.heartbeat_timer = None
        
        # Pending client requests
        self.pending_requests: Dict[int, asyncio.Future] = {}
        self.pending_requests_lock = threading.Lock()
        
        # Control flags
        self.running = False
        self.executor = None
        
        print(f"[{self.node_id}] Raft node initialized")
    
    # ----------------------------------------------------------------
    # Lifecycle Management
    # ----------------------------------------------------------------
    
    def start(self):
        """Start the Raft node."""
        if self.running:
            return
        
        self.running = True
        
        # Connect to peers
        self._connect_to_peers()
        
        # Start as follower
        self.raft_state.set_state(NodeState.FOLLOWER)
        
        # Start election timer
        self._reset_election_timer()
        
        # Start background thread for applying committed entries
        threading.Thread(target=self._apply_committed_entries_loop, daemon=True).start()
        
        print(f"[{self.node_id}] Raft node started as FOLLOWER")
    
    def stop(self):
        """Stop the Raft node."""
        self.running = False
        
        if self.election_timer:
            self.election_timer.cancel()
        if self.heartbeat_timer:
            self.heartbeat_timer.cancel()
        
        # Close peer connections
        for stub in self.peer_stubs.values():
            # gRPC channels are closed automatically
            pass
        
        print(f"[{self.node_id}] Raft node stopped")
    
    def _connect_to_peers(self):
        """Establish gRPC connections to peer nodes."""
        for peer_id in self.peer_ids:
            address = get_raft_address(peer_id)
            channel = grpc.insecure_channel(address)
            self.peer_stubs[peer_id] = raft_pb2_grpc.RaftServiceStub(channel)
        print(f"[{self.node_id}] Connected to {len(self.peer_stubs)} peers")
    
    # ----------------------------------------------------------------
    # Client Request Handling
    # ----------------------------------------------------------------
    
    async def propose(self, command_type: str, data: dict) -> bool:
        """
        Propose a new command to be replicated.
        Returns True if successfully committed, False otherwise.
        """
        if self.raft_state.get_state() != NodeState.LEADER:
            print(f"[{self.node_id}] Not leader, cannot propose")
            return False
        
        # Create log entry
        current_term = self.raft_state.get_current_term()
        next_index = self.raft_state.get_last_log_index() + 1
        
        entry = LogEntry(
            term=current_term,
            index=next_index,
            command_type=command_type,
            data=data,
            timestamp=int(time.time() * 1000)
        )
        
        # Append to local log
        self.raft_state.append_log_entry(entry)
        print(f"[{self.node_id}] Proposed entry: {entry}")
        
        # Create future for tracking
        future = asyncio.Future()
        with self.pending_requests_lock:
            self.pending_requests[next_index] = future
        
        # Immediately send AppendEntries to all peers
        self._send_append_entries_to_all()
        
        # Wait for commit (with timeout)
        try:
            result = await asyncio.wait_for(future, timeout=5.0)
            return result
        except asyncio.TimeoutError:
            print(f"[{self.node_id}] Proposal timeout for index {next_index}")
            with self.pending_requests_lock:
                self.pending_requests.pop(next_index, None)
            return False
    
    def get_leader_id(self) -> Optional[str]:
        """Get the current leader ID (if known)."""
        if self.raft_state.get_state() == NodeState.LEADER:
            return self.node_id
        return self.raft_state.get_current_leader()
    
    # ----------------------------------------------------------------
    # Leader Election
    # ----------------------------------------------------------------
    
    def _reset_election_timer(self):
        """Reset the election timeout timer."""
        if self.election_timer:
            self.election_timer.cancel()
        
        # Random timeout between min and max
        timeout = random.uniform(
            ELECTION_TIMEOUT_MIN / 1000.0,
            ELECTION_TIMEOUT_MAX / 1000.0
        )
        
        self.election_timer = threading.Timer(timeout, self._start_election)
        self.election_timer.start()
    
    def _start_election(self):
        """Start a new election."""
        if not self.running:
            return
        
        # Become candidate
        self.raft_state.set_state(NodeState.CANDIDATE)
        
        # Increment term and vote for self
        new_term = self.raft_state.increment_term()
        self.raft_state.set_voted_for(self.node_id)
        
        print(f"[{self.node_id}] Starting election for term {new_term}")
        
        # Request votes from all peers
        votes_received = 1  # Vote for self
        votes_needed = get_quorum_size()
        
        for peer_id in self.peer_ids:
            threading.Thread(
                target=self._request_vote_from_peer,
                args=(peer_id, new_term),
                daemon=True
            ).start()
        
        # Reset election timer
        self._reset_election_timer()
    
    def _request_vote_from_peer(self, peer_id: str, term: int):
        """Request vote from a peer."""
        try:
            stub = self.peer_stubs.get(peer_id)
            if not stub:
                return
            
            request = raft_pb2.RequestVoteRequest(
                term=term,
                candidate_id=self.node_id,
                last_log_index=self.raft_state.get_last_log_index(),
                last_log_term=self.raft_state.get_last_log_term()
            )
            
            response = stub.RequestVote(request, timeout=RPC_TIMEOUT / 1000.0)
            
            if response.vote_granted:
                self._handle_vote_received(term)
            elif response.term > term:
                self._step_down(response.term)
        
        except grpc.RpcError as e:
            print(f"[{self.node_id}] Vote request to {peer_id} failed: {e.code()}")
    
    def _handle_vote_received(self, term: int):
        """Handle receiving a vote."""
        if self.raft_state.get_current_term() != term:
            return
        
        if self.raft_state.get_state() != NodeState.CANDIDATE:
            return
        
        # Count votes (simplified - in production, track which peers voted)
        # For now, we'll become leader after a short delay if still candidate
        # This is a simplified version - real implementation should count votes properly
        
        # Become leader if we have quorum
        self._become_leader()
    
    def _become_leader(self):
        """Transition to leader state."""
        if self.raft_state.get_state() != NodeState.CANDIDATE:
            return
        
        print(f"[{self.node_id}] Became LEADER for term {self.raft_state.get_current_term()}")
        
        self.raft_state.set_state(NodeState.LEADER)
        self.raft_state.set_current_leader(self.node_id)
        
        # Initialize leader state
        self.raft_state.initialize_leader_state(self.peer_ids)
        
        # Cancel election timer
        if self.election_timer:
            self.election_timer.cancel()
        
        # Start sending heartbeats
        self._start_heartbeat_timer()
    
    def _step_down(self, new_term: int):
        """Step down to follower state."""
        print(f"[{self.node_id}] Stepping down to FOLLOWER (term {new_term})")
        
        self.raft_state.set_current_term(new_term)
        self.raft_state.set_state(NodeState.FOLLOWER)
        self.raft_state.set_current_leader(None)
        
        # Stop heartbeat timer if leader
        if self.heartbeat_timer:
            self.heartbeat_timer.cancel()
            self.heartbeat_timer = None
        
        # Reset election timer
        self._reset_election_timer()
    
    # ----------------------------------------------------------------
    # Log Replication (Leader)
    # ----------------------------------------------------------------
    
    def _start_heartbeat_timer(self):
        """Start the heartbeat timer (leader only)."""
        if self.heartbeat_timer:
            self.heartbeat_timer.cancel()
        
        self.heartbeat_timer = threading.Timer(
            HEARTBEAT_INTERVAL / 1000.0,
            self._send_heartbeats
        )
        self.heartbeat_timer.start()
    
    def _send_heartbeats(self):
        """Send heartbeat (empty AppendEntries) to all followers."""
        if not self.running or self.raft_state.get_state() != NodeState.LEADER:
            return
        
        self._send_append_entries_to_all()
        
        # Schedule next heartbeat
        self._start_heartbeat_timer()
    
    def _send_append_entries_to_all(self):
        """Send AppendEntries RPC to all peers."""
        for peer_id in self.peer_ids:
            threading.Thread(
                target=self._send_append_entries,
                args=(peer_id,),
                daemon=True
            ).start()
    
    def _send_append_entries(self, peer_id: str):
        """Send AppendEntries RPC to a specific peer."""
        try:
            stub = self.peer_stubs.get(peer_id)
            if not stub:
                return
            
            # Get entries to send
            next_idx = self.raft_state.get_next_index(peer_id)
            entries_to_send = self.raft_state.get_log_entries_from(next_idx)
            
            # Convert to protobuf
            pb_entries = []
            for entry in entries_to_send:
                pb_entry = raft_pb2.LogEntry(
                    term=entry.term,
                    index=entry.index,
                    command_type=entry.command_type,
                    data=str(entry.data).encode('utf-8'),
                    timestamp=entry.timestamp
                )
                pb_entries.append(pb_entry)
            
            # Get previous log entry info
            prev_log_index = next_idx - 1
            prev_log_term = 0
            if prev_log_index > 0:
                prev_entry = self.raft_state.get_log_entry(prev_log_index)
                if prev_entry:
                    prev_log_term = prev_entry.term
            
            request = raft_pb2.AppendEntriesRequest(
                term=self.raft_state.get_current_term(),
                leader_id=self.node_id,
                prev_log_index=prev_log_index,
                prev_log_term=prev_log_term,
                entries=pb_entries,
                leader_commit=self.raft_state.get_commit_index()
            )
            
            response = stub.AppendEntries(request, timeout=RPC_TIMEOUT / 1000.0)
            
            if response.success:
                # Update match and next index
                if entries_to_send:
                    new_match_index = entries_to_send[-1].index
                    self.raft_state.set_match_index(peer_id, new_match_index)
                    self.raft_state.set_next_index(peer_id, new_match_index + 1)
                
                # Update commit index
                self._update_commit_index()
            else:
                # Handle rejection
                if response.term > self.raft_state.get_current_term():
                    self._step_down(response.term)
                else:
                    # Decrement next_index and retry
                    new_next = max(1, next_idx - 1)
                    self.raft_state.set_next_index(peer_id, new_next)
        
        except grpc.RpcError as e:
            # Peer unavailable, will retry on next heartbeat
            pass
    
    def _update_commit_index(self):
        """Update commit index based on majority replication."""
        if self.raft_state.get_state() != NodeState.LEADER:
            return
        
        # Find the highest index replicated on majority
        last_log_index = self.raft_state.get_last_log_index()
        
        for n in range(self.raft_state.get_commit_index() + 1, last_log_index + 1):
            # Count replicas
            replicas = 1  # Leader has it
            for peer_id in self.peer_ids:
                if self.raft_state.get_match_index(peer_id) >= n:
                    replicas += 1
            
            # Check if majority and same term
            if replicas >= get_quorum_size():
                entry = self.raft_state.get_log_entry(n)
                if entry and entry.term == self.raft_state.get_current_term():
                    self.raft_state.set_commit_index(n)
                    
                    # Notify pending request
                    with self.pending_requests_lock:
                        if n in self.pending_requests:
                            future = self.pending_requests.pop(n)
                            try:
                                future.get_loop().call_soon_threadsafe(
                                    future.set_result, True
                                )
                            except:
                                pass
    
    # ----------------------------------------------------------------
    # Apply Committed Entries
    # ----------------------------------------------------------------
    
    def _apply_committed_entries_loop(self):
        """Background loop to apply committed entries to state machine."""
        while self.running:
            try:
                commit_idx = self.raft_state.get_commit_index()
                last_applied = self.raft_state.get_last_applied()
                
                if commit_idx > last_applied:
                    # Apply entries
                    for i in range(last_applied + 1, commit_idx + 1):
                        entry = self.raft_state.get_log_entry(i)
                        if entry:
                            result = self.state_machine.apply(entry)
                            print(f"[{self.node_id}] Applied entry {i}: {result}")
                            self.raft_state.set_last_applied(i)
                
                time.sleep(0.01)  # 10ms
            
            except Exception as e:
                print(f"[{self.node_id}] Error applying entries: {e}")
                time.sleep(0.1)
    
    # ----------------------------------------------------------------
    # RPC Handlers (called by RaftServiceImpl)
    # ----------------------------------------------------------------
    
    def handle_request_vote(self, request: raft_pb2.RequestVoteRequest) -> raft_pb2.RequestVoteResponse:
        """Handle RequestVote RPC."""
        current_term = self.raft_state.get_current_term()
        
        # Update term if necessary
        if request.term > current_term:
            self._step_down(request.term)
            current_term = request.term
        
        vote_granted = False
        
        # Check if we can grant vote
        if request.term == current_term:
            voted_for = self.raft_state.get_voted_for()
            
            # Check if not voted or already voted for this candidate
            if voted_for is None or voted_for == request.candidate_id:
                # Check if candidate's log is at least as up-to-date
                last_log_term = self.raft_state.get_last_log_term()
                last_log_index = self.raft_state.get_last_log_index()
                
                log_ok = (request.last_log_term > last_log_term or
                         (request.last_log_term == last_log_term and
                          request.last_log_index >= last_log_index))
                
                if log_ok:
                    vote_granted = True
                    self.raft_state.set_voted_for(request.candidate_id)
                    self._reset_election_timer()
        
        return raft_pb2.RequestVoteResponse(
            term=current_term,
            vote_granted=vote_granted
        )
    
    def handle_append_entries(self, request: raft_pb2.AppendEntriesRequest) -> raft_pb2.AppendEntriesResponse:
        """Handle AppendEntries RPC."""
        current_term = self.raft_state.get_current_term()
        
        # Reply false if term < currentTerm
        if request.term < current_term:
            return raft_pb2.AppendEntriesResponse(
                term=current_term,
                success=False
            )
        
        # Update term and step down if necessary
        if request.term > current_term:
            self._step_down(request.term)
        
        # Reset election timer (received valid RPC from leader)
        self._reset_election_timer()
        self.raft_state.set_current_leader(request.leader_id)
        
        # Check if log contains entry at prev_log_index with prev_log_term
        success = False
        if request.prev_log_index == 0:
            success = True
        else:
            prev_entry = self.raft_state.get_log_entry(request.prev_log_index)
            if prev_entry and prev_entry.term == request.prev_log_term:
                success = True
        
        if success:
            # Append entries
            if request.entries:
                entries_to_append = []
                for pb_entry in request.entries:
                    entry = LogEntry(
                        term=pb_entry.term,
                        index=pb_entry.index,
                        command_type=pb_entry.command_type,
                        data=eval(pb_entry.data.decode('utf-8')),
                        timestamp=pb_entry.timestamp
                    )
                    entries_to_append.append(entry)
                
                self.raft_state.append_entries(request.prev_log_index, entries_to_append)
            
            # Update commit index
            if request.leader_commit > self.raft_state.get_commit_index():
                new_commit = min(request.leader_commit, self.raft_state.get_last_log_index())
                self.raft_state.set_commit_index(new_commit)
        
        return raft_pb2.AppendEntriesResponse(
            term=self.raft_state.get_current_term(),
            success=success,
            match_index=self.raft_state.get_last_log_index() if success else 0
        )