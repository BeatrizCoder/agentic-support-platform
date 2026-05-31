"""FastAPI routes for the AAMAD support platform."""

import logging
import time
import uuid
from datetime import datetime
from typing import Any, Dict, List

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import StreamingResponse
from sqlalchemy import text as sa_text

from pydantic import BaseModel

from ..config import ENABLE_MOCK_INTEGRATIONS
from ..core.config import verify_api_key, limiter, JWT_EXPIRE_HOURS, INTERNAL_API_KEY, JWT_SECRET, JWT_ALGORITHM
from ..core import services as _svc
from ..data_store import data_store, SupportTicketData
from ..flow.support_flow import SupportFlow
from ..auth import create_guest_token, verify_token, optional_token
from .models import (
    FeedbackRequest, RunMetrics, StatusResponse,
    StepsResponse, SupportResponse, SupportTicket, TraceResponse,
)

logger = logging.getLogger(__name__)

try:
    from ..exports.excel_export import generate_excel_report
    from ..exports.pdf_export import generate_pdf_report
    _EXPORTS_AVAILABLE = True
except ImportError as _exc:
    logger.warning("Export dependencies not available (%s). Install openpyxl and reportlab.", _exc)
    _EXPORTS_AVAILABLE = False

router = APIRouter()


def _fallback_ticket_data(inquiry: str, error: Exception, started_at: float) -> SupportTicketData:
    """Create a minimal fallback ticket payload when the support flow fails."""
    now = datetime.now().isoformat()
    wall_time = round(time.time() - started_at, 3)
    reference_id = f"REF-{int(time.time())}"
    run_id = str(uuid.uuid4())
    return SupportTicketData(
        reference_id=reference_id,
        inquiry=inquiry,
        category="General Support",
        category_confidence=55,
        sentiment="Neutral",
        sentiment_confidence=55,
        urgency="Low",
        articles=[],
        response=(
            "Thanks for your message. We received your request and are working to restore "
            "the support service. If you need immediate help, please try again in a moment."
        ),
        response_confidence=50,
        escalation_required=False,
        escalation_reason="Support service fallback response.",
        triggered_keyword=None,
        steps=[
            {
                "agent": "Fallback",
                "timestamp": now,
                "details": {
                    "error": str(error),
                    "mode": "fallback",
                },
            }
        ],
        knowledge_source="fallback",
        memory_saved=False,
        execution_mode="fallback",
        prompt_template_used=None,
        skills_used=[],
        tools_used=[],
        cache_used=False,
        status="completed",
        created_at=now,
        updated_at=now,
        feedback=None,
        run_id=run_id,
        execution_time_ms=int(wall_time * 1000),
        wall_time_sec=wall_time,
        token_usage={},
        cost_usd=0.0,
        api_tags=[],
        quality_evaluation={},
        pending_action={},
        user_id="anonymous",
    )


def _fallback_support_response(ticket_data: SupportTicketData) -> SupportResponse:
    return SupportResponse(
        inquiry=ticket_data.inquiry,
        category=ticket_data.category,
        category_confidence=ticket_data.category_confidence,
        sentiment=ticket_data.sentiment,
        sentiment_confidence=ticket_data.sentiment_confidence,
        urgency=ticket_data.urgency,
        articles=ticket_data.articles,
        response=ticket_data.response,
        response_confidence=ticket_data.response_confidence,
        escalation_required=ticket_data.escalation_required,
        escalation_reason=ticket_data.escalation_reason,
        reference_id=ticket_data.reference_id,
        triggered_keyword=ticket_data.triggered_keyword,
        steps=ticket_data.steps,
        knowledge_source=ticket_data.knowledge_source,
        memory_saved=ticket_data.memory_saved,
        execution_mode=ticket_data.execution_mode,
        prompt_template_used=ticket_data.prompt_template_used,
        skills_used=ticket_data.skills_used,
        tools_used=ticket_data.tools_used,
        cache_used=ticket_data.cache_used,
        run_id=ticket_data.run_id,
        wall_time_sec=ticket_data.wall_time_sec,
        token_usage=ticket_data.token_usage,
        cost_usd=ticket_data.cost_usd,
        routing_action="resolve",
        routing_reason="fallback",
        routing_missing_info=[],
        api_tags=ticket_data.api_tags,
        quality_evaluation=ticket_data.quality_evaluation,
        pending_action=ticket_data.pending_action,
    )


async def _verify_export_key(
    request: Request,
    api_key: str = Query(default=None),
) -> None:
    """Accept API key from either X-API-Key header or ?api_key= query param."""
    key = request.headers.get("X-API-Key") or api_key
    if key != INTERNAL_API_KEY:
        raise HTTPException(status_code=403, detail="Invalid or missing API key")


# ── Auth models ───────────────────────────────────────────────────────────────

class GuestLoginRequest(BaseModel):
    accepted_terms: bool
    accepted_privacy: bool
    persistent_user_id: str = None


# ── Auth endpoints ────────────────────────────────────────────────────────────

