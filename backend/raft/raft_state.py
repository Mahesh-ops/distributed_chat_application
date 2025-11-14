"""
Raft State Management
Handles persistent and volatile state for a Raft node.

Save this file as: backend/raft/raft_state.py
"""

import json
import os
import threading
from enum import Enum
from typing import List, Dict, Optional
import sys
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '../..')))

from backend.cluster_config import get_raft_state_file, get_raft_log_file


class NodeState(Enum):
    """Possible states for a Raft node."""
    FOLLOWER = "FOLLOWER"
    CANDIDATE = "CANDIDATE"
    LEADER = "LEADER"


class LogEntry:
    """Represents a single entry in the Raft log."""
    
    def __init__(self, term: int, index: int, command_type: str, 
                 data: dict, client_id: str = "", timestamp: int = 0):
        self.term = term
        self.index = index
        self.command_type = command_type
        self.data = data
        self.client_id = client_id
        self.timestamp = timestamp
    
    def to_dict(self):
        """Convert to dictionary for JSON serialization."""
        return {
            "term": self.term,
            "index": self.index,
            "command_type": self.command_type,
            "data": self.data,
            "client_id": self.client_id,
            "timestamp": self.timestamp
        }
    
    @staticmethod
    def from_dict(d):
        """Create LogEntry from dictionary."""
        return LogEntry(
            term=d["term"],
            index=d["index"],
            command_type=d["command_type"],
            data=d["data"],
            client_id=d.get("client_id", ""),
            timestamp=d.get("timestamp", 0)
        )
    
    def __repr__(self):
        return f"LogEntry(idx={self.index}, term={self.term}, type={self.command_type})"


