import grpc
import sys
import os
from colorama import init, Fore, Style

# Add project root to the Python path
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

import protos.chat_pb2 as chat_pb2
import protos.chat_pb2_grpc as chat_pb2_grpc

# Initialize colorama
init(autoreset=True)

class AdminClient:
    """A terminal-based client for chat administrators."""
    def __init__(self, host='localhost', port=50051):
        self.channel = grpc.insecure_channel(f'{host}:{port}')
        self.stub = chat_pb2_grpc.ChatServiceStub(self.channel)
        self.token = None
        self.username = None

    def login(self):
        """Handles the admin login process."""
        print(Style.BRIGHT + "--- Admin Login ---")
        username = input("Enter admin username: ")
        password = input("Enter admin password: ")
        
        if not username or not password:
            print(Fore.RED + "Username and password cannot be empty.")
            return False

        try:
            response = self.stub.Login(chat_pb2.LoginRequest(username=username, password=password))
            if response.success:
                self.token = response.token
                self.username = username
                print(Fore.GREEN + Style.BRIGHT + "\nAdmin login successful.")
                return True
            else:
                print(Fore.RED + f"Login failed: {response.message}")
                return False
        except grpc.RpcError as e:
            print(Fore.RED + f"An error occurred during login: {e.details()}")
            return False

    def list_all_users(self):
        """Fetches and displays all registered users and their status."""
        print(Style.BRIGHT + "\n--- All Registered Users ---")
        try:
            response = self.stub.GetUsers(chat_pb2.GetUsersRequest(token=self.token))
            if not response.users:
                print("No users found.")
                return

            for user in sorted(response.users, key=lambda u: u.id):
                status = Fore.GREEN + "(Online)" if user.online else Fore.RED + "(Offline)"
                print(f"- {user.id} {status}")
        except grpc.RpcError as e:
            print(Fore.RED + f"Error fetching users: {e.details()}")

    def list_all_groups(self):
        """Fetches and displays all created groups and their members."""
        print(Style.BRIGHT + "\n--- All Groups ---")
        try:
            response = self.stub.GetAllGroups(chat_pb2.GetAllGroupsRequest(token=self.token))
            if not response.groups:
                print("No groups created yet.")
                return
            
            for group in sorted(response.groups, key=lambda g: g.name):
                print(f"[{group.id}] {group.name}")
                if group.member_ids:
                    members = ", ".join(sorted(group.member_ids))
                    print(f"  Members: {members}")
                else:
                    print("  Members: (none)")
        except grpc.RpcError as e:
            print(Fore.RED + f"Error fetching groups: {e.details()}")
            
    def create_group(self):
        """Prompts for and creates a new group."""
        print(Style.BRIGHT + "\n--- Create New Group ---")
        group_name = input("Enter new group name: ")
        if not group_name:
            print(Fore.YELLOW + "Group name cannot be empty.")
            return

        try:
            request = chat_pb2.CreateGroupRequest(token=self.token, group_name=group_name)
            response = self.stub.CreateGroup(request)
            if response.success:
                print(Fore.GREEN + response.message)
            else:
                print(Fore.RED + f"Failed to create group: {response.message}")
        except grpc.RpcError as e:
            print(Fore.RED + f"Error: {e.details()}")
            
    def add_user_to_group(self):
        """Prompts for and adds a user to a group."""
        print(Style.BRIGHT + "\n--- Add User to Group ---")
        group_id = input("Enter Group ID to add user to: ")
        user_id = input(f"Enter Username to add to '{group_id}': ")

        if not group_id or not user_id:
            print(Fore.YELLOW + "Group ID and Username cannot be empty.")
            return

        try:
            request = chat_pb2.ManageGroupRequest(token=self.token, group_id=group_id, user_id=user_id)
            response = self.stub.AddUserToGroup(request)
            if response.success:
                print(Fore.GREEN + response.message)
            else:
                print(Fore.RED + f"Failed to add user: {response.message}")
        except grpc.RpcError as e:
            print(Fore.RED + f"Error: {e.details()}")

    def remove_user_from_group(self):
        """Prompts for and removes a user from a group."""
        print(Style.BRIGHT + "\n--- Remove User from Group ---")
        group_id = input("Enter Group ID to remove user from: ")
        user_id = input(f"Enter Username to remove from '{group_id}': ")

        if not group_id or not user_id:
            print(Fore.YELLOW + "Group ID and Username cannot be empty.")
            return

        try:
            request = chat_pb2.ManageGroupRequest(token=self.token, group_id=group_id, user_id=user_id)
            response = self.stub.RemoveUserFromGroup(request)
            if response.success:
                print(Fore.GREEN + response.message)
            else:
                print(Fore.RED + f"Failed to remove user: {response.message}")
        except grpc.RpcError as e:
            print(Fore.RED + f"Error: {e.details()}")

    def run(self):
        """Main loop for the admin client."""
        if not self.login():
            return

        while True:
            print(Style.BRIGHT + Fore.CYAN + "\n" + "="*30)
            print(f"Admin Control Panel ({self.username})")
            print("="*30)
            print("1. List All Users")
            print("2. List All Groups")
            print("3. Create a New Group")
            print("4. Add a User to a Group")
            print("5. Remove a User from a Group")
            print("6. Logout and Exit")
            print("="*30)
            
            choice = input("Choose an option: ")

            if choice == '1':
                self.list_all_users()
            elif choice == '2':
                self.list_all_groups()
            elif choice == '3':
                self.create_group()
            elif choice == '4':
                self.add_user_to_group()
            elif choice == '5':
                self.remove_user_from_group()
            elif choice == '6':
                try:
                    self.stub.Logout(chat_pb2.LogoutRequest(token=self.token))
                except grpc.RpcError as e:
                    print(Fore.RED + f"An error occurred during logout: {e.details()}")
                print(Fore.YELLOW + "Logged out.")
                break
            else:
                print(Fore.YELLOW + "Invalid option, please try again.")

if __name__ == '__main__':
    client = AdminClient()
    client.run()