@router.post("/auth/guest")
async def guest_login(request: GuestLoginRequest):
    if not request.accepted_terms:
        raise HTTPException(status_code=400, detail="Must accept Terms of Service")
    if not request.accepted_privacy:
        raise HTTPException(status_code=400, detail="Must accept Privacy Policy")
    token = create_guest_token(persistent_user_id=request.persistent_user_id)
    from jose import jwt as jose_jwt
    payload = jose_jwt.decode(token, JWT_SECRET, algorithms=[JWT_ALGORITHM])
    user_id = payload.get("sub")
    logger.info("Guest login: user_id=%s", user_id)
    return {
        "access_token": token,
        "token_type": "bearer",
        "expires_in": JWT_EXPIRE_HOURS * 3600,
        "user": {
            "name": "Demo User",
            "role": "guest",
            "email": "guest@demo.com",
            "user_id": user_id,
        },
    }


@router.get("/auth/me")
async def get_me(user=Depends(verify_token)):
    return {
        "name": user.get("name", "Demo User"),
        "role": user.get("role", "guest"),
        "email": user.get("sub"),
        "user_id": user.get("sub"),
    }


# ── Dataset helpers ───────────────────────────────────────────────────────────

def _get_demo_tickets() -> List[SupportTicketData]:
    """Read tickets from demo_dataset.db (or historical_seed.json fallback)."""
    import json as _json
    from pathlib import Path as _Path

    def _j(r, key, default="[]"):
        raw = r.get(key)
        if raw is None:
            return _json.loads(default)
        if isinstance(raw, (list, dict)):
            return raw
        try:
            return _json.loads(raw)
        except Exception:
            return _json.loads(default)

    def _rows_to_tickets(rows):
        result = []
        for r in rows:
            r = dict(r)
            result.append(SupportTicketData(
                reference_id=r.get("reference_id", ""),
                run_id=r.get("run_id", ""),
                inquiry=r.get("inquiry", ""),
                category=r.get("category", ""),
                category_confidence=r.get("category_confidence") or 0,
                sentiment=r.get("sentiment", ""),
                sentiment_confidence=r.get("sentiment_confidence") or 0,
                urgency=r.get("urgency", ""),
                articles=_j(r, "articles"),
                escalation_required=bool(r.get("escalation_required", False)),
                escalation_reason=r.get("escalation_reason", ""),
                triggered_keyword=r.get("triggered_keyword"),
                response=r.get("response", ""),
                response_confidence=r.get("response_confidence") or 0,
                quality_evaluation=_j(r, "quality_evaluation", "{}"),
                steps=_j(r, "steps"),
                tools_used=_j(r, "tools_used"),
                api_tags=_j(r, "api_tags"),
                execution_time_ms=r.get("execution_time_ms") or 0,
                created_at=r.get("created_at", ""),
                updated_at=r.get("updated_at") or r.get("created_at", ""),
                pending_action=_j(r, "pending_action", "{}"),
                knowledge_source=r.get("knowledge_source", ""),
                memory_saved=bool(r.get("memory_saved", False)),
                execution_mode=r.get("execution_mode", ""),
                cache_used=bool(r.get("cache_used", False)),
            ))
        return result

    demo_db = _Path("src/aamad/data/demo_dataset.db")
    seed_json = _Path("src/aamad/data/historical_seed.json")

    if demo_db.exists():
        try:
            from sqlalchemy import create_engine, text as _text
            engine = create_engine(f"sqlite:///{demo_db}")
            with engine.connect() as conn:
                rows = conn.execute(
                    _text("SELECT * FROM support_tickets ORDER BY created_at DESC")
                ).mappings().all()
            return _rows_to_tickets(rows)
        except Exception as _e:
            logger.error("Error reading demo_dataset.db: %s", _e)

    if seed_json.exists():
        try:
            with open(seed_json, encoding="utf-8") as f:
                raw = _json.load(f)
            if raw:
                return _rows_to_tickets(raw)
        except Exception as _e:
            logger.error("Error reading historical_seed.json: %s", _e)

    # Fallback: read is_historical=TRUE rows from Neon
    try:
        neon_tickets = data_store.get_historical_tickets()
        if neon_tickets:
            logger.info("_get_demo_tickets: loaded %d historical tickets from Neon", len(neon_tickets))
            return neon_tickets
    except Exception as _e:
        logger.error("Error reading historical tickets from Neon: %s", _e)

    return []


def _get_ticket_count(mode: str) -> int:
    """Get ticket count for a given mode without side effects."""
    import json as _json
    from pathlib import Path as _Path

    try:
        if mode == "historical":
            demo_db = _Path("src/aamad/data/demo_dataset.db")
            seed_json = _Path("src/aamad/data/historical_seed.json")
            if demo_db.exists():
                from sqlalchemy import create_engine, text as _text
                engine = create_engine(f"sqlite:///{demo_db}")
                with engine.connect() as conn:
                    return conn.execute(
                        _text("SELECT COUNT(*) FROM support_tickets")
                    ).scalar() or 0
            if seed_json.exists():
                with open(seed_json, encoding="utf-8") as f:
                    return len(_json.load(f))
            return 0
        else:
            from sqlalchemy import text as _text
            with data_store.SessionLocal() as session:
                return session.execute(
                    _text("SELECT COUNT(*) FROM support_tickets")
                ).scalar() or 0
    except Exception:
        return 0


