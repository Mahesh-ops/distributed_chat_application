import grpc
import jwt
import os
import json
import queue
import threading
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
JWT_SECRET = "a-super-secret-key-that-should-be-in-an-env-var"
# IMPORTANT: Replace with your actual Gemini API key
GEMINI_API_KEY = "hidden_for_privacy"

# Configure the Gemini API
if GEMINI_API_KEY == "YOUR_GEMINI_API_KEY_HERE":
    print("⚠️ WARNING: Gemini API key is not set. Recommendation feature will be disabled.")
    GEMINI_ENABLED = False
else:
    try:
        genai.configure(api_key=GEMINI_API_KEY)
        GEMINI_ENABLED = True
    except Exception as e:
        print(f"🔴 ERROR: Failed to configure Gemini AI: {e}")
        GEMINI_ENABLED = False


class StorageManager:
    """Handles all file I/O for the server's persistent storage."""
    def __init__(self, base_dir):
        self.base_dir = base_dir
        self.users_file = os.path.join(base_dir, "users.json")
        self.groups_file = os.path.join(base_dir, "groups.json")
        self.lock = threading.Lock()
        self._initialize_storage()

    def _initialize_storage(self):
        os.makedirs(self.base_dir, exist_ok=True)
        if not os.path.exists(self.users_file):
            # Create default admin user
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
            with open(file_path, 'r') as f:
                try:
                    return json.load(f)
                except json.JSONDecodeError:
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
        channel_dir = os.path.join(self.base_dir, channel_id)
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
        
        message_dict = {
            "sender_id": message_pb.sender_id,
            "text": message_pb.text,
            "timestamp": message_pb.timestamp,
            "channel_id": message_pb.channel_id
        }
        messages.append(message_dict)
        
        self._write_json(history_file, {"messages": messages})


