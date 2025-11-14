"""
Admin Client for Raft-based Chat System
Follows the same robust patterns as client.py for fault tolerance.

SAVE THIS FILE AS: admin_client/admin_client.py
"""

import grpc
import sys
import os
import time
import random
import threading
from colorama import init, Fore, Style

# Add project root to the Python path
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

import protos.chat_pb2 as chat_pb2
import protos.chat_pb2_grpc as chat_pb2_grpc

# Initialize colorama
init(autoreset=True)

# Cluster configuration (matching client.py)
SERVER_ADDRESSES = [
    "localhost:50051",  # node1
    "localhost:50052",  # node2
    "localhost:50053",  # node3
]


class AdminClient:
    """A terminal-based client for chat administrators with robust Raft support."""
    
    def __init__(self, initial_node='localhost:50051'):
        """
        Initialize admin client with cluster awareness.
        Following the same pattern as client.py
        """
        self.cluster_nodes = SERVER_ADDRESSES
        self.current_node = initial_node
        self.SERVER_ADDRESSES = SERVER_ADDRESSES
        self.current_leader_hint = initial_node
        
        self.channel = None
        self.stub = None
        self.token = None
        self.username = None
        
        # Thread safety (like client.py)
        self.reconnect_lock = threading.Lock()
        self._is_reconnecting = False
        
        # Connect to initial node
        self.connect(initial_node)
    
    def connect(self, target_address=None, is_retry=False):
        """
        Safely connect or reconnect to an available Raft node.
        Based on client.py's connect() method.
        """
        # Ensure required attributes exist
        if not hasattr(self, "reconnect_lock"):
            self.reconnect_lock = threading.Lock()
        if not hasattr(self, "current_leader_hint"):
            self.current_leader_hint = None
        if not hasattr(self, "SERVER_ADDRESSES"):
            self.SERVER_ADDRESSES = ["localhost:50051", "localhost:50052", "localhost:50053"]

        with self.reconnect_lock:
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

            print(f"\n{Fore.YELLOW}🔌 Connecting to the leader node...{Style.RESET_ALL}")

            try:
                # Build new channel + stub
                self.channel = grpc.insecure_channel(self.current_leader_hint)
                grpc.channel_ready_future(self.channel).result(timeout=3)
                self.stub = chat_pb2_grpc.ChatServiceStub(self.channel)

                print(f"{Fore.GREEN}✅ Connection successfully established.{Style.RESET_ALL}")
                self.current_node = self.current_leader_hint
                return True

            except Exception as e:
                print(f"{Fore.RED}❌ Connection to {self.current_leader_hint} failed: {e}{Style.RESET_ALL}")
                time.sleep(1)
                return self.safe_reconnect(is_retry=True)
    
    def safe_reconnect(self, target_address=None, is_retry=False, reason=None):
        """
        Thread-safe reconnect wrapper (like client.py).
        Ensures only one reconnect occurs at a time.
        """
        if not hasattr(self, "reconnect_lock"):
            self.reconnect_lock = threading.Lock()
        if not hasattr(self, "_is_reconnecting"):
            self._is_reconnecting = False

        if self._is_reconnecting:
            # Another thread is already handling reconnect
            time.sleep(1)
            return

        with self.reconnect_lock:
            self._is_reconnecting = True
            try:
                msg = f"{Fore.YELLOW}🔄 Reconnecting"
                if reason:
                    msg += f" due to {reason}"
                msg += f"...{Style.RESET_ALL}"
                print(msg)
                self.connect(target_address, is_retry)
            finally:
                self._is_reconnecting = False
    
    def _handle_rpc_error(self, e, retry_func, *args, **kwargs):
        """
        Handles NOT_LEADER, unavailable, and leader redirect errors cleanly.
        Based on client.py's _handle_rpc_error() method.
        """
        # Ensure the attribute always exists
        if not hasattr(self, "current_leader_hint"):
            self.current_leader_hint = random.choice(SERVER_ADDRESSES)

        try:
            code = e.code()
            details = e.details() or ""

            # Handle leader redirection or unavailable nodes
            if code in (
                grpc.StatusCode.UNAVAILABLE,
                grpc.StatusCode.INTERNAL,
                grpc.StatusCode.FAILED_PRECONDITION,  # covers NOT_LEADER
            ):
                hint = None

                # Extract leader hint from either format
                if "leader_hint:" in details:
                    hint = details.split("leader_hint:")[-1].strip()
                elif "NOT_LEADER:" in details:
                    hint = details.split("NOT_LEADER:")[-1].strip()

                if hint and hint.startswith("localhost") and hint in SERVER_ADDRESSES:
                    if hint != self.current_leader_hint:
                        print(
                            f"{Fore.CYAN}🔄 Detected leader change. "
                            f"Reconnecting to {hint}...{Style.RESET_ALL}"
                        )
                        # Connect to the new leader and retry the operation
                        self.connect(hint)
                        return retry_func(*args, **kwargs)

                # No hint or failed redirect → random reconnect
                print(
                    f"{Fore.RED}⚠️  Connection issue: {details or code.name}. "
                    f"Retrying via random node...{Style.RESET_ALL}"
                )
                self.connect(is_retry=True)
                return retry_func(*args, **kwargs)

            # Non-network / non-leader errors → just log
            print(f"{Fore.RED}❌ Unhandled RPC error: {details}{Style.RESET_ALL}")

        except Exception as retry_e:
            print(
                f"{Fore.RED}❌ Failed to recover from RPC error: {retry_e}{Style.RESET_ALL}"
            )

        return None
    
    def _call_with_retry(self, rpc_func, *args, **kwargs):
        """
        Wrapper that calls an RPC function with automatic retry on errors.
        Uses _handle_rpc_error for consistent error handling.
        """
        def attempt():
            return rpc_func(*args, **kwargs)
        
        try:
            return attempt()
        except grpc.RpcError as e:
            return self._handle_rpc_error(e, attempt)
    
    def login(self):
        """Handles the admin login process."""
        print(Style.BRIGHT + "\n--- Admin Login ---")
        username = input("Enter admin username: ")
        password = input("Enter admin password: ")
        
        if not username or not password:
            print(Fore.RED + "❌ Username and password cannot be empty.")
            return False

        def do_login():
            return self.stub.Login(
                chat_pb2.LoginRequest(username=username, password=password)
            )

        try:
            response = self._call_with_retry(do_login)
            
            if response and response.success:
                self.token = response.token
                self.username = username
                print(Fore.GREEN + Style.BRIGHT + "\n✅ Admin login successful.")
                return True
            elif response:
                print(Fore.RED + f"❌ Login failed: {response.message}")
                return False
            else:
                print(Fore.RED + "❌ Login failed: No response from server")
                return False
                
        except Exception as e:
            print(Fore.RED + f"❌ An error occurred during login: {str(e)}")
            return False

    def list_all_users(self):
        """Fetches and displays all registered users and their status."""
        print(Style.BRIGHT + "\n--- All Registered Users ---")
        
        def do_get_users():
            return self.stub.GetUsers(chat_pb2.GetUsersRequest(token=self.token))
        
        try:
            response = self._call_with_retry(do_get_users)
            
            if not response or not response.users:
                print("No users found.")
                return

            for user in sorted(response.users, key=lambda u: u.id):
                status = Fore.GREEN + "(Online)" if user.online else Fore.RED + "(Offline)"
                print(f"- {user.id} {status}")
                
        except Exception as e:
            print(Fore.RED + f"❌ Error fetching users: {str(e)}")

    def list_all_groups(self):
        """Fetches and displays all created groups and their members."""
        print(Style.BRIGHT + "\n--- All Groups ---")
        
        def do_get_groups():
            return self.stub.GetAllGroups(chat_pb2.GetAllGroupsRequest(token=self.token))
        
        try:
            response = self._call_with_retry(do_get_groups)
            
            if not response or not response.groups:
                print("No groups created yet.")
                return
            
            for group in sorted(response.groups, key=lambda g: g.name):
                print(f"{Fore.CYAN}[{group.id}]{Style.RESET_ALL} {group.name}")
                if group.member_ids:
                    members = ", ".join(sorted(group.member_ids))
                    print(f"  Members: {members}")
                else:
                    print(f"  {Fore.YELLOW}Members: (none){Style.RESET_ALL}")
                    
        except Exception as e:
            print(Fore.RED + f"❌ Error fetching groups: {str(e)}")
            
    def create_group(self):
        """Prompts for and creates a new group."""
        print(Style.BRIGHT + "\n--- Create New Group ---")
        group_name = input("Enter new group name: ").strip()
        
        if not group_name:
            print(Fore.YELLOW + "⚠️  Group name cannot be empty.")
            return

        def do_create_group():
            return self.stub.CreateGroup(
                chat_pb2.CreateGroupRequest(token=self.token, group_name=group_name)
            )

        try:
            response = self._call_with_retry(do_create_group)
            
            if response and response.success:
                print(Fore.GREEN + f"✅ {response.message}")
            elif response:
                print(Fore.RED + f"❌ Failed to create group: {response.message}")
            else:
                print(Fore.RED + "❌ Failed to create group: No response")
                
        except Exception as e:
            print(Fore.RED + f"❌ Error: {str(e)}")
            
    def add_user_to_group(self):
        """Prompts for and adds a user to a group."""
        print(Style.BRIGHT + "\n--- Add User to Group ---")
        group_id = input("Enter Group ID to add user to: ").strip()
        user_id = input(f"Enter Username to add to '{group_id}': ").strip()

        if not group_id or not user_id:
            print(Fore.YELLOW + "⚠️  Group ID and Username cannot be empty.")
            return

        def do_add_user():
            return self.stub.AddUserToGroup(
                chat_pb2.ManageGroupRequest(
                    token=self.token, 
                    group_id=group_id, 
                    user_id=user_id
                )
            )

        try:
            response = self._call_with_retry(do_add_user)
            
            if response and response.success:
                print(Fore.GREEN + f"✅ {response.message}")
            elif response:
                print(Fore.RED + f"❌ Failed to add user: {response.message}")
            else:
                print(Fore.RED + "❌ Failed to add user: No response")
                
        except Exception as e:
            print(Fore.RED + f"❌ Error: {str(e)}")

    def remove_user_from_group(self):
        """Prompts for and removes a user from a group."""
        print(Style.BRIGHT + "\n--- Remove User from Group ---")
        group_id = input("Enter Group ID to remove user from: ").strip()
        user_id = input(f"Enter Username to remove from '{group_id}': ").strip()

        if not group_id or not user_id:
            print(Fore.YELLOW + "⚠️  Group ID and Username cannot be empty.")
            return

        def do_remove_user():
            return self.stub.RemoveUserFromGroup(
                chat_pb2.ManageGroupRequest(
                    token=self.token, 
                    group_id=group_id, 
                    user_id=user_id
                )
            )

        try:
            response = self._call_with_retry(do_remove_user)
            
            if response and response.success:
                print(Fore.GREEN + f"✅ {response.message}")
            elif response:
                print(Fore.RED + f"❌ Failed to remove user: {response.message}")
            else:
                print(Fore.RED + "❌ Failed to remove user: No response")
                
        except Exception as e:
            print(Fore.RED + f"❌ Error: {str(e)}")
    
    def show_cluster_status(self):
        """Display current cluster status with accurate leader detection."""
        print(Style.BRIGHT + "\n--- Cluster Status ---")
        #print(f"Your connection: {Fore.CYAN}{self.current_node}{Style.RESET_ALL}")
        print(f"\nProbing all nodes...\n")
        
        node_info = []
        leader_votes = {}  # Track which node is reported as leader
        
        for node_addr in self.cluster_nodes:
            info = {
                'address': node_addr,
                'online': False,
                'is_leader': False,
                'leader_hint': None,
                'response_time': None
            }
            
            try:
                start = time.time()
                
                # Connect to node
                test_channel = grpc.insecure_channel(node_addr)
                grpc.channel_ready_future(test_channel).result(timeout=2)
                test_stub = chat_pb2_grpc.ChatServiceStub(test_channel)
                
                info['online'] = True
                
                # CRITICAL TEST: Try a write operation
                # This is the ONLY reliable way to detect leader vs follower
                try:
                    test_stub.CreateGroup(
                        chat_pb2.CreateGroupRequest(
                            token="__probe__",  # Invalid token
                            group_name="__test__"
                        ),
                        timeout=1.0
                    )
                except grpc.RpcError as e:
                    details = e.details() or ""
                    code = e.code()
                    
                    # Parse the response
                    if code == grpc.StatusCode.FAILED_PRECONDITION and "NOT_LEADER" in details:
                        # This is a FOLLOWER - it redirected us
                        info['is_leader'] = False
                        
                        # Extract leader address from error
                        if "NOT_LEADER:" in details:
                            parts = details.split("NOT_LEADER:")
                            if len(parts) > 1:
                                leader_hint = parts[1].strip()
                                info['leader_hint'] = leader_hint
                                # Vote for this leader
                                if leader_hint in self.cluster_nodes:
                                    leader_votes[leader_hint] = leader_votes.get(leader_hint, 0) + 1
                    
                    elif code in (grpc.StatusCode.UNAUTHENTICATED, grpc.StatusCode.PERMISSION_DENIED):
                        # This is the LEADER - it processed our request and rejected the token
                        info['is_leader'] = True
                        leader_votes[node_addr] = leader_votes.get(node_addr, 0) + 1
                    
                    else:
                        # Ambiguous response - could be either
                        info['is_leader'] = False
                
                info['response_time'] = (time.time() - start) * 1000
                test_channel.close()
                
            except Exception as e:
                info['online'] = False
            
            node_info.append(info)
        
        # Determine the true leader based on votes
        true_leader = None
        if leader_votes:
            # The leader is the one with the most votes
            true_leader = max(leader_votes.items(), key=lambda x: x[1])[0]
            
            # Mark only the true leader
            for info in node_info:
                info['is_leader'] = (info['address'] == true_leader)
        
        # Display results
        online_nodes = [n for n in node_info if n['online']]
        offline_nodes = [n for n in node_info if not n['online']]
        
        if online_nodes:
            print(f"{Fore.GREEN}Online Nodes:{Style.RESET_ALL}")
            for info in online_nodes:
                addr = info['address']
                
                # Markers
                current_marker = f" {Fore.CYAN}← (your connection){Style.RESET_ALL}" if addr == self.current_node else ""
                leader_marker = f" {Fore.YELLOW}[LEADER]{Style.RESET_ALL}" if info['is_leader'] else ""
                time_marker = f" ({info['response_time']:.0f}ms)" if info['response_time'] else ""
                
                # Hint info (for debugging)
                hint_marker = ""
                if info['leader_hint'] and info['leader_hint'] != addr:
                    hint_marker = f" {Fore.LIGHTBLACK_EX}(→ {info['leader_hint']}){Style.RESET_ALL}"
                
                print(f"  {Fore.GREEN}✓{Style.RESET_ALL} {addr}{time_marker}{hint_marker}")
        
        if offline_nodes:
            print(f"\n{Fore.RED}Offline Nodes:{Style.RESET_ALL}")
            for info in offline_nodes:
                print(f"  {Fore.RED}✗{Style.RESET_ALL} {info['address']}")
        
        # Summary
        total = len(self.cluster_nodes)
        online = len(online_nodes)
        quorum = (total // 2) + 1
        has_quorum = online >= quorum
        
        print(f"\n{Fore.CYAN}Summary:{Style.RESET_ALL}")
        print(f"  Total nodes: {total}")
        print(f"  Online: {Fore.GREEN}{online}{Style.RESET_ALL}")
        print(f"  Offline: {Fore.RED}{len(offline_nodes)}{Style.RESET_ALL}")
        print(f"  Quorum: {quorum} nodes needed")
        
        if has_quorum:
            print(f"  Status: {Fore.GREEN}✓ Cluster has quorum{Style.RESET_ALL}")
        else:
            print(f"  Status: {Fore.RED}✗ Cluster lost quorum!{Style.RESET_ALL}")
        
        if true_leader:
            votes = leader_votes.get(true_leader, 0)
            #print(f"  Detected leader: {Fore.YELLOW}{true_leader}{Style.RESET_ALL} (confirmed by {votes} node(s))")
        #else:
            #print(f"  Detected leader: {Fore.RED}Unknown - election may be in progress{Style.RESET_ALL}")
        
        # Connection status
        # if self.current_node != true_leader:
        #     print(f"\n  {Fore.YELLOW}ℹ️  Note: You're connected to {self.current_node}")
        #     print(f"     For writes, you'll be redirected to the leader at {true_leader}{Style.RESET_ALL}")


    def run(self):
        """Main loop for the admin client."""
        if not self.login():
            print(Fore.RED + "\n❌ Login failed. Exiting...")
            return

        while True:
            print(Style.BRIGHT + Fore.CYAN + "\n" + "="*50)
            print(f"         Admin Control Panel ({self.username})")
            print(f"         Connection successfully established")
            print("="*50 + Style.RESET_ALL)
            print(f"{Fore.YELLOW}1.{Style.RESET_ALL} List All Users")
            print(f"{Fore.YELLOW}2.{Style.RESET_ALL} List All Groups")
            print(f"{Fore.YELLOW}3.{Style.RESET_ALL} Create a New Group")
            print(f"{Fore.YELLOW}4.{Style.RESET_ALL} Add a User to a Group")
            print(f"{Fore.YELLOW}5.{Style.RESET_ALL} Remove a User from a Group")
            print(f"{Fore.YELLOW}6.{Style.RESET_ALL} Show Cluster Status")
            print(f"{Fore.YELLOW}7.{Style.RESET_ALL} Logout and Exit")
            print(Fore.CYAN + "="*50 + Style.RESET_ALL)
            
            choice = input(f"{Fore.GREEN}Choose an option: {Style.RESET_ALL}").strip()

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
                self.show_cluster_status()
            elif choice == '7':
                def do_logout():
                    return self.stub.Logout(chat_pb2.LogoutRequest(token=self.token))
                
                try:
                    if self.token:
                        self._call_with_retry(do_logout)
                except Exception as e:
                    print(Fore.YELLOW + f"⚠️  An error occurred during logout: {str(e)}")
                
                print(Fore.YELLOW + "\n👋 Logged out. Goodbye!")
                break
            else:
                print(Fore.YELLOW + "⚠️  Invalid option, please try again.")


if __name__ == '__main__':
    client = AdminClient()
    try:
        client.run()
    except KeyboardInterrupt:
        print(f"\n\n{Fore.YELLOW}👋 Admin client shutting down...{Style.RESET_ALL}")
    except Exception as e:
        print(f"\n{Fore.RED}❌ Fatal error: {str(e)}{Style.RESET_ALL}")
    finally:
        if hasattr(client, 'channel') and client.channel:
            try:
                client.channel.close()
            except:
                pass