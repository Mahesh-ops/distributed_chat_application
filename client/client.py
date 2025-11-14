import grpc
import threading
import sys
import os
import time
import queue
import random 
from datetime import datetime
from prompt_toolkit import prompt
from prompt_toolkit.patch_stdout import patch_stdout
import colorama
colorama.init(convert=True, autoreset=True)

# Allow relative imports from project root
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

import protos.chat_pb2 as chat_pb2
import protos.chat_pb2_grpc as chat_pb2_grpc
import protos.llm_service_pb2 as llm_service_pb2
import protos.llm_service_pb2_grpc as llm_service_pb2_grpc
SERVER_ADDRESSES = [
    "localhost:50051",
    "localhost:50052",
    "localhost:50053"
]
CHUNK_SIZE = 1024 * 1024 # 1MB
DOWNLOADS_DIR = os.path.join(os.path.dirname(__file__), "downloads")

def safe_input(prompt_text: str) -> str:
    """Thread-safe user input using prompt_toolkit."""
    try:
        # patch_stdout ensures that background prints don't mess up the input line
        with patch_stdout():
            return prompt(prompt_text)
    except (EOFError, KeyboardInterrupt):
        return "/exit" # Treat Ctrl+C/Ctrl+D as an exit command


class Client:
    """Terminal-based gRPC chat client with file sharing and recommendations."""

    def __init__(self, initial_node='localhost:50051'):
        """
        Initialize client with cluster awareness.
        
        Args:
            initial_node: Initial server to connect to (format: 'host:port')
        """
        self.cluster_nodes = [
            'localhost:50051',  # node1
            'localhost:50052',  # node2
            'localhost:50053',  # node3
        ]
        self.current_node = initial_node
        self.channel = grpc.insecure_channel(self.current_node)
        self.chat_stub = chat_pb2_grpc.ChatServiceStub(self.channel)
        self.llm_stub = llm_service_pb2_grpc.LLMServiceStub(self.channel)
        self.token = None
        self.username = None
        self.current_channel = None
        self.in_chat_session = False
        self.message_queue = queue.Queue()
        
        # NEW: Create client-specific download directory
        # This will be set when user logs in
        self.downloads_dir = None
        
        self.chat_display_lock = threading.Lock()
        self.current_recommendations = []
        self.recommendation_lock = threading.Lock()


    def _reconnect_to_leader(self, leader_address):
        """
        Reconnect to a new leader node.
        
        Args:
            leader_address: Address of the leader (format: 'host:port')
        """
        print(f"\n🔄 Redirecting to leader at {leader_address}...")
        
        # Close old channel
        if self.channel:
            self.channel.close()
        
        # Connect to new node
        self.current_node = leader_address
        self.channel = grpc.insecure_channel(leader_address)
        self.chat_stub = chat_pb2_grpc.ChatServiceStub(self.channel)
        self.llm_stub = llm_service_pb2_grpc.LLMServiceStub(self.channel)
        
        print(f"✅ Connected to {leader_address}")
    

    def _find_available_node(self):
        """
        Try to find an available node in the cluster.
        Returns True if found, False otherwise.
        """
        print("\n🔍 Searching for available node...")
        
        for node_addr in self.cluster_nodes:
            if node_addr == self.current_node:
                continue
            
            try:
                print(f"   Trying {node_addr}...")
                test_channel = grpc.insecure_channel(node_addr)
                test_stub = chat_pb2_grpc.ChatServiceStub(test_channel)
                
                # Try a simple RPC to check if node is up
                test_stub.GetUsers(
                    chat_pb2.GetUsersRequest(token=self.token),
                    timeout=2.0
                )
                
                # Node is up, switch to it
                self.channel.close()
                self.current_node = node_addr
                self.channel = test_channel
                self.chat_stub = test_stub
                self.llm_stub = llm_service_pb2_grpc.LLMServiceStub(test_channel)
                
                print(f"✅ Connected to {node_addr}")
                return True
            
            except grpc.RpcError:
                continue
        
        print("❌ No available nodes found")
        return False


    def _call_with_retry(self, rpc_method, request, max_retries=3):
        """
        Call an RPC method with automatic retry on leader change.
        
        Args:
            rpc_method: The RPC method to call
            request: The request object
            max_retries: Maximum number of retry attempts
            
        Returns:
            The RPC response
            
        Raises:
            Exception if all retries fail
        """
        for attempt in range(max_retries):
            try:
                return rpc_method(request)
            
            except grpc.RpcError as e:
                error_details = e.details()
                
                # Check if error is a leader redirect
                if "NOT_LEADER" in error_details:
                    # Parse leader address from error
                    parts = error_details.split(":")
                    if len(parts) >= 3:
                        leader_addr = ":".join(parts[1:])
                        self._reconnect_to_leader(leader_addr)
                        continue
                
                # Check if no leader is elected
                elif "NO_LEADER" in error_details:
                    print("\n⚠️  No leader elected yet, waiting...")
                    time.sleep(1)
                    # Try to find an available node
                    if self._find_available_node():
                        continue
                
                # Check if node is unavailable
                elif e.code() == grpc.StatusCode.UNAVAILABLE:
                    print(f"\n⚠️  Node {self.current_node} unavailable")
                    if self._find_available_node():
                        continue
                
                # Other errors, re-raise
                raise
        
        raise Exception("Failed after maximum retries")


    # -------------------------------
    # BACKGROUND MESSAGE STREAMING
    # -------------------------------
    def _listen_for_messages(self):
        """
        Robust listener that properly follows leader hints.
        """
        # Ensure we have the attributes used below
        if not hasattr(self, "chat_display_lock"):
            self.chat_display_lock = threading.RLock()
        if not hasattr(self, "message_queue"):
            self.message_queue = queue.Queue()

        # Give any previous reconnect a moment to finish
        time.sleep(0.2)

        # Single-pass attempt - if fails, connect() will start new thread
        try:
            # Check if we're still in a chat session
            if not getattr(self, "in_chat_session", False):
                return

            # Check if stub is available
            if self.chat_stub is None:
                with self.chat_display_lock:
                    print(f"⚠️  Chat stub not ready")
                return

            stream_request = chat_pb2.StreamRequest(
                token=self.token,
                channel_id=self.current_channel
            )

            # Blocking iterate over the server stream
            for message in self.chat_stub.StreamMessages(stream_request):
                # Check if session ended
                if not getattr(self, "in_chat_session", False):
                    break
                
                # Push incoming message to queue (only from others)
                if message.sender_id != self.username:
                    self.message_queue.put(message)

            # If we exit the for-loop normally (server closed stream)
            if getattr(self, "in_chat_session", False):
                with self.chat_display_lock:
                    print(f"{colorama.Fore.YELLOW}⚠️  Stream ended by server{colorama.Style.RESET_ALL}")
                
                # Trigger reconnection to any available node
                try:
                    self.safe_reconnect(is_retry=True, reason="stream ended")
                except Exception as e:
                    with self.chat_display_lock:
                        print(f"⚠️  Reconnect failed: {e}")

        except grpc.RpcError as e:
            # Only handle errors if still in chat session
            if not getattr(self, "in_chat_session", False):
                with self.chat_display_lock:
                    print(f"🧹 Listener exiting (session ended)")
                return

            code = e.code() if hasattr(e, "code") else None
            details = e.details() if hasattr(e, "details") else str(e)

            # CANCELLED - just exit, reconnection will be handled elsewhere
            if code == grpc.StatusCode.CANCELLED:
                with self.chat_display_lock:
                    print(f"⚠️  Stream cancelled and Reconnected successfully.✅")
                return

            # ✅ FIX: Extract leader hint from error details
            leader_hint = None
            
            # Check for NOT_LEADER error with leader address
            if "NOT_LEADER:" in (details or ""):
                hint = details.split("NOT_LEADER:")[-1].strip()
                if hint and hint.startswith("localhost") and ":" in hint:
                    leader_hint = hint
            
            # Check for FAILED_PRECONDITION (another format for NOT_LEADER)
            elif code == grpc.StatusCode.FAILED_PRECONDITION and "NOT_LEADER" in (details or ""):
                # Try to extract from format like "NOT_LEADER:localhost:50052"
                parts = details.split("NOT_LEADER:")
                if len(parts) > 1:
                    hint = parts[1].strip()
                    if hint.startswith("localhost") and ":" in hint:
                        leader_hint = hint

            # Display error with extracted info
            # with self.chat_display_lock:
            #     if leader_hint:
            #         print(f"\n{colorama.Fore.YELLOW}⚠️  Stream error: {code or 'Unknown'}. "
            #             f"Leader is at {leader_hint}{colorama.Style.RESET_ALL}")
            #     else:
            #         print(f"\n{colorama.Fore.YELLOW}⚠️  Stream error: {code or 'Unknown'}. "
            #             f"Reconnecting...{colorama.Style.RESET_ALL}")

            # Trigger reconnection with leader hint if available
            try:
                if leader_hint:
                    # ✅ Connect directly to the leader
                    self.safe_reconnect(target_address=leader_hint, reason=f"leader at {leader_hint}")
                else:
                    # No hint, try random node
                    self.safe_reconnect(is_retry=True, reason="stream error")
            
            except Exception as reconnect_exc:
                with self.chat_display_lock:
                    print(f"⚠️  Reconnect failed: {reconnect_exc}")

        except Exception as e:
            # Unexpected error
            if not getattr(self, "in_chat_session", False):
                return

            with self.chat_display_lock:
                print(f"\n⚠️  Unexpected error: {e}")

            # Trigger reconnection to random node
            try:
                self.safe_reconnect(is_retry=True, reason="unexpected error")
            except Exception as reconnect_exc:
                with self.chat_display_lock:
                    print(f"⚠️  Reconnect failed: {reconnect_exc}")

        # finally:
        #     # Always exit cleanly
        #     with self.chat_display_lock:
        #         print(f"🧹 Listener thread exiting")




    def _process_message_queue(self):
        """(Thread 2) Processes incoming messages from the queue one by one."""
        while self.in_chat_session:
            try:
                # Wait for a message to arrive
                msg = self.message_queue.get(timeout=1)
                
                # A message has arrived. Acquire the lock to print it
                # and then show new recommendations.
                with self.chat_display_lock:
                    self._print_message(msg, is_history=False)
                    # After printing the message, fetch and display new recommendations
                    self._display_recommendations() 
                
                self.message_queue.task_done()
            except queue.Empty:
                # No messages? No problem. Loop continues.
                continue

    # -------------------------------
    # AUTHENTICATION
    # -------------------------------
    def signup(self):
        username = safe_input("Enter new username: ").strip()
        password = safe_input("Enter new password: ").strip()
        if not username or not password:
            print("Username and password cannot be empty.")
            return
        
        try:
            request = chat_pb2.SignUpRequest(username=username, password=password)
            response = self._call_with_retry(self.chat_stub.SignUp, request)
            print(f"Server: {response.message}")
        except Exception as e:
            print(f"❌ Signup failed: {str(e)}")

    def login(self):
        username = safe_input("Enter username: ").strip()
        password = safe_input("Enter password: ").strip()
        response = self.chat_stub.Login(chat_pb2.LoginRequest(username=username, password=password))
        if response.success:
            self.token = response.token
            self.username = username
            
            # NEW: Create user-specific download directory
            self.downloads_dir = os.path.join(
                os.path.dirname(__file__), 
                f"{username}_downloads"
            )
            os.makedirs(self.downloads_dir, exist_ok=True)
            
            print(f"✅ Login successful.")
            print(f"📂 Downloads will be saved to: {self.downloads_dir}")
            return True
        else:
            print(f"❌ Login failed: {response.message}")
            return False

    def logout(self):
        if self.token:
            self.chat_stub.Logout(chat_pb2.LogoutRequest(token=self.token))
        self.token = None
        self.username = None
        print("🚪 You have been logged out.")

    # -------------------------------
    # GEMINI RECOMMENDATIONS
    # -------------------------------
    def _display_recommendations(self):
        """
        Fetches, prints, and updates the current recommendations safely.
        Prevents overlapping refreshes that cause wrong option selection.
        """
        if not hasattr(self, "recommendation_lock"):
            self.recommendation_lock = threading.Lock()

        if not self.recommendation_lock.acquire(blocking=False):
            return  # Skip if another thread is refreshing

        try:
            print("\nFetching message recommendations...")
            reco_response = self.llm_stub.GetChatRecommendations(
                llm_service_pb2.GetChatRecommendationsRequest(
                    token=self.token, channel_id=self.current_channel
                )
            )

            if not reco_response.recommendations:
                # Keep old recommendations if fetch fails
                return

            self.current_recommendations = list(reco_response.recommendations)

            print("\n--- Recommended Messages ---")
            for i, r in enumerate(reco_response.recommendations, 1):
                print(f"{i}: \"{r}\"")
            print("3: Type my own message")
            print("4: Share a file")
            print("--------------------------")

        except grpc.RpcError as e:
            print(f"⚠️  Could not fetch recommendations: {e.details()}")
            print("\n--- Options ---")
            print("1. Type my own message")
            print("2. Share a file")
            print("---------------")
        finally:
            self.recommendation_lock.release()


    # -------------------------------
    # FILE SHARING
    # -------------------------------
    def _upload_file(self):
        """
        Handles the client-side file upload process.
        NOTE: This function MUST be called from a thread that already
        holds the 'chat_display_lock'.
        """
        file_path = safe_input("Enter the full path to the file you want to share: ").strip()
        
        if not os.path.exists(file_path):
            print(f"❌ Error: File not found at '{file_path}'")
            return
        
        file_name = os.path.basename(file_path)
        print(f"Uploading '{file_name}'...")

        def file_chunk_generator():
            # 1. Send metadata first
            yield chat_pb2.FileChunk(info=chat_pb2.FileUploadInfo(
                token=self.token,
                channel_id=self.current_channel,
                file_name=file_name
            ))
            
            # 2. Send file data in chunks
            with open(file_path, 'rb') as f:
                while True:
                    chunk = f.read(CHUNK_SIZE)
                    if not chunk:
                        break
                    yield chat_pb2.FileChunk(chunk=chunk)

        try:
            response = self.chat_stub.UploadFile(file_chunk_generator())
            if response.success:
                print(f"✅ File '{file_name}' sent.")
            else:
                print(f"❌ Server failed to upload file: {response.message}")
        except grpc.RpcError as e:
            print(f"❌ File upload failed: {e.details()}")

    def _download_file(self, file_id, file_name):
        """Handles the client-side file download process."""
        print(f"\n⬇️  Downloading '{file_name}'...")
        
        # Use user-specific download directory
        if not self.downloads_dir:
            # Fallback if somehow called before login
            self.downloads_dir = os.path.join(os.path.dirname(__file__), "downloads")
            os.makedirs(self.downloads_dir, exist_ok=True)
        
        # Sanitize filename to prevent path issues
        safe_file_name = os.path.basename(file_name)
        local_file_path = os.path.join(self.downloads_dir, f"{file_id}_{safe_file_name}")
        
        try:
            request = chat_pb2.DownloadRequest(token=self.token, file_id=file_id)
            chunks = self.chat_stub.DownloadFile(request)
            
            with open(local_file_path, 'wb') as f:
                for chunk in chunks:
                    f.write(chunk.chunk)
            
            print(f"✅ File saved to: {local_file_path}")
            return local_file_path
        except grpc.RpcError as e:
            print(f"❌ Download failed for '{file_name}': {e.details()}")
            return None
        except Exception as e:
            print(f"❌ An error occurred while saving the file: {e}")
            return None
    # -------------------------------
    # CHAT SESSION
    # -------------------------------
    def _print_message(self, msg, is_history=False):
        """
        Prints a single chat message (text or file) to the console.
        NOTE: This function MUST be called from a thread that already
        holds the 'chat_display_lock'.
        """
        sender = "You" if msg.sender_id == self.username else msg.sender_id
        try:
            dt_object = datetime.fromisoformat(msg.timestamp.replace('Z', '+00:00'))
            timestamp = dt_object.strftime('%H:%M:%S')
        except (ValueError, AttributeError):
            timestamp = "??:??:??"
        
        # Check message type
        if msg.HasField("text"):
            print(f"[{timestamp}] {sender}> {msg.text}")
        elif msg.HasField("file_info"):
            file_info = msg.file_info
            print(f"[{timestamp}] {sender}> 📎 sent a file: '{file_info.file_name}'")
            
            # If this is not history and we are the receiver, download it
            if not is_history and msg.sender_id != self.username:
                # We are already inside the lock, so this is safe.
                self._download_file(file_info.file_id, file_info.file_name)
            elif is_history:
                # For history, just show it's available
                print(f"   (File ID: {file_info.file_id})")
    
    def _get_channel_id(self, target, is_group):
        """
        FIX: Generates a consistent, sorted channel ID for 1-to-1 chats
        to ensure both users are on the same channel.
        """
        if is_group:
            return target # Group IDs are already unique
        else:
            # Sort usernames to create a predictable, shared ID
            users = sorted([self.username, target])
            return f"dm_{users[0]}_{users[1]}"

    def _handle_user_input(self, choice: str):
        """Handles user input safely — fixes wrong option selection."""
        if choice == '/exit':
            self.in_chat_session = False
            return True

        message_text = None
        recommendations_snapshot = list(self.current_recommendations) if self.current_recommendations else []

        # --- CASE 1: Active recommendations ---
        if recommendations_snapshot:
            if choice in ['1', '2']:
                try:
                    message_text = recommendations_snapshot[int(choice) - 1]
                except (IndexError, ValueError):
                    message_text = None
            elif choice == '3':
                with self.chat_display_lock:
                    message_text = safe_input(f"{self.username} (custom)> ").strip()
            elif choice == '4':
                with self.chat_display_lock:
                    self._upload_file()
                with self.chat_display_lock:
                    self._display_recommendations()
                return False
            else:
                message_text = choice.strip()
        else:
            # --- CASE 2: Fallback mode (no recommendations) ---
            if choice == '1':
                with self.chat_display_lock:
                    message_text = safe_input(f"{self.username} (custom)> ").strip()
            elif choice == '2':
                with self.chat_display_lock:
                    self._upload_file()
                with self.chat_display_lock:
                    self._display_recommendations()
                return False
            else:
                message_text = choice.strip()

        if not message_text:
            return False

        def do_post():
            return self.chat_stub.PostMessage(
                chat_pb2.PostMessageRequest(
                    token=self.token,
                    channel_id=self.current_channel,
                    text=message_text,
                )
            )

        try:
            do_post()
            with self.chat_display_lock:
                print(f"[{datetime.now().strftime('%H:%M:%S')}] "
                    f"You> {message_text}")
                self._display_recommendations()
        except grpc.RpcError as e:
            self._handle_rpc_error(e, do_post)

        return False



    def _handle_rpc_error(self, e, retry_func, *args, **kwargs):
        """Handles NOT_LEADER, unavailable, and leader redirect errors cleanly."""
        # Ensure the attribute always exists
        if not hasattr(self, "current_leader_hint"):
            self.current_leader_hint = random.choice(SERVER_ADDRESSES)

        try:
            code = e.code()
            details = e.details() or ""

            # ✅ Better leader hint extraction
            hint = None
            
            # Format 1: "NOT_LEADER:localhost:50052"
            if "NOT_LEADER:" in details:
                parts = details.split("NOT_LEADER:")
                if len(parts) > 1:
                    potential_hint = parts[1].strip()
                    # Validate it looks like an address
                    if potential_hint.startswith("localhost") and ":" in potential_hint:
                        hint = potential_hint
            
            # Format 2: "leader_hint:localhost:50052"
            elif "leader_hint:" in details:
                parts = details.split("leader_hint:")
                if len(parts) > 1:
                    potential_hint = parts[1].strip()
                    if potential_hint.startswith("localhost") and ":" in potential_hint:
                        hint = potential_hint

            # Handle leader redirection or unavailable nodes
            if code in (
                grpc.StatusCode.UNAVAILABLE,
                grpc.StatusCode.INTERNAL,
                grpc.StatusCode.FAILED_PRECONDITION,
            ):
                if hint and hint in SERVER_ADDRESSES:
                    # ✅ We have a valid leader hint
                    with self.chat_display_lock:
                        print(
                            f"{colorama.Fore.CYAN}🔄 Detected leader change. Kindly retry sending the message."
                            f"\nReconnecting...{colorama.Style.RESET_ALL}"
                        )
                    self.connect(hint)
                    return retry_func(*args, **kwargs)
                else:
                    # No valid hint, try random reconnect
                    with self.chat_display_lock:
                        print(
                            f"{colorama.Fore.RED}⚠️  Connection issue: {code.name}. "
                            f"Trying another node...{colorama.Style.RESET_ALL}"
                        )
                    self.connect(is_retry=True)
                    return retry_func(*args, **kwargs)

            # Non-network errors
            with self.chat_display_lock:
                print(f"{colorama.Fore.RED}❌ Unhandled RPC error: {details}{colorama.Style.RESET_ALL}")

        except Exception as retry_e:
            with self.chat_display_lock:
                print(
                    f"{colorama.Fore.RED}❌ Failed to recover from RPC error: {retry_e}{colorama.Style.RESET_ALL}"
                )

        return None




    def safe_reconnect(self, target_address=None, is_retry=False, reason=None):
        """
        Thread-safe reconnect wrapper that ensures only one reconnect occurs at a time.
        """
        if not hasattr(self, "reconnect_lock"):
            self.reconnect_lock = threading.Lock()
        if not hasattr(self, "_is_reconnecting"):
            self._is_reconnecting = False

        if self._is_reconnecting:
            # Another thread is already reconnecting - wait
            time.sleep(1)
            return

        with self.reconnect_lock:
            self._is_reconnecting = True
            try:
                msg = f"🔁 {colorama.Fore.YELLOW}Reconnecting"
                if reason:
                    msg += f" due to {reason}"
                msg += f"...{colorama.Style.RESET_ALL}"
                # with self.chat_display_lock:
                #     print(msg)
                
                # Small delay before reconnecting to avoid tight loop
                time.sleep(0.5)
                
                self.connect(target_address, is_retry)
            finally:
                self._is_reconnecting = False




    def connect(self, target_address=None, is_retry=False):
        """Safely safe_reconnect or resafe_reconnect to an available Raft node."""
        # --- Always ensure required attributes exist ---
        if not hasattr(self, "resafe_reconnect_lock"):
            self.resafe_reconnect_lock = threading.Lock()
        if not hasattr(self, "current_leader_hint"):
            self.current_leader_hint = None
        if not hasattr(self, "SERVER_ADDRESSES"):
            self.SERVER_ADDRESSES = ["localhost:50051", "localhost:50052", "localhost:50053"]

        with self.resafe_reconnect_lock:
            # Close old channel if any
            if getattr(self, "channel", None):
                try:
                    self.channel.close()
                except Exception:
                    pass

            # Decide which node to use
            if target_address:
                self.current_leader_hint = target_address
            elif is_retry:
                available_nodes = [
                    addr for addr in self.SERVER_ADDRESSES
                    if addr != self.current_leader_hint
                ]
                if not available_nodes:
                    available_nodes = self.SERVER_ADDRESSES
                self.current_leader_hint = random.choice(available_nodes)
            elif not self.current_leader_hint:
                self.current_leader_hint = random.choice(self.SERVER_ADDRESSES)

            with self.chat_display_lock:
                print(f"\n🔌Trying to Reconnect with the leader node...")

            try:
                # Build new channel + stubs
                self.channel = grpc.insecure_channel(self.current_leader_hint)
                grpc.channel_ready_future(self.channel).result(timeout=3)
                self.chat_stub = chat_pb2_grpc.ChatServiceStub(self.channel)
                self.llm_stub = llm_service_pb2_grpc.LLMServiceStub(self.channel)

                with self.chat_display_lock:
                    #print(f"✅ Connection with leader successfull.")
                    if self.in_chat_session:
                        threading.Thread(target=self.refresh_recommendations_after_reconnect, daemon=True).start()

                # Restart message stream if needed
                if self.in_chat_session and not any(
                    t.name == "_listener_thread" for t in threading.enumerate()
                ):
                    threading.Thread(
                        target=self._listen_for_messages,
                        daemon=True,
                        name="_listener_thread",
                    ).start()

                return True

            except Exception as e:
                with self.chat_display_lock:
                    print(f"{colorama.Fore.RED}❌ Connection to leader failed...Retry sending a message to connect with leader node...{colorama.Style.RESET_ALL}")
                time.sleep(1)
                return self.safe_reconnect(is_retry=True)
            

    def refresh_recommendations_after_reconnect(self):
        """Re-fetch LLM suggestions safely after reconnecting."""
        if not self.token or not self.current_channel:
            return  # Not in chat
        try:
            with self.chat_display_lock:
                print(f"💡 Refreshing message recommendations...")
            self._display_recommendations()
        except grpc.RpcError as e:
            with self.chat_display_lock:
                print(f"⚠️  Could not refresh recommendations: {e.details()}")




    def chat_session(self):
        try:
            users_response = self.chat_stub.GetUsers(chat_pb2.GetUsersRequest(token=self.token))
            groups_response = self.chat_stub.GetMyGroups(chat_pb2.GetMyGroupsRequest(token=self.token))
        except grpc.RpcError as e:
            print(f"Error fetching data: {e.details()}"); return

        print("\n--- Select a User or Group to Chat With ---")
        options = [] # Will store tuples of (id, is_group)
        print("Users:")
        for user in users_response.users:
            if user.id != self.username:
                status = "Online" if user.online else "Offline"
                print(f"  {len(options) + 1}. {user.id} ({status})")
                options.append((user.id, False))

        print("\nGroups:")
        for group in groups_response.groups:
            print(f"  {len(options) + 1}. {group.id} (Group)")
            options.append((group.id, True))

        choice = safe_input("\nEnter number (or 'back' to return): ").strip()
        if choice.lower() == 'back': return

        try:
            index = int(choice) - 1
            if not (0 <= index < len(options)):
                print("Invalid selection."); return
            
            target_id, is_group = options[index]
            self.current_channel = self._get_channel_id(target_id, is_group)
            
        except ValueError:
            print("Invalid input."); return

        self.in_chat_session = True
        # Start the two background threads
        threading.Thread(target=self._listen_for_messages, daemon=True).start()
        threading.Thread(target=self._process_message_queue, daemon=True).start()

        history = self.chat_stub.GetChatHistory(
            chat_pb2.ChatHistoryRequest(token=self.token, channel_id=self.current_channel)
        )
        print(f"\n--- Chatting with {target_id} (type '/exit' to leave) ---")
        
        # Use the lock to print history and initial recommendations atomically
        with self.chat_display_lock:
            for msg in history.messages:
                self._print_message(msg, is_history=True)
            # Initial recommendations
            self._display_recommendations()
        
        # Main input loop (This is the Main Thread)
        while self.in_chat_session:
            # 1. Get input. This is the ONLY thing the main thread does.
            #    It does NOT hold the lock. The prompt_toolkit handles
            #    background prints from other threads.
            choice = safe_input(f"{self.username}> ").strip()
            
            # 2. Process the input.
            self._handle_user_input(choice)
            
            # 3. The loop repeats. The background thread (_process_message_queue)
            #    is free to acquire the lock and print messages at any time.
            time.sleep(0.1)


        self.in_chat_session = False
        self.current_channel = None
        print("\nExited chat session.")
        # Give background threads a moment to exit
        time.sleep(1)

    # -------------------------------
    # MENUS
    # -------------------------------
    def main_menu(self):
        while self.token:
            print("\n==============================")
            print(f"Logged in as: {self.username}")
            print("1. Start a Chat")
            print("2. Logout")
            print("==============================")
            choice = safe_input("Choose an option: ").strip()
            if choice == '1':
                self.chat_session()
            elif choice == '2':
                self.logout()
                break
            else:
                print("Invalid choice.")

    def run(self):
        while not self.token:
            print("\n--- Main Menu ---")
            print("1. Login")
            print("2. Sign Up")
            print("3. Exit")
            choice = safe_input("Choose an option: ").strip()
            if choice == '1':
                if self.login():
                    self.main_menu()
            elif choice == '2':
                self.signup()
            elif choice == '3':
                break
            else:
                print("Invalid option.")
        print("Goodbye!")

if __name__ == '__main__':
    client = Client()
    try:
        client.run()
    except KeyboardInterrupt:
        print("\nClient shutting down.")
    finally:
        if client.token:
            client.logout()