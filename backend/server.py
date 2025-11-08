import grpc
import jwt
import os
import json
import queue
import threading
import uuid
from concurrent import futures
from datetime import datetime, timezone
from werkzeug.security import generate_password_hash, check_password_hash
import google.generativeai as genai

# Add project root to the Python path
import sys
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

import protos.chat_pb2 as chat_pb2
import protos.chat_pb2_grpc as chat_pb2_grpc
import protos.llm_service_pb2 as llm_service_pb2
import protos.llm_service_pb2_grpc as llm_service_pb2_grpc


# --- Configuration ---
SERVER_NAME = "server1"
STORAGE_BASE_DIR = os.path.join(os.path.dirname(__file__), f"{SERVER_NAME}_storage")
FILES_DIR = os.path.join(STORAGE_BASE_DIR, "files") # For file sharing
JWT_SECRET = "a-super-secret-key-that-should-be-in-an-env-var"
# IMPORTANT: Replace with your actual Gemini API key
GEMINI_API_KEY = "AIzaSyAzPfdsgFQq29EbXzBlT7YL6BK3NV8rTag"
CHUNK_SIZE = 1024 * 1024 # 1MB

# Configure the Gemini API
if GEMINI_API_KEY == "YOUR_GEMINI_API_KEY_HERE":
    print("⚠️ WARNING: Gemini API key is not set. Recommendation feature will be disabled.")
    GEMINI_ENABLED = False
    GEMINI_MODEL = None
