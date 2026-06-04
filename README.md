# 🤖 Agentic Support Platform

> Multi-agent AI customer support platform built with FastAPI, CrewAI Flow, Claude Haiku 4.5, and vanilla JS.
> Capstone project — "Become An Agentic Architect" course by Carmelo Iaria.

🔧 **Backend API (Railway):** https://web-production-126e2.up.railway.app  
🔧 **Backend API (Render backup):** https://agentic-support-platform.onrender.com  
📦 **GitHub:** https://github.com/BeatrizCoder/agentic-support-platform

---

## 📋 Table of Contents

- [Overview](#overview)
- [Architecture](#architecture)
- [Agent Pipeline](#agent-pipeline)
- [LLM vs Deterministic Decision Matrix](#llm-vs-deterministic-decision-matrix)
- [External API Integrations](#external-api-integrations)
- [Knowledge Base RAG](#knowledge-base-rag)
- [Routing Engine](#routing-engine)
- [HITL Human in the Loop](#hitl-human-in-the-loop)
- [Quality Evaluation LLM as Judge](#quality-evaluation-llm-as-judge)
- [Observability and Metrics](#observability-and-metrics)
- [Authentication and Privacy](#authentication-and-privacy)
- [Frontend](#frontend)
- [Export Features](#export-features)
- [Setup and Installation](#setup-and-installation)
- [Environment Variables](#environment-variables)
- [Running the Project](#running-the-project)
- [Deploy](#deploy)
- [Project Structure](#project-structure)

---

## Overview

The Agentic Support Platform is a production-grade multi-agent customer support system that combines LLM intelligence with deterministic business rules to handle customer inquiries automatically and escalate to humans when needed.

**Key capabilities:**
- 3 CrewAI Crews with 6 specialized AI agents
- CrewAI Flow orchestrating the full pipeline
- Intelligent routing with priority-based business rules
- Real-time external API integration (ViaCEP + OpenWeatherMap + SQLite Refund DB)
- Human-in-the-loop (HITL) review with approve/reject/await
- Cross-model quality evaluation (Sonnet judges Haiku)
- Full observability with token tracking and cost per agent
- JWT guest authentication with 24h session isolation
- LGPD-compliant privacy with automatic data deletion
- Export to Excel and PDF with rich formatting
- Dual-view: Customer Portal + Operator Dashboard
- Dataset toggle: Live (isolated per user, 24h auto-delete) vs Historical (431 demo tickets)

---

## Architecture

```
Customer Inquiry
      |
      v
+------------------------------------------+
|           FastAPI Backend                |
|                                          |
|  +----------+   +--------------------+  |
|  | CrewAI   |   |   Routing Engine   |  |
|  |  Flow    |-->|  (Business Rules)  |  |
|  +----------+   +--------------------+  |
|       |                                  |
|  +----v---------------------------------+|
|  |         3 CrewAI Crews               ||
|  |                                      ||
|  | Crew 1: Analysis (parallel)          ||
|  |   Classification Agent               ||
|  |   Sentiment Agent                    ||
|  |         |                            ||
|  | Python: Routing + RAG + APIs         ||
|  |   ViaCEP / OpenWeatherMap            ||
|  |   Refund DB / Pending Actions        ||
|  |         |                            ||
|  | Crew 2: Response (sequential)        ||
|  |   Knowledge Agent                    ||
|  |   Response Agent                     ||
|  |         |                            ||
|  | Python: Escalation Evaluator         ||
|  |         |                            ||
|  | Crew 3: Evaluation (background)      ||
|  |   Summary Agent                      ||
|  |   Quality Agent (Sonnet)             ||
|  +--------------------------------------+|
|                    |                     |
|         Neon PostgreSQL (São Paulo)      |
+------------------------------------------+
      |
      v
+-------------+     +----------------------+
|  Customer   |     |  Operator Dashboard  |
|   Portal    |     |  HITL + Analytics    |
+-------------+     +----------------------+
      |                        |
   Vercel                   Vercel
```

---

## Agent Pipeline

| Step | Agent/Component | Type | Description |
|------|----------------|------|-------------|
| 1 | **Crew 1: Analysis** | 🤖 CrewAI | Classification + Sentiment in parallel |
| 1a | Classification Agent | 🤖 LLM Haiku | Category + language detection |
| 1b | Sentiment Agent | 🤖 LLM Haiku | Sentiment + urgency (parallel with 1a) |
| 2 | **Routing Engine** | ⚙️ Rules | Priority-based routing decision |
| 3 | **Knowledge RAG** | ⚙️ Local | Retrieves relevant KB snippets |
| 4 | **External Data Enrichment** | 🌐 APIs | ViaCEP + OpenWeatherMap + Refund DB |
| 5 | **Crew 2: Response** | 🤖 CrewAI | Knowledge + Response sequential |
| 5a | Knowledge Agent | 🤖 LLM Haiku | Synthesizes KB snippets |
| 5b | Response Agent | 🤖 LLM Haiku | Generates personalized response |
| 6 | **Escalation Evaluator** | ⚙️ Rules | Final routing decision |
| 7 | **Crew 3: Evaluation** | 🤖 CrewAI | Runs in background (non-blocking) |
| 7a | Summary Agent | 🤖 LLM Haiku | 2-line operator summary |
| 7b | Quality Agent | 🤖 LLM Sonnet | Cross-model evaluation |

**Crew 3 runs in background** — customer receives response in ~8s while evaluation happens asynchronously (~10s later in Operator Dashboard).

---

## LLM vs Deterministic Decision Matrix

### 🤖 Uses LLM (CrewAI Agents)

| Component | Agent | Model | Avg Tokens | Avg Cost |
|-----------|-------|-------|-----------|---------|
| Classification | Classification Agent | Haiku 4.5 | ~170 | ~$0.000200 |
| Sentiment Analysis | Sentiment Agent | Haiku 4.5 | ~145 | ~$0.000178 |
| Knowledge Synthesis | Knowledge Agent | Haiku 4.5 | ~200 | ~$0.000245 |
| Response Generation | Response Agent | Haiku 4.5 | ~620 | ~$0.001199 |
| Logistics Alert Message | Response Agent | Haiku 4.5 | ~180 | ~$0.000220 |
| Weather Alert Message | Response Agent | Haiku 4.5 | ~180 | ~$0.000220 |
| Refund Status Message | Response Agent | Haiku 4.5 | ~200 | ~$0.000245 |
| Operator Summary | Summary Agent | Haiku 4.5 | ~200 | ~$0.000250 |
| Quality Evaluation | Quality Agent | **Sonnet 4.6** | ~300 | ~$0.001200 |

> **Note:** External APIs (ViaCEP, OpenWeatherMap) are called deterministically.
> The **response messages** based on their data are generated by LLM for personalization.

### ⚙️ Uses Deterministic Rules (No LLM)

| Component | Why Deterministic | Latency |
|-----------|-----------------|---------|
| Routing Engine | Business rules must be auditable | ~2ms |
| Escalation Evaluator | Compliance — cannot hallucinate | ~2ms |
| Knowledge RAG retrieval | Local search, instant, free | ~45ms |
| ViaCEP API call | REST API — no LLM needed | ~420ms |
| OpenWeatherMap call | REST API — no LLM needed | ~380ms |
| Refund DB lookup | SQLite query — deterministic | ~12ms |
| Pending Actions lookup | SQLite query — deterministic | ~8ms |
| Escalation keyword detection | Regex — faster and auditable | <1ms |

### 💡 Cost Per Ticket

```
Classification:   $0.000200  (6.5%)
Sentiment:        $0.000178  (5.7%)
Knowledge:        $0.000245  (7.9%)
Response:         $0.001199  (38.7%)
Summary:          $0.000250  (8.1%)
Quality (Sonnet): $0.001200  (38.7%)
──────────────────────────────────
Total:           ~$0.003272 per ticket
```

---

## External API Integrations

### 📍 ViaCEP (Address Validation)

```
Purpose:  Validates Brazilian postal codes
URL:      https://viacep.com.br/ws/{cep}/json/
Auth:     None (free, no limits)
Client:   httpx.AsyncClient (truly async)
Latency:  ~420ms avg | Retries: 2 | Timeout: 5s
```

**Business rule:**
```
CEP from Sul/Sudeste (SP, RJ, MG, ES, PR, SC, RS)
  → Logistics alert (fleet maintenance)
  → LLM Response Agent generates personalized message
  → Auto-resolved

CEP from other regions
  → No alert → normal routing
```

### 🌤️ OpenWeatherMap (Weather Check)

```
Purpose:  Real-time weather for customer city
URL:      https://api.openweathermap.org/data/2.5/weather
Auth:     OPENWEATHER_API_KEY (free: 1000 calls/day)
Client:   httpx.AsyncClient (truly async)
Latency:  ~380ms avg | Retries: 2 | Timeout: 5s
City extraction: LLM-based (handles any location mention)
```

**Severity classification (from weather_id, wind speed, visibility):**
```
SEVERE  → weather_id: 2xx (thunderstorm), 502–531 (heavy rain),
           6xx (snow/ice), 762/771/781 (extreme)
           OR wind_speed > 50 km/h OR visibility < 1000m
MODERATE → weather_id: 3xx (drizzle), 500–501 (light rain)
           OR wind_speed 20–50 km/h
CLEAR   → weather_id: 800–804 (clear/clouds)
```

**Business rules:**
```
SEVERE adverse weather
  → ⛈️ Auto-resolved — broad operational notice
  → DO NOT ask for order number
  → Estimated recovery: 2-3 business days

MODERATE adverse weather
  → 🌧️ Contextual response — explains weather may contribute to delay
  → Asks for order number to investigate specific delivery
  → Awaiting form

Clear weather + no order number
  → "☀️ Clima normal em {city}. Preciso do número do pedido."
  → Awaiting form

Clear weather + has order number
  → "☀️ Clima normal. Escalando para investigar."
  → Escalate
```

### 🗄️ Refund Database (SQLite → Neon in production)

```
Purpose:  Lookup refund status by order number
Latency:  ~12ms avg
Seed:     10 realistic records (orders 11111–99999)
```

**Refund seed data:**

| Order | Status | Value | Product |
|-------|--------|-------|---------|
| 11111 | aprovado | R$150,00 | Tênis Nike Air |
| 22222 | pendente | R$89,90 | Camiseta Adidas |
| 33333 | negado | R$45,00 | Acessório USB |
| 44444 | processado | R$299,00 | Smartwatch |
| 55555 | aprovado | R$199,90 | Mochila Escolar |
| 66666 | pendente | R$520,00 | Monitor 24" |
| 77777 | processado | R$35,00 | Carregador USB-C |
| 88888 | aprovado | R$750,00 | iPhone Case Premium |
| 99999 | negado | R$180,00 | Perfume Importado |
| 10000 | em_analise | R$440,00 | Notebook Sleeve |

**Business rules:**
```
aprovado/processado/pendente/em_analise
  → LLM generates empathetic status message
  → Auto-resolved with feedback buttons 👍 👎

negado (denied)
  → Initial response: explains denial reason (no "human will contact you" yet)
  → Customer gets 2 options:
    👍 Entendo a decisão → closes (accepted)
    👎 Quero contestar  → AWAITING_CUSTOMER_DOCUMENTS form
  → After customer submits contest documents:
    → Escalates to specialist review
    → "Nossos especialistas irão realizar uma revisão humana do seu caso"

Not found
  → Investigation form (required fields, no skip)
  → Escalates with collected details
```

### ⏳ Pending Actions Database

```
Purpose:  Detect open cases requiring customer action
Seed:     7 realistic scenarios (orders 77701–77707)
```

| Order | Status | Product | Routing | Action |
|-------|--------|---------|---------|--------|
| 77701 | AWAITING_PHOTO | Tênis Nike Air Max | ⏳ Awaiting | Customer sends damage photo |
| 77702 | LABEL_EXPIRED | Smartwatch Samsung | ✅ Auto-resolve | System provides new label link |
| 77703 | AWAITING_RETURN_SHIPMENT | Notebook Dell | ⏳ Awaiting | Customer ships product back |
| 77704 | AWAITING_DOCUMENTATION | iPhone Case | ⏳ Awaiting | Customer sends payment proof |
| 77705 | DELIVERY_FAILED | Monitor LG 27" | ✅ Auto-resolve | System gives reschedule URL + pickup address |
| 77706 | UNDER_TECHNICAL_ANALYSIS | Fone Sony WH-1000XM5 | ✅ Auto-resolve | System shows analysis progress + expected date |
| 77707 | AWAITING_RETURN | Cafeteira Nespresso | ⏳ Awaiting | Customer returns product |

**Auto-resolve statuses** (`DELIVERY_FAILED`, `LABEL_EXPIRED`, `UNDER_TECHNICAL_ANALYSIS`): system has enough
information to resolve without customer action — provides next steps directly.

**Awaiting statuses** (`AWAITING_PHOTO`, `AWAITING_RETURN_SHIPMENT`, `AWAITING_DOCUMENTATION`, `AWAITING_RETURN`):
customer must act first — shows awaiting form with specific instructions.

---

## Knowledge Base RAG

Token-controlled retrieval from local markdown documents.

```
knowledge/
  order_issues.md       ← tracking, wrong items, damaged
  billing.md            ← refunds, charges, invoices
  account_access.md     ← password reset, locked accounts, 2FA
  technical_issues.md   ← site errors, payment failures
  general_support.md    ← return policy, cancellation, contact
  escalation_policy.md  ← when to escalate, PT + EN keywords
```

**Token control:**
```python
MAX_KNOWLEDGE_SNIPPETS = 3    # max snippets per query
MAX_SNIPPET_CHARS = 800       # max chars per snippet
# Result: max ~600 tokens of context sent to LLM
# vs ~5,000+ if full documents were sent (~88% savings)
```

---

## Routing Engine

Priority-based routing decision matrix (progressive escalation — never jumps to human without first resolving or collecting info):

| Priority | Condition | Action | Details |
|----------|-----------|--------|---------|
| 1 | **Logistics alert** (Sul/Sudeste CEP) | ✅ Auto-resolve | `skip_routing` flag set — always wins, never asks for order number |
| 2a | **Severe weather** (storm/snow/extreme) | ✅ Auto-resolve | Broad operational notice, no order number asked |
| 2b | **Moderate weather** (drizzle/light rain) | ⏳ Awaiting | Contextual message with real city + temp, asks for order number |
| 3 | **Pending action** — auto-resolve statuses | ✅ Auto-resolve | DELIVERY_FAILED → reschedule; LABEL_EXPIRED → new label; UNDER_TECHNICAL_ANALYSIS → wait message |
| 3b | **Pending action** — awaiting statuses | ⏳ Awaiting | AWAITING_PHOTO / AWAITING_RETURN_SHIPMENT / AWAITING_DOCUMENTATION / AWAITING_RETURN |
| 4 | **Refund found** + auto-resolvable | ✅ Auto-resolve | LLM generates empathetic status message |
| 5 | **Refund denied** | 🔴 2 options | Accept / Contest → awaiting docs → specialist review |
| 6 | **Explicit escalation keyword** | 🚨 Escalate | Customer asked for manager, legal action, etc. |
| 7 | **Billing category** | 🚨 Always escalate | Financial security |
| 8 | **Account hacked / unauthorized** | 🚨 Always escalate | Security team review |
| 9 | **Exchange/return already approved** | ✅ Auto-resolve | Numbered steps: pack → label → drop off → new item |
| 10 | **Order Issues + has order number** | 🚨 Escalate | Enough info to route to specialist |
| 11 | **Order Issues + no order number** | ⏳ Awaiting | Ask for order number first |
| 12 | **Account Access** (normal) | 📋 Step-by-step | Guided recovery instructions |
| 13 | **Technical Issue** | 📋 Step-by-step | Troubleshooting first; awaiting only if "Not Helpful" |
| 14 | **General Support** | ✅ Auto-resolve | Informational answer |

**Progressive escalation philosophy:**
```
System always follows: Auto-resolve → Contextual response → Awaiting info → Escalate
Never jumps straight to "I'll connect you with a human agent" when context allows resolution.
Responses reference real operational data: CEP, weather, refund status, pending actions.
```

**Intake data stripping:**
```python
# Phone/email from intake form stripped before routing
# Prevents false order number detection
_clean_for_routing("My order\nPhone: 11999999999")
→ "My order" (phone not detected as order number)
```

---

## HITL Human in the Loop

```
Escalated ticket
      |
      v
Operator Dashboard — Response tab
      |
      +── ✅ Approve & Resolve  → status: completed
      |    (buttons disappear, shows "Resolved by human review")
      +── ⏳ Awaiting Customer  → status: awaiting_customer_info
      +── ❌ Reject             → returns to queue

Auto-resolved tickets:
  → Show "✅ Resolved automatically by AI" badge
  → NO HITL buttons shown
```

**Pre-escalation modal:** Collects info BEFORE escalating:
- Order number, purchase date, amount (R$), reason

**Awaiting form:** Dynamic fields based on what's missing:
- Order number, email, screenshot (drag and drop)

**Refund denied dispute flow (progressive — never simultaneous awaiting + escalation):**
```
1. Initial denial  → 2-button choice card (no "human will contact you" message yet)
                       👍 Entendo a decisão  → closes
                       👎 Quero contestar   → AWAITING_CUSTOMER_DOCUMENTS form
2. Customer fills form → submits proof/bank statement
3. After submit        → Specialist review card
                         "Nossos especialistas irão realizar uma revisão humana"
                         Status: 🚨 Em Revisão
```

**UX state rules:**
```
AWAITING  → Show awaiting form only (⏳ Awaiting Your Response badge)
            NO escalation message shown simultaneously
ESCALATED → Show escalation confirmation + reference ID
            Status: 🚨 Under Specialist Review
            "Our team will contact you within 24h"
```

---

## Quality Evaluation LLM as Judge

**Cross-model evaluation: Claude Sonnet 4.6 judges Claude Haiku 4.5 responses.**

```python
# Industry pattern: stronger model evaluates weaker model
JUDGE_MODEL = "claude-sonnet-4-20250514"   # evaluator
RESPONSE_MODEL = "claude-haiku-4-5"         # responder
```

**Evaluation dimensions (0-10):**

| Dimension | What it measures |
|-----------|-----------------|
| **Faithfulness** | Did it use real data? Any hallucinations? |
| **Relevance** | Did it answer what was asked? |
| **Empathy** | Was the tone warm and professional? |
| **Completeness** | Was enough info provided to help? |

**Grade scale:**

| Grade | Score | Meaning |
|-------|-------|---------|
| A | ≥ 8.5 | Excellent |
| B | ≥ 7.0 | Good |
| C | ≥ 5.5 | Fair |
| D | ≥ 4.0 | Poor |
| F | < 4.0 | Failed |

**Hallucination detection:** Sonnet explicitly checks if the response invented facts not present in the context (knowledge base or external API data).

**Runs in background** — does not block customer response. Results appear in Operator Dashboard ~10s after ticket creation.

---

## Observability and Metrics

### Per-ticket (Operator → Observability tab)

```
Token Usage per Agent:
  Analysis Crew:   in=401  out=49   cost=$0.000378
  Knowledge Agent: in=312  out=89   cost=$0.000422
  Response Agent:  in=716  out=362  cost=$0.002021
  Summary Agent:   in=245  out=67   cost=$0.000321
  Quality Agent:   in=812  out=134  cost=$0.003450
  ─────────────────────────────────────────────────
  Total:           2,486   701      $0.006592
```

### Aggregated (Analytics tab)

**Customer Metrics:**
- CSAT Score (👍 👎 buttons)
- Not Helpful top reasons
- Tickets by category, sentiment, urgency
- Ticket timeline (last 7/30/90 days)
- External API Business Impact

**System Metrics — Agent Health Dashboard (9 cards):**

| Row | Cards |
|-----|-------|
| Quality | Classification Accuracy, Avg Response Time, Model Confidence |
| Operational | Cost per Ticket, Fallback Rate, Pipeline Depth |
| Pipeline | KB Coverage, Throughput, Fallback Rate |
| External APIs | ViaCEP Latency, Weather Latency, Refund DB Latency, Resilience |

**Additional Analytics:**
- ⏱️ Resolution time by category (bar chart with color coding)
- 💰 Cost forecasting (daily/weekly/monthly/yearly projections)
- ⚖️ Quality Evaluation section (avg scores + grade distribution)
- 🧠 Hallucination Rate card

---

## Authentication and Privacy

### JWT Guest Authentication

```
Login screen → accept T&C + Privacy Policy (must scroll to read)
      ↓
POST /auth/guest → JWT token generated
  {
    "sub": "guest-{unique-12-char-id}",
    "role": "guest",
    "exp": 24 hours
  }
      ↓
Token stored ONLY in browser localStorage
NEVER sent to database
      ↓
All tickets linked to guest-{unique-id}
Completely isolated from other users
      ↓
After 24 hours: token expires + tickets auto-deleted
```

### Privacy (LGPD Compliant)

```
What we store in Neon:
  ✅ Support tickets (linked to anonymous session ID)
  ✅ Usage metrics (response time, category)
  ❌ No real names, emails, or phone numbers

What we DON'T store:
  ❌ JWT token (browser only)
  ❌ IP addresses (Render processes, not stored)
  ❌ Payment information

Automatic deletion:
  Guest tickets deleted after 24 hours
  Historical demo data: permanent (not personal)

Your rights (LGPD Art. 18):
  Contact: beatrizcostaleal1996@gmail.com
```

### Data isolation

```
User A (guest-a1b2c3) → sees only their tickets
User B (guest-x9y8z7) → sees only their tickets
Historical mode → shared demo dataset (266 tickets)
```

---

## Frontend

Dual-view single-page application (vanilla JS, no framework):

### Customer Portal (light violet theme)

- Intake form: name, email, phone (not stored permanently)
- ⚡ **Quick Demo dropdown** — 25+ pre-filled scenarios
- Dynamic response cards:
  - ✅ Auto-resolve → response + 👍 👎 feedback
  - 📋 Step-by-step → numbered steps + links
  - ⏳ Awaiting → dynamic fields + screenshot upload
  - 🚨 Escalation → reference ID + confirmation
  - 💰 Refund denied → accept / dispute / talk to human
  - ⏳ Pending action → contextual instructions with deadline
- Not Helpful questionnaire (5 reasons)

### Operator Dashboard (dark glassmorphism theme)

- **Dataset Toggle:** Live (My Tickets) ↔ Historical Demo Dataset
- Mode explanation banner (honest about isolation)
- Ticket queue with filters: status, category, API tags
- Quick filter pills: 🚨 Pending | ⏳ Awaiting | ✅ Resolved | 👤 Human Req
- API tags on tickets: 🚛 Logistics | ⛈️ Weather | ✅ Refund Found | ❌ Denied
- Ticket detail tabs:
  - **Response** — AI response + AI Summary + HITL buttons + Quality Evaluation
  - **Agent Timeline** — colored dots, collab badges
  - **Knowledge** — snippets sent to LLM
  - **Observability** — token table + cost breakdown
- Analytics tab (full metrics)
- Export: 📊 Excel (4 sheets) + 📄 PDF (3 pages) with date filter
- Demo Controls: clear tickets, manage backups

---

## Export Features

### Excel (.xlsx) — 4 sheets

| Sheet | Contents |
|-------|---------|
| 📊 Dashboard | KPI cards + pie chart + resolution time bars |
| 🎫 Tickets | Full table with color-coded status + auto-filter |
| 🤖 Agent Performance | Metrics table + cost bar chart |
| 💰 Cost Analysis | Projections + agent breakdown |

### PDF — 3 pages

| Page | Contents |
|------|---------|
| 1 | Cover + Executive Summary + Category breakdown |
| 2 | Resolution time table + Agent performance |
| 3 | Cost forecasting + Agent cost breakdown |

**Date filter:** Last 7 days / Last 30 days / Last 90 days / All time

---

## Setup and Installation

### Prerequisites
- Python 3.11+
- Git

```bash
git clone https://github.com/BeatrizCoder/agentic-support-platform.git
cd agentic-support-platform

python3 -m venv .venv
source .venv/bin/activate

pip install -r requirements.txt

cp .env.example .env
# Edit .env with your API keys
```

---

## Environment Variables

| Variable | Required | Description |
|----------|----------|-------------|
| `ANTHROPIC_API_KEY` | ✅ | Claude Haiku 4.5 + Sonnet 4.6 |
| `OPENWEATHER_API_KEY` | ✅ | OpenWeatherMap free tier |
| `INTERNAL_API_KEY` | ✅ | Operator dashboard auth |
| `DATABASE_URL` | ✅ | PostgreSQL (Neon) or SQLite fallback |
| `JWT_SECRET` | ✅ | Secret for JWT signing |
| `ALLOWED_ORIGINS` | No | CORS origins (comma-separated) |
| `MAX_KNOWLEDGE_SNIPPETS` | No | Default: 3 |
| `MAX_SNIPPET_CHARS` | No | Default: 800 |
| `USE_LLM` | No | Default: True |
| `ENABLE_EXTERNAL_APIS` | No | Default: true |
| `JWT_EXPIRE_HOURS` | No | Default: 24 |

---

## Running the Project

```bash
# Option 1 — Start script
chmod +x start_demo.sh
./start_demo.sh

# Option 2 — Manual
# Terminal 1: Backend
source .venv/bin/activate
uvicorn aamad.backend:app --reload --port 8000

# Terminal 2: Frontend
python3 -m http.server 5500
```

**URLs:**
- Customer Portal: http://localhost:5500/index.html
- Operator Dashboard: same URL → Operator View
- API Docs: http://127.0.0.1:8000/docs
- Health Check: http://127.0.0.1:8000/health

---

## Deploy

| Service | Purpose | Plan |
|---------|---------|------|
| **Railway** | Backend principal (FastAPI + Docker) | Hobby ($5 crédito/mês, sem cold start) |
| **Render** | Backend backup (FastAPI + Docker) | Free (cold start após 15min) |
| **Vercel** | Frontend (static HTML) | Free |
| **Neon** | PostgreSQL database | Free (0.5GB, São Paulo) |
| **UptimeRobot** | Keep backend awake | Free (5min ping) |

**Deploy flow:**
```
git push → GitHub → Railway auto-deploy (backend principal)
                  → Render auto-deploy (backend backup)
                  → Vercel auto-deploy (frontend)
```

---

## Project Structure

```
agentic-support-platform/
│
├── src/aamad/
│   ├── backend.py              ← FastAPI app (49 lines — entry point only)
│   ├── routing_engine.py       ← Priority routing matrix
│   ├── observability.py        ← Structured event tracking
│   ├── services.py             ← KnowledgeService (RAG)
│   ├── data_store.py           ← SQLAlchemy + Neon PostgreSQL
│   ├── auth.py                 ← JWT guest authentication
│   ├── api/
│   │   ├── models.py           ← Pydantic request/response models
│   │   └── routes.py           ← 20+ FastAPI endpoints via APIRouter
│   ├── agents/
│   │   ├── definitions.py      ← 6 CrewAI Agent definitions
│   │   ├── tasks.py            ← CrewAI Task factories
│   │   └── crews.py            ← 3 CrewAI Crew orchestrators
│   ├── exports/
│   │   ├── excel_export.py     ← openpyxl Excel report (4 sheets)
│   │   └── pdf_export.py       ← reportlab PDF report (3 pages)
│   ├── flow/
│   │   ├── state.py            ← SupportState model
│   │   ├── steps.py            ← SupportFlowStepsMixin
│   │   └── support_flow.py     ← CrewAI Flow orchestrator
│   └── core/
│       ├── config.py           ← Env vars, JWT, rate limiter
│       └── services.py         ← Singleton container
│
├── tools/
│   ├── utils.py                    ← clean_inquiry, detect_language
│   ├── classification_tool.py      ← (legacy, replaced by CrewAI Agent)
│   ├── sentiment_tool.py           ← (legacy, replaced by CrewAI Agent)
│   ├── knowledge_tool.py           ← ⚙️ Local RAG retrieval
│   ├── response_tool.py            ← (legacy, replaced by CrewAI Agent)
│   ├── escalation_tool.py          ← ⚙️ Keyword matching
│   ├── address_validation_tool.py  ← 🌐 ViaCEP (httpx async)
│   ├── weather_check_tool.py       ← 🌐 OpenWeatherMap (httpx async)
│   ├── refund_lookup_tool.py       ← 🗄️ SQLite refunds table
│   ├── pending_action_tool.py      ← 🗄️ SQLite pending actions
│   └── quality_evaluator.py        ← 🤖 Sonnet LLM-as-judge
│
├── knowledge/
│   ├── order_issues.md
│   ├── billing.md
│   ├── account_access.md
│   ├── technical_issues.md
│   ├── general_support.md
│   └── escalation_policy.md
│
├── tests/
│   └── test_external_tools.py  ← 5/5 passing
│
├── index.html          ← Full frontend (vanilla JS, ~8000 lines)
├── Dockerfile          ← Python 3.11-slim for Render
├── start_demo.sh       ← Local startup script
├── prepare_demo.sh     ← Pre-demo: backup + merge + clean
├── railway.toml        ← Railway deploy config
├── render.yaml         ← Render deploy config
├── vercel.json         ← Vercel static deploy config
├── requirements.txt
├── .env.example
└── README.md
```

---

## Tech Stack

| Layer | Technology | Why |
|-------|-----------|-----|
| Backend | FastAPI | Fast, async, auto-docs |
| AI Orchestration | CrewAI Flow | Multi-agent pipeline with state |
| AI Agents | CrewAI (Agents + Tasks + Crews) | Real agent roles, goals, backstories |
| LLM (agents) | Claude Haiku 4.5 | Fast + cost-effective |
| LLM (judge) | Claude Sonnet 4.6 | Stronger model for evaluation |
| HTTP Client | httpx (async) | Truly async, no thread blocking |
| Database | PostgreSQL via Neon | Production-grade, AWS São Paulo |
| ORM | SQLAlchemy | Type-safe, migration support |
| Auth | JWT (python-jose) | Stateless, 24h sessions |
| Rate Limiting | slowapi | FastAPI-native, IP-based |
| Export | openpyxl + reportlab | Professional Excel + PDF |
| Frontend | Vanilla JS | Zero dependencies, fast |
| Deploy (backend) | Railway + Render + Docker | Railway: no cold start; Render: backup |
| Deploy (frontend) | Vercel | CDN, instant, free |
| Database hosting | Neon | Serverless PostgreSQL, free tier |
| Monitoring | UptimeRobot | Prevents cold start |

---

## Roadmap

```
Near term:
  - Streaming responses (SSE/WebSocket)
  - Vector DB for semantic RAG (ChromaDB/Pinecone)
  - GCP Cloud Run (production-grade deploy)
  - JWT with real user accounts (email + password)

LLM quality:
  - Ground truth evaluation dataset
  - A/B testing prompts
  - DeepEval/Ragas integration

Analytics:
  - NPS tracking
  - Predictive escalation (ML model)
  - Multi-language support expansion

✅ Already implemented (previously in roadmap):
  - Railway deploy (no cold start)
  - JWT guest authentication (24h sessions)
  - Excel + PDF export
  - Resolution time by category
  - Cost forecasting
  - User-isolated tickets
  - LGPD-compliant privacy
```

---

*Built by Beatriz Costa — May 2026*  
*Course: "Become An Agentic Architect" by Carmelo Iaria*  
*Open source for educational purposes — github.com/BeatrizCoder*
