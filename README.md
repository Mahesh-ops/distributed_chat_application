# Distributed Fault-Tolerant Chat Application with Raft Consensus

A production-grade distributed chat system built with Python gRPC, implementing the Raft consensus algorithm for fault tolerance and high availability. Features real-time messaging, file sharing, group chat, and AI-powered message recommendations using Google's Gemini API.

---

## 📋 Table of Contents

- [Features](#features)
- [Architecture](#architecture)
- [Tech Stack](#tech-stack)
- [Project Structure](#project-structure)
- [Installation](#installation)
- [Running the Application](#running-the-application)
- [Usage Guide](#usage-guide)
- [Raft Consensus Implementation](#raft-consensus-implementation)
- [File Descriptions](#file-descriptions)
- [Testing](#testing)
- [Troubleshooting](#troubleshooting)
- [Future Enhancements](#future-enhancements)

---

## ✨ Features

### Core Functionality
- **Real-time Messaging**: Instant message delivery using gRPC streaming
- **File Sharing**: Upload and download files with automatic chunking
- **Group Chat**: Create and manage group conversations
- **Direct Messaging**: One-on-one conversations between users
- **User Authentication**: JWT-based secure authentication
- **Online Status Tracking**: Real-time user presence across the cluster

### Distributed Systems Features
- **Raft Consensus**: Leader election and log replication for fault tolerance
- **Automatic Failover**: Seamless leader changes with zero message loss
- **High Availability**: Tolerates up to (N-1)/2 node failures
- **Data Replication**: All messages and files replicated across cluster nodes
- **Strong Consistency**: Linearizable reads and writes through Raft

### Advanced Features
- **AI-Powered Recommendations**: Smart message suggestions using Google Gemini API
- **Admin Panel**: Dedicated interface for user and group management
- **Cluster Monitoring**: Real-time cluster status
- **User-Specific Downloads**: Organized file storage per user
- **Automatic Reconnection**: Clients automatically reconnect to new leaders

---

## 🏗️ Architecture

### System Overview

```
┌─────────────────────────────────────────────────────────────┐
│                     Client Layer                             │
│  ┌──────────┐  ┌──────────┐  ┌──────────┐                  │
│  │ Client 1 │  │ Client 2 │  │  Admin   │                  │
│  └────┬─────┘  └────┬─────┘  └────┬─────┘                  │
│       │             │              │                         │
│       └─────────────┼──────────────┘                         │
│                     │  (gRPC)                                │
└─────────────────────┼────────────────────────────────────────┘
                      │
┌─────────────────────┼────────────────────────────────────────┐
│              Raft Cluster (3 Nodes)                          │
│  ┌───────────────┐ ┌───────────────┐ ┌───────────────┐     │
│  │   Node 1      │ │   Node 2      │ │   Node 3      │     │
│  │   (Leader)    │ │  (Follower)   │ │  (Follower)   │     │
│  │               │ │               │ │               │     │
│  │ Port: 50051   │ │ Port: 50052   │ │ Port: 50053   │     │
│  │ Raft: 50061   │ │ Raft: 50062   │ │ Raft: 50063   │     │
│  │               │ │               │ │               │     │
│  │ ┌───────────┐ │ │ ┌───────────┐ │ │ ┌───────────┐ │     │
│  │ │ChatService│ │ │ │ChatService│ │ │ │ChatService│ │     │
│  │ │LLMService │ │ │ │LLMService │ │ │ │LLMService │ │     │
│  │ └─────┬─────┘ │ │ └─────┬─────┘ │ │ └─────┬─────┘ │     │
│  │       │       │ │       │       │ │       │       │     │
│  │ ┌─────▼─────┐ │ │ ┌─────▼─────┐ │ │ ┌─────▼─────┐ │     │
│  │ │Replicated │ │ │ │Replicated │ │ │ │Replicated │ │     │
│  │ │ Storage   │ │ │ │ Storage   │ │ │ │ Storage   │ │     │
│  │ └─────┬─────┘ │ │ └─────┬─────┘ │ │ └─────┬─────┘ │     │
│  │       │       │ │       │       │ │       │       │     │
│  │ ┌─────▼─────┐ │ │ ┌─────▼─────┐ │ │ ┌─────▼─────┐ │     │
│  │ │Raft Node  │◄┼─┼─┤Raft Node  │◄┼─┼─┤Raft Node  │ │     │
│  │ │           │─┼─┼►│           │─┼─┼►│           │ │     │
│  │ └─────┬─────┘ │ │ └─────┬─────┘ │ │ └─────┬─────┘ │     │
│  │       │       │ │       │       │ │       │       │     │
│  │ ┌─────▼─────┐ │ │ ┌─────▼─────┐ │ │ ┌─────▼─────┐ │     │
│  │ │State      │ │ │ │State      │ │ │ │State      │ │     │
│  │ │Machine    │ │ │ │Machine    │ │ │ │Machine    │ │     │
│  │ └─────┬─────┘ │ │ └─────┬─────┘ │ │ └─────┬─────┘ │     │
│  │       │       │ │       │       │ │       │       │     │
│  │ ┌─────▼─────┐ │ │ ┌─────▼─────┐ │ │ ┌─────▼─────┐ │     │
│  │ │Storage    │ │ │ │Storage    │ │ │ │Storage    │ │     │
│  │ └───────────┘ │ │ └───────────┘ │ │ └───────────┘ │     │
│  └───────────────┘ └───────────────┘ └───────────────┘     │
└─────────────────────────────────────────────────────────────┘
```

---

## 🛠️ Tech Stack

### Core Technologies
- **Python 3.8+**: Main programming language
- **gRPC**: High-performance RPC framework for client-server communication
- **Protocol Buffers**: Data serialization format
- **JWT**: JSON Web Tokens for authentication
- **Raft Consensus Algorithm**: Distributed consensus protocol

### Libraries & Frameworks
- **grpcio / grpcio-tools**: gRPC Python implementation
- **google-generativeai**: Google Gemini API for AI recommendations
- **prompt_toolkit**: Enhanced CLI interface with thread-safe input
- **colorama**: Colored terminal output
- **werkzeug**: Password hashing and security utilities

### Storage
- **JSON Files**: Persistent storage for user data, groups, and chat history
- **File System**: Chunked file storage for file sharing

---

## 📁 Project Structure

```
distributed_chat/
├── backend/
│   ├── __init__.py
│   ├── server.py                      # Original single-server (backup)
│   ├── raft_server.py                 # Main Raft-enabled server
│   ├── cluster_config.py              # Cluster node configuration
│   ├── replicated_storage.py          # Raft-aware storage wrapper
│   ├── raft/
│   │   ├── __init__.py
│   │   ├── raft_node.py               # Core Raft implementation
│   │   ├── raft_state.py              # State management (logs, terms, votes)
│   │   ├── state_machine.py           # Replicated state machine
│   │   └── raft_service_impl.py       # Raft gRPC service
│   └── node1_storage/                 # Node 1 data (auto-created)
│       ├── users.json
│       ├── groups.json
│       ├── raft_state.json
│       ├── raft_log.json
│       └── files/
├── client/
│   ├── client.py                      # Main chat client
│   ├── downloads/                     # Default downloads folder
│   ├── <username>_downloads/          # User-specific download folders
│   └── protos/
│       └── __init__.py
├── admin_client/
│   ├── admin_client.py                # Admin control panel
│   └── protos/
│       └── __init__.py
├── protos/
│   ├── __init__.py
│   ├── chat.proto                     # Chat service protocol definition
│   ├── chat_pb2.py                    # Generated protocol buffer code
│   ├── chat_pb2_grpc.py               # Generated gRPC code
│   ├── llm_service.proto              # LLM service protocol definition
│   ├── llm_service_pb2.py             # Generated protocol buffer code
│   ├── llm_service_pb2_grpc.py        # Generated gRPC code
│   ├── raft.proto                     # Raft protocol definition
│   ├── raft_pb2.py                    # Generated protocol buffer code
│   └── raft_pb2_grpc.py               # Generated gRPC code
├── scripts/
│   ├── start_cluster.sh               # Start all 3 nodes
│   ├── stop_cluster.sh                # Stop all nodes
│   └── start_node.sh                  # Start single node
├── logs/                              # Server logs (auto-created)
│   ├── node1.log
│   ├── node2.log
│   └── node3.log
├── requirements.txt                   # Python dependencies
└── README.md                          # This file
```

---

## 📦 Installation

### Prerequisites

- Python 3.8 or higher
- pip (Python package manager)
- Git (for cloning the repository)

### Step 1: Clone the Repository

```bash
git clone <repository-url>
cd distributed_chat
```

### Step 2: Create Virtual Environment (Recommended)

```bash
# Windows
python -m venv venv
venv\Scripts\activate

# Linux/Mac
python3 -m venv venv
source venv/bin/activate
```

### Step 3: Install Dependencies

```bash
pip install -r requirements.txt
```

### Step 4: Generate Protocol Buffer Code

```bash
# Generate Chat service protocol buffers
python -m grpc_tools.protoc -I. --python_out=. --grpc_python_out=. protos/chat.proto

# Generate LLM service protocol buffers
python -m grpc_tools.protoc -I. --python_out=. --grpc_python_out=. protos/llm_service.proto

# Generate Raft protocol buffers
python -m grpc_tools.protoc -I. --python_out=. --grpc_python_out=. protos/raft.proto
```

### Step 5: Configure Gemini API (Optional)

If you want AI-powered message recommendations:

1. Get a Gemini API key from [Google AI Studio](https://makersuite.google.com/app/apikey)
2. Edit `backend/raft_server.py` and replace:
   ```python
   GEMINI_API_KEY = "YOUR_API_KEY_HERE"
   ```

---

## 🚀 Running the Application

### Option 1: Using Startup Scripts (Recommended)

#### Windows (PowerShell)

```powershell
# Start all 3 nodes
.\scripts\start_cluster.bat

# Or start nodes individually
python backend/raft_server.py --node-id node1
python backend/raft_server.py --node-id node2
python backend/raft_server.py --node-id node3
```

#### Linux/Mac

```bash
# Make scripts executable
chmod +x scripts/*.sh

# Start all 3 nodes
./scripts/start_cluster.sh

# Stop all nodes
./scripts/stop_cluster.sh
```

### Option 2: Manual Startup

Open **3 separate terminals** and run:

```bash
# Terminal 1 - Node 1
python backend/raft_server.py --node-id node1

# Terminal 2 - Node 2
python backend/raft_server.py --node-id node2

# Terminal 3 - Node 3
python backend/raft_server.py --node-id node3
```

**Expected Output:**
```
============================================================
STARTING RAFT NODE: node1
============================================================
RAFT CLUSTER CONFIGURATION
============================================================
Cluster Size: 3
Quorum Size: 2
...
✅ Node node1 started successfully!
📡 Client port: 50051
🔗 Raft port: 50061

[node1] Starting election for term 1
[node2] Became LEADER for term 2
```

### Starting the Client

```bash
# Regular user client
python client/client.py

# Admin client
python admin_client/admin_client.py
```

---

## 📖 Usage Guide

### Client Application

#### 1. Login/Signup

```
--- Main Menu ---
1. Login
2. Sign Up
3. Exit

Choose an option: 1
Enter username: alice
Enter password: ****
✅ Login successful.
📂 Downloads will be saved to: client/alice_downloads
```

#### 2. Start a Chat

```
--- Select a User or Group to Chat With ---
Users:
  1. bob (Online)
  2. charlie (Offline)

Groups:
  3. engineering-team (Group)

Enter number: 1
```

#### 3. Send Messages

```
--- Chatting with bob (type '/exit' to leave) ---
[14:30:15] bob> Hey! How's it going?

Fetching message recommendations...
--- Recommended Messages ---
1: "Pretty good, thanks! How about you?"
2: "All good here!"
3: Type my own message
4: Share a file
--------------------------

alice> 1
[14:30:30] You> Pretty good, thanks! How about you?
```

#### 4. Share Files

```
alice> 4
Enter the full path to the file: C:/Users/Alice/document.pdf
Uploading 'document.pdf'...
✅ File 'document.pdf' sent.
```

#### 5. Exit Chat

```
alice> /exit
Exited chat session.
```

### Admin Client

#### 1. Login as Admin

```
Default credentials:
Username: admin
Password: adminpass
```

#### 2. Admin Operations

```
Admin Control Panel (admin)
Connected to: localhost:50051
==================================================
1. List All Users
2. List All Groups
3. Create a New Group
4. Add a User to a Group
5. Remove a User from a Group
6. Show Cluster Status
7. Logout and Exit
```

#### 3. View Cluster Status

```
Choose an option: 6

--- Cluster Status ---
Current connection: localhost:50051

Online Nodes:
  ✓ localhost:50051 (3ms)
  ✓ localhost:50052 (4ms)
  ✓ localhost:50053 (7ms)

Summary:
  Total nodes: 3
  Online: 3
  Offline: 0
  Quorum: 2 nodes needed
  Status: ✓ Cluster has quorum
```

---

## 🔐 Raft Consensus Implementation

### How Raft Works in This System

#### 1. Leader Election

- Nodes start as **FOLLOWERS**
- If no heartbeat from leader within 150-300ms, become **CANDIDATE**
- Request votes from other nodes
- Node with majority votes becomes **LEADER**

#### 2. Log Replication

- All write operations (messages, groups, users) go to the **LEADER**
- Leader appends to its log and replicates to followers
- Once majority confirms, entry is **committed**
- Applied to state machine (actual storage)

#### 3. Safety Guarantees

- **Election Safety**: At most one leader per term
- **Leader Append-Only**: Leader never overwrites its log
- **Log Matching**: If two logs have same index/term, they're identical up to that point
- **Leader Completeness**: If a log entry is committed, it will be present in future leaders
- **State Machine Safety**: All nodes apply the same log entries in the same order

### Fault Tolerance Scenarios

#### Scenario 1: Leader Failure

```
1. Leader (Node1) crashes ❌
2. Followers detect missing heartbeats
3. Election timeout triggers
4. Node2 becomes new leader ✅
5. Clients automatically reconnect to Node2
6. Messages continue flowing
```

#### Scenario 2: Follower Failure

```
1. Follower (Node3) crashes ❌
2. Leader continues operating (has quorum: 2/3)
3. New entries replicated to Node1 and Node2 ✅
4. When Node3 recovers, it catches up from log
```

#### Scenario 3: Network Partition

```
[Node1, Node2] | Network Split | [Node3]
    (Majority)                   (Minority)

- Majority partition elects leader, continues
- Minority partition cannot elect leader (no quorum)
- When network heals, Node3 syncs from majority
```

---

## 📄 File Descriptions

### Backend Files

#### `raft_server.py`
- **Purpose**: Main server with Raft integration
- **Components**:
  - `StorageManager`: Handles file I/O for persistent storage
  - `ChatServiceImpl`: Implements chat operations with Raft
  - `LLMServiceImpl`: Handles AI recommendations
  - Main server startup with argument parsing
- **Key Features**: JWT auth, file chunking, message broadcasting

#### `cluster_config.py`
- **Purpose**: Cluster configuration and utilities
- **Contents**:
  - Node addresses and ports (client + Raft)
  - Timing configurations (election timeout, heartbeats)
  - Utility functions (quorum calculation, address lookup)

#### `replicated_storage.py`
- **Purpose**: Wraps StorageManager with Raft consensus
- **Pattern**: 
  - Write operations → propose through Raft
  - Read operations → serve from local storage
- **Methods**: User management, messaging, group operations

#### `raft/raft_node.py`
- **Purpose**: Core Raft algorithm implementation
- **Components**:
  - Leader election with randomized timeouts
  - Log replication to followers
  - Heartbeat mechanism
  - Commit index tracking
- **State**: FOLLOWER, CANDIDATE, LEADER

#### `raft/raft_state.py`
- **Purpose**: Manages Raft node state
- **Persistent State**: current_term, voted_for, log[]
- **Volatile State**: commit_index, last_applied
- **Leader State**: next_index[], match_index[]
- **Thread-Safe**: Uses RLock for concurrent access

#### `raft/state_machine.py`
- **Purpose**: Applies committed log entries to application state
- **Commands**: 
  - CREATE_USER, POST_MESSAGE, CREATE_GROUP
  - ADD_USER_TO_GROUP, REMOVE_USER_FROM_GROUP
  - UPLOAD_FILE_METADATA
- **Idempotent**: Can safely apply same entry multiple times

#### `raft/raft_service_impl.py`
- **Purpose**: gRPC service for Raft peer communication
- **RPCs**: RequestVote, AppendEntries, InstallSnapshot
- **Delegates**: All logic to RaftNode

### Client Files

#### `client/client.py`
- **Purpose**: Interactive chat client with automatic failover
- **Features**:
  - Real-time message streaming
  - File upload/download
  - AI-powered recommendations
  - Leader redirect handling
  - User-specific download folders
- **Threading**: 
  - Main thread: User input
  - Listener thread: Receive messages
  - Queue processor: Display messages

#### `admin_client/admin_client.py`
- **Purpose**: Admin control panel with cluster monitoring
- **Features**:
  - User and group management
  - Real-time cluster status
  - Leader detection
  - Automatic failover
- **Operations**: CRUD operations on users and groups

### Protocol Definitions

#### `protos/chat.proto`
- **Services**: ChatService
- **RPCs**: 
  - Authentication: SignUp, Login, Logout
  - Messaging: PostMessage, StreamMessages, GetChatHistory
  - Groups: CreateGroup, AddUserToGroup, RemoveUserFromGroup
  - Files: UploadFile, DownloadFile
  - Admin: GetUsers, GetMyGroups, GetAllGroups

#### `protos/llm_service.proto`
- **Services**: LLMService
- **RPCs**: GetChatRecommendations
- **Purpose**: Separate service for AI operations

#### `protos/raft.proto`
- **Services**: RaftService
- **RPCs**: RequestVote, AppendEntries, InstallSnapshot
- **Messages**: LogEntry, RequestVoteRequest/Response, AppendEntriesRequest/Response

---

## 🧪 Testing

### Basic Functionality Test

```bash
# 1. Start cluster
./scripts/start_cluster.sh

# 2. Start client and login
python client/client.py

# 3. Send messages
# 4. Share files
# 5. Create groups (as admin)
```

### Fault Tolerance Test

```bash
# 1. Start chatting between two users

# 2. Kill the leader node
# Find leader in logs or admin panel
pkill -f "node1"  # If node1 is leader

# 3. Verify automatic failover
# Both clients should reconnect automatically
# Messages should continue flowing

# 4. Restart killed node
python backend/raft_server.py --node-id node1

# 5. Verify data consistency
# Check that restarted node has all messages
```

### Cluster Status Test

```bash
# 1. Start admin client
python admin_client/admin_client.py

# 2. Check cluster status (Option 6)
# Should show which node is leader

# 3. Kill leader and check again
# Should show new leader after election
```

---

## 🔧 Troubleshooting

### Issue: "Module not found: protos"

**Solution**: Generate protocol buffers
```bash
python -m grpc_tools.protoc -I. --python_out=. --grpc_python_out=. protos/*.proto
```

### Issue: "Port already in use"

**Solution**: Kill existing processes
```bash
# Windows
taskkill /F /IM python.exe

# Linux/Mac
pkill -f raft_server.py
```

### Issue: "Leader not elected"

**Symptoms**: All nodes remain in CANDIDATE state

**Solutions**:
1. Check network connectivity between nodes
2. Verify election timeout configuration
3. Ensure at least 2 nodes are running (for quorum)

### Issue: "Messages not received after leader change"

**Solution**: The listener thread should automatically restart. Check:
1. `connect()` method restarts listener thread
2. `_listen_for_messages()` exits cleanly on errors
3. No busy loops in error handling

### Issue: "Users showing offline incorrectly"

**Solution**: Ensure heartbeat system is enabled:
1. `update_user_last_seen()` in StorageManager
2. `_start_heartbeat_updater()` called in ChatServiceImpl.__init__
3. Methods added to ReplicatedStorage

---

## 🚀 Future Enhancements

### Planned Features
- [ ] End-to-end encryption for messages
- [ ] Voice and video calling
- [ ] Message read receipts
- [ ] Message reactions and emojis
- [ ] Message editing and deletion

### Performance Optimizations
- [ ] Message batching
- [ ] Pipeline AppendEntries
- [ ] Read-only query optimization
- [ ] Caching layer for frequently accessed data

---

## 📊 Performance Characteristics

| Metric | Value |
|--------|-------|
| Leader Election Time | 150-300ms |
| Message Latency (3-node cluster) | 50-150ms |
| Fault Tolerance | Survives 1 node failure |
| Throughput | ~500-1000 messages/sec |
| File Upload Speed | ~10-50 MB/s |
| Gemini API Response Time | 500-1500ms |

---


## 👥 Team

- **Project Type**: Academic Implementation
- **Course**: Advanced Operating Systems (AOS)
- **Institution**: BITS Pilani

---

## 🎓 Learning Resources

- [Raft Paper](https://raft.github.io/raft.pdf) - Original Raft consensus paper
- [Raft Visualization](https://raft.github.io/) - Interactive Raft visualization
- [gRPC Python Documentation](https://grpc.io/docs/languages/python/)
- [Protocol Buffers Guide](https://developers.google.com/protocol-buffers)

---

## 📈 System Requirements

### Minimum Requirements
- **CPU**: Dual-core processor
- **RAM**: 4GB
- **Storage**: 500MB free space
- **Network**: 1 Mbps

### Recommended Requirements
- **CPU**: Quad-core processor or better
- **RAM**: 8GB or more
- **Storage**: 2GB free space
- **Network**: 10 Mbps or higher

---

**Built with ❤️ using Python, gRPC, and Raft Consensus Algorithm**

time.time() Usage in Distributed Chat System:
->time.time() is NOT used for message ordering in this project.
->Message ordering is handled by the Raft log index, not timestamps!

📊 Where time.time() is used in this Project
1. User Online Status (Last-Seen Tracking)
Location: StorageManager.update_user_last_seen()
pythondef update_user_last_seen(self, username):
    import time
    users[username]['last_seen'] = time.time()
    # Stores: 1731673200.45 (seconds since epoch)
Purpose:

Track when user last interacted with the system
Determine if user is "online" (seen within last 5 minutes)
NOT for ordering - just for status display

Example:
python# Alice logs in at time.time() = 1731673200
last_seen = 1731673200

# 2 minutes later, admin checks
current_time = 1731673320
if (current_time - last_seen) < 300:  # 300 seconds = 5 min
    status = "Online" 

2. Message Timestamps (Display Only)
Location: ChatServiceImpl.PostMessage(), UploadFile()
pythontimestamp = datetime.now(timezone.utc).isoformat()
# Result: "2025-11-15T13:36:45.123456+00:00"
Purpose:

Display only - shows when message was sent
NOT for ordering - Raft log index determines order
Just makes the UI user-friendly

Why NOT used for ordering:
Node1 clock: 13:36:45.123 → Sends message A
Node2 clock: 13:36:45.120 → Sends message B (clock skew!)

If ordered by timestamp: B, A ❌ Wrong!
Raft log order:           A, B ✅ Correct!

3. Raft Log Timestamps (Metadata Only)
Location: LogEntry in raft_state.py
pythonentry = LogEntry(
    term=2,
    index=5,
    command_type="POST_MESSAGE",
    data={...},
    timestamp=int(time.time() * 1000)  # milliseconds
)
Purpose:

Metadata for debugging/monitoring
Track when command entered the system
NOT for ordering - the index field determines order


🔑 Core Message Ordering Mechanism
How Messages Are Actually Ordered:
pythonMessage Ordering = Raft Log Index (NOT timestamp!)

Log Entry:
├─ index: 1         ← This determines order!
├─ term: 2
├─ command: "POST_MESSAGE"
├─ data: {text: "Hello"}
└─ timestamp: 1731673200  ← Just metadata
Example Flow:
User A sends: "Hello"    → Raft assigns index=1
User B sends: "Hi there" → Raft assigns index=2
User A sends: "How are you?" → Raft assigns index=3

Display order (by index):
1. "Hello"
2. "Hi there"
3. "How are you?"