class ChatServiceImpl(chat_pb2_grpc.ChatServiceServicer):
    """Implements ChatService RPCs with correct streaming and broadcast behavior."""

    def __init__(self, storage, online_users):
        self.storage = storage
        self.online_users = online_users
        # Each channel_id maps to a list of tuples: (username, queue)
        self.subscriptions = {}
        self.subscriptions_lock = threading.Lock()

    # --- Helper Methods ---
    def _validate_token(self, token):
        try:
            return jwt.decode(token, JWT_SECRET, algorithms=["HS256"])
        except jwt.PyJWTError:
            return None

    def _broadcast_message(self, channel_id, message_pb):
        """Broadcast message to all active subscribers except the sender."""
        with self.subscriptions_lock:
            subscribers = self.subscriptions.get(channel_id, [])
            for (username, q) in subscribers:
                if username == message_pb.sender_id:
                    continue  # ✅ Skip echo to sender
                try:
                    q.put_nowait(message_pb)
                except queue.Full:
                    pass

    # --- Authentication RPCs ---
    def SignUp(self, request, context):
        users = self.storage.get_users()
        if request.username in users:
            return chat_pb2.StatusResponse(success=False, message="Username already exists.")
        hashed_password = generate_password_hash(request.password)
        self.storage.save_user(request.username, {"hash": hashed_password, "is_admin": False})
        return chat_pb2.StatusResponse(success=True, message="User created successfully.")

    def Login(self, request, context):
        user_data = self.storage.get_user(request.username)
        if not user_data or not check_password_hash(user_data.get("hash", ""), request.password):
            return chat_pb2.LoginResponse(success=False, message="Invalid credentials.")
        self.online_users.add(request.username)
        token = jwt.encode({"username": request.username}, JWT_SECRET, algorithm="HS256")
        return chat_pb2.LoginResponse(success=True, token=token)

    def Logout(self, request, context):
        payload = self._validate_token(request.token)
        if payload and payload["username"] in self.online_users:
            self.online_users.remove(payload["username"])
        return chat_pb2.StatusResponse(success=True)

    # --- Core Chat RPCs ---
    def GetUsers(self, request, context):
        if not self._validate_token(request.token):
            context.abort(grpc.StatusCode.UNAUTHENTICATED, "Invalid token")
        all_users = self.storage.get_users()
        user_list = [
            chat_pb2.User(id=u, name=u, online=(u in self.online_users))
            for u in all_users
        ]
        return chat_pb2.UserList(users=user_list)

    def PostMessage(self, request, context):
        payload = self._validate_token(request.token)
        if not payload:
            context.abort(grpc.StatusCode.UNAUTHENTICATED, "Invalid token")

        message = chat_pb2.ChatMessage(
            sender_id=payload["username"],
            text=request.text,
            timestamp=datetime.now(timezone.utc).isoformat(),
            channel_id=request.channel_id,
        )

        # Save + broadcast
        self.storage.add_message_to_history(request.channel_id, message)
        self._broadcast_message(request.channel_id, message)
        return chat_pb2.StatusResponse(success=True, message="Message sent.")

    def StreamMessages(self, request, context):
        """Server streaming: continuously yields messages for a channel."""
        payload = self._validate_token(request.token)
        if not payload:
            context.abort(grpc.StatusCode.UNAUTHENTICATED, "Invalid token")

        username = payload["username"]
        channel_id = request.channel_id
        q = queue.Queue(maxsize=100)

        # Register this subscriber
        with self.subscriptions_lock:
            if channel_id not in self.subscriptions:
                self.subscriptions[channel_id] = []
            self.subscriptions[channel_id].append((username, q))

        try:
            # Send recent history first
            history = self.storage.get_chat_history(channel_id)
            for msg in history[-10:]:
                yield chat_pb2.ChatMessage(**msg)

            # Now stream live messages
            while context.is_active():
                try:
                    msg = q.get(timeout=1)
                    yield msg
                except queue.Empty:
                    continue
        finally:
            # Clean up subscriber on disconnect
            with self.subscriptions_lock:
                if channel_id in self.subscriptions:
                    self.subscriptions[channel_id] = [
                        sub for sub in self.subscriptions[channel_id] if sub[0] != username
                    ]

    def GetChatHistory(self, request, context):
        if not self._validate_token(request.token):
            context.abort(grpc.StatusCode.UNAUTHENTICATED, "Invalid token")
        history = self.storage.get_chat_history(request.channel_id)
        messages = [chat_pb2.ChatMessage(**m) for m in history]
        return chat_pb2.ChatHistoryResponse(messages=messages)

    # --- Admin RPCs ---
    def CreateGroup(self, request, context):
        payload = self._validate_token(request.token)
        user_data = self.storage.get_user(payload["username"]) if payload else None
        if not user_data or not user_data.get("is_admin"):
            context.abort(grpc.StatusCode.PERMISSION_DENIED, "Admin access required")
        group_id = request.group_name.lower().replace(" ", "-")
        if self.storage.get_group(group_id):
            return chat_pb2.StatusResponse(success=False, message="Group already exists.")
        self.storage.save_group(group_id, {"name": request.group_name, "members": []})
        return chat_pb2.StatusResponse(success=True, message=f"Group '{request.group_name}' created.")

    def AddUserToGroup(self, request, context):
        payload = self._validate_token(request.token)
        user_data = self.storage.get_user(payload["username"]) if payload else None
        if not user_data or not user_data.get("is_admin"):
            context.abort(grpc.StatusCode.PERMISSION_DENIED, "Admin access required")

        group = self.storage.get_group(request.group_id)
        user_to_add = self.storage.get_user(request.user_id)
        if not group:
            return chat_pb2.StatusResponse(success=False, message="Group not found.")
        if not user_to_add:
            return chat_pb2.StatusResponse(success=False, message="User not found.")
        if request.user_id not in group["members"]:
            group["members"].append(request.user_id)
            self.storage.save_group(request.group_id, group)
        return chat_pb2.StatusResponse(success=True, message=f"User '{request.user_id}' added.")

    def RemoveUserFromGroup(self, request, context):
        payload = self._validate_token(request.token)
        user_data = self.storage.get_user(payload["username"]) if payload else None
        if not user_data or not user_data.get("is_admin"):
            context.abort(grpc.StatusCode.PERMISSION_DENIED, "Admin access required")

        group = self.storage.get_group(request.group_id)
        if not group:
            return chat_pb2.StatusResponse(success=False, message="Group not found.")
        if request.user_id in group["members"]:
            group["members"].remove(request.user_id)
            self.storage.save_group(request.group_id, group)
            return chat_pb2.StatusResponse(success=True, message=f"User '{request.user_id}' removed.")
        return chat_pb2.StatusResponse(success=False, message="User not in group.")
    
    def GetMyGroups(self, request, context):
        payload = self._validate_token(request.token)
        if not payload:
            context.abort(grpc.StatusCode.UNAUTHENTICATED, "Invalid token")

        username = payload["username"]
        all_groups = self.storage.get_groups()
        my_groups = [
            chat_pb2.Group(
                id=gid,
                name=gdata["name"],
                member_ids=gdata.get("members", []),
            )
            for gid, gdata in all_groups.items()
            if username in gdata.get("members", [])
        ]
        return chat_pb2.GroupList(groups=my_groups)


    def GetAllGroups(self, request, context):
        payload = self._validate_token(request.token)
        user_data = self.storage.get_user(payload["username"]) if payload else None
        if not user_data or not user_data.get("is_admin"):
            context.abort(grpc.StatusCode.PERMISSION_DENIED, "Admin access required")
        all_groups = self.storage.get_groups()
        group_list = [
            chat_pb2.Group(id=gid, name=g["name"], member_ids=g["members"])
            for gid, g in all_groups.items()
        ]
        return chat_pb2.GroupList(groups=group_list)

