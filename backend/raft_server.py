"""
Raft-enabled Chat Server
Main server file that integrates Raft consensus for fault tolerance.

Save this file as: backend/raft_server.py

Usage:
    python backend/raft_server.py --node-id node1
    python backend/raft_server.py --node-id node2
    python backend/raft_server.py --node-id node3
"""

import grpc
import jwt
import os
import json
import queue
"""
Raft-enabled Chat Server - PATH FIX VERSION
Replace the import section (lines 1-32) in your raft_server.py with this
"""

import grpc
import jwt
import os
import json
import queue
import threading
import uuid
import argparse
import asyncio
from concurrent import futures
from datetime import datetime, timezone
from werkzeug.security import generate_password_hash, check_password_hash
import google.generativeai as genai

# Fix path to find protos module
import sys
# Get the parent directory of backend (the project root)
current_dir = os.path.dirname(os.path.abspath(__file__))
parent_dir = os.path.dirname(current_dir)
sys.path.insert(0, parent_dir)

# Now import protos
import protos.chat_pb2 as chat_pb2
import protos.chat_pb2_grpc as chat_pb2_grpc
import protos.llm_service_pb2 as llm_service_pb2
import protos.llm_service_pb2_grpc as llm_service_pb2_grpc
import protos.raft_pb2_grpc as raft_pb2_grpc

# Import from backend
from backend.cluster_config import (
    validate_node_id, get_storage_dir, get_node_config,
    get_client_address, get_raft_address, print_cluster_config
)
from backend.raft.raft_node import RaftNode
from backend.raft.raft_service_impl import RaftServiceImpl
from backend.raft.state_machine import StateMachine
from backend.replicated_storage import ReplicatedStorage

# ... rest of the file stays the same

# --- Configuration ---
JWT_SECRET = "a-super-secret-key-that-should-be-in-an-env-var"
GEMINI_API_KEY = "YOUR_GEMINI_API_KEY_HERE"
CHUNK_SIZE = 1024 * 1024  # 1MB

# Configure the Gemini API
if GEMINI_API_KEY == "YOUR_GEMINI_API_KEY_HERE":
    print("⚠️  WARNING: Gemini API key is not set. Recommendation feature will be disabled.")
    GEMINI_ENABLED = False
    GEMINI_MODEL = None
else:
    try:
        genai.configure(api_key=GEMINI_API_KEY)
        GEMINI_MODEL = genai.GenerativeModel('gemini-2.5-flash-lite')
        print("✅ Gemini Model initialized successfully.")
        GEMINI_ENABLED = True
    except Exception as e:
        print(f"🔴 ERROR: Failed to configure Gemini AI: {e}")
        GEMINI_ENABLED = False
        GEMINI_MODEL = None


