"""Pydantic request/response models for the support API."""

from typing import Any, Dict, List, Optional
from pydantic import BaseModel, Field


class SupportTicket(BaseModel):
    inquiry: str = Field(..., min_length=1, max_length=2000, description="Customer inquiry text. Max 2000 characters.")


class FeedbackRequest(BaseModel):
    helpful: bool
    comments: Optional[str] = None
    feedback_reason: Optional[str] = None
    approval_status: Optional[str] = Field(None, description="approved/rejected/needs_revision")


class StatusResponse(BaseModel):
    reference_id: str
    status: str
    created_at: str
    updated_at: str
    escalation_required: bool
    escalation_reason: Optional[str] = None


class StepsResponse(BaseModel):
    reference_id: str
    steps: List[Dict[str, Any]]
    status: str


class TraceResponse(BaseModel):
    reference_id: str
    run_id: str
    steps: List[Dict[str, Any]]
    total_steps: int
    execution_time_ms: int
    tools_used: List[str]
    tracing_enabled: bool


class RunMetrics(BaseModel):
    run_id: str
    reference_id: str
    status: str
    started_at: float
    finished_at: Optional[float] = None
    wall_time_sec: Optional[float] = None
    token_usage: Dict[str, int] = {}
    tool_latency_ms: Dict[str, float] = {}
    step_count: int = 0
    success_rate: float = 1.0
    error: Optional[str] = None


class SupportResponse(BaseModel):
    inquiry: str
    category: str
    category_confidence: int
    sentiment: str
    sentiment_confidence: int
    urgency: str
    articles: list[str]
    response: str
    response_confidence: int
    escalation_required: bool
    escalation_reason: str
    reference_id: str
    status: str | None = None
    triggered_keyword: str | None = None
    steps: list[dict[str, Any]]
    knowledge_source: str | None = None
    memory_saved: bool | None = None
    execution_mode: str | None = None
    prompt_template_used: str | None = None
    skills_used: list[str] | None = None
    tools_used: list[str] | None = None
    cache_used: bool | None = None
    run_id: str | None = None
    wall_time_sec: float | None = None
    token_usage: dict | None = None
    cost_usd: float | None = None
    routing_action: str | None = None
    routing_reason: str | None = None
    routing_missing_info: list[str] | None = None
    api_tags: list[str] | None = None
    quality_evaluation: dict | None = None
    pending_action: dict | None = None