def _get_historical_csat_metrics() -> Dict[str, Any]:
    """Calculate CSAT from the demo SQLite DB, handling JSON-dict and plain-string feedback."""
    import json as _json
    from sqlalchemy import create_engine, text as _text
    from pathlib import Path as _Path

    empty: Dict[str, Any] = {"csat_score": None, "csat_positive": 0, "csat_negative": 0, "total_feedback": 0}
    demo_db = _Path("src/aamad/data/demo_dataset.db")
    seed_json = _Path("src/aamad/data/historical_seed.json")
    if not demo_db.exists() and not seed_json.exists():
        return empty

    feedback_values = []
    if demo_db.exists():
        try:
            engine = create_engine(f"sqlite:///{demo_db}")
            with engine.connect() as conn:
                rows = conn.execute(
                    _text(
                        "SELECT feedback FROM support_tickets "
                        "WHERE feedback IS NOT NULL "
                        "AND CAST(feedback AS TEXT) != 'null' "
                        "AND CAST(feedback AS TEXT) != '\"\"'"
                    )
                ).fetchall()
            feedback_values = [r[0] for r in rows]
        except Exception as e:
            logger.warning("Historical CSAT SQLite error: %s", e)
    if not feedback_values and seed_json.exists():
        try:
            with open(seed_json, encoding="utf-8") as f:
                seed = _json.load(f)
            feedback_values = [
                t.get("feedback") for t in seed
                if t.get("feedback") not in (None, "null", '""', "")
            ]
        except Exception as e:
            logger.warning("Historical CSAT JSON error: %s", e)

    positive = 0
    negative = 0

    for fb in feedback_values:
        try:
            if isinstance(fb, dict):
                if fb.get("helpful") is True or fb.get("value") == "positive":
                    positive += 1
                elif fb.get("helpful") is False or fb.get("value") == "negative":
                    negative += 1
            elif isinstance(fb, str):
                try:
                    parsed = _json.loads(fb)
                    if isinstance(parsed, dict):
                        if parsed.get("helpful") is True or parsed.get("value") == "positive":
                            positive += 1
                        elif parsed.get("helpful") is False or parsed.get("value") == "negative":
                            negative += 1
                    elif isinstance(parsed, bool):
                        if parsed:
                            positive += 1
                        else:
                            negative += 1
                    elif isinstance(parsed, str):
                        if parsed in ["positive", "helpful", "true", "1"]:
                            positive += 1
                        elif parsed in ["negative", "not_helpful", "false", "0"]:
                            negative += 1
                except Exception:
                    if fb in ("positive", "helpful", "1", "true"):
                        positive += 1
                    elif fb in ("negative", "not_helpful", "0", "false"):
                        negative += 1
        except Exception as e:
            logger.warning("CSAT parse error (historical): %s", e)
            continue

    total_feedback = positive + negative
    csat = round(positive / total_feedback * 100, 1) if total_feedback > 0 else None
    return {
        "csat_score": csat,
        "csat_positive": positive,
        "csat_negative": negative,
        "total_feedback": total_feedback,
    }


# ── Endpoints ─────────────────────────────────────────────────────────────────

@router.api_route("/health", methods=["GET", "HEAD"])
@limiter.limit("60/minute")
async def health(request: Request) -> dict[str, str]:
    return {"status": "ok", "service": "aamad backend", "timestamp": datetime.now().isoformat()}