else:
    try:
        genai.configure(api_key=GEMINI_API_KEY)
        # Using a stable, widely available model
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
        self.files_dir = files_dir # Directory to store uploaded files
        self.users_file = os.path.join(base_dir, "users.json")
        self.groups_file = os.path.join(base_dir, "groups.json")
        self.lock = threading.Lock()
        self._initialize_storage()

    def _initialize_storage(self):
        os.makedirs(self.base_dir, exist_ok=True)
        os.makedirs(self.files_dir, exist_ok=True) # Create files directory
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
            if not os.path.exists(file_path): return {}
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
        # Sanitize channel_id to prevent path traversal
        safe_channel_id = os.path.basename(channel_id)
        channel_dir = os.path.join(self.base_dir, safe_channel_id)
        os.makedirs(channel_dir, exist_ok=True)
        return channel_dir

    def get_chat_history(self, channel_id):
        """Robustly reads chat history, handling different possible JSON formats."""
        history_file = os.path.join(self.get_channel_dir(channel_id), "history.json")
        data = self._read_json(history_file)
        
        if isinstance(data, dict):
            return data.get("messages", [])
        elif isinstance(data, list):
            return data
        
        return []

    def add_message_to_history(self, channel_id, message_pb):
        history_file = os.path.join(self.get_channel_dir(channel_id), "history.json")
        messages = self.get_chat_history(channel_id)
        
        # Convert the Protobuf message to a dictionary for JSON serialization
        message_dict = {
            "sender_id": message_pb.sender_id,
            "channel_id": message_pb.channel_id,
            "timestamp": message_pb.timestamp
        }
        
        # Add the correct content type (text or file)
        if message_pb.HasField("text"):
            message_dict["text"] = message_pb.text
        elif message_pb.HasField("file_info"):
            message_dict["file_info"] = {
                "file_name": message_pb.file_info.file_name,
                "file_id": message_pb.file_info.file_id
            }

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
        safe_file_id = os.path.basename(file_id) # Prevent path traversal
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
    """Implements the core ChatService RPCs."""
    def __init__(self, storage, online_users):
        self.storage = storage
        self.online_users = online_users
        self.subscriptions = {} # channel_id -> list of client streams (queues)
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
                    # Client's queue is full, they might be lagging
                    pass


    # --- Authentication RPCs ---
    def SignUp(self, request, context):
        users = self.storage.get_users()
        if request.username in users:
            return chat_pb2.StatusResponse(success=False, message="Username already exists.")
        
        hashed_password = generate_password_hash(request.password)
        new_user = {"hash": hashed_password, "is_admin": False}
        self.storage.save_user(request.username, new_user)
        return chat_pb2.StatusResponse(success=True, message="User created successfully.")

    def Login(self, request, context):
        user_data = self.storage.get_user(request.username)
        if not user_data or not check_password_hash(user_data.get('hash', ''), request.password):
            return chat_pb2.LoginResponse(success=False, message="Invalid credentials.")
        
        self.online_users.add(request.username)
        token = jwt.encode({"username": request.username}, JWT_SECRET, algorithm="HS256")
        return chat_pb2.LoginResponse(success=True, token=token)

    def Logout(self, request, context):
        payload = self._validate_token(request.token)
        if payload and payload['username'] in self.online_users:
            self.online_users.remove(payload['username'])
        return chat_pb2.StatusResponse(success=True)

    # --- Core Chat RPCs ---
    def GetUsers(self, request, context):
        if not self._validate_token(request.token):
            context.abort(grpc.StatusCode.UNAUTHENTICATED, "Invalid token")
        
        all_users = self.storage.get_users()
        user_list = [
            chat_pb2.User(id=username, name=username, online=(username in self.online_users))
            for username in all_users
        ]
        return chat_pb2.UserList(users=user_list)

    def GetMyGroups(self, request, context):
        payload = self._validate_token(request.token)
        if not payload:
            context.abort(grpc.StatusCode.UNAUTHENTICATED, "Invalid token")
        
        username = payload['username']
        all_groups = self.storage.get_groups()
        my_groups = [
            chat_pb2.Group(id=gid, name=gdata['name'], member_ids=gdata['members'])
            for gid, gdata in all_groups.items() if username in gdata['members']
        ]
        return chat_pb2.GroupList(groups=my_groups)
    
    def PostMessage(self, request, context):
        payload = self._validate_token(request.token)
        if not payload:
            context.abort(grpc.StatusCode.UNAUTHENTICATED, "Invalid token")

        message = chat_pb2.ChatMessage(
            sender_id=payload['username'],
            text=request.text, # This is a text-only message
            timestamp=datetime.now(timezone.utc).isoformat(),
            channel_id=request.channel_id
        )
        self.storage.add_message_to_history(request.channel_id, message)
        self._broadcast_message(request.channel_id, message)
        return chat_pb2.StatusResponse(success=True, message="Message sent.")

    def StreamMessages(self, request, context):
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
                    # This is normal, just means no new messages
                    continue
        except Exception:
            # Handle client disconnection gracefully
            pass
        finally:
            # Cleanup when client disconnects
            with self.subscriptions_lock:
                if channel_id in self.subscriptions and q in self.subscriptions[channel_id]:
                    self.subscriptions[channel_id].remove(q)

    def GetChatHistory(self, request, context):
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
        file_path = None
        file_name = None
        channel_id = None
        sender_id = None
        
        try:
            # First chunk must be metadata
            first_chunk = next(request_iterator)
            if not first_chunk.HasField("info"):
                context.abort(grpc.StatusCode.INVALID_ARGUMENT, "First chunk must be metadata.")
            
            info = first_chunk.info
            payload = self._validate_token(info.token)
            if not payload:
                context.abort(grpc.StatusCode.UNAUTHENTICATED, "Invalid token.")
            
            sender_id = payload['username']
            file_name = os.path.basename(info.file_name) # Sanitize filename
            channel_id = info.channel_id
            
            # Generate a unique file ID to store on server
            file_id = f"{uuid.uuid4()}_{file_name}"
            file_path = self.storage.get_file_path(file_id)
            
            # Receive the rest of the file chunks
            for chunk in request_iterator:
                if not chunk.HasField("chunk"):
                    context.abort(grpc.StatusCode.INVALID_ARGUMENT, "Subsequent chunks must be data.")
                self.storage.save_file_chunk(file_path, chunk.chunk)
            
            # Now that file is uploaded, create and broadcast the file message
            file_message = chat_pb2.ChatMessage(
                sender_id=sender_id,
                channel_id=channel_id,
                timestamp=datetime.now(timezone.utc).isoformat(),
                file_info=chat_pb2.FileInfo(file_name=file_name, file_id=file_id)
            )
            self.storage.add_message_to_history(channel_id, file_message)
            self._broadcast_message(channel_id, file_message)

            return chat_pb2.StatusResponse(success=True, message="File uploaded.")

        except Exception as e:
            print(f"🔴 ERROR in UploadFile: {e}")
            context.abort(grpc.StatusCode.INTERNAL, "File upload failed.")

    def DownloadFile(self, request, context):
        payload = self._validate_token(request.token)
        if not payload:
            context.abort(grpc.StatusCode.UNAUTHENTICATED, "Invalid token.")

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
        payload = self._validate_token(request.token)
        user_data = self.storage.get_user(payload['username']) if payload else None
        if not user_data or not user_data.get('is_admin'):
            context.abort(grpc.StatusCode.PERMISSION_DENIED, "Admin access required")
        
        group_id = request.group_name.lower().replace(" ", "-")
        if self.storage.get_group(group_id):
            return chat_pb2.StatusResponse(success=False, message="Group name already exists.")
            
        new_group = {"name": request.group_name, "members": []}
        self.storage.save_group(group_id, new_group)
        return chat_pb2.StatusResponse(success=True, message=f"Group '{request.group_name}' created.")

    def AddUserToGroup(self, request, context):
        payload = self._validate_token(request.token)
        user_data = self.storage.get_user(payload['username']) if payload else None
        if not user_data or not user_data.get('is_admin'):
            context.abort(grpc.StatusCode.PERMISSION_DENIED, "Admin access required")

        group = self.storage.get_group(request.group_id)
        user_to_add = self.storage.get_user(request.user_id)

        if not group:
            return chat_pb2.StatusResponse(success=False, message="Group not found.")
        if not user_to_add:
            return chat_pb2.StatusResponse(success=False, message="User not found.")
        if request.user_id not in group['members']:
            group['members'].append(request.user_id)
            self.storage.save_group(request.group_id, group)
        
        return chat_pb2.StatusResponse(success=True, message=f"User '{request.user_id}' added to group.")

    def RemoveUserFromGroup(self, request, context):
        payload = self._validate_token(request.token)
        user_data = self.storage.get_user(payload['username']) if payload else None
        if not user_data or not user_data.get('is_admin'):
            context.abort(grpc.StatusCode.PERMISSION_DENIED, "Admin access required")
            
        group = self.storage.get_group(request.group_id)
        if not group:
            return chat_pb2.StatusResponse(success=False, message="Group not found.")
        if request.user_id in group['members']:
            group['members'].remove(request.user_id)
            self.storage.save_group(request.group_id, group)
            return chat_pb2.StatusResponse(success=True, message=f"User '{request.user_id}' removed from group.")
        else:
            return chat_pb2.StatusResponse(success=False, message="User not in group.")

    def GetAllGroups(self, request, context):
        payload = self._validate_token(request.token)
        user_data = self.storage.get_user(payload['username']) if payload else None
        if not user_data or not user_data.get('is_admin'):
            context.abort(grpc.StatusCode.PERMISSION_DENIED, "Admin access required")
            
        all_groups = self.storage.get_groups()
        group_list = [
            chat_pb2.Group(id=gid, name=gdata['name'], member_ids=gdata['members'])
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
        if not GEMINI_ENABLED or not self.model:
            context.abort(grpc.StatusCode.UNAVAILABLE, "Gemini API not configured or model failed to initialize on server.")
        
        payload = self._validate_token(request.token)
        if not payload:
            context.abort(grpc.StatusCode.UNAUTHENTICATED, "Invalid token")
        
        history = self.storage.get_chat_history(request.channel_id)
        if not history:
            return llm_service_pb2.GetChatRecommendationsResponse(recommendations=["Hi!", "How are you?"])

        # Format chat history, only including text messages for the prompt
        formatted_history = "\n".join([
            f"{msg['sender_id']}: {msg['text']}" 
            for msg in history[-10:] if 'text' in msg
        ])
        
        prompt = (f"This is a chat history:\n---\n{formatted_history}\n---\n"
                  f"Based on this, suggest two very short, natural, and distinct next messages for '{payload['username']}' to send. "
                  "The suggestions should be simple conversation starters or continuations. "
                  "Do not use markdown or quotes. Return only the two suggestions separated by a newline character.")
        
        print("\n⏳ [SERVER] Attempting to generate recommendations from Gemini...")
        print(f"   [SERVER] Prompt being sent: \n---\n{prompt}\n---")

        try:
            response = self.model.generate_content(prompt)
            
            # Check for safety blocks
            if not response.parts:
                if response.prompt_feedback and response.prompt_feedback.block_reason:
                    print(f"🔴 WARNING: Gemini API call was BLOCKED. Reason: {response.prompt_feedback.block_reason}")
                    context.abort(grpc.StatusCode.INTERNAL, "Recommendation blocked by safety filter.")
                else:
                    print("🔴 ERROR: Gemini API call returned no parts and no block reason.")
                    context.abort(grpc.StatusCode.INTERNAL, "Empty response from LLM.")
                return llm_service_pb2.GetChatRecommendationsResponse()

            print("✅ [SERVER] Received response from Gemini.")
            suggestions = response.text.strip().split('\n')
            suggestions = [s.strip() for s in suggestions if s.strip()]
            if len(suggestions) < 2:
                suggestions.extend(["Sounds good.", "What's up?"])
            return llm_service_pb2.GetChatRecommendationsResponse(recommendations=suggestions[:2])
        
        except Exception as e:
            print(f"🔴 ERROR: Gemini API call FAILED with an exception: {e}")
            context.abort(grpc.StatusCode.INTERNAL, f"Failed to get recommendations from LLM: {e}")


def serve():
    """Starts the gRPC server and registers both services."""
    storage = StorageManager(STORAGE_BASE_DIR, FILES_DIR)
    online_users = set()
    server = grpc.server(futures.ThreadPoolExecutor(max_workers=10))
    
    chat_pb2_grpc.add_ChatServiceServicer_to_server(ChatServiceImpl(storage, online_users), server)
    llm_service_pb2_grpc.add_LLMServiceServicer_to_server(LLMServiceImpl(storage), server)

    server.add_insecure_port('[::]:50051')
    server.start()
    print(f"✅ gRPC server started on port 50051, hosting ChatService and LLMService...")
    print(f"🗄️  Data will be stored in: {os.path.abspath(STORAGE_BASE_DIR)}")
    try:
        server.wait_for_termination()
    except KeyboardInterrupt:
        print("\nShutting down server...")
        users_to_logout = list(online_users)
        for user in users_to_logout:
            online_users.remove(user)
        server.stop(0)

if __name__ == '__main__':
    serve()