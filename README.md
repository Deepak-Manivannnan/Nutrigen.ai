# NutriGen.ai

> **Multi-Agent System for Safe Meal Planning and Grocery Cost Estimation**

NutriGen.ai is an intelligent nutrition and meal planning platform powered by a coordinated multi-agent workflow. It generates diet-safe, calorie-tailored meal plans, automatically compiles categorized grocery lists with cost estimates, and supports interactive conversational modifications in real time.

---

## 🌟 Key Features

- **Coordinated Multi-Agent Architecture**: Powered by LangGraph, specialized AI agents collaborate to plan, validate, and audit nutrition recommendations before they reach the user.
- **Safety & Allergy Guardrails**: Dedicated verification against dietary restrictions and allergens (e.g., gluten, nuts, dairy, shellfish).
- **Nutritional Accuracy**: Validates daily calories and macronutrient ratios (protein, carbs, fats) with nutritional database lookups.
- **Automated Grocery & Cost Estimation**: Consolidates ingredients across meal plans, aggregates quantities, and provides estimated grocery budgets.
- **Conversational Editing**: Natural language editing for swapping meals, adding snacks, adjusting portion targets, or regenerating entire days.
- **Real-Time Agent Observability**: Live Server-Sent Events (SSE) streaming of step-by-step agent activity (trace display currently hidden in the UI).

---

## 🤖 Multi-Agent Workflow

The system employs a swarm of specialized agents orchestrated via LangGraph:

```text
                    User message
                         │
                         ▼
              Supervisor (understands + routes)
                         │
                         ▼
               Planner (drafts meals)
                         │
            ┌────────────┴────────────┐
            ▼                         ▼
   Nutrition check             Allergy check
   (calories + macros)         (allergens + veto)
            └────────────┬────────────┘
                         ▼
        Supervisor judges: approve,
        or send back (max 3 rounds)
                         │
                         ▼
            Shopper (prices groceries)
                         │
                         ▼
               Plan + grocery list
```

| Agent | Responsibility |
| :--- | :--- |
| **Supervisor** | Intent classification, conversational routing, state management, and user interaction. |
| **Planner** | Generates diverse, cuisine-aware meals tailored to user profile preferences and calorie targets. |
| **Nutrition** | Verifies portion sizing, macro balances, and caloric boundaries using food nutrition databases. |
| **Allergy & Safety** | Enforces clinical guardrails, detecting hidden allergens and ingredient conflicts. |
| **Shopper** | Consolidates pantry items, normalizes units, and generates categorized shopping lists. |

---

## 🛠️ Tech Stack

### Backend
- **Framework**: FastAPI (Python 3.11+)
- **Orchestration**: LangGraph (workflow graph; agents themselves are custom-built, no agent framework)
- **LLM Engine**: Google Gemini API (`gemini-3.1-flash-lite`, `gemini-3.5-flash-lite`, `gemini-3.5-flash`, `gemini-2.5-flash` for ingredient lookups)
- **Database & ORM**: SQLAlchemy 2.0 (Async), MySQL / aiomysql
- **Validation**: Pydantic v2 & Pydantic Settings
- **Streaming**: Server-Sent Events (SSE) via `sse-starlette`

### Frontend
- **Framework**: React 19 with Vite
- **Routing**: React Router v7
- **Icons**: Lucide React
- **Design System**: Vanilla CSS, modern cards, and responsive layouts

---

## 📁 Repository Structure

```text
├── backend/
│   ├── app/
│   │   ├── agents/          # Specialized agent implementations (Planner, Nutrition, Allergy, Shopper)
│   │   ├── api/             # FastAPI app wiring (routes/, streaming, shared deps)
│   │   ├── db/              # Database models, async session, and repositories
│   │   ├── guardrails/      # Clinical safety rules and nutritional boundary checks
│   │   ├── llm/             # Gemini client integration and model configurations
│   │   ├── observability/   # Structured logging and execution tracing
│   │   ├── orchestration/   # LangGraph workflow definitions and supervisor routing
│   │   ├── schemas/         # Pydantic data schemas
│   │   └── tools/           # Nutrition lookup and pricing tools
│   ├── tests/               # Backend test suite (unit, agents, integration)
│   ├── tests_live/          # Live-AI tests (call the real API — billed, run by hand)
│   └── requirements.txt     # Python dependencies
├── frontend/
│   ├── src/
│   │   ├── components/      # Reusable UI pieces (AppLayout, Sidebar, PlanSelect, TagInput, Toasts)
│   │   ├── context/         # React Context for global state & authentication
│   │   ├── pages/           # Chat, Plans, Grocery, Home, Landing, Onboarding, Sign In/Up
│   │   ├── utils/           # API client and shared helpers
│   │   └── index.css        # Core styling and theme tokens
│   ├── package.json         # Frontend dependencies and scripts
│   └── vite.config.js       # Vite configuration
├── .env.example             # Template for environment variables
└── README.md
```