@router.post("/api/support", response_model=SupportResponse)
@limiter.limit("10/minute")
async def create_support_ticket(
    request: Request,
    ticket: SupportTicket,
    user=Depends(optional_token),
) -> SupportResponse:
    user_id = user.get("sub", "anonymous") if user else "anonymous"
    inquiry = ticket.inquiry.strip()
    if not inquiry:
        raise HTTPException(status_code=400, detail="Inquiry text cannot be empty.")

    run_id = str(uuid.uuid4())
    start_time = time.time()

    try:
        # Process the support request through the CrewAI flow
        support_flow = SupportFlow()
        await support_flow.kickoff_async({"inquiry": inquiry, "run_id": run_id})
        # Unwrap the StateProxy so Pydantic/pydantic-core sees real Python lists,
        # not LockedListProxy (whose C-level list buffer is always empty).
        final_state = support_flow.state._unwrap()

        wall_time = round(time.time() - start_time, 3)
        execution_time_ms = int(wall_time * 1000)

        # Generate reference ID
        reference_id = (
            final_state.reference_id
            if final_state.escalation_required
            else f"REF-{int(time.time())}"
        )

        # Remap observability events from run_id to the final reference_id.
        # ESC-* tickets do this inside the escalation step; REF-* tickets need
        # it here because the reference_id only exists after kickoff_async returns.
        if not final_state.escalation_required:
            _svc.observability_service.remap_events(run_id, reference_id)

        # Determine status based on routing action and escalation
        if final_state.routing_action == "awaiting":
            status = "awaiting_customer_info"
        elif final_state.escalation_required:
            status = "pending_human_review"
        else:
            status = "completed"

        # Capture RunMetrics and store in shared metrics_store
        metrics = RunMetrics(
            run_id=run_id,
            reference_id=reference_id,
            status=status,
            started_at=start_time,
            finished_at=time.time(),
            wall_time_sec=wall_time,
            step_count=len(final_state.steps),
            tool_latency_ms={
                step["agent"]: step.get("elapsed_ms", 0)
                for step in final_state.steps
            },
            token_usage={},
            success_rate=1.0 if not final_state.escalation_required else 0.8,
        )
        _svc.metrics_store[reference_id] = metrics

        # Create ticket data for persistence
        ticket_data = SupportTicketData(
            reference_id=reference_id,
            inquiry=inquiry,
            category=final_state.category,
            category_confidence=final_state.category_confidence,
            sentiment=final_state.sentiment,
            sentiment_confidence=final_state.sentiment_confidence,
            urgency=final_state.urgency,
            articles=final_state.articles,
            response=final_state.response,
            response_confidence=final_state.response_confidence,
            escalation_required=final_state.escalation_required,
            escalation_reason=final_state.escalation_reason,
            triggered_keyword=final_state.triggered_keyword,
            steps=final_state.steps,
            knowledge_source=final_state.knowledge_source,
            memory_saved=final_state.memory_saved,
            execution_mode=final_state.execution_mode,
            prompt_template_used=final_state.prompt_template_used,
            skills_used=final_state.skills_used,
            tools_used=final_state.tools_used,
            cache_used=final_state.cache_used,
            status=status,
            created_at=datetime.now().isoformat(),
            updated_at=datetime.now().isoformat(),
            run_id=run_id,
            execution_time_ms=execution_time_ms,
            wall_time_sec=wall_time,
            token_usage=final_state.token_usage,
            cost_usd=final_state.cost_usd,
            api_tags=final_state.api_tags,
            quality_evaluation=final_state.quality_evaluation or {},
            pending_action=final_state.pending_action or {},
            user_id=user_id,
        )

        try:
            # Save to data store
            data_store.save_ticket(ticket_data)

            # Write per-ticket observability summary to SQLite
            _svc.observability_service.finalize_ticket(
                reference_id=reference_id,
                wall_time_sec=wall_time,
                quality_evaluation=final_state.quality_evaluation or {},
            )
        except Exception:
            logger.exception("Failed to persist support ticket for reference_id=%s", reference_id)

        # Handle integrations if enabled
        if ENABLE_MOCK_INTEGRATIONS:
            try:
                if final_state.escalation_required:
                    _svc.ticketing_client.create_ticket({
                        "inquiry": inquiry,
                        "category": final_state.category,
                        "urgency": final_state.urgency,
                        "reference_id": reference_id,
                    })
                    _svc.notification_client.send_support_notification(
                        "support@company.com",
                        ticket_data.model_dump(),
                    )
                _svc.notification_client.send_customer_notification(
                    "customer@example.com",
                    ticket_data.model_dump(),
                )
            except Exception as e:
                logger.error("Integration error: %s", e)

        return SupportResponse(
            inquiry=inquiry,
            category=final_state.category,
            category_confidence=final_state.category_confidence,
            sentiment=final_state.sentiment,
            sentiment_confidence=final_state.sentiment_confidence,
            urgency=final_state.urgency,
            articles=final_state.articles,
            response=final_state.response,
            response_confidence=final_state.response_confidence,
            escalation_required=final_state.escalation_required,
            escalation_reason=final_state.escalation_reason,
            reference_id=reference_id,
            triggered_keyword=final_state.triggered_keyword,
            steps=final_state.steps,
            knowledge_source=final_state.knowledge_source,
            memory_saved=final_state.memory_saved,
            execution_mode=final_state.execution_mode,
            prompt_template_used=final_state.prompt_template_used,
            tools_used=final_state.tools_used,
            skills_used=final_state.skills_used,
            cache_used=final_state.cache_used,
            run_id=run_id,
            wall_time_sec=wall_time,
            token_usage=final_state.token_usage or None,
            cost_usd=final_state.cost_usd or None,
            routing_action=final_state.routing_action or None,
            routing_reason=final_state.routing_reason or None,
            routing_missing_info=final_state.routing_missing_info or None,
            api_tags=final_state.api_tags or None,
            quality_evaluation=final_state.quality_evaluation or None,
            pending_action=final_state.pending_action or None,
        )
    except Exception as exc:
        logger.exception("Support flow failed for inquiry=%r", inquiry)
        fallback_ticket = _fallback_ticket_data(inquiry, exc, start_time)
        try:
            data_store.save_ticket(fallback_ticket)
        except Exception:
            logger.exception("Failed to persist fallback support ticket")
        try:
            _svc.observability_service.finalize_ticket(
                reference_id=fallback_ticket.reference_id,
                wall_time_sec=fallback_ticket.wall_time_sec or 0.0,
                quality_evaluation={},
            )
        except Exception:
            logger.exception("Failed to finalize fallback observability")
        return _fallback_support_response(fallback_ticket)


@router.get("/api/support/{reference_id}/status", response_model=StatusResponse)
async def get_ticket_status(reference_id: str) -> StatusResponse:
    """Get the status of a support ticket."""
    ticket = data_store.get_ticket(reference_id)
    if not ticket:
        raise HTTPException(status_code=404, detail="Ticket not found")

    return StatusResponse(
        reference_id=ticket.reference_id,
        status=ticket.status,
        created_at=ticket.created_at,
        updated_at=ticket.updated_at,
        escalation_required=ticket.escalation_required,
        escalation_reason=ticket.escalation_reason,
    )


@router.get("/api/support/{reference_id}/steps", response_model=StepsResponse)
async def get_ticket_steps(reference_id: str) -> StepsResponse:
    """Get the execution steps of a support ticket."""
    ticket = data_store.get_ticket(reference_id)
    if not ticket:
        raise HTTPException(status_code=404, detail="Ticket not found")

    return StepsResponse(
        reference_id=ticket.reference_id,
        steps=ticket.steps,
        status=ticket.status,
    )