class RaftState:
    """
    Manages the state of a Raft node.
    
    Persistent State (must survive crashes):
    - current_term: Latest term server has seen
    - voted_for: CandidateId that received vote in current term
    - log: Log entries
    
    Volatile State (on all servers):
    - commit_index: Index of highest log entry known to be committed
    - last_applied: Index of highest log entry applied to state machine
    
    Volatile State (on leaders):
    - next_index: For each server, index of next log entry to send
    - match_index: For each server, index of highest log entry known to be replicated
    """
    
    def __init__(self, node_id: str):
        self.node_id = node_id
        self.lock = threading.RLock()  # Reentrant lock for thread safety
        
        # Persistent state
        self.current_term = 0
        self.voted_for: Optional[str] = None
        self.log: List[LogEntry] = []
        
        # Volatile state (all servers)
        self.commit_index = 0
        self.last_applied = 0
        
        # Volatile state (leaders only)
        self.next_index: Dict[str, int] = {}
        self.match_index: Dict[str, int] = {}
        
        # Node state
        self.state = NodeState.FOLLOWER
        self.current_leader: Optional[str] = None
        
        # File paths
        self.state_file = get_raft_state_file(node_id)
        self.log_file = get_raft_log_file(node_id)
        
        # Ensure storage directory exists
        os.makedirs(os.path.dirname(self.state_file), exist_ok=True)
        
        # Load persistent state from disk
        self._load_persistent_state()
    
    # ----------------------------------------------------------------
    # Persistent State Management
    # ----------------------------------------------------------------
    
    def _load_persistent_state(self):
        """Load persistent state from disk."""
        # Load term and voted_for
        if os.path.exists(self.state_file):
            try:
                with open(self.state_file, 'r') as f:
                    data = json.load(f)
                    self.current_term = data.get("current_term", 0)
                    self.voted_for = data.get("voted_for")
            except (json.JSONDecodeError, FileNotFoundError):
                pass
        
        # Load log
        if os.path.exists(self.log_file):
            try:
                with open(self.log_file, 'r') as f:
                    log_data = json.load(f)
                    self.log = [LogEntry.from_dict(entry) for entry in log_data]
            except (json.JSONDecodeError, FileNotFoundError):
                pass
    
    def _save_persistent_state(self):
        """Save persistent state to disk."""
        # Save term and voted_for
        with open(self.state_file, 'w') as f:
            json.dump({
                "current_term": self.current_term,
                "voted_for": self.voted_for
            }, f, indent=2)
        
        # Save log
        with open(self.log_file, 'w') as f:
            log_data = [entry.to_dict() for entry in self.log]
            json.dump(log_data, f, indent=2)
    
    # ----------------------------------------------------------------
    # Term Management
    # ----------------------------------------------------------------
    
    def get_current_term(self) -> int:
        """Get the current term."""
        with self.lock:
            return self.current_term
    
    def set_current_term(self, term: int):
        """Set the current term and persist to disk."""
        with self.lock:
            if term > self.current_term:
                self.current_term = term
                self.voted_for = None  # Reset vote when term changes
                self._save_persistent_state()
    
    def increment_term(self) -> int:
        """Increment term (used when becoming candidate)."""
        with self.lock:
            self.current_term += 1
            self.voted_for = None
            self._save_persistent_state()
            return self.current_term
    
    # ----------------------------------------------------------------
    # Vote Management
    # ----------------------------------------------------------------
    
    def get_voted_for(self) -> Optional[str]:
        """Get who we voted for in current term."""
        with self.lock:
            return self.voted_for
    
    def set_voted_for(self, candidate_id: Optional[str]):
        """Record our vote and persist to disk."""
        with self.lock:
            self.voted_for = candidate_id
            self._save_persistent_state()
    
    # ----------------------------------------------------------------
    # Log Management
    # ----------------------------------------------------------------
    
    def append_log_entry(self, entry: LogEntry):
        """Append a new entry to the log."""
        with self.lock:
            self.log.append(entry)
            self._save_persistent_state()
    
    def get_log_entry(self, index: int) -> Optional[LogEntry]:
        """Get log entry at index (1-indexed)."""
        with self.lock:
            if 0 < index <= len(self.log):
                return self.log[index - 1]
            return None
    
    def get_last_log_index(self) -> int:
        """Get index of last log entry."""
        with self.lock:
            return len(self.log)
    
    def get_last_log_term(self) -> int:
        """Get term of last log entry."""
        with self.lock:
            if self.log:
                return self.log[-1].term
            return 0
    
    def get_log_entries_from(self, start_index: int) -> List[LogEntry]:
        """Get all log entries starting from index (inclusive)."""
        with self.lock:
            if start_index <= 0:
                return []
            return self.log[start_index - 1:]
    
    def delete_log_entries_from(self, start_index: int):
        """Delete log entries starting from index (inclusive)."""
        with self.lock:
            if start_index <= 0:
                return
            self.log = self.log[:start_index - 1]
            self._save_persistent_state()
    
    def append_entries(self, prev_log_index: int, entries: List[LogEntry]):
        """
        Append entries to log after prev_log_index.
        Deletes conflicting entries if necessary.
        """
        with self.lock:
            # Delete conflicting entries
            if entries:
                # Check for conflicts
                for i, entry in enumerate(entries):
                    existing_index = prev_log_index + i + 1
                    if existing_index <= len(self.log):
                        existing_entry = self.log[existing_index - 1]
                        if existing_entry.term != entry.term:
                            # Delete this and all following entries
                            self.log = self.log[:existing_index - 1]
                            break
                
                # Append new entries
                start_index = prev_log_index + 1
                for entry in entries:
                    if start_index > len(self.log):
                        self.log.append(entry)
                    start_index += 1
                
                self._save_persistent_state()
    
    # ----------------------------------------------------------------
    # Commit Index Management
    # ----------------------------------------------------------------
    
    def get_commit_index(self) -> int:
        """Get the commit index."""
        with self.lock:
            return self.commit_index
    
    def set_commit_index(self, index: int):
        """Set the commit index."""
        with self.lock:
            self.commit_index = max(self.commit_index, index)
    
    def get_last_applied(self) -> int:
        """Get the last applied index."""
        with self.lock:
            return self.last_applied
    
    def set_last_applied(self, index: int):
        """Set the last applied index."""
        with self.lock:
            self.last_applied = index
    
    # ----------------------------------------------------------------
    # Leader State Management
    # ----------------------------------------------------------------
    
    def initialize_leader_state(self, peer_ids: List[str]):
        """Initialize leader-specific state."""
        with self.lock:
            last_log_index = len(self.log)
            for peer_id in peer_ids:
                self.next_index[peer_id] = last_log_index + 1
                self.match_index[peer_id] = 0
    
    def get_next_index(self, peer_id: str) -> int:
        """Get next index to send to a peer."""
        with self.lock:
            return self.next_index.get(peer_id, 1)
    
    def set_next_index(self, peer_id: str, index: int):
        """Set next index for a peer."""
        with self.lock:
            self.next_index[peer_id] = index
    
    def get_match_index(self, peer_id: str) -> int:
        """Get match index for a peer."""
        with self.lock:
            return self.match_index.get(peer_id, 0)
    
    def set_match_index(self, peer_id: str, index: int):
        """Set match index for a peer."""
        with self.lock:
            self.match_index[peer_id] = index
    
    # ----------------------------------------------------------------
    # Node State Management
    # ----------------------------------------------------------------
    
    def get_state(self) -> NodeState:
        """Get current node state."""
        with self.lock:
            return self.state
    
    def set_state(self, state: NodeState):
        """Set node state."""
        with self.lock:
            self.state = state
    
    def get_current_leader(self) -> Optional[str]:
        """Get the current leader (if known)."""
        with self.lock:
            return self.current_leader
    
    def set_current_leader(self, leader_id: Optional[str]):
        """Set the current leader."""
        with self.lock:
            self.current_leader = leader_id
    
    # ----------------------------------------------------------------
    # Utility Methods
    # ----------------------------------------------------------------
    
    def get_state_summary(self) -> dict:
        """Get a summary of current state for debugging."""
        with self.lock:
            return {
                "node_id": self.node_id,
                "state": self.state.value,
                "current_term": self.current_term,
                "voted_for": self.voted_for,
                "log_length": len(self.log),
                "commit_index": self.commit_index,
                "last_applied": self.last_applied,
                "current_leader": self.current_leader
            }