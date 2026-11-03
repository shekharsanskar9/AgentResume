# Multi-Agent Platform

Build multiple AI agents, keep them in sync through one shared orchestration layer, and make every agent available from a single frontend.

## Overview

Each agent is a self-contained module (its own prompt, tools and logic). A central **agent registry** discovers them automatically, an **orchestrator** runs them and keeps shared state in sync, and a **frontend** lists every registered agent so users can chat with any of them, or run several together.

```
┌────────────┐   REST / WebSocket   ┌──────────────────────────────┐
│  Frontend  │ ───────────────────▶ │  API Gateway (FastAPI)       │
│ (agent UI) │ ◀─────────────────── │                              │
└────────────┘      live events     │  ┌────────────────────────┐  │
                                    │  │ Orchestrator           │  │
                                    │  │ - routing              │  │
                                    │  │ - shared state / sync  │  │
                                    │  └──────────┬─────────────┘  │
                                    │             │                │
                                    │  ┌──────────▼─────────────┐  │
                                    │  │ Agent Registry         │  │
                                    │  └─┬────────┬────────┬────┘  │
                                    └────┼────────┼────────┼───────┘
                                      Agent A  Agent B  Agent C ...
```

## Features

- **Pluggable agents**: add a new agent by dropping a folder into `agents/`; no core changes needed.
- **Auto-discovery**: the registry scans `agents/` and exposes every agent to the API and frontend.
- **Sync across agents**: agents read and write a shared context store, so one agent's output is visible to the others.
- **Single frontend**: one UI with an agent picker, chat view, and a multi-agent (workflow) mode.
- **Streaming**: responses and agent status stream to the UI over WebSockets / SSE.
- **Async by default**: agents run concurrently without blocking each other.

## Project Structure

```
.
├── backend/
│   ├── main.py                 # App entrypoint (API + WebSocket)
│   ├── core/
│   │   ├── base_agent.py       # BaseAgent interface every agent implements
│   │   ├── registry.py         # Discovers and registers agents
│   │   ├── orchestrator.py     # Runs agents, routes tasks, manages sync
│   │   └── state.py            # Shared state / memory store
│   ├── agents/
│   │   ├── research_agent/
│   │   │   ├── agent.py
│   │   │   ├── prompt.md
│   │   │   └── tools.py
│   │   ├── coding_agent/
│   │   └── summarizer_agent/
│   ├── routes/
│   │   ├── agents.py           # GET /agents, GET /agents/{id}
│   │   └── chat.py             # POST /chat, WS /ws/{agent_id}
│   └── requirements.txt
├── frontend/
│   ├── src/
│   │   ├── components/         # AgentList, ChatWindow, WorkflowView
│   │   ├── api/                # API + WebSocket client
│   │   └── App.tsx
│   └── package.json
├── .env.example
└── README.md
```

## Getting Started

### Prerequisites

- Python 3.10+
- Node.js 18+
- An API key for your LLM provider

### 1. Clone and configure

```bash
git clone <your-repo-url>
cd <your-repo>
cp .env.example .env     # add your API keys
```

### 2. Run the backend

```bash
cd backend
python -m venv venv
source venv/bin/activate
pip install -r requirements.txt
uvicorn main:app --reload --port 8000
```

### 3. Run the frontend

```bash
cd frontend
npm install
npm run dev
```

Open http://localhost:5173. All registered agents appear in the sidebar.

## Creating a New Agent

1. Create a folder: `backend/agents/my_agent/`
2. Add `agent.py` implementing the base interface:

```python
from core.base_agent import BaseAgent

class MyAgent(BaseAgent):
    id = "my_agent"
    name = "My Agent"
    description = "What this agent does"

    async def run(self, message: str, context: dict) -> str:
        # call your LLM / tools here
        # read shared info from `context`, return the result
        return "response"
```

3. Restart the backend (or hot-reload). The registry picks it up and it shows up in the frontend automatically.

## How Syncing Works

- **Shared state**: every run reads from and writes to a common store (`core/state.py`), keyed by session. Backends can be in-memory (dev), Redis (production), or a database.
- **Event bus**: each agent publishes events (`started`, `tool_call`, `partial_output`, `done`, `error`). The orchestrator forwards these to the frontend in real time.
- **Handoffs**: an agent can pass work to another (`orchestrator.handoff("summarizer_agent", payload)`), and the second agent sees the first one's context.
- **Concurrency**: independent agents run in parallel with `asyncio.gather`; dependent agents run in sequence.

### Execution Modes

| Mode | Description |
|------|-------------|
| Single | Chat with one selected agent |
| Router | Orchestrator picks the best agent for each message |
| Pipeline | Agents run in a fixed order, each using the previous output |
| Parallel | Several agents work on the same task at once, results merged |

## API Reference

| Method | Endpoint | Description |
|--------|----------|-------------|
| GET | `/agents` | List all registered agents |
| GET | `/agents/{id}` | Agent details and capabilities |
| POST | `/chat` | Send a message to an agent (`agent_id`, `message`, `session_id`) |
| POST | `/workflow` | Run a multi-agent pipeline or parallel workflow |
| WS | `/ws/{agent_id}` | Streaming chat and live agent events |
| GET | `/state/{session_id}` | Inspect shared state for a session |

## Frontend

- **Agent sidebar**: lists every agent from `GET /agents` with name, description and status.
- **Chat window**: streaming responses, tool-call indicators.
- **Workflow builder**: choose agents and run them in pipeline or parallel mode.
- **Session state panel**: view what's currently shared between agents.

## Configuration

`.env` example:

```
LLM_API_KEY=your_key_here
LLM_MODEL=your_model_name
STATE_BACKEND=memory        # memory | redis
REDIS_URL=redis://localhost:6379
CORS_ORIGINS=http://localhost:5173
```

## Roadmap

- [ ] Base agent interface and registry
- [ ] Orchestrator with shared state
- [ ] Streaming API (WebSocket)
- [ ] Frontend agent list and chat
- [ ] Pipeline and parallel workflows
- [ ] Persistent memory (Redis / database)
- [ ] Auth and per-user sessions
- [ ] Agent metrics and logging dashboard

## Contributing

1. Fork the repo and create a feature branch.
2. Add your agent or fix, with tests where possible.
3. Open a pull request describing what changed.

## License

MIT