@router.get("/api/support/{reference_id}/trace", response_model=TraceResponse)
async def get_ticket_trace(
    reference_id: str, _=Depends(verify_api_key)
) -> TraceResponse:
    """Get observability trace for a support ticket execution."""
    ticket = data_store.get_ticket(reference_id)
    if not ticket:
        raise HTTPException(status_code=404, detail="Ticket not found")

    return TraceResponse(
        reference_id=ticket.reference_id,
        run_id=ticket.run_id or "",
        steps=ticket.steps,
        total_steps=len(ticket.steps),
        execution_time_ms=ticket.execution_time_ms,
        tools_used=ticket.tools_used,
        tracing_enabled=True,
    )


@router.get("/api/metrics/summary")
async def get_metrics_summary(
    user=Depends(optional_token),
    _=Depends(verify_api_key),
) -> Dict[str, Any]:
    """Aggregate performance metrics — strictly isolated by dataset mode."""
    historical = _svc.dataset_mode == "historical"
    user_id = user.get("sub") if user else "anonymous"

    logger.info("GET /metrics/summary: mode=%s user_id=%s has_jwt=%s", _svc.dataset_mode, user_id, bool(user))

    if historical:
        tickets = _get_demo_tickets()
        csat_metrics = _get_historical_csat_metrics()
    else:
        tickets = data_store.get_live_tickets(user_id=user_id)
        csat_metrics = data_store.get_csat_metrics(user_id=user_id)
        logger.info("Live metrics: tickets=%d csat=%s feedback=%s", len(tickets), csat_metrics.get("csat_score"), csat_metrics.get("total_feedback"))

    logger.info("Metrics: %d tickets loaded for mode=%s", len(tickets), _svc.dataset_mode)

    if not tickets:
        return {
            "total_runs": 0,
            "avg_wall_time_sec": 0.0,
            "success_rate": 1.0,
            "total_escalated": 0,
            "avg_step_count": 0.0,
            "slowest_tool": None,
            "fastest_run_sec": 0.0,
            "slowest_run_sec": 0.0,
            "csat_score": None,
            "helpful_count": 0,
            "not_helpful_count": 0,
            "total_feedback": 0,
            "feedback_rate": 0,
        }

    total = len(tickets)
    total_escalated = sum(1 for t in tickets if t.escalation_required)
    avg_wall_time = sum(t.execution_time_ms for t in tickets) / total / 1000
    success_rate = round((total - total_escalated) / total, 3)
    avg_step_count = round(sum(len(t.steps) for t in tickets) / total, 1)

    tool_times: Dict[str, list] = {}
    for ticket in tickets:
        for step in ticket.steps:
            details = step.get("details", {})
            tool = details.get("tool_used")
            exec_time = details.get("execution_time")
            if tool and exec_time:
                tool_times.setdefault(tool, []).append(float(exec_time))

    slowest_tool = None
    if tool_times:
        slowest_tool = max(
            tool_times, key=lambda t: sum(tool_times[t]) / len(tool_times[t])
        )

    run_times = [t.execution_time_ms / 1000 for t in tickets if t.execution_time_ms]
    fastest = round(min(run_times), 3) if run_times else 0.0
    slowest = round(max(run_times), 3) if run_times else 0.0

    csat_score = csat_metrics["csat_score"]
    helpful_count = csat_metrics["csat_positive"]
    not_helpful_count = csat_metrics["csat_negative"]
    total_feedback = csat_metrics["total_feedback"]

    obs_summary = data_store.get_observability_summary(
        user_id=user_id if not historical else None,
        historical_only=historical,
    )

    logger.info(
        "Metrics result: total=%s csat=%s feedback=%s",
        total, csat_score, total_feedback,
    )

    return {
        "total_runs": total,
        "avg_wall_time_sec": round(avg_wall_time, 3),
        "success_rate": success_rate,
        "total_escalated": total_escalated,
        "avg_step_count": avg_step_count,
        "slowest_tool": slowest_tool,
        "fastest_run_sec": fastest,
        "slowest_run_sec": slowest,
        "csat_score": csat_score,
        "csat_positive": helpful_count,
        "csat_negative": not_helpful_count,
        "helpful_count": helpful_count,
        "not_helpful_count": not_helpful_count,
        "total_feedback": total_feedback,
        "feedback_rate": round((total_feedback / total) * 100) if total > 0 else 0,
        "agent_performance": obs_summary.get("agent_performance", {}),
        "tool_usage": obs_summary.get("tool_usage", {}),
        "total_llm_calls": obs_summary.get("llm_calls", 0),
        "total_events": obs_summary.get("total_events", 0),
        "avg_tool_latency_ms": obs_summary.get("avg_latency_ms", 0),
        "total_real_cost_usd": obs_summary.get("total_cost_usd", 0.0),
    }


@router.get("/api/support/{reference_id}/metrics", response_model=RunMetrics)
async def get_ticket_metrics(
    reference_id: str, _=Depends(verify_api_key)
) -> RunMetrics:
    """Return RunMetrics for a specific ticket (in-memory, current session only)."""
    if reference_id not in _svc.metrics_store:
        raise HTTPException(
            status_code=404, detail="Metrics not found for this ticket"
        )
    return _svc.metrics_store[reference_id]


