"""
Replicated Storage Manager
Wraps StorageManager to route write operations through Raft consensus.
"""

import asyncio
from typing import Optional, Dict, Any
import sys
import os
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))


class ReplicatedStorage:
    """
    Provides a replicated storage interface that uses Raft for consensus.
    
    - Write operations are proposed through Raft and wait for consensus
    - Read operations are served directly from local storage
    """
    
    def __init__(self, storage_manager, raft_node):
        """
        Initialize the replicated storage.
        
        Args:
            storage_manager: The underlying StorageManager instance
            raft_node: The RaftNode instance for consensus
        """
        self.storage = storage_manager
        self.raft = raft_node
    
    # ----------------------------------------------------------------
    # User Operations
    # ----------------------------------------------------------------
    
    async def save_user(self, username: str, user_data: dict) -> bool:
        """
        Save a user (replicated write).
        
        Args:
            username: The username
            user_data: Dictionary containing 'hash' and 'is_admin'
            
        Returns:
            bool: True if successfully replicated, False otherwise
        """
        command_data = {
            "username": username,
            "password_hash": user_data.get("hash", ""),
            "is_admin": user_data.get("is_admin", False)
        }
        
        success = await self.raft.propose("CREATE_USER", command_data)
        return success
    
    def get_user(self, username: str) -> Optional[dict]:
        """
        Get a user (local read).
        
        Args:
            username: The username
            
        Returns:
            dict: User data or None if not found
        """
        return self.storage.get_user(username)
    
    def get_users(self) -> dict:
        """
        Get all users (local read).
        
        Returns:
            dict: All users
        """
        return self.storage.get_users()
    
    # ----------------------------------------------------------------
    # Message Operations
    # ----------------------------------------------------------------
    
    async def post_message(self, channel_id: str, sender_id: str, 
                          text: str, timestamp: str) -> bool:
        """
        Post a message (replicated write).
        
        Args:
            channel_id: The channel ID
            sender_id: The sender's username
            text: The message text
            timestamp: ISO format timestamp
            
        Returns:
            bool: True if successfully replicated
        """
        command_data = {
            "channel_id": channel_id,
            "sender_id": sender_id,
            "text": text,
            "timestamp": timestamp
        }
        
        success = await self.raft.propose("POST_MESSAGE", command_data)
        return success
    
    async def post_file_message(self, channel_id: str, sender_id: str,
                                file_name: str, file_id: str, timestamp: str) -> bool:
        """
        Post a file message (replicated write).
        
        Args:
            channel_id: The channel ID
            sender_id: The sender's username
            file_name: Original file name
            file_id: Unique file identifier
            timestamp: ISO format timestamp
            
        Returns:
            bool: True if successfully replicated
        """
        command_data = {
            "channel_id": channel_id,
            "sender_id": sender_id,
            "file_name": file_name,
            "file_id": file_id,
            "timestamp": timestamp
        }
        
        success = await self.raft.propose("UPLOAD_FILE_METADATA", command_data)
        return success
    
    def get_chat_history(self, channel_id: str) -> list:
        """
        Get chat history (local read).
        
        Args:
            channel_id: The channel ID
            
        Returns:
            list: List of message dictionaries
        """
        return self.storage.get_chat_history(channel_id)
    
    # ----------------------------------------------------------------
    # Group Operations
    # ----------------------------------------------------------------
    
    async def create_group(self, group_id: str, group_name: str) -> bool:
        """
        Create a group (replicated write).
        
        Args:
            group_id: The group ID
            group_name: The group name
            
        Returns:
            bool: True if successfully replicated
        """
        command_data = {
            "group_id": group_id,
            "group_name": group_name
        }
        
        success = await self.raft.propose("CREATE_GROUP", command_data)
        return success
    
    async def add_user_to_group(self, group_id: str, user_id: str) -> bool:
        """
        Add a user to a group (replicated write).
        
        Args:
            group_id: The group ID
            user_id: The user ID to add
            
        Returns:
            bool: True if successfully replicated
        """
        command_data = {
            "group_id": group_id,
            "user_id": user_id
        }
        
        success = await self.raft.propose("ADD_USER_TO_GROUP", command_data)
        return success
    
    async def remove_user_from_group(self, group_id: str, user_id: str) -> bool:
        """
        Remove a user from a group (replicated write).
        
        Args:
            group_id: The group ID
            user_id: The user ID to remove
            
        Returns:
            bool: True if successfully replicated
        """
        command_data = {
            "group_id": group_id,
            "user_id": user_id
        }
        
        success = await self.raft.propose("REMOVE_USER_FROM_GROUP", command_data)
        return success
    
    def get_group(self, group_id: str) -> Optional[dict]:
        """
        Get a group (local read).
        
        Args:
            group_id: The group ID
            
        Returns:
            dict: Group data or None if not found
        """
        return self.storage.get_group(group_id)
    
    def get_groups(self) -> dict:
        """
        Get all groups (local read).
        
        Returns:
            dict: All groups
        """
        return self.storage.get_groups()
    
    def update_user_last_seen(self, username: str):
        """
        Update the last-seen timestamp for a user (local write, no consensus needed).
        
        Args:
            username: The username to update
        """
        # This is a read operation that updates local state
        # No consensus needed - just updates the local JSON file
        self.storage.update_user_last_seen(username)

    def is_user_online(self, username: str, timeout: int = 300) -> bool:
        """
        Check if a user is online based on last-seen time (local read).
        
        Args:
            username: The username to check
            timeout: Seconds before considering user offline
            
        Returns:
            bool: True if user was seen within timeout period
        """
        return self.storage.is_user_online(username, timeout)
    
    # ----------------------------------------------------------------
    # File Operations (Note: Actual file data is not replicated through Raft)
    # ----------------------------------------------------------------
    
    def save_file_chunk(self, file_path: str, chunk: bytes):
        """
        Save file chunk (local write - files are replicated separately).
        
        Args:
            file_path: Path to the file
            chunk: Chunk of data to append
        """
        self.storage.save_file_chunk(file_path, chunk)
    
    def get_file_path(self, file_id: str) -> str:
        """Get the file path for a file ID."""
        return self.storage.get_file_path(file_id)
    
    def file_exists(self, file_id: str) -> bool:
        """Check if a file exists."""
        return self.storage.file_exists(file_id)
    
    def read_file_chunks(self, file_path: str):
        """Read file chunks (generator)."""
        return self.storage.read_file_chunks(file_path)
    
    # ----------------------------------------------------------------
    # Utility Methods
    # ----------------------------------------------------------------
    
    def is_leader(self) -> bool:
        """Check if this node is the leader."""
        return self.raft.raft_state.get_state().value == "LEADER"
    
    def get_leader_id(self) -> Optional[str]:
        """Get the current leader ID."""
        return self.raft.get_leader_id()
    
    def get_raft_state_summary(self) -> dict:
        """Get a summary of the Raft state for debugging."""
        return self.raft.raft_state.get_state_summary()