class StorageManager:
    """Handles all file I/O for the server's persistent storage."""
    
    def __init__(self, base_dir, files_dir):
        self.base_dir = base_dir
        self.files_dir = files_dir
        self.users_file = os.path.join(base_dir, "users.json")
        self.groups_file = os.path.join(base_dir, "groups.json")
        self.lock = threading.Lock()
        self._initialize_storage()
    
    def _initialize_storage(self):
        os.makedirs(self.base_dir, exist_ok=True)
        os.makedirs(self.files_dir, exist_ok=True)
        if not os.path.exists(self.users_file):
            admin_hash = generate_password_hash("adminpass")
            initial_users = {
                "admin": {"hash": admin_hash, "is_admin": True}
            }
            with open(self.users_file, 'w') as f:
                json.dump(initial_users, f, indent=2)
        if not os.path.exists(self.groups_file):
            with open(self.groups_file, 'w') as f:
                json.dump({}, f, indent=2)
    
    def _read_json(self, file_path):
        with self.lock:
            if not os.path.exists(file_path):
                return {}
            try:
                with open(file_path, 'r') as f:
                    return json.load(f)
            except (json.JSONDecodeError, FileNotFoundError):
                return {}
    
    def _write_json(self, file_path, data):
        with self.lock:
            with open(file_path, 'w') as f:
                json.dump(data, f, indent=2)
    
    # --- User Methods ---
    def get_users(self):
        return self._read_json(self.users_file)
    
    def get_user(self, username):
        return self.get_users().get(username)
    
    def save_user(self, username, data):
        users = self.get_users()
        users[username] = data
        self._write_json(self.users_file, users)
    
    # --- Group Methods ---
    def get_groups(self):
        return self._read_json(self.groups_file)
    
    def get_group(self, group_id):
        return self.get_groups().get(group_id)
    
    def save_group(self, group_id, data):
        groups = self.get_groups()
        groups[group_id] = data
        self._write_json(self.groups_file, groups)
    
    # --- Chat History Methods ---
    def get_channel_dir(self, channel_id):
        safe_channel_id = os.path.basename(channel_id)
        channel_dir = os.path.join(self.base_dir, safe_channel_id)
        os.makedirs(channel_dir, exist_ok=True)
        return channel_dir
    
    def get_chat_history(self, channel_id):
        """Robustly reads chat history."""
        history_file = os.path.join(self.get_channel_dir(channel_id), "history.json")
        data = self._read_json(history_file)
        
        if isinstance(data, dict):
            return data.get("messages", [])
        elif isinstance(data, list):
            return data
        
        return []
    
    def add_message_to_history(self, channel_id, message_pb):
        """Add message from protobuf object."""
        history_file = os.path.join(self.get_channel_dir(channel_id), "history.json")
        messages = self.get_chat_history(channel_id)
        
        message_dict = {
            "sender_id": message_pb.sender_id,
            "channel_id": message_pb.channel_id,
            "timestamp": message_pb.timestamp
        }
        
        if message_pb.HasField("text"):
            message_dict["text"] = message_pb.text
        elif message_pb.HasField("file_info"):
            message_dict["file_info"] = {
                "file_name": message_pb.file_info.file_name,
                "file_id": message_pb.file_info.file_id
            }
        
        messages.append(message_dict)
        self._write_json(history_file, {"messages": messages})
    
    def add_message_to_history_from_dict(self, channel_id, message_dict):
        """Add message from dictionary (used by state machine)."""
        history_file = os.path.join(self.get_channel_dir(channel_id), "history.json")
        messages = self.get_chat_history(channel_id)
        messages.append(message_dict)
        self._write_json(history_file, {"messages": messages})
    
    # --- File Storage Methods ---
    def save_file_chunk(self, file_path, chunk):
        """Appends a chunk of data to a file."""
        with self.lock:
            with open(file_path, 'ab') as f:
                f.write(chunk)
    
    def get_file_path(self, file_id):
        """Safely gets the full path for a file ID."""
        safe_file_id = os.path.basename(file_id)
        return os.path.join(self.files_dir, safe_file_id)
    
    def file_exists(self, file_id):
        return os.path.exists(self.get_file_path(file_id))
    
    def read_file_chunks(self, file_path):
        """Yields file chunks for streaming download."""
        with open(file_path, 'rb') as f:
            while True:
                chunk = f.read(CHUNK_SIZE)
                if not chunk:
                    break
                yield chunk