@router.get("/api/observability/summary")
async def get_observability_summary(
    user=Depends(optional_token),
    _=Depends(verify_api_key),
) -> dict:
    """Get aggregate observability metrics scoped to the active dataset mode."""
    historical = _svc.dataset_mode == "historical"
    user_id = user.get("sub") if user else "anonymous"
    return data_store.get_observability_summary(
        user_id=user_id if not historical else None,
        historical_only=historical,
    )


@router.get("/api/observability/tickets/{reference_id}")
async def get_ticket_observability(
    reference_id: str,
    _=Depends(verify_api_key),
) -> dict:
    """Get structured observability events for a ticket (SQLite-backed)."""
    events = _svc.observability_service.get_events(reference_id)
    if not events:
        raise HTTPException(
            status_code=404,
            detail="No observability events found for this ticket",
        )
    total_tokens = sum(
        e.get("total_tokens") or e.get("estimated_tokens", 0) for e in events
    )
    llm_calls = sum(
        1 for e in events
        if e.get("execution_mode") == "llm" or e.get("llm_used", False)
    )
    return {
        "reference_id": reference_id,
        "events": events,
        "total_events": len(events),
        "total_latency_ms": sum(e.get("latency_ms", 0) for e in events),
        "llm_calls": llm_calls,
        "total_tokens": total_tokens,
    }


@router.post("/api/support/{reference_id}/feedback")
async def submit_feedback(reference_id: str, feedback: FeedbackRequest):
    """Submit feedback for a support ticket."""
    logger.info(
        "Feedback received: ref=%s helpful=%s reason=%s",
        reference_id, feedback.helpful, feedback.feedback_reason,
    )

    ticket = data_store.get_ticket(reference_id)
    if not ticket:
        raise HTTPException(status_code=404, detail="Ticket not found")

    result = data_store.save_feedback(
        reference_id=reference_id,
        helpful=feedback.helpful,
        feedback_reason=feedback.feedback_reason or feedback.comments,
    )
    logger.info("Feedback saved: ref=%s result=%s", reference_id, result)
    return {"success": True, "message": "Feedback submitted successfully", "reference_id": reference_id}


@router.post("/api/support/{reference_id}/approve")
async def approve_ticket(reference_id: str, _=Depends(verify_api_key)):
    """Approve a support ticket (mark as resolved)."""
    ticket = data_store.get_ticket(reference_id)
    if not ticket:
        raise HTTPException(status_code=404, detail="Ticket not found")

    if ticket.status in ("completed", "approved"):
        raise HTTPException(
            status_code=400, detail="Ticket is already resolved"
        )

    data_store.update_ticket_status(reference_id, "approved")

    if ENABLE_MOCK_INTEGRATIONS:
        try:
            _svc.ticketing_client.update_ticket_status(
                f"EXT-{reference_id}", "resolved"
            )
        except Exception as e:
            logger.error("Integration error: %s", e)

    return {"message": "Ticket approved successfully", "reference_id": reference_id}


@router.post("/api/support/{reference_id}/reject")
async def reject_ticket(reference_id: str, _=Depends(verify_api_key)):
    """Reject a support ticket (requires revision)."""
    ticket = data_store.get_ticket(reference_id)
    if not ticket:
        raise HTTPException(status_code=404, detail="Ticket not found")

    if ticket.status != "pending_human_review":
        raise HTTPException(
            status_code=400, detail="Ticket is not pending human review"
        )

    data_store.update_ticket_status(reference_id, "rejected")

    if ENABLE_MOCK_INTEGRATIONS:
        try:
            _svc.ticketing_client.update_ticket_status(
                f"EXT-{reference_id}", "needs_revision"
            )
        except Exception as e:
            logger.error("Integration error: %s", e)

    return {"message": "Ticket rejected and marked for revision", "reference_id": reference_id}


@router.post("/api/support/{reference_id}/await-customer")
async def await_customer_info(reference_id: str, _=Depends(verify_api_key)):
    """Mark ticket as awaiting customer information."""
    ticket = data_store.get_ticket(reference_id)
    if not ticket:
        raise HTTPException(status_code=404, detail="Ticket not found")

    if ticket.status not in ("pending_human_review", "awaiting_customer_info"):
        raise HTTPException(
            status_code=400,
            detail="Ticket cannot be set to awaiting from current status",
        )

    data_store.update_ticket_status(reference_id, "awaiting_customer_info")
    return {"message": "Ticket awaiting customer response", "reference_id": reference_id}


@router.get("/api/tickets")
async def get_all_tickets_endpoint(
    user=Depends(optional_token),
    _=Depends(verify_api_key),
):
    """Get all tickets from the current dataset mode, filtered by user in live mode."""
    if _svc.dataset_mode == "historical":
        tickets = _get_demo_tickets()
    else:
        user_id = user.get("sub", "anonymous") if user else "anonymous"
        tickets = data_store.get_user_tickets(user_id)
        tickets = sorted(tickets, key=lambda t: t.created_at, reverse=True)
    return [ticket.model_dump() for ticket in tickets]


@router.get("/api/dataset/mode")
async def get_dataset_mode():
    """Get current dataset mode and ticket counts for both modes."""
    from pathlib import Path as _Path
    return {
        "mode": _svc.dataset_mode,
        "live_tickets": _get_ticket_count("live"),
        "historical_tickets": _get_ticket_count("historical"),
        "demo_dataset_exists": _Path("src/aamad/data/demo_dataset.db").exists(),
    }


