"""SupportState — CrewAI Flow state model."""

from typing import Any
from datetime import datetime
from pydantic import BaseModel


class SupportState(BaseModel):
    inquiry: str = ""
    user_id: str = ""  # User/session ID for tracking conversation history
    category: str = "General Support"
    category_confidence: int = 0
    sentiment: str = "Neutral"
    sentiment_confidence: int = 0
    urgency: str = "Low"
    articles: list[str] = []
    response: str = ""
    response_confidence: int = 0
    escalation_required: bool = False
    escalation_reason: str = ""
    reference_id: str = ""
    triggered_keyword: str | None = None
    steps: list[dict[str, Any]] = []
    knowledge_source: str = "unknown"
    memory_saved: bool = False
    execution_mode: str = "deterministic"
    prompt_template_used: str | None = None
    skills_used: list[str] = []
    tools_used: list[str] = []
    cache_used: bool = False
    run_id: str = ""
    token_usage: dict = {}
    cost_usd: float = 0.0
    knowledge_context: str = ""
    knowledge_sources: list[str] = []
    estimated_context_tokens: int = 0
    routing_action: str = ""
    routing_reason: str = ""
    routing_missing_info: list[str] = []
    routing_confidence: float = 0.0
    external_context: str = ""
    logistics_alert: dict = {}
    weather_delay: dict = {}
    weather_result: dict = {}
    refund_data: dict = {}
    api_tags: list[str] = []
    detected_city: str = ""
    auto_resolve_reason: str = ""
    awaiting_weather_context: str = ""
    quality_evaluation: dict = {}
    pending_action: dict = {}
    detected_language: str = "pt"
    ticket_summary: str = ""
    action_needed: str = ""
    key_facts: list[str] = []
    skip_routing: bool = False

    def log_step(self, agent_name: str, details: dict[str, Any]) -> None:
        self.steps.append({
            "agent": agent_name,
            "timestamp": datetime.now().isoformat(),
            "details": details,
        })