class LLMServiceImpl(llm_service_pb2_grpc.LLMServiceServicer):
    """Implements the dedicated LLMService for AI tasks."""
    def __init__(self, storage):
        self.storage = storage
        self.model = None
        if GEMINI_ENABLED:
            try:
                # UPDATED: Changed to the requested working model name
                self.model = genai.GenerativeModel('gemini-2.5-flash-lite')
                print("✅ Gemini Model initialized successfully.")
            except Exception as e:
                print(f"🔴 ERROR: Failed to initialize Gemini Model: {e}")
                
    
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

        formatted_history = "\n".join([f"{msg['sender_id']}: {msg['text']}" for msg in history[-10:]])
        prompt = (f"This is a chat history:\n---\n{formatted_history}\n---\n"
                  f"Based on this, suggest two very short, natural, and distinct next messages for '{payload['username']}' to send. "
                  "The suggestions should be simple conversation starters or continuations. "
                  "Do not use markdown or quotes. Return only the two suggestions separated by a newline character.")
        
        print("\n⏳ [SERVER] Attempting to generate recommendations from Gemini...")
        print(f"   [SERVER] Prompt being sent: \n---\n{prompt}\n---")

        try:
            response = self.model.generate_content(prompt)
            
            if not response.parts:
                print(f"🔴 WARNING: Gemini API call was BLOCKED. Reason: {response.prompt_feedback}")
                context.abort(grpc.StatusCode.INTERNAL, "Recommendation blocked by safety filter.")
                return llm_service_pb2.GetChatRecommendationsResponse()

            print("✅ [SERVER] Received response from Gemini.")
            suggestions = response.text.strip().split('\n')
            suggestions = [s.strip() for s in suggestions if s.strip()]
            if len(suggestions) < 2:
                suggestions.extend(["Sounds good.", "What's up?"])
            return llm_service_pb2.GetChatRecommendationsResponse(recommendations=suggestions[:2])
        
        except Exception as e:
            print(f"🔴 ERROR: Gemini API call FAILED with an exception: {e}")
            context.abort(grpc.StatusCode.INTERNAL, "Failed to get recommendations from LLM.")


def serve():
    """Starts the gRPC server and registers both services."""
    storage = StorageManager(STORAGE_BASE_DIR)
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