@router.post("/api/admin/cleanup")
async def cleanup_sessions(_=Depends(verify_api_key)):
    """Delete expired guest session data (>24h). Safe to call via cron/UptimeRobot."""
    deleted = data_store.cleanup_expired_sessions()
    return {"deleted": deleted, "message": f"Cleaned up {deleted} expired tickets"}


@router.post("/api/admin/seed-historical")
async def force_seed_historical(_=Depends(verify_api_key)):
    """Force-seed historical_seed.json into Neon (idempotent — safe to call multiple times)."""
    count = data_store.seed_historical_tickets()
    return {
        "seeded": count,
        "message": f"Seeded {count} historical tickets",
    }


@router.post("/api/dataset/mode")
async def set_dataset_mode(payload: dict, _=Depends(verify_api_key)):
    """Switch dataset mode. Historical mode reads demo_dataset.db (read-only)."""
    from pathlib import Path as _Path

    mode = payload.get("mode", "live")
    if mode not in ("live", "historical"):
        raise HTTPException(
            status_code=400, detail="Mode must be 'live' or 'historical'"
        )

    _has_demo_db   = _Path("src/aamad/data/demo_dataset.db").exists()
    _has_seed_json = _Path("src/aamad/data/historical_seed.json").exists()
    if mode == "historical" and not _has_demo_db and not _has_seed_json:
        raise HTTPException(
            status_code=404,
            detail="Historical dataset not found (demo_dataset.db or historical_seed.json).",
        )

    _svc.dataset_mode = mode
    return {
        "success": True,
        "mode": _svc.dataset_mode,
        "live_tickets": _get_ticket_count("live"),
        "historical_tickets": _get_ticket_count("historical"),
        "message": f"Switched to {mode} mode",
    }


@router.get("/api/analytics/timeline")
async def get_ticket_timeline(
    days: int = 30,
    user=Depends(optional_token),
    _=Depends(verify_api_key),
):
    """Daily ticket counts for the last N days, with zeros for empty days."""
    historical = _svc.dataset_mode == "historical"
    user_id = user.get("sub") if user else None
    return data_store.get_ticket_timeline(
        days=days,
        historical_only=historical,
        user_id=user_id if not historical else None,
    )


@router.get("/api/analytics/resolution-time")
async def get_resolution_time(_=Depends(verify_api_key)):
    """Average pipeline execution time per category."""
    return data_store.get_resolution_time_by_category(
        historical_only=(_svc.dataset_mode == "historical")
    )


@router.get("/api/analytics/cost-forecast")
async def get_cost_forecast_endpoint(_=Depends(verify_api_key)):
    """Projected AI costs based on current ticket volume."""
    return data_store.get_cost_forecast(
        historical_only=(_svc.dataset_mode == "historical")
    )


def _build_export_metrics(tickets: list, historical: bool) -> dict:
    """Compute metrics dict for export functions from a ticket list."""
    from collections import Counter
    total = len(tickets)
    if not total:
        return {
            "total_runs": 0, "resolved": 0, "escalated": 0,
            "csat_score": None, "by_category": [],
        }

    escalated = sum(1 for t in tickets if (
        t.escalation_required if hasattr(t, "escalation_required")
        else t.get("escalation_required", False)
    ))
    resolved = total - escalated

    cat_counter: Counter = Counter()
    for t in tickets:
        cat = t.category if hasattr(t, "category") else t.get("category", "")
        cat_counter[cat] += 1

    by_category = [
        {"category": cat, "count": count}
        for cat, count in cat_counter.most_common()
    ]

    if historical:
        csat = _get_historical_csat_metrics()
    else:
        csat = data_store.get_csat_metrics()

    return {
        "total_runs": total,
        "resolved": resolved,
        "escalated": escalated,
        "csat_score": csat.get("csat_score"),
        "helpful_count": csat.get("csat_positive", 0),
        "not_helpful_count": csat.get("csat_negative", 0),
        "by_category": by_category,
    }


def _obs_to_agent_list(obs_summary: dict) -> list:
    """Convert observability agent_performance dict to list for exports."""
    result = []
    for name, data in (obs_summary.get("agent_performance") or {}).items():
        result.append({
            "agent_name": name,
            "step_name": name,
            "calls": data.get("calls", 0),
            "avg_latency_ms": data.get("avg_latency_ms", 0),
            "avg_input_tokens": 0,
            "avg_output_tokens": 0,
            "avg_cost_usd": round(
                data.get("total_cost_usd", 0) / data.get("calls", 1), 8
            ) if data.get("calls") else 0,
            "execution_mode": "llm",
        })
    return result