---

## 🚀 Getting Started

### Prerequisites
- **Python**: 3.11 or higher
- **Node.js**: 20.19+ or 22.12+ (required by Vite 8)
- **MySQL**: 8.0+ (**required** — the app connects through the MySQL/aiomysql driver; no other database is supported)

---

### 1. Backend Setup

1. **Navigate to the project root and create a virtual environment:**
   ```bash
   python -m venv .venv
   ```

2. **Activate the virtual environment:**

   **Windows (PowerShell):**
   ```powershell
   .\.venv\Scripts\Activate.ps1
   ```
   **macOS / Linux:**
   ```bash
   source .venv/bin/activate
   ```

3. **Install dependencies:**
   ```bash
   pip install -r backend/requirements.txt
   ```

4. **Create the database (one-time):**

   The backend creates all of its tables automatically on first start, but the
   database itself must already exist. Log in to MySQL and run:

   ```sql
   CREATE DATABASE nutrigen CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci;
   ```

   No tables, migrations or seed data need to be created by hand.

5. **Configure environment variables:**

   Copy `.env.example` to `.env`, then set **your own** MySQL username and password
   in `DATABASE_URL`:

   **Windows (PowerShell):**
   ```powershell
   Copy-Item .env.example .env
   ```
   **macOS / Linux:**
   ```bash
   cp .env.example .env
   ```

6. **Start the FastAPI backend:**
   ```bash
   uvicorn backend.app.main:app --reload --port 8000
   ```
   The backend API will be available at `http://127.0.0.1:8000`. Interactive documentation is available at `http://127.0.0.1:8000/docs`.

---

### 2. Frontend Setup

1. **Navigate to the frontend directory:**
   ```bash
   cd frontend
   ```

2. **Install dependencies:**
   ```bash
   npm install
   ```

3. **Start the development server:**
   ```bash
   npm run dev
   ```
   The frontend will be available at `http://localhost:5173`.

---

## ⚙️ Environment Configuration

| Variable | Description | Default / Required |
| :--- | :--- | :--- |
| `DATABASE_URL` | MySQL connection string. Use your own username, password and database name. URL-encode special characters in the password (`@` → `%40`) | `mysql+aiomysql://root:password@127.0.0.1:3306/nutrigen` |
| `GEMINI_API_KEY_1` | Gemini API key for the Supervisor and Planner agents | **Required** |
| `GEMINI_API_KEY_2` | Second Gemini API key for the Nutrition, Allergy and Shopper agents. This project runs two keys so the agent that writes plans and the agents that audit and price them draw on separate quota — a heavy planning step can no longer exhaust the quota the other agents need | Optional |
| `USDA_API_KEY` | USDA FoodData Central API key for food lookups | `DEMO_KEY` |
| `APP_ENV` | Application environment (`development` / `production`) | `development` |
| `MAX_ROUNDS` | Maximum iterative revision rounds per plan run | `3` |
| `AGENT_TIMEOUT_SECONDS`| Timeout threshold per individual agent call | `30` |
| `AUTH_TOKEN_SECRET` | Long random string that signs login tokens. **Required in production**: if it is empty the code falls back to a public development secret and anyone could forge a login | Required in production |
| `FRONTEND_ORIGINS` | Website address(es) allowed to call the API from a browser, comma separated, no trailing slash (e.g. `https://nutrigen-frontend.onrender.com`). Empty in production means no website is allowed; in development any `localhost` port works | Required in production |
| `REFRESH_TOKEN_TTL_DAYS` | How many days of not using the app before a user must sign in again (each use renews it) | `30` |
| `DEBUG` | Verbose debug logging (can include user messages). Keep off in production | `false` |

---

## 🧪 Running Tests

To run the backend test suite (mocked, free):

```bash
pytest backend/tests
```

> The `backend/tests_live/` suite calls the real Gemini API and is billed — run it by hand only when needed.

---

## 🧭 Architecture Decisions

The reasoning behind the system's shape — hub-and-spoke orchestration, deterministic safety rules, LLM routing, data fallbacks, persistence, auth, and the known trade-offs — is recorded in [`docs/adr/`](docs/adr/README.md). Start with the index; it includes reading paths and maps each decision to the code it governs.

<!-- No license file ships with the repo yet; add one (e.g. MIT LICENSE) before publishing. -->