class ChatServiceImpl(chat_pb2_grpc.ChatServiceServicer):
    """Implements the core ChatService RPCs with Raft consensus."""
    
    def __init__(self, replicated_storage, online_users, raft_node):
        self.storage = replicated_storage
        self.online_users = online_users
        self.raft = raft_node
        self.subscriptions = {}
        self.subscriptions_lock = threading.Lock()
    
    # --- Helper Methods ---
    def _validate_token(self, token):
        try:
            return jwt.decode(token, JWT_SECRET, algorithms=["HS256"])
        except jwt.PyJWTError:
            return None
    
    def _broadcast_message(self, channel_id, message_pb):
        with self.subscriptions_lock:
            subscribers = self.subscriptions.get(channel_id, [])
            for q in subscribers:
                try:
                    q.put_nowait(message_pb)
                except queue.Full:
                    pass
    
    def _check_leader(self, context):
        """Check if this node is the leader. If not, abort with leader info."""
        if not self.storage.is_leader():
            leader_id = self.storage.get_leader_id()
            if leader_id:
                leader_addr = get_client_address(leader_id)
                context.abort(
                    grpc.StatusCode.FAILED_PRECONDITION,
                    f"NOT_LEADER:{leader_addr}"
                )
            else:
                context.abort(
                    grpc.StatusCode.UNAVAILABLE,
                    "NO_LEADER:No leader currently elected"
                )
    
    # --- Authentication RPCs ---
    def SignUp(self, request, context):
        """Create new user (requires consensus)."""
        self._check_leader(context)
        
        users = self.storage.get_users()
        if request.username in users:
            return chat_pb2.StatusResponse(
                success=False,
                message="Username already exists."
            )
        
        hashed_password = generate_password_hash(request.password)
        new_user = {"hash": hashed_password, "is_admin": False}
        
        # Propose through Raft
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        try:
            success = loop.run_until_complete(
                self.storage.save_user(request.username, new_user)
            )
            if success:
                return chat_pb2.StatusResponse(
                    success=True,
                    message="User created successfully."
                )
            else:
                return chat_pb2.StatusResponse(
                    success=False,
                    message="Failed to replicate user creation."
                )
        finally:
            loop.close()
    
    def Login(self, request, context):
        """Login (read-only, no consensus needed)."""
        user_data = self.storage.get_user(request.username)
        if not user_data or not check_password_hash(
            user_data.get('hash', ''), request.password
        ):
            return chat_pb2.LoginResponse(
                success=False,
                message="Invalid credentials."
            )
        
        self.online_users.add(request.username)
        token = jwt.encode(
            {"username": request.username},
            JWT_SECRET,
            algorithm="HS256"
        )
        return chat_pb2.LoginResponse(success=True, token=token)
    
    def Logout(self, request, context):
        """Logout (local operation)."""
        payload = self._validate_token(request.token)
        if payload and payload['username'] in self.online_users:
            self.online_users.remove(payload['username'])
        return chat_pb2.StatusResponse(success=True)
    
    # --- Core Chat RPCs ---
    def GetUsers(self, request, context):
        """Get users (read-only)."""
        if not self._validate_token(request.token):
            context.abort(grpc.StatusCode.UNAUTHENTICATED, "Invalid token")
        
        all_users = self.storage.get_users()
        user_list = [
            chat_pb2.User(
                id=username,
                name=username,
                online=(username in self.online_users)
            )
            for username in all_users
        ]
        return chat_pb2.UserList(users=user_list)
    
    def GetMyGroups(self, request, context):
        """Get user's groups (read-only)."""
        payload = self._validate_token(request.token)
        if not payload:
            context.abort(grpc.StatusCode.UNAUTHENTICATED, "Invalid token")
        
        username = payload['username']
        all_groups = self.storage.get_groups()
        my_groups = [
            chat_pb2.Group(
                id=gid,
                name=gdata['name'],
                member_ids=gdata['members']
            )
            for gid, gdata in all_groups.items()
            if username in gdata['members']
        ]
        return chat_pb2.GroupList(groups=my_groups)
    
    def PostMessage(self, request, context):
        """Post message (requires consensus)."""
        payload = self._validate_token(request.token)
        if not payload:
            context.abort(grpc.StatusCode.UNAUTHENTICATED, "Invalid token")
        
        self._check_leader(context)
        
        timestamp = datetime.now(timezone.utc).isoformat()
        
        # Propose through Raft
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        try:
            success = loop.run_until_complete(
                self.storage.post_message(
                    request.channel_id,
                    payload['username'],
                    request.text,
                    timestamp
                )
            )
            
            if success:
                # Broadcast to subscribers
                message = chat_pb2.ChatMessage(
                    sender_id=payload['username'],
                    text=request.text,
                    timestamp=timestamp,
                    channel_id=request.channel_id
                )
                self._broadcast_message(request.channel_id, message)
                
                return chat_pb2.StatusResponse(
                    success=True,
                    message="Message sent."
                )
            else:
                return chat_pb2.StatusResponse(
                    success=False,
                    message="Failed to replicate message."
                )
        finally:
            loop.close()
    
    def StreamMessages(self, request, context):
        """Stream messages (read-only)."""
        payload = self._validate_token(request.token)
        if not payload:
            context.abort(grpc.StatusCode.UNAUTHENTICATED, "Invalid token")
        
        channel_id = request.channel_id
        q = queue.Queue()
        
        with self.subscriptions_lock:
            if channel_id not in self.subscriptions:
                self.subscriptions[channel_id] = []
            self.subscriptions[channel_id].append(q)
        
        try:
            while context.is_active():
                try:
                    msg = q.get(timeout=1)
                    yield msg
                except queue.Empty:
                    continue
        except Exception:
            pass
        finally:
            with self.subscriptions_lock:
                if channel_id in self.subscriptions:
                    if q in self.subscriptions[channel_id]:
                        self.subscriptions[channel_id].remove(q)
    
    def GetChatHistory(self, request, context):
        """Get chat history (read-only)."""
        if not self._validate_token(request.token):
            context.abort(grpc.StatusCode.UNAUTHENTICATED, "Invalid token")
        
        history_dicts = self.storage.get_chat_history(request.channel_id)
        messages = []
        for msg in history_dicts:
            chat_msg = chat_pb2.ChatMessage(
                sender_id=msg["sender_id"],
                channel_id=msg["channel_id"],
                timestamp=msg["timestamp"]
            )
            if "text" in msg:
                chat_msg.text = msg["text"]
            elif "file_info" in msg:
                chat_msg.file_info.file_name = msg["file_info"]["file_name"]
                chat_msg.file_info.file_id = msg["file_info"]["file_id"]
            messages.append(chat_msg)
        return chat_pb2.ChatHistoryResponse(messages=messages)
    
    # --- File Sharing RPCs ---
    def UploadFile(self, request_iterator, context):
        """Upload file (requires consensus for metadata)."""
        file_path = None
        file_name = None
        channel_id = None
        sender_id = None
        
        try:
            first_chunk = next(request_iterator)
            if not first_chunk.HasField("info"):
                context.abort(
                    grpc.StatusCode.INVALID_ARGUMENT,
                    "First chunk must be metadata."
                )
            
            info = first_chunk.info
            payload = self._validate_token(info.token)
            if not payload:
                context.abort(
                    grpc.StatusCode.UNAUTHENTICATED,
                    "Invalid token."
                )
            
            self._check_leader(context)
            
            sender_id = payload['username']
            file_name = os.path.basename(info.file_name)
            channel_id = info.channel_id
            
            file_id = f"{uuid.uuid4()}_{file_name}"
            file_path = self.storage.get_file_path(file_id)
            
            # Save file chunks
            for chunk in request_iterator:
                if not chunk.HasField("chunk"):
                    context.abort(
                        grpc.StatusCode.INVALID_ARGUMENT,
                        "Subsequent chunks must be data."
                    )
                self.storage.save_file_chunk(file_path, chunk.chunk)
            
            # Propose file metadata through Raft
            timestamp = datetime.now(timezone.utc).isoformat()
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
            try:
                success = loop.run_until_complete(
                    self.storage.post_file_message(
                        channel_id, sender_id, file_name, file_id, timestamp
                    )
                )
                
                if success:
                    # Broadcast to subscribers
                    file_message = chat_pb2.ChatMessage(
                        sender_id=sender_id,
                        channel_id=channel_id,
                        timestamp=timestamp,
                        file_info=chat_pb2.FileInfo(
                            file_name=file_name,
                            file_id=file_id
                        )
                    )
                    self._broadcast_message(channel_id, file_message)
                    
                    return chat_pb2.StatusResponse(
                        success=True,
                        message="File uploaded."
                    )
                else:
                    return chat_pb2.StatusResponse(
                        success=False,
                        message="Failed to replicate file metadata."
                    )
            finally:
                loop.close()
        
        except Exception as e:
            print(f"🔴 ERROR in UploadFile: {e}")
            context.abort(grpc.StatusCode.INTERNAL, "File upload failed.")
    
    def DownloadFile(self, request, context):
        """Download file (read-only)."""
        payload = self._validate_token(request.token)
        if not payload:
            context.abort(
                grpc.StatusCode.UNAUTHENTICATED,
                "Invalid token."
            )
        
        file_id = request.file_id
        file_path = self.storage.get_file_path(file_id)
        
        if not self.storage.file_exists(file_id):
            context.abort(grpc.StatusCode.NOT_FOUND, "File not found.")
        
        try:
            for chunk_data in self.storage.read_file_chunks(file_path):
                yield chat_pb2.FileChunk(chunk=chunk_data)
        except Exception as e:
            print(f"🔴 ERROR in DownloadFile: {e}")
            context.abort(grpc.StatusCode.INTERNAL, "File download failed.")
    
    # --- Admin RPCs ---
    def CreateGroup(self, request, context):
        """Create group (requires consensus)."""
        payload = self._validate_token(request.token)
        user_data = self.storage.get_user(payload['username']) if payload else None
        if not user_data or not user_data.get('is_admin'):
            context.abort(
                grpc.StatusCode.PERMISSION_DENIED,
                "Admin access required"
            )
        
        self._check_leader(context)
        
        group_id = request.group_name.lower().replace(" ", "-")
        if self.storage.get_group(group_id):
            return chat_pb2.StatusResponse(
                success=False,
                message="Group name already exists."
            )
        
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        try:
            success = loop.run_until_complete(
                self.storage.create_group(group_id, request.group_name)
            )
            if success:
                return chat_pb2.StatusResponse(
                    success=True,
                    message=f"Group '{request.group_name}' created."
                )
            else:
                return chat_pb2.StatusResponse(
                    success=False,
                    message="Failed to replicate group creation."
                )
        finally:
            loop.close()
    
    def AddUserToGroup(self, request, context):
        """Add user to group (requires consensus)."""
        payload = self._validate_token(request.token)
        user_data = self.storage.get_user(payload['username']) if payload else None
        if not user_data or not user_data.get('is_admin'):
            context.abort(
                grpc.StatusCode.PERMISSION_DENIED,
                "Admin access required"
            )
        
        self._check_leader(context)
        
        group = self.storage.get_group(request.group_id)
        user_to_add = self.storage.get_user(request.user_id)
        
        if not group:
            return chat_pb2.StatusResponse(
                success=False,
                message="Group not found."
            )
        if not user_to_add:
            return chat_pb2.StatusResponse(
                success=False,
                message="User not found."
            )
        
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        try:
            success = loop.run_until_complete(
                self.storage.add_user_to_group(request.group_id, request.user_id)
            )
            if success:
                return chat_pb2.StatusResponse(
                    success=True,
                    message=f"User '{request.user_id}' added to group."
                )
            else:
                return chat_pb2.StatusResponse(
                    success=False,
                    message="Failed to replicate operation."
                )
        finally:
            loop.close()
    
    def RemoveUserFromGroup(self, request, context):
        """Remove user from group (requires consensus)."""
        payload = self._validate_token(request.token)
        user_data = self.storage.get_user(payload['username']) if payload else None
        if not user_data or not user_data.get('is_admin'):
            context.abort(
                grpc.StatusCode.PERMISSION_DENIED,
                "Admin access required"
            )
        
        self._check_leader(context)
        
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        try:
            success = loop.run_until_complete(
                self.storage.remove_user_from_group(
                    request.group_id, request.user_id
                )
            )
            if success:
                return chat_pb2.StatusResponse(
                    success=True,
                    message=f"User '{request.user_id}' removed from group."
                )
            else:
                return chat_pb2.StatusResponse(
                    success=False,
                    message="Failed to replicate operation."
                )
        finally:
            loop.close()
    
    def GetAllGroups(self, request, context):
        """Get all groups (read-only)."""
        payload = self._validate_token(request.token)
        user_data = self.storage.get_user(payload['username']) if payload else None
        if not user_data or not user_data.get('is_admin'):
            context.abort(
                grpc.StatusCode.PERMISSION_DENIED,
                "Admin access required"
            )
        
        all_groups = self.storage.get_groups()
        group_list = [
            chat_pb2.Group(
                id=gid,
                name=gdata['name'],
                member_ids=gdata['members']
            )
            for gid, gdata in all_groups.items()
        ]
        return chat_pb2.GroupList(groups=group_list)


