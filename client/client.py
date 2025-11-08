import grpc
import threading
import sys
import os
import time
import queue
from datetime import datetime
from prompt_toolkit import prompt
from prompt_toolkit.patch_stdout import patch_stdout

# Allow relative imports from project root
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

import protos.chat_pb2 as chat_pb2
import protos.chat_pb2_grpc as chat_pb2_grpc
import protos.llm_service_pb2 as llm_service_pb2
import protos.llm_service_pb2_grpc as llm_service_pb2_grpc

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

    def __init__(self, host='localhost', port=50051):
        self.channel = grpc.insecure_channel(f'{host}:{port}')
        self.chat_stub = chat_pb2_grpc.ChatServiceStub(self.channel)
        self.llm_stub = llm_service_pb2_grpc.LLMServiceStub(self.channel)
        self.token = None
        self.username = None
        self.current_channel = None
        self.in_chat_session = False
        self.message_queue = queue.Queue()
        os.makedirs(DOWNLOADS_DIR, exist_ok=True)
        
        # This lock is to ensure that printing messages and recommendations
        # happens as a single, "atomic" operation, so they don't get jumbled.
        self.chat_display_lock = threading.Lock()
        
        # This will store the current recommendations for the input handler
        self.current_recommendations = []

    # -------------------------------
    # BACKGROUND MESSAGE STREAMING
    # -------------------------------
    def _listen_for_messages(self):
        """(Thread 1) Listens for server message streams and enqueues them."""
        while self.in_chat_session:
            try:
                stream_request = chat_pb2.StreamRequest(
                    token=self.token, channel_id=self.current_channel
                )
                for message in self.chat_stub.StreamMessages(stream_request):
                    if not self.in_chat_session:
                        break
                    # IMPORTANT: Only add messages from OTHER users to the queue.
                    if message.sender_id != self.username:
                        self.message_queue.put(message)
            except grpc.RpcError as e:
                # Handle cases where the server restarts or the stream breaks
                if e.code() == grpc.StatusCode.UNAVAILABLE:
                    # Use the lock to safely print the error
                    with self.chat_display_lock:
                        print("\n⚠️  Stream connection lost. Reconnecting...")
                elif e.code() == grpc.StatusCode.CANCELLED:
                    # This is expected when the chat session ends
                    break
                else:
                    with self.chat_display_lock:
                        print(f"\n⚠️  Stream error: {e.details()}. Reconnecting...")
                time.sleep(2)
                continue
            except Exception as e:
                with self.chat_display_lock:
                    print(f"\n⚠️  Unexpected error in listener thread: {e}. Reconnecting...")
                time.sleep(2)

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
        response = self.chat_stub.SignUp(chat_pb2.SignUpRequest(username=username, password=password))
        print(f"Server: {response.message}")

    def login(self):
        username = safe_input("Enter username: ").strip()
        password = safe_input("Enter password: ").strip()
        response = self.chat_stub.Login(chat_pb2.LoginRequest(username=username, password=password))
        if response.success:
            self.token = response.token
            self.username = username
            print("✅ Login successful.")
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
        Fetches, prints, and updates the current recommendations.
        NOTE: This function MUST be called from a thread that already
        holds the 'chat_display_lock'.
        """
        print("\nFetching message recommendations...")
        try:
            reco_response = self.llm_stub.GetChatRecommendations(
                llm_service_pb2.GetChatRecommendationsRequest(
                    token=self.token, channel_id=self.current_channel
                )
            )
            if not reco_response.recommendations:
                # Clear old recommendations if none are new
                self.current_recommendations = []
                return

            print("\n--- Recommended Messages ---")
            for i, r in enumerate(reco_response.recommendations, 1):
                print(f"{i}: \"{r}\"")
            print("3: Type my own message")
            print("4: Share a file") # New Option
            print("--------------------------")
            # Update the class variable so the input handler knows what "1" and "2" mean
            self.current_recommendations = reco_response.recommendations
        
        except grpc.RpcError as e:
            print(f"⚠️  Could not fetch recommendations: {e.details()}")
            print("\n--- Options ---")
            print("1. Type my own message")
            print("2. Share a file")
            print("---------------")
            self.current_recommendations = [] # Clear old recommendations

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
        # Sanitize filename to prevent path issues
        safe_file_name = os.path.basename(file_name)
        local_file_path = os.path.join(DOWNLOADS_DIR, f"{file_id}_{safe_file_name}")
        
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
        """
        (Main Thread) Processes user input from the main chat loop.
        This function does NOT print recommendations.
        """
        if choice == '/exit':
            self.in_chat_session = False
            return
        
        # Check if choice is a recommendation
        if self.current_recommendations and choice in ['1', '2']:
            message_text = self.current_recommendations[int(choice) - 1]
            self.chat_stub.PostMessage(chat_pb2.PostMessageRequest(
                token=self.token, channel_id=self.current_channel, text=message_text
            ))
            # We must lock to print our own message safely
            with self.chat_display_lock:
                print(f"[{datetime.now().strftime('%H:%M:%S')}] You> {message_text}")
        
        elif choice == '3' or (not self.current_recommendations and choice == '1'):
            # "Type my own message"
            with self.chat_display_lock:
                message_text = safe_input(f"{self.username} (custom)> ").strip()
            if message_text:
                self.chat_stub.PostMessage(chat_pb2.PostMessageRequest(
                    token=self.token, channel_id=self.current_channel, text=message_text
                ))
                with self.chat_display_lock:
                    print(f"[{datetime.now().strftime('%H:%M:%S')}] You> {message_text}")
        
        elif choice == '4' or (not self.current_recommendations and choice == '2'):
            # "Share a file"
            with self.chat_display_lock:
                self._upload_file()
        
        elif choice: # User typed a message directly
            self.chat_stub.PostMessage(chat_pb2.PostMessageRequest(
                token=self.token, channel_id=self.current_channel, text=choice
            ))
            with self.chat_display_lock:
                print(f"[{datetime.now().strftime('%H:%M:%S')}] You> {choice}")
        
        # After we send a message, clear recommendations until a new one is received
        self.current_recommendations = []


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