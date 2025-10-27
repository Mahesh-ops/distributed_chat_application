import grpc
import sys
import os
import threading
import time
import select
from colorama import init, Fore, Style

# Add project root to the Python path
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

import protos.chat_pb2 as chat_pb2
import protos.chat_pb2_grpc as chat_pb2_grpc
import protos.llm_service_pb2 as llm_service_pb2
import protos.llm_service_pb2_grpc as llm_service_pb2_grpc

# Initialize colorama
init(autoreset=True)

class ChatClient:
    """A terminal-based client for the chat service."""
    def __init__(self, host='localhost', port=50051):
        self.channel = grpc.insecure_channel(f'{host}:{port}')
        # Create stubs for BOTH services
        self.chat_stub = chat_pb2_grpc.ChatServiceStub(self.channel)
        self.llm_stub = llm_service_pb2_grpc.LLMServiceStub(self.channel)
        
        self.token = None
        self.username = None
        self.current_channel = None
        self.listening_thread = None
        self.stop_listening = threading.Event()
        self.new_message_received = threading.Event()
        self.message_lock = threading.Lock()
        self.last_message_count = 0

    # --- Authentication and Main Menu (No changes here) ---

    def signup(self):
        """Handles the user signup process."""
        print(Style.BRIGHT + "\n--- Sign Up ---")
        username = input("Enter new username: ")
        password = input("Enter new password: ")
        if not username or not password:
            print(Fore.YELLOW + "Username and password cannot be empty.")
            return

        try:
            response = self.chat_stub.SignUp(chat_pb2.SignUpRequest(username=username, password=password))
            if response.success:
                print(Fore.GREEN + Style.BRIGHT + response.message)
            else:
                print(Fore.RED + f"Signup failed: {response.message}")
        except grpc.RpcError as e:
            print(Fore.RED + f"An error occurred: {e.details()}")

    def login(self):
        """Handles the user login process."""
        print(Style.BRIGHT + "\n--- Login ---")
        username = input("Enter username: ")
        password = input("Enter password: ")
        if not username or not password:
            print(Fore.YELLOW + "Username and password cannot be empty.")
            return False
            
        try:
            response = self.chat_stub.Login(chat_pb2.LoginRequest(username=username, password=password))
            if response.success:
                self.token = response.token
                self.username = username
                print(Fore.GREEN + Style.BRIGHT + "\nLogin successful.")
                return True
            else:
                print(Fore.RED + f"Login failed: {response.message}")
                return False
        except grpc.RpcError as e:
            print(Fore.RED + f"An error occurred: {e.details()}")
            return False

    def listen_for_messages(self):
        """Listens for incoming messages on a separate thread."""
        try:
            stream_request = chat_pb2.StreamRequest(token=self.token, channel_id=self.current_channel)
            for message in self.chat_stub.StreamMessages(stream_request):
                if self.stop_listening.is_set():
                    break
                if message.sender_id != self.username:
                    timestamp = message.timestamp.split("T")[1].split(".")[0]
                    print(f"\r{Style.DIM}[{timestamp}] {Fore.MAGENTA}{message.sender_id}{Style.RESET_ALL}> {message.text}")
                    # Signal that a new message was received
                    self.new_message_received.set()
        except grpc.RpcError as e:
            if e.code() != grpc.StatusCode.CANCELLED:
                 print(Fore.RED + f"\nMessage stream error: {e.details()}")

    def fetch_recommendations(self):
        """Fetch message recommendations from LLM service."""
        try:
            rec_response = self.llm_stub.GetChatRecommendations(
                llm_service_pb2.GetChatRecommendationsRequest(token=self.token, channel_id=self.current_channel)
            )
            return rec_response.recommendations
        except grpc.RpcError as e:
            print(Fore.YELLOW + f"Could not fetch recommendations: {e.details()}")
            return []

    def show_recommendations(self, recommendations):
        """Display recommendations to the user."""
        if recommendations and len(recommendations) >= 2:
            print(Style.BRIGHT + Fore.GREEN + "\n--- Recommended Messages ---")
            print(f"1: \"{recommendations[0]}\"")
            print(f"2: \"{recommendations[1]}\"")
            print("3: Type my own message")
        else:
            print(Style.DIM + "(No recommendations available)")

    def get_user_input_with_interrupt(self, recommendations):
        """Get user input while allowing interruption by incoming messages."""
        print(f"{Fore.CYAN}{self.username}> ", end="", flush=True)
        
        input_buffer = ""
        
        # Check for incoming messages while waiting for input
        while True:
            # Check if new message arrived
            if self.new_message_received.is_set():
                print("\r" + " " * 80 + "\r", end="", flush=True)  # Clear line
                self.new_message_received.clear()
                return None  # Signal to refresh recommendations
            
            # Check if input is available (non-blocking on Unix-like systems)
            if sys.platform != 'win32':
                # Unix/Linux/Mac
                if select.select([sys.stdin], [], [], 0.1)[0]:
                    char = sys.stdin.read(1)
                    if char == '\n':
                        print()  # New line after enter
                        return input_buffer
                    else:
                        input_buffer += char
                        print(char, end="", flush=True)
            else:
                # Windows fallback - check periodically with timeout
                import msvcrt
                if msvcrt.kbhit():
                    char = msvcrt.getwche()
                    if char == '\r':  # Enter key on Windows
                        print()
                        return input_buffer
                    elif char == '\x08':  # Backspace
                        if input_buffer:
                            input_buffer = input_buffer[:-1]
                            print('\b \b', end="", flush=True)
                    else:
                        input_buffer += char
                else:
                    time.sleep(0.1)
                    
    def process_user_choice(self, user_input, recommendations):
        """Process user's choice from recommendations or custom input."""
        if not user_input:
            return None
            
        if recommendations and len(recommendations) >= 2:
            if user_input == '1':
                return recommendations[0]
            elif user_input == '2':
                return recommendations[1]
            elif user_input == '3':
                print(f"{Fore.CYAN}{self.username}> ", end="", flush=True)
                custom_input = input()
                return custom_input
        
        # If not a valid choice, treat as custom message
        return user_input
    
    def chat_session(self):
        """Manages the main chat selection and interaction loop."""
        print(Style.BRIGHT + "\n--- Select a User or Group to Chat With ---")
        
        try:
            # Fetch users
            users_response = self.chat_stub.GetUsers(chat_pb2.GetUsersRequest(token=self.token))
            users = [u for u in users_response.users if u.id != self.username]
            print(Style.BRIGHT + "Users:")
            if not users:
                print("  (No other users found)")
            for i, user in enumerate(users):
                status = Fore.GREEN + "(Online)" if user.online else Fore.RED + "(Offline)"
                print(f"  {i + 1}. {user.id} {status}")
            
            # Fetch groups
            groups_response = self.chat_stub.GetMyGroups(chat_pb2.GetMyGroupsRequest(token=self.token))
            print(Style.BRIGHT + "\nGroups:")
            if not groups_response.groups:
                 print("  (You are not in any groups)")
            for i, group in enumerate(groups_response.groups):
                print(f"  {len(users) + i + 1}. {group.name} (Group)")

            choice = input("\nEnter number (or 'back' to return): ")
            if choice.lower() == 'back':
                return

            target_index = int(choice) - 1
            target_name = ""

            if 0 <= target_index < len(users):
                target_name = users[target_index].id
                self.current_channel = "-".join(sorted([self.username, target_name]))
            elif len(users) <= target_index < len(users) + len(groups_response.groups):
                group_index = target_index - len(users)
                target_name = groups_response.groups[group_index].name
                self.current_channel = groups_response.groups[group_index].id
            else:
                print(Fore.YELLOW + "Invalid selection.")
                return

            print(Style.BRIGHT + f"\n--- Chatting with {target_name} (type '/exit' to leave) ---")
            
            # Load chat history
            history = self.chat_stub.GetChatHistory(
                chat_pb2.ChatHistoryRequest(token=self.token, channel_id=self.current_channel)
            )
            for msg in history.messages:
                timestamp = msg.timestamp.split("T")[1].split(".")[0]
                sender = Fore.MAGENTA + msg.sender_id if msg.sender_id != self.username else Fore.CYAN + "You"
                print(f"{Style.DIM}[{timestamp}] {sender}{Style.RESET_ALL}> {msg.text}")
            
            # Start listening thread AFTER loading history
            self.stop_listening.clear()
            self.new_message_received.clear()
            self.listening_thread = threading.Thread(target=self.listen_for_messages, daemon=True)
            self.listening_thread.start()
            
            # Small delay to ensure listener is ready
            time.sleep(0.3)
            
            # Clear any spurious events from initial connection
            self.new_message_received.clear()

            # Main chat loop with recommendations
            first_iteration = True
            while True:
                # Fetch fresh recommendations
                print(Style.DIM + "\nFetching message recommendations...")
                recommendations = self.fetch_recommendations()
                self.show_recommendations(recommendations)
                
                # Get input with ability to interrupt on new messages
                user_input = self.get_user_input_with_interrupt(recommendations)
                
                # If None, new message arrived - loop back to get fresh recommendations
                if user_input is None:
                    # Only print refresh message if not first iteration
                    if not first_iteration:
                        print(Style.DIM + "\n(New message received, refreshing recommendations...)")
                    first_iteration = False
                    continue
                
                # After first successful input, clear the flag
                first_iteration = False
                
                # Process the user's choice
                message_text = self.process_user_choice(user_input, recommendations)
                
                if message_text and message_text.lower() == '/exit':
                    break
                
                if message_text:
                    # Send the message
                    self.chat_stub.PostMessage(chat_pb2.PostMessageRequest(
                        token=self.token,
                        channel_id=self.current_channel,
                        text=message_text
                    ))
                    
                    # Small delay to allow message to propagate
                    time.sleep(0.2)
            
            self.stop_listening.set()
            self.listening_thread.join(timeout=2)
            self.current_channel = None

        except (ValueError, IndexError):
            print(Fore.YELLOW + "Invalid input. Please enter a number.")
        except grpc.RpcError as e:
            print(Fore.RED + f"An error occurred: {e.details()}")

    def main_menu(self):
        """Displays the main menu after login."""
        while True:
            print(Style.BRIGHT + Fore.CYAN + "\n" + "="*30)
            print(f"Logged in as: {self.username}")
            print("1. Start a Chat")
            print("2. Logout")
            print("="*30)
            choice = input("Choose an option: ")
            
            if choice == '1':
                self.chat_session()
            elif choice == '2':
                self.chat_stub.Logout(chat_pb2.LogoutRequest(token=self.token))
                print(Fore.YELLOW + "You have been logged out.")
                break
            else:
                print(Fore.YELLOW + "Invalid choice.")

    def run(self):
        """Main entry point for the client application."""
        while True:
            print(Style.BRIGHT + "\n--- Main Menu ---")
            print("1. Login")
            print("2. Sign Up")
            print("3. Exit")
            choice = input("Choose an option: ")

            if choice == '1':
                if self.login():
                    self.main_menu()
            elif choice == '2':
                self.signup()
            elif choice == '3':
                print("Goodbye!")
                break
            else:
                print(Fore.YELLOW + "Invalid choice. Please try again.")

if __name__ == '__main__':
    client = ChatClient()
    client.run()