class LLMServiceImpl(llm_service_pb2_grpc.LLMServiceServicer):
    """Implements the dedicated LLMService for AI tasks."""
    
    def __init__(self, storage):
        self.storage = storage
        self.model = GEMINI_MODEL
    
    def _validate_token(self, token):
        try:
            return jwt.decode(token, JWT_SECRET, algorithms=["HS256"])
        except jwt.PyJWTError:
            return None
    
    def GetChatRecommendations(self, request, context):
        """Get chat recommendations (read-only, no consensus needed)."""
        if not GEMINI_ENABLED or not self.model:
            context.abort(
                grpc.StatusCode.UNAVAILABLE,
                "Gemini API not configured."
            )
        
        payload = self._validate_token(request.token)
        if not payload:
            context.abort(grpc.StatusCode.UNAUTHENTICATED, "Invalid token")
        
        history = self.storage.get_chat_history(request.channel_id)
        if not history:
            return llm_service_pb2.GetChatRecommendationsResponse(
                recommendations=["Hi!", "How are you?"]
            )
        
        formatted_history = "\n".join([
            f"{msg['sender_id']}: {msg['text']}"
            for msg in history[-10:] if 'text' in msg
        ])
        
        prompt = (
            f"This is a chat history:\n---\n{formatted_history}\n---\n"
            f"Based on this, suggest two very short, natural, and distinct "
            f"next messages for '{payload['username']}' to send. "
            "The suggestions should be simple conversation starters or continuations. "
            "Do not use markdown or quotes. Return only the two suggestions "
            "separated by a newline character."
        )
        
        try:
            response = self.model.generate_content(prompt)
            
            if not response.parts:
                return llm_service_pb2.GetChatRecommendationsResponse(
                    recommendations=["Sounds good.", "What's up?"]
                )
            
            suggestions = response.text.strip().split('\n')
            suggestions = [s.strip() for s in suggestions if s.strip()]
            if len(suggestions) < 2:
                suggestions.extend(["Sounds good.", "What's up?"])
            return llm_service_pb2.GetChatRecommendationsResponse(
                recommendations=suggestions[:2]
            )
        
        except Exception as e:
            print(f"🔴 ERROR: Gemini API call FAILED: {e}")
            context.abort(
                grpc.StatusCode.INTERNAL,
                f"Failed to get recommendations: {e}"
            )


