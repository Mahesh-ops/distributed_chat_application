"""
Raft Service Implementation
gRPC service for Raft peer-to-peer communication.
"""

import sys
import os
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '../..')))

import protos.raft_pb2 as raft_pb2
import protos.raft_pb2_grpc as raft_pb2_grpc


class RaftServiceImpl(raft_pb2_grpc.RaftServiceServicer):
    """
    Implements the RaftService gRPC service.
    Delegates actual Raft logic to the RaftNode.
    """
    
    def __init__(self, raft_node):
        """
        Initialize the Raft service.
        
        Args:
            raft_node: The RaftNode instance
        """
        self.raft_node = raft_node
    
    def RequestVote(self, request, context):
        """
        Handle RequestVote RPC.
        Invoked by candidates to gather votes.
        """
        print(f"[{self.raft_node.node_id}] Received RequestVote from {request.candidate_id} "
              f"(term={request.term})")
        
        response = self.raft_node.handle_request_vote(request)
        
        print(f"[{self.raft_node.node_id}] Vote response: granted={response.vote_granted} "
              f"(term={response.term})")
        
        return response
    
    def AppendEntries(self, request, context):
        """
        Handle AppendEntries RPC.
        Invoked by leader to replicate log entries and provide heartbeats.
        """
        is_heartbeat = len(request.entries) == 0
        
        if is_heartbeat:
            # Don't log heartbeats to reduce noise
            pass
        else:
            print(f"[{self.raft_node.node_id}] Received AppendEntries from {request.leader_id} "
                  f"(term={request.term}, entries={len(request.entries)})")
        
        response = self.raft_node.handle_append_entries(request)
        
        if not is_heartbeat:
            print(f"[{self.raft_node.node_id}] AppendEntries response: success={response.success}")
        
        return response
    
    def InstallSnapshot(self, request, context):
        """
        Handle InstallSnapshot RPC.
        Invoked by leader to send chunks of a snapshot to a follower.
        (Optional - for production use with large state)
        """
        # TODO: Implement snapshot support
        print(f"[{self.raft_node.node_id}] InstallSnapshot not implemented yet")
        
        return raft_pb2.InstallSnapshotResponse(
            term=self.raft_node.raft_state.get_current_term()
        )