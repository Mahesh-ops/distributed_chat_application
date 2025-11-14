"""
Replicated State Machine
Applies committed log entries to the application state.

SAVE THIS FILE AS: backend/raft/state_machine.py
"""

import json
import threading
from typing import Dict, Any
import sys
import os
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '../..')))

from backend.raft.raft_state import LogEntry


class StateMachine:
    """
    The replicated state machine that applies committed commands.
    This is where the actual application state changes happen.
    """
    
    def __init__(self, storage_manager):
        """
        Initialize the state machine with a storage manager.
        
        Args:
            storage_manager: The StorageManager instance from server.py
        """
        self.storage = storage_manager
        self.lock = threading.Lock()
        self.applied_entries = {}  # Track which entries have been applied
    
    def apply(self, log_entry: LogEntry) -> Dict[str, Any]:
        """
        Apply a committed log entry to the state machine.
        
        Args:
            log_entry: The log entry to apply
            
        Returns:
            dict: Result of the operation (success, message, data)
        """
        with self.lock:
            # Check if already applied (idempotency)
            entry_key = f"{log_entry.term}_{log_entry.index}"
            if entry_key in self.applied_entries:
                return self.applied_entries[entry_key]
            
            result = self._execute_command(log_entry)
            
            # Cache the result
            self.applied_entries[entry_key] = result
            
            return result
    
    def _execute_command(self, log_entry: LogEntry) -> Dict[str, Any]:
        """
        Execute the command based on its type.
        
        Args:
            log_entry: The log entry containing the command
            
        Returns:
            dict: Result of the operation
        """
        command_type = log_entry.command_type
        data = log_entry.data
        
        try:
            if command_type == "CREATE_USER":
                return self._apply_create_user(data)
            
            elif command_type == "POST_MESSAGE":
                return self._apply_post_message(data)
            
            elif command_type == "CREATE_GROUP":
                return self._apply_create_group(data)
            
            elif command_type == "ADD_USER_TO_GROUP":
                return self._apply_add_user_to_group(data)
            
            elif command_type == "REMOVE_USER_FROM_GROUP":
                return self._apply_remove_user_from_group(data)
            
            elif command_type == "UPLOAD_FILE_METADATA":
                return self._apply_upload_file_metadata(data)
            
            else:
                return {
                    "success": False,
                    "message": f"Unknown command type: {command_type}"
                }
        
        except Exception as e:
            return {
                "success": False,
                "message": f"Error applying command: {str(e)}"
            }
    
    # ----------------------------------------------------------------
    # User Operations
    # ----------------------------------------------------------------
    
    def _apply_create_user(self, data: dict) -> Dict[str, Any]:
        """
        Apply CREATE_USER command.
        
        Expected data:
        {
            "username": str,
            "password_hash": str,
            "is_admin": bool
        }
        """
        username = data["username"]
        password_hash = data["password_hash"]
        is_admin = data.get("is_admin", False)
        
        # Check if user already exists
        existing_user = self.storage.get_user(username)
        if existing_user:
            return {
                "success": False,
                "message": f"User {username} already exists"
            }
        
        # Create the user
        user_data = {
            "hash": password_hash,
            "is_admin": is_admin
        }
        self.storage.save_user(username, user_data)
        
        return {
            "success": True,
            "message": f"User {username} created successfully"
        }
    
    # ----------------------------------------------------------------
    # Message Operations
    # ----------------------------------------------------------------
    
    def _apply_post_message(self, data: dict) -> Dict[str, Any]:
        """
        Apply POST_MESSAGE command.
        
        Expected data:
        {
            "channel_id": str,
            "sender_id": str,
            "text": str (optional),
            "timestamp": str,
            "file_info": dict (optional)
        }
        """
        channel_id = data["channel_id"]
        sender_id = data["sender_id"]
        timestamp = data["timestamp"]
        
        # Create a message dictionary
        message_dict = {
            "sender_id": sender_id,
            "channel_id": channel_id,
            "timestamp": timestamp
        }
        
        # Add text or file_info
        if "text" in data:
            message_dict["text"] = data["text"]
        elif "file_info" in data:
            message_dict["file_info"] = data["file_info"]
        
        # Add to history
        self.storage.add_message_to_history_from_dict(channel_id, message_dict)
        
        return {
            "success": True,
            "message": "Message posted successfully",
            "data": message_dict
        }
    
    # ----------------------------------------------------------------
    # Group Operations
    # ----------------------------------------------------------------
    
    def _apply_create_group(self, data: dict) -> Dict[str, Any]:
        """
        Apply CREATE_GROUP command.
        
        Expected data:
        {
            "group_id": str,
            "group_name": str
        }
        """
        group_id = data["group_id"]
        group_name = data["group_name"]
        
        # Check if group already exists
        existing_group = self.storage.get_group(group_id)
        if existing_group:
            return {
                "success": False,
                "message": f"Group {group_id} already exists"
            }
        
        # Create the group
        group_data = {
            "name": group_name,
            "members": []
        }
        self.storage.save_group(group_id, group_data)
        
        return {
            "success": True,
            "message": f"Group {group_name} created successfully"
        }
    
    def _apply_add_user_to_group(self, data: dict) -> Dict[str, Any]:
        """
        Apply ADD_USER_TO_GROUP command.
        
        Expected data:
        {
            "group_id": str,
            "user_id": str
        }
        """
        group_id = data["group_id"]
        user_id = data["user_id"]
        
        # Get the group
        group = self.storage.get_group(group_id)
        if not group:
            return {
                "success": False,
                "message": f"Group {group_id} not found"
            }
        
        # Check if user exists
        user = self.storage.get_user(user_id)
        if not user:
            return {
                "success": False,
                "message": f"User {user_id} not found"
            }
        
        # Add user if not already in group
        if user_id not in group["members"]:
            group["members"].append(user_id)
            self.storage.save_group(group_id, group)
        
        return {
            "success": True,
            "message": f"User {user_id} added to group {group_id}"
        }
    
    def _apply_remove_user_from_group(self, data: dict) -> Dict[str, Any]:
        """
        Apply REMOVE_USER_FROM_GROUP command.
        
        Expected data:
        {
            "group_id": str,
            "user_id": str
        }
        """
        group_id = data["group_id"]
        user_id = data["user_id"]
        
        # Get the group
        group = self.storage.get_group(group_id)
        if not group:
            return {
                "success": False,
                "message": f"Group {group_id} not found"
            }
        
        # Remove user if in group
        if user_id in group["members"]:
            group["members"].remove(user_id)
            self.storage.save_group(group_id, group)
            return {
                "success": True,
                "message": f"User {user_id} removed from group {group_id}"
            }
        else:
            return {
                "success": False,
                "message": f"User {user_id} not in group {group_id}"
            }
    
    # ----------------------------------------------------------------
    # File Operations
    # ----------------------------------------------------------------
    
    def _apply_upload_file_metadata(self, data: dict) -> Dict[str, Any]:
        """
        Apply UPLOAD_FILE_METADATA command.
        Note: The actual file content is replicated separately.
        This only stores the metadata in the chat history.
        
        Expected data:
        {
            "channel_id": str,
            "sender_id": str,
            "file_name": str,
            "file_id": str,
            "timestamp": str
        }
        """
        channel_id = data["channel_id"]
        sender_id = data["sender_id"]
        file_name = data["file_name"]
        file_id = data["file_id"]
        timestamp = data["timestamp"]
        
        # Create file message
        message_dict = {
            "sender_id": sender_id,
            "channel_id": channel_id,
            "timestamp": timestamp,
            "file_info": {
                "file_name": file_name,
                "file_id": file_id
            }
        }
        
        # Add to history
        self.storage.add_message_to_history_from_dict(channel_id, message_dict)
        
        return {
            "success": True,
            "message": "File metadata stored successfully",
            "data": message_dict
        }
    
    # ----------------------------------------------------------------
    # Utility Methods
    # ----------------------------------------------------------------
    
    def get_applied_count(self) -> int:
        """Get the number of applied entries."""
        with self.lock:
            return len(self.applied_entries)
    
    def clear_cache(self, before_index: int = None):
        """
        Clear the applied entries cache.
        Optionally only clear entries before a certain index.
        """
        with self.lock:
            if before_index is None:
                self.applied_entries.clear()
            else:
                # Clear entries with index < before_index
                keys_to_remove = [
                    key for key in self.applied_entries.keys()
                    if int(key.split('_')[1]) < before_index
                ]
                for key in keys_to_remove:
                    del self.applied_entries[key]