def serve(node_id: str):
    """
    Start the Raft-enabled gRPC server.
    
    Args:
        node_id: The ID of this node (e.g., 'node1', 'node2', 'node3')
    """
    # Validate node ID
    validate_node_id(node_id)
    node_config = get_node_config(node_id)
    
    print("="*60)
    print(f"STARTING RAFT NODE: {node_id}")
    print("="*60)
    print_cluster_config()
    
    # Initialize storage
    storage_dir = get_storage_dir(node_id)
    files_dir = os.path.join(storage_dir, "files")
    storage_manager = StorageManager(storage_dir, files_dir)
    
    # Initialize Raft components
    state_machine = StateMachine(storage_manager)
    raft_node = RaftNode(node_id, state_machine)
    replicated_storage = ReplicatedStorage(storage_manager, raft_node)
    
    # Start Raft node
    raft_node.start()
    
    # Initialize services
    online_users = set()
    chat_service = ChatServiceImpl(replicated_storage, online_users, raft_node)
    llm_service = LLMServiceImpl(replicated_storage)
    raft_service = RaftServiceImpl(raft_node)
    
    # Create gRPC servers
    # Client-facing server (ChatService + LLMService)
    client_server = grpc.server(futures.ThreadPoolExecutor(max_workers=10))
    chat_pb2_grpc.add_ChatServiceServicer_to_server(chat_service, client_server)
    llm_service_pb2_grpc.add_LLMServiceServicer_to_server(llm_service, client_server)
    client_server.add_insecure_port(f"[::]:{node_config['client_port']}")
    
    # Raft peer-to-peer server (RaftService)
    raft_server = grpc.server(futures.ThreadPoolExecutor(max_workers=10))
    raft_pb2_grpc.add_RaftServiceServicer_to_server(raft_service, raft_server)
    raft_server.add_insecure_port(f"[::]:{node_config['raft_port']}")
    
    # Start servers
    client_server.start()
    raft_server.start()
    
    print(f"\n✅ Node {node_id} started successfully!")
    print(f"📡 Client port: {node_config['client_port']}")
    print(f"🔗 Raft port: {node_config['raft_port']}")
    print(f"🗄️  Storage: {os.path.abspath(storage_dir)}")
    print(f"\nPress Ctrl+C to stop the server.")
    print("="*60)
    
    try:
        client_server.wait_for_termination()
    except KeyboardInterrupt:
        print("\n\nShutting down server...")
        
        # Cleanup
        raft_node.stop()
        users_to_logout = list(online_users)
        for user in users_to_logout:
            online_users.remove(user)
        
        client_server.stop(0)
        raft_server.stop(0)
        
        print("Server stopped.")


def main():
    """Main entry point with argument parsing."""
    parser = argparse.ArgumentParser(
        description="Raft-enabled Chat Server"
    )
    parser.add_argument(
        '--node-id',
        type=str,
        required=True,
        choices=['node1', 'node2', 'node3'],
        help='ID of this node in the cluster'
    )
    
    args = parser.parse_args()
    
    try:
        serve(args.node_id)
    except Exception as e:
        print(f"🔴 FATAL ERROR: {e}")
        import traceback
        traceback.print_exc()


if __name__ == '__main__':
    main()