@router.get("/api/export/excel")
async def export_excel(
    request: Request,
    days: int = 30,
    user=Depends(optional_token),
    _=Depends(_verify_export_key),
):
    """Export analytics report as Excel (.xlsx)."""
    if not _EXPORTS_AVAILABLE:
        raise HTTPException(status_code=503, detail="Export dependencies (openpyxl) not installed on this server.")
    historical = _svc.dataset_mode == "historical"
    user_id = user.get("sub", "anonymous") if user else "anonymous"

    tickets_for_export = data_store.get_tickets_filtered(
        days=days,
        historical_only=historical,
        user_id=user_id if not historical else None,
    )
    if not tickets_for_export and historical:
        tickets_for_export = _get_demo_tickets()

    metrics = _build_export_metrics(tickets_for_export, historical)
    obs = data_store.get_observability_summary(
        user_id=user_id if not historical else None,
        historical_only=historical,
    )
    agent_metrics = _obs_to_agent_list(obs)

    period_label = (
        "Historical Dataset" if historical
        else ("All Time" if days == 0 else f"Last {days} Days")
    )
    buffer = generate_excel_report(
        tickets=[t.model_dump() if hasattr(t, "model_dump") else t for t in tickets_for_export],
        metrics=metrics,
        agent_metrics=agent_metrics,
        cost_forecast=data_store.get_cost_forecast(historical_only=historical),
        resolution_time=data_store.get_resolution_time_by_category(historical_only=historical),
        period=period_label,
    )

    filename = f"agentic_support_report_{datetime.now().strftime('%Y%m%d_%H%M')}.xlsx"
    return StreamingResponse(
        buffer,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={
            "Content-Disposition": f"attachment; filename={filename}",
            "Access-Control-Expose-Headers": "Content-Disposition",
            "Cache-Control": "no-cache",
        },
    )


@router.get("/api/export/pdf")
async def export_pdf(
    request: Request,
    days: int = 30,
    user=Depends(optional_token),
    _=Depends(_verify_export_key),
):
    """Export analytics report as PDF."""
    if not _EXPORTS_AVAILABLE:
        raise HTTPException(status_code=503, detail="Export dependencies (reportlab) not installed on this server.")
    historical = _svc.dataset_mode == "historical"
    user_id = user.get("sub", "anonymous") if user else "anonymous"

    tickets_for_export = data_store.get_tickets_filtered(
        days=days,
        historical_only=historical,
        user_id=user_id if not historical else None,
    )
    if not tickets_for_export and historical:
        tickets_for_export = _get_demo_tickets()

    metrics = _build_export_metrics(tickets_for_export, historical)
    obs = data_store.get_observability_summary(
        user_id=user_id if not historical else None,
        historical_only=historical,
    )
    agent_metrics = _obs_to_agent_list(obs)

    period_label = (
        "Historical Dataset" if historical
        else ("All Time" if days == 0 else f"Last {days} Days")
    )
    buffer = generate_pdf_report(
        tickets=[t.model_dump() if hasattr(t, "model_dump") else t for t in tickets_for_export],
        metrics=metrics,
        agent_metrics=agent_metrics,
        cost_forecast=data_store.get_cost_forecast(historical_only=historical),
        resolution_time=data_store.get_resolution_time_by_category(historical_only=historical),
        period=period_label,
    )

    filename = f"agentic_support_report_{datetime.now().strftime('%Y%m%d_%H%M')}.pdf"
    return StreamingResponse(
        buffer,
        media_type="application/pdf",
        headers={
            "Content-Disposition": f"attachment; filename={filename}",
            "Access-Control-Expose-Headers": "Content-Disposition",
            "Cache-Control": "no-cache",
        },
    )


@router.delete("/api/tickets/clear")
async def clear_tickets(_=Depends(verify_api_key)):
    """Delete all tickets and create an automatic backup first."""
    import shutil
    from pathlib import Path

    db_path = Path("src/aamad/data/tickets.db")
    backup_path = None
    if db_path.exists():
        backup_name = f"support_backup_{datetime.now().strftime('%Y%m%d_%H%M%S')}.db"
        backup_path = Path(f"src/aamad/data/{backup_name}")
        shutil.copy2(db_path, backup_path)

    with data_store.SessionLocal() as session:
        result = session.execute(sa_text("DELETE FROM support_tickets"))
        session.commit()
        deleted = result.rowcount

    return {
        "success": True,
        "deleted_tickets": deleted,
        "backup_created": str(backup_path) if backup_path else None,
        "backup_filename": backup_path.name if backup_path else None,
        "message": (
            f"Cleared {deleted} tickets. "
            f"Backup: {backup_path.name if backup_path else 'none'}"
        ),
    }


@router.get("/api/tickets/backups")
async def list_backups(_=Depends(verify_api_key)):
    """List all automatic database backups."""
    from pathlib import Path
    data_dir = Path("src/aamad/data")
    backups = sorted(data_dir.glob("support_backup_*.db"), reverse=True)
    return {
        "backups": [
            {
                "filename": b.name,
                "created_at": b.stem.replace("support_backup_", ""),
                "size_kb": round(b.stat().st_size / 1024, 1),
            }
            for b in backups
        ]
    }


@router.post("/api/tickets/restore")
async def restore_backup(payload: dict, _=Depends(verify_api_key)):
    """Restore the database from a named backup file."""
    import shutil
    from pathlib import Path

    backup_file = payload.get("backup_file")
    if not backup_file:
        raise HTTPException(status_code=400, detail="backup_file is required")
    backup_path = Path(f"src/aamad/data/{backup_file}")
    db_path = Path("src/aamad/data/tickets.db")
    if not backup_path.exists():
        raise HTTPException(
            status_code=404, detail=f"Backup {backup_file} not found"
        )
    shutil.copy2(backup_path, db_path)
    return {
        "success": True,
        "restored_from": backup_file,
        "message": "Database restored successfully",
    }
