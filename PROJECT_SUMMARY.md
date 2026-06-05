# Agentic Support Platform

## 1. One-line value proposition
Multi-agent AI customer support platform that
combines CrewAI Flow orchestration, real-time
external APIs, and LLM-as-judge quality evaluation
to automate e-commerce support at scale.

## 2. Target market
E-commerce companies needing intelligent
customer support automation with HITL oversight.

## 3. Key capabilities
- 3 CrewAI Crews with 6 specialized AI agents
- CrewAI Flow orchestrating full 9-step pipeline
- Real-time integrations: ViaCEP + OpenWeatherMap + Refund DB
- Cross-model quality evaluation (Sonnet judges Haiku)
- Human-in-the-loop (HITL) with approve/reject/await
- JWT guest authentication with persistent session isolation
- Export to Excel (4 sheets) + PDF (3 pages)
- Dataset toggle: Live (user-isolated) vs Historical (491 demo tickets)
- LGPD-compliant privacy policy
- Progressive escalation routing (13 priority levels)

## 4. Technical architecture
- Frontend: Vanilla JS (no framework, ~8000 lines)
- Backend: FastAPI + CrewAI Flow + Python 3.11
- Database: Neon PostgreSQL (AWS São Paulo)
- LLM: Claude Haiku 4.5 (agents) + Sonnet 4.6 (judge)
- Deploy: Railway (backend) + Vercel (frontend)
- Auth: JWT guest tokens (python-jose)
- Export: openpyxl + reportlab

## 5. Multi-agent crew design

| Agent | Role | Model | Trigger |
|-------|------|-------|---------|
| Classification Agent | Category + language detection | Haiku 4.5 | Every ticket |
| Sentiment Agent | Emotion + urgency analysis | Haiku 4.5 | Parallel with Classification |
| Knowledge Agent | RAG synthesis | Haiku 4.5 | After routing |
| Response Agent | Personalized response generation | Haiku 4.5 | After knowledge |
| Summary Agent | 2-line operator summary | Haiku 4.5 | Background |
| Quality Agent | Cross-model evaluation | Sonnet 4.6 | Background (judge) |

## 6. Deployment
- Backend: Railway (primary) + Render (backup)
- Frontend: Vercel
- Database: Neon PostgreSQL
- Monitoring: UptimeRobot
