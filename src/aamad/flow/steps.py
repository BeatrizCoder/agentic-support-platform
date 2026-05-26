"""
SupportFlowStepsMixin — all @start() / @listen() step methods.

Global singletons (tool_registry, observability_service, skill_service,
response_cache) are accessed via `core.services` to avoid circular imports
with backend.py.
"""

import asyncio
import hashlib
import logging
import time
from datetime import datetime
from typing import Any

from crewai.flow.flow import start, listen

from ..config import ENABLE_EXTERNAL_APIS, ENABLE_MEMORY, DEFAULT_CONFIG
from ..routing_engine import route_ticket
from ..core import services as _svc
from tools.refund_lookup_tool import is_refund_inquiry, extract_order_number

logger = logging.getLogger(__name__)


# ── Module-level language detector (used in evaluate_escalation) ─────────────

def _is_portuguese(text: str) -> bool:
    en_words = [
        'what', 'how', 'where', 'when', 'why', 'is',
        'the', 'your', 'my', 'order', 'return', 'policy',
        'refund', 'help', 'please', 'can', 'could',
        'have', 'not', 'arrived', 'want', 'need', 'this',
    ]
    pt_words = [
        'meu', 'minha', 'não', 'nao', 'quero', 'preciso',
        'pedido', 'ajuda', 'problema', 'como', 'olá',
        'obrigado', 'produto', 'conta', 'chegou', 'estou',
        'política', 'politica', 'devolução', 'devolucao',
        'qual', 'reembolso', 'estorno', 'fatura', 'prazo',
    ]
    text_lower = text.lower()
    en_score = sum(1 for w in en_words if w in text_lower)
    pt_score = sum(1 for w in pt_words if w in text_lower)
    return pt_score > en_score


# ── LLM location extractor ───────────────────────────────────────────────────

async def extract_location_with_llm(inquiry: str) -> str | None:
    """
    Use LLM to extract any location mention from inquiry.
    Returns city/state name for OpenWeatherMap query,
    or None if no location detected.
    """
    import anthropic
    import os
    import re

    location_hints = [
        'sou de', 'sou do', 'sou da', 'moro em', 'moro no', 'moro na',
        'cidade de', 'estado de', 'região de',
        'i live in', 'i am from', 'located in',
        'from', 'in the city', 'cep', 'bairro',
        'zona sul', 'zona norte', 'interior',
    ]

    inquiry_lower = inquiry.lower()
    has_location_hint = any(hint in inquiry_lower for hint in location_hints)
    has_cep = bool(re.search(r'\b\d{5}-?\d{3}\b', inquiry))

    if not has_location_hint and not has_cep:
        return None

    # If CEP detected, location will come from ViaCEP
    if has_cep:
        return None

    try:
        client = anthropic.Anthropic(api_key=os.environ.get("ANTHROPIC_API_KEY"))

        response = client.messages.create(
            model="claude-haiku-4-5",
            max_tokens=50,
            messages=[{
                "role": "user",
                "content": f"""Extract the location (city or state) \
from this text for a weather API query.

Text: "{inquiry}"

Rules:
- Return ONLY the location name in English or Portuguese
- If state mentioned: return state capital city
- If city mentioned: return city name
- If neighborhood/region: return nearest major city
- If no location found: return null
- Do NOT include country
- Keep it short: "Cuiabá" not "Cuiabá, Mato Grosso, Brasil"

Examples:
"sou do Mato Grosso" → "Cuiabá"
"moro em Florianópolis" → "Florianópolis"
"sou do interior de SP" → "São Paulo"
"I live in Bahia" → "Salvador"
"zona sul do Rio" → "Rio de Janeiro"
"moro no Nordeste" → "Recife"

Return ONLY the city name or null. No explanation."""
            }]
        )

        result = response.content[0].text.strip()
        result = result.replace('"', '').replace("'", '').strip()

        if result.lower() in ['null', 'none', '', 'n/a']:
            return None

        logger.debug("location_extraction: %r", result)
        return result

    except Exception as e:
        logger.debug("location_extraction failed: %s", e)
        return None


# ── Flow step mixin ──────────────────────────────────────────────────────────

class SupportFlowStepsMixin:
    """All @start() and @listen() step methods for SupportFlow."""

    def log_step(self, agent_name: str, details: dict[str, Any]) -> None:
        self.steps.append({
            "agent": agent_name,
            "timestamp": datetime.now().isoformat(),
            "elapsed_ms": round(
                (time.time() - self._start_time) * 1000
            ) if hasattr(self, '_start_time') else 0,
            "details": details,
        })

    async def execute_tool(self, tool_name: str, *args, **kwargs) -> dict[str, Any]:
        """Execute tool through registry with logging."""
        start_timestamp = datetime.now().isoformat()
        t0 = time.time()
        result = await asyncio.to_thread(
            _svc.tool_registry.execute_tool, tool_name, *args, **kwargs
        )
        end_timestamp = datetime.now().isoformat()
        self.log_step(f"{tool_name} Agent", {
            "tool_used": tool_name,
            "start_timestamp": start_timestamp,
            "end_timestamp": end_timestamp,
            "input_summary": str(args)[:300],
            "output_summary": str(result)[:300],
            "cached": result.get("cached", False),
            "execution_time": result.get("execution_time", 0),
            "execution_mode": result.get("execution_mode"),
            "input_tokens": result.get("input_tokens"),
            "output_tokens": result.get("output_tokens"),
            "total_tokens": result.get("total_tokens"),
            "cost_usd": result.get("cost_usd"),
        })
        _svc.observability_service.record_tool_execution(
            reference_id=self.state.run_id,
            agent_name=f"{tool_name} Agent",
            tool_name=tool_name,
            start_time=t0,
            input_summary=str(args)[:200],
            output_summary=str(result)[:200],
            status="success",
            confidence=float(result.get("confidence", 0)),
            execution_mode=result.get("execution_mode", "deterministic"),
            llm_used=result.get("execution_mode") == "llm",
            cache_used=bool(result.get("cached", False)),
            knowledge_sources=result.get("sources_used", []),
            estimated_tokens=result.get("estimated_context_tokens", 0),
            cost_usd=float(result.get("cost_usd", 0.0)),
            input_tokens=result.get("input_tokens", 0),
            output_tokens=result.get("output_tokens", 0),
            total_tokens=result.get("total_tokens", 0),
        )
        return result

    async def _run_viacep(self, cep: str) -> dict:
        tool = _svc.tool_registry.tools.get("Address Validation Tool")
        if tool is None:
            return {"valid": False, "error": "Address Validation Tool not found"}
        return await tool._arun(cep)

    async def _run_weather(self, city: str) -> dict:
        tool = _svc.tool_registry.tools.get("Weather Check Tool")
        if tool is None:
            return {"available": False, "error": "Weather Check Tool not found"}
        return await tool._arun(city)

    @staticmethod
    def _is_portuguese(text: str) -> bool:
        import re as _re
        pt_indicators = {
            "meu", "minha", "não", "nao", "quero", "preciso",
            "pedido", "ajuda", "problema", "como", "obrigado",
            "produto", "conta", "fatura", "chegou", "comprei",
            "recebi", "estou", "qual", "política", "politica",
            "prazo", "entrega", "cancelar", "cancelamento",
            "devolução", "devolucao", "reembolso", "estorno",
        }
        en_indicators = {
            "my", "your", "what", "how", "where", "when", "why",
            "please", "help", "want", "need", "order", "return",
            "policy", "refund", "account", "issue", "problem",
            "cannot", "the", "this", "that", "have", "has",
            "not", "arrived", "i", "we", "is", "are",
        }
        words = set(_re.split(r"\W+", text.lower()))
        pt_score = len(words & pt_indicators)
        en_score = len(words & en_indicators)
        return pt_score > en_score

    @start()
    async def classify_and_sentiment(self):
        """
        Crew 1: Classification + Sentiment sequential.
        Uses CrewAI agents with real roles and goals.
        """
        self._start_time = time.time()
        self.steps = []

        # --- Response cache check ---
        inquiry_hash = hashlib.md5(
            self.state.inquiry.lower().strip().encode()
        ).hexdigest()[:8]
        self._inquiry_hash = inquiry_hash
        cached_entry = _svc.response_cache.get(inquiry_hash)
        if cached_entry and (time.time() - cached_entry['ts'] < 300):
            logger.info("Cache hit for inquiry hash: %s", inquiry_hash)
            for k, v in cached_entry['state'].items():
                setattr(self.state, k, v)
            self.state.cache_used = True
            self._cache_hit = True
            return "Cache hit"

        # --- Crew 1: Analysis ---
        from ..agents.crews import run_analysis_crew

        start = time.time()
        result = await asyncio.to_thread(run_analysis_crew, self.state.inquiry)

        self.state.category = result.get("category", "General Support")
        self.state.category_confidence = result.get("confidence", 70)
        self.state.detected_language = result.get("language", "pt")
        self.state.sentiment = result.get("sentiment", "Neutral")
        self.state.urgency = result.get("urgency", "Low")
        self.state.sentiment_confidence = result.get("sentiment_confidence", 70)

        latency = round((time.time() - start) * 1000, 2)

        self.log_step("Analysis Crew", {
            "agents": ["Classification Agent", "Sentiment Agent"],
            "category": self.state.category,
            "confidence": self.state.category_confidence,
            "sentiment": self.state.sentiment,
            "urgency": self.state.urgency,
            "language": self.state.detected_language,
            "execution_mode": "crewai_sequential",
            "latency_ms": latency,
        })

        return f"Analysis complete: {self.state.category} / {self.state.sentiment}"

    @listen(classify_and_sentiment)
    async def enrich_with_external_data(self):
        import re
        from ..routing_engine import check_logistics_alert, check_weather_delay
        logger.debug("enrich_with_external_data: inquiry=%s", self.state.inquiry[:50])
        logger.debug("external_context before: %r", self.state.external_context)

        context_parts = []

        # ── Pending-action lookup (local DB — always runs, tool owns pattern matching) ──
        try:
            from tools.pending_action_tool import pending_action_tool
            pa_result = await asyncio.to_thread(
                pending_action_tool.run, self.state.inquiry
            )
            self.state.pending_action = pa_result
            self.state.tools_used.append("Pending Action Tool")
            if pa_result.get("found"):
                self.state.api_tags.append("pending_action")
                status = pa_result.get("status", "")
                product = pa_result.get("product", "")
                valor = pa_result.get("valor", 0)
                ticket_id = pa_result.get("ticket_id", "")
                deadline = pa_result.get("deadline", "")
                action = pa_result.get("action_required", "")
                description = pa_result.get("description", "")
                urgency = pa_result.get("urgency", "medium")
                additional = pa_result.get("additional_info", {})
                context_parts.append(
                    f"PENDING ACTION FOUND:"
                    f"\n  Order: #{pa_result.get('order_number')}"
                    f"\n  Product: {product}"
                    f"\n  Value: R${valor:.2f}"
                    f"\n  Existing ticket: {ticket_id}"
                    f"\n  Status: {status}"
                    f"\n  Action required: {action}"
                    f"\n  Description: {description}"
                    f"\n  Deadline: {deadline}"
                    f"\n  Urgency: {urgency}"
                    f"\n  Details: {additional}"
                )
                self.log_step("Pending Action Tool", {
                    "order_number": pa_result.get("order_number"),
                    "status": status,
                    "ticket_id": ticket_id,
                    "action_required": action,
                    "urgency": urgency,
                    "latency_ms": pa_result.get("latency_ms"),
                    "execution_mode": "deterministic",
                })
                logger.info(
                    "enrich: pending action found order=%s status=%s auto_resolve=%s",
                    pa_result.get("order_number"), status, pa_result.get("auto_resolve"),
                )
                if pa_result.get("auto_resolve"):
                    self.state.routing_action = "resolve"
        except Exception as e:
            logger.error("enrich: pending_action_tool error: %s", e)

        if context_parts:
            self.state.external_context = "\n".join(context_parts)

        if not ENABLE_EXTERNAL_APIS:
            logger.debug("enrich_with_external_data: ENABLE_EXTERNAL_APIS=False, skipping external APIs")
            return f"External enrichment done: {len(context_parts)} data point(s) (external APIs disabled)"

        # ── Detect CEP in inquiry (cheap, no I/O) ──
        cep_match = re.search(r'\b(\d{5}-?\d{3})\b', self.state.inquiry)
        cep = cep_match.group(1) if cep_match else None
        is_order_issue = self.state.category == "Order Issues"

        # ── Fetch external data (parallel when possible) ──
        addr_result = None
        weather_result = None
        pre_weather_city = None   # city used for parallel weather call

        if cep and is_order_issue:
            # Pre-extract city via LLM so we can run ViaCEP + Weather simultaneously
            pre_weather_city = await extract_location_with_llm(self.state.inquiry)
            if pre_weather_city:
                logger.info(
                    "Running ViaCEP + Weather in parallel: cep=%s city=%s",
                    cep, pre_weather_city,
                )
                gathered = await asyncio.gather(
                    self._run_viacep(cep),
                    self._run_weather(pre_weather_city),
                    return_exceptions=True,
                )
                if isinstance(gathered[0], Exception):
                    logger.error("ViaCEP parallel failed: %s", gathered[0])
                    addr_result = {"valid": False}
                else:
                    addr_result = gathered[0]
                if isinstance(gathered[1], Exception):
                    logger.error("Weather parallel failed: %s", gathered[1])
                    weather_result = {"available": False}
                else:
                    weather_result = gathered[1]
            else:
                addr_result = await self._run_viacep(cep)
        elif cep:
            addr_result = await self._run_viacep(cep)

        # ── Process CEP result → logistics alert ──
        if addr_result is not None:
            self.state.tools_used.append("Address Validation Tool")
            if addr_result.get("valid"):
                self.state.api_tags.append("cep_validated")
                city_from_cep = addr_result.get("city", "")
                state_code = addr_result.get("state", "")
                self.state.detected_city = city_from_cep
                formatted = addr_result.get("formatted") or (
                    f"{addr_result.get('street')}, {addr_result.get('neighborhood')}, "
                    f"{addr_result.get('city')} - {addr_result.get('state')}"
                )
                alert = check_logistics_alert(state_code)
                logger.debug("CEP result: state=%s, valid=True", state_code)
                logger.debug("logistics alert: %s", alert)
                if alert:
                    self.state.logistics_alert = alert
                    self.state.routing_action = "resolve"
                    self.state.skip_routing = True
                    self.state.api_tags.append("logistics_alert")
                    context_parts.append(
                        f"Validated address: {formatted}"
                        f"\nLOGISTICS ALERT ACTIVE for {state_code}: "
                        f"Fleet maintenance causing 3-day delay in this region."
                    )
                    self.log_step("Address Validation Agent", {
                        "cep_valid": True,
                        "address": formatted,
                        "state": state_code,
                        "logistics_alert": True,
                        "alert_key": alert["alert_key"],
                        "execution_mode": "external_api",
                    })
                else:
                    context_parts.append(
                        f"Validated address: {formatted}"
                        f"\nNo logistics alerts for {state_code}."
                    )
                    self.log_step("Address Validation Agent", {
                        "cep_valid": True,
                        "address": formatted,
                        "state": state_code,
                        "logistics_alert": False,
                        "execution_mode": "external_api",
                    })
            else:
                logger.debug(
                    "CEP result: valid=False, error=%s, logistics_alert=N/A",
                    addr_result.get("error", addr_result.get("error_type", "unknown")),
                )
            if addr_result.get("fallback"):
                context_parts.append(f"Address validation: {addr_result.get('fallback')}")

        # ── Location detection ──
        # STEP A: Use city already set from CEP, or search partial context
        city_from_cep = None
        if self.state.logistics_alert and self.state.logistics_alert.get("alert_active"):
            pass  # logistics has priority, skip weather
        else:
            if self.state.detected_city:
                city_from_cep = self.state.detected_city
            else:
                partial_ctx = "\n".join(context_parts)
                cep_city_match = re.search(
                    r'Validated address: .+, (.+?) -',
                    partial_ctx
                )
                if cep_city_match:
                    city_from_cep = cep_city_match.group(1).strip()

        # STEP B: Determine final city — CEP city > LLM city
        detected_city = city_from_cep
        if not detected_city and not (
            self.state.logistics_alert and self.state.logistics_alert.get("alert_active")
        ):
            # Use pre-extracted LLM city (from parallel path) or run now
            detected_city = pre_weather_city or await extract_location_with_llm(self.state.inquiry)

        self.state.detected_city = detected_city or ""
        logger.debug("detected_city: %s", self.state.detected_city)

        # STEP C: Weather check for Order Issues with a detected city
        if (detected_city and is_order_issue and
                not (self.state.logistics_alert and self.state.logistics_alert.get("alert_active"))):

            if weather_result is None:
                # Not pre-fetched (no parallel path) — run now
                logger.debug("weather: checking %s", detected_city)
                weather_result = await self._run_weather(detected_city)

            self.state.tools_used.append("Weather Check Tool")
            self.state.weather_result = weather_result

            if weather_result.get("available"):
                self.state.api_tags.append("weather_checked")
                conditions = weather_result.get("conditions", "")
                temp = weather_result.get("temperature_c", "")
                adverse = weather_result.get("adverse_conditions", False)

                if adverse:
                    weather_delay = check_weather_delay(detected_city, weather_result)
                    if weather_delay:
                        self.state.weather_delay = weather_delay
                        self.state.api_tags.append("weather_alert")

                    _weather_sev = weather_result.get("severity", "severe")
                    _sev_label = "SEVERE" if _weather_sev == "severe" else "MODERATE"
                    context_parts.append(
                        f"WEATHER DELAY ALERT: {_sev_label} — "
                        f"{conditions} in {detected_city} "
                        f"({temp}°C). Adverse conditions "
                        f"affecting deliveries."
                    )
                else:
                    if self.state.routing_action == "escalate":
                        context_parts.append(
                            f"CLEAR WEATHER ESCALATION: "
                            f"Weather in {detected_city} is normal "
                            f"({conditions}, {temp}°C). "
                            f"Escalating for order investigation."
                        )
                    else:
                        context_parts.append(
                            f"WEATHER CHECK: {detected_city} — "
                            f"{conditions}, {temp}°C. "
                            f"Normal conditions, no weather impact."
                        )

            self.log_step("Weather Check Agent", {
                "city": detected_city,
                "available": weather_result.get("available"),
                "conditions": weather_result.get("conditions"),
                "temperature": weather_result.get("temperature_c"),
                "adverse": weather_result.get("adverse_conditions"),
                "source": "city_detection",
                "execution_mode": "external_api",
            })

        # ── Refund status lookup ──
        has_refund_intent = is_refund_inquiry(self.state.inquiry)
        logger.debug("enrich: has_refund_intent=%s routing_action=%s", has_refund_intent, self.state.routing_action)
        if has_refund_intent:
            order_num = extract_order_number(self.state.inquiry)
            if order_num:
                is_pt = self._is_portuguese(self.state.inquiry)
                refund_result = await asyncio.to_thread(
                    _svc.tool_registry.execute_tool, "Refund Status Tool", order_num
                )
                self.state.tools_used.append("Refund Status Tool")
                self.state.refund_data = refund_result
                self.state.api_tags.append("refund_lookup")
                if refund_result.get("found"):
                    self.state.api_tags.append("refund_found")
                    refund_details = [f"REFUND DATA FROM DATABASE - Order: #{order_num}"]
                    refund_details.append(f"Status: {refund_result.get('status', '')}")
                    produto = refund_result.get("produto", "")
                    valor = refund_result.get("valor")
                    aprovado_em = refund_result.get("aprovado_em", "")
                    previsao = refund_result.get("previsao_credito", "")
                    if produto:
                        refund_details.append(f"Product: {produto}")
                    if valor is not None:
                        refund_details.append(f"Amount: R${float(valor):.2f}")
                    if aprovado_em:
                        refund_details.append(f"Approved: {aprovado_em}")
                    if previsao:
                        refund_details.append(f"Expected credit: {previsao}")
                    status_val = refund_result.get("status", "")
                    if status_val == "processado":
                        refund_details.append("Bank processed: yes")
                    elif status_val in ("aprovado", "pendente", "em_analise"):
                        refund_details.append("Bank processed: not yet")
                    context_parts.append("\n".join(refund_details))
                    if refund_result.get("auto_resolve"):
                        self.state.routing_action = "resolve"
                else:
                    msg = refund_result.get("message_pt" if is_pt else "message_en", "")
                    if msg:
                        context_parts.append(f"REFUND NOT FOUND (pedido {order_num}): {msg}")
                if refund_result.get("should_escalate"):
                    self.state.api_tags.append("refund_denied")
                self.log_step("Refund Status Agent", {
                    "order_number": order_num,
                    "refund_status": refund_result.get("status"),
                    "found": refund_result.get("found"),
                    "auto_resolve": refund_result.get("auto_resolve"),
                    "should_escalate": refund_result.get("should_escalate"),
                    "execution_mode": "deterministic",
                })

        self.state.external_context = "\n".join(context_parts)
        logger.debug("external_context after: %r", self.state.external_context)
        return f"External enrichment done: {len(context_parts)} data point(s)"

    @listen(enrich_with_external_data)
    async def route_inquiry(self):
        if self.state.skip_routing:
            self.log_step("Routing Engine", {
                "action": self.state.routing_action,
                "reason": "skip_routing — enrichment already resolved this ticket",
                "execution_mode": "skip",
            })
            return f"Skipped routing: {self.state.routing_action} (skip_routing)"

        decision = route_ticket(
            inquiry=self.state.inquiry,
            category=self.state.category,
            sentiment=self.state.sentiment,
            urgency=self.state.urgency,
        )
        self.state.routing_action = decision.action
        self.state.routing_reason = decision.reason
        self.state.routing_missing_info = decision.missing_info
        self.state.routing_confidence = decision.confidence
        if decision.triggered_keyword:
            self.state.triggered_keyword = decision.triggered_keyword

        # If refund intent + order number in the inquiry, always defer to the
        # refund lookup tool regardless of the routing engine's decision
        # (catches both "awaiting" and billing "escalate" cases).
        import re as _re
        _has_refund_intent = is_refund_inquiry(self.state.inquiry)
        _has_order = bool(_re.search(r'\b\d{4,12}\b', self.state.inquiry))
        if _has_refund_intent and _has_order:
            self.state.routing_action = "pending_lookup"
            self.state.escalation_required = False

        self.log_step("Routing Engine", {
            "action": self.state.routing_action,
            "reason": decision.reason,
            "missing_info": decision.missing_info,
            "has_order_number": decision.has_order_number,
            "has_email": decision.has_email,
            "explicit_escalation": decision.explicit_escalation,
            "confidence": decision.confidence,
            "execution_mode": "deterministic",
        })
        return f"Routed as: {self.state.routing_action}"

    @listen(route_inquiry)
    async def retrieve_knowledge(self):
        result = await self.execute_tool(
            "Knowledge Retrieval Tool", self.state.category, self.state.inquiry
        )
        self.state.articles = result["articles"]
        self.state.knowledge_source = result.get("source", "unknown")
        self.state.knowledge_context = result.get("context_string", "")
        self.state.knowledge_sources = result.get("sources_used", [])
        self.state.estimated_context_tokens = result.get("estimated_context_tokens", 0)
        self.state.tools_used.append("Knowledge Retrieval Tool")
        self.state.cache_used = self.state.cache_used or result.get("cached", False)
        return (
            f"Retrieved {len(self.state.articles)} knowledge articles "
            f"from {self.state.knowledge_source}"
        )

    @listen(retrieve_knowledge)
    async def generate_response(self):
        """
        Crew 2: Knowledge + Response sequential.
        Response Agent uses external context for
        weather/CEP/refund personalized messages.
        """
        from ..agents.crews import run_response_crew

        logger.debug("knowledge_context: %s", bool(self.state.knowledge_context))
        logger.debug("external_context: %r", self.state.external_context)

        start = time.time()

        result = await asyncio.to_thread(
            run_response_crew,
            inquiry=self.state.inquiry,
            category=self.state.category,
            sentiment=self.state.sentiment,
            urgency=self.state.urgency,
            routing_action=self.state.routing_action,
            knowledge_context=self.state.knowledge_context,
            external_context=self.state.external_context,
            detected_language=self.state.detected_language,
        )

        if result.get("response"):
            self.state.response = result["response"]

        self.state.response_confidence = 80  # Crew doesn't expose token-level confidence
        self.state.tools_used.append("Response Crew")

        latency = round((time.time() - start) * 1000, 2)

        self.log_step("Response Crew", {
            "agents": ["Knowledge Agent", "Response Agent"],
            "routing_action": self.state.routing_action,
            "has_external_context": bool(self.state.external_context),
            "response_length": len(self.state.response or ""),
            "execution_mode": "crewai_sequential",
            "latency_ms": latency,
        })

        # Validate response with skills
        skills_validation = _svc.skill_service.validate_response_with_skills(
            self.state.response, "response_agent"
        )
        self.state.skills_used.extend(skills_validation.get("skills_used", []))

        return f"Generated response ({len(self.state.response)} chars)"

    @listen(generate_response)
    async def evaluate_escalation(self):
        logger.debug(
            "evaluate_escalation: routing_action=%s refund_data=%s logistics_alert=%s "
            "weather_delay=%s escalation_required=%s external_context=%r",
            self.state.routing_action, self.state.refund_data, self.state.logistics_alert,
            self.state.weather_delay, self.state.escalation_required,
            self.state.external_context[:100],
        )
        # Simulate agent collaboration
        self.log_step("Escalation Agent", {
            "collaboration": "delegate",
            "target_agent": "Sentiment Analysis Agent",
            "task": "Review response confidence and sentiment for escalation decision",
            "context": {
                "response_confidence": self.state.response_confidence,
                "sentiment": self.state.sentiment,
            },
        })

        result = await self.execute_tool(
            "Escalation Evaluation Tool",
            self.state.response_confidence,
            self.state.sentiment,
            len(self.state.articles),
            self.state.inquiry,
            self.state.urgency,
        )
        self.state.escalation_required = result["escalation_required"]
        self.state.escalation_reason = result["reason"]
        self.state.reference_id = result["reference_id"]
        if result.get("triggered_keyword") and not self.state.triggered_keyword:
            self.state.triggered_keyword = result.get("triggered_keyword")
        self.state.tools_used.append("Escalation Evaluation Tool")

        # Re-key observability events from run_id to the final reference_id
        if self.state.reference_id and self.state.run_id:
            _svc.observability_service.remap_events(
                self.state.run_id, self.state.reference_id
            )

        # Validate escalation with skills
        escalation_skills = _svc.skill_service.validate_response_with_skills(
            f"Escalation decision: {self.state.escalation_reason}", "escalation_agent"
        )
        self.state.skills_used.extend(escalation_skills.get("skills_used", []))

        # ── Priority chain: external alerts first, routing decision last ──
        logger.debug("escalation: refund_data=%s routing_action=%s skip_routing=%s", self.state.refund_data, self.state.routing_action, self.state.skip_routing)

        if self.state.skip_routing:
            # Logistics alert resolved this ticket in enrich_with_external_data — do not override
            self.state.escalation_required = False
            self.state.routing_action = "resolve"
            self.state.auto_resolve_reason = "logistics_skip"
            self.log_step("Routing Engine", {
                "override": "logistics_skip",
                "reason": "skip_routing flag — logistics alert auto-resolved before routing",
                "auto_resolved": True,
                "execution_mode": "deterministic",
            })

        elif self.state.logistics_alert and self.state.logistics_alert.get("alert_active"):
            # PRIORITY 1: logistics alert — LLM already generated response using external_context
            alert = self.state.logistics_alert
            self.state.escalation_required = False
            self.state.routing_action = "resolve"
            self.state.auto_resolve_reason = "logistics_alert"
            self.log_step("Routing Engine", {
                "override": "logistics_alert",
                "reason": "Active logistics delay — auto-resolved",
                "alert_key": alert.get("alert_key"),
                "auto_resolved": True,
                "execution_mode": "deterministic",
            })

        elif self.state.weather_delay and self.state.weather_delay.get("delay_active"):
            # PRIORITY 2: severe weather → auto-resolve; moderate → awaiting order number
            _weather_sev = self.state.weather_result.get("severity", "severe")
            if _weather_sev == "severe":
                self.state.escalation_required = False
                self.state.routing_action = "resolve"
                self.state.auto_resolve_reason = "weather_delay_severe"
            else:
                # Moderate — ask for order number to investigate
                self.state.escalation_required = True
                self.state.routing_action = "awaiting"
                self.state.auto_resolve_reason = "weather_delay_moderate"
                _is_pt_w = self._is_portuguese(self.state.inquiry)
                _city_w = self.state.detected_city or ("sua região" if _is_pt_w else "your area")
                _temp_w = self.state.weather_result.get("temperature_c", "")
                _cond_w = self.state.weather_result.get("conditions", "")
                if _is_pt_w:
                    self.state.response = (
                        f"🌧️ Verificamos as condições em {_city_w} "
                        f"({_cond_w}, {_temp_w}°C). As condições climáticas "
                        f"podem estar contribuindo para atrasos na sua região.\n\n"
                        f"Para investigarmos seu pedido específico, "
                        f"por favor informe o número do pedido."
                    )
                else:
                    self.state.response = (
                        f"🌧️ We checked conditions in {_city_w} "
                        f"({_cond_w}, {_temp_w}°C). Weather may be contributing "
                        f"to delays in your area.\n\n"
                        f"To investigate your specific order, "
                        f"please provide your order number."
                    )
                self.state.escalation_reason = (
                    "Moderate weather — awaiting order number for investigation"
                )
            self.log_step("Routing Engine", {
                "override": "weather_delay",
                "severity": _weather_sev,
                "reason": (
                    f"Weather ({_weather_sev}) — "
                    f"{'auto-resolved' if _weather_sev == 'severe' else 'awaiting order number'}"
                ),
                "auto_resolved": _weather_sev == "severe",
                "execution_mode": "deterministic",
            })

        elif (self.state.weather_result and
              self.state.weather_result.get("available") and
              not self.state.weather_result.get("adverse_conditions") and
              not self.state.weather_delay.get("delay_active") and
              self.state.detected_city and
              self.state.category == "Order Issues" and
              self.state.routing_action in ("awaiting", "escalate")):
            # PRIORITY 3: Clear weather cases (Order Issues with location)
            city = self.state.detected_city
            temp = self.state.weather_result.get("temperature_c", "")
            conditions = self.state.weather_result.get("conditions", "")
            is_pt = self._is_portuguese(self.state.inquiry)

            if self.state.routing_action == "awaiting":
                # CASE 2: Clear weather + no order number → awaiting form with ☀️ message
                if is_pt:
                    self.state.response = (
                        f"☀️ Verificamos o clima em {city} — "
                        f"{conditions}, {temp}°C. "
                        f"As condições climáticas estão normais "
                        f"e não estão afetando as entregas "
                        f"na sua região.\n\n"
                        f"Para investigar o atraso do seu pedido, "
                        f"preciso do número do pedido. "
                        f"Você pode encontrá-lo no e-mail "
                        f"de confirmação da compra."
                    )
                else:
                    self.state.response = (
                        f"☀️ We checked the weather in {city} — "
                        f"{conditions}, {temp}°C. "
                        f"Weather conditions are normal and "
                        f"not affecting deliveries in your area.\n\n"
                        f"To investigate your order delay, "
                        f"I need your order number. "
                        f"You can find it in your purchase "
                        f"confirmation email."
                    )
                self.state.escalation_required = True
                self.state.escalation_reason = "Awaiting customer information: order_number"
                self.log_step("Routing Engine", {
                    "override": "clear_weather_awaiting",
                    "reason": "Clear weather — awaiting order number",
                    "city": city,
                    "auto_resolved": False,
                    "execution_mode": "deterministic",
                })

            elif self.state.routing_action == "escalate":
                # CASE 3: Clear weather + has order number → escalate with context
                self.state.external_context += (
                    f"\nCLEAR WEATHER ESCALATION: "
                    f"Weather in {city} is normal. "
                    f"Escalating for order investigation."
                )
                self.state.escalation_required = True
                self.state.escalation_reason = (
                    "Clima normal — escalando para investigação do pedido"
                    if is_pt else
                    "Clear weather — escalating for order investigation"
                )
                self.log_step("Routing Engine", {
                    "override": "clear_weather_escalate",
                    "reason": "Clear weather — escalating",
                    "city": city,
                    "auto_resolved": False,
                    "hitl": True,
                    "execution_mode": "deterministic",
                })

        elif (self.state.refund_data.get("found") and
              self.state.refund_data.get("auto_resolve")):
            # PRIORITY 3: refund found + auto-resolvable — LLM generated response via REFUND DATA FOUND context
            refund_status = self.state.refund_data.get("status")
            self.state.escalation_required = False
            self.state.routing_action = "resolve"
            self.state.auto_resolve_reason = "refund_found"
            self.log_step("Routing Engine", {
                "override": "refund_status",
                "refund_status": refund_status,
                "auto_resolved": True,
                "execution_mode": "deterministic",
            })

        elif self.state.refund_data.get("should_escalate"):
            # PRIORITY 4: refund found + denied → route to human
            refund_status = self.state.refund_data.get("status")
            is_pt = self._is_portuguese(self.state.inquiry)
            msg = self.state.refund_data.get(
                "message_pt" if is_pt else "message_en", ""
            )
            self.state.escalation_required = True
            self.state.routing_action = "escalate"
            self.state.escalation_reason = (
                "Reembolso negado — requer explicação e revisão humana"
                if is_pt else
                "Refund denied — requires human explanation and review"
            )
            if msg:
                self.state.response = msg
            self.log_step("Routing Engine", {
                "override": "refund_status",
                "refund_status": refund_status,
                "auto_resolved": False,
                "hitl": True,
                "execution_mode": "deterministic",
            })

        elif (self.state.pending_action and self.state.pending_action.get("found")):
            # PRIORITY 4b: auto-resolve if system can handle it (DELIVERY_FAILED,
            # LABEL_EXPIRED, UNDER_TECHNICAL_ANALYSIS), else awaiting customer action
            pa = self.state.pending_action
            if pa.get("auto_resolve"):
                self.state.escalation_required = False
                self.state.routing_action = "resolve"
                self.state.auto_resolve_reason = "pending_action_auto"
            else:
                self.state.escalation_required = True
                self.state.routing_action = "awaiting"
                self.state.auto_resolve_reason = "pending_action"
                self.state.escalation_reason = (
                    f"Pending action {pa.get('status', '')} "
                    f"requires {pa.get('action_required', '')}"
                )
            self.log_step("Routing Engine", {
                "override": "pending_action",
                "order_number": pa.get("order_number"),
                "ticket_id": pa.get("ticket_id"),
                "status": pa.get("status"),
                "action_required": pa.get("action_required"),
                "urgency": pa.get("urgency"),
                "auto_resolve": pa.get("auto_resolve", False),
                "execution_mode": "deterministic",
            })

        else:
            # PRIORITY 5: routing engine decision (no external alert / refund match)
            if self.state.routing_action == "escalate":
                self.state.escalation_required = True
                self.state.escalation_reason = self.state.routing_reason

            elif self.state.routing_action == "pending_lookup":
                is_pt = self._is_portuguese(self.state.inquiry)
                _rd = self.state.refund_data
                if _rd.get("found") and _rd.get("auto_resolve"):
                    # Safety net: lookup found auto-resolvable refund
                    _msg = _rd.get("message_pt" if is_pt else "message_en", "")
                    self.state.escalation_required = False
                    self.state.routing_action = "resolve"
                    if _msg:
                        self.state.response = _msg
                elif _rd.get("should_escalate"):
                    # Safety net: lookup found denied refund
                    _msg = _rd.get("message_pt" if is_pt else "message_en", "")
                    self.state.escalation_required = True
                    self.state.routing_action = "escalate"
                    self.state.escalation_reason = (
                        "Reembolso negado — requer explicação e revisão humana"
                        if is_pt else
                        "Refund denied — requires human explanation and review"
                    )
                    if _msg:
                        self.state.response = _msg
                else:
                    # Refund not found → route to human for manual investigation
                    self.state.escalation_required = True
                    self.state.routing_action = "escalate"
                    self.state.escalation_reason = (
                        "Reembolso não localizado — requer verificação manual"
                        if is_pt else
                        "Refund not found — requires manual investigation"
                    )

            elif self.state.routing_action == "awaiting":
                self.state.escalation_required = True
                self.state.escalation_reason = (
                    f"Awaiting customer information: "
                    f"{', '.join(self.state.routing_missing_info)}"
                )
                missing = self.state.routing_missing_info
                pt = self._is_portuguese(self.state.inquiry)

                if "order_number" in missing and "email" in missing:
                    self.state.response = (
                        "Entendo sua situação e quero ajudá-la o mais rápido possível! "
                        "Para isso, preciso de algumas informações:\n\n"
                        "1. Número do pedido\n"
                        "2. E-mail cadastrado na conta\n\n"
                        "Assim que receber essas informações, encaminharei para nossa equipe especializada."
                    ) if pt else (
                        "I'd love to help resolve this quickly! To proceed, I need:\n\n"
                        "1. Your order number\n"
                        "2. Email address on the account\n\n"
                        "Once I have these details, I'll route your case to our specialist team immediately."
                    )
                elif "order_number" in missing:
                    self.state.response = (
                        "Para localizar seu pedido e ajudá-la, preciso do número do pedido. "
                        "Você pode encontrá-lo no e-mail de confirmação da compra. Pode me informar?"
                    ) if pt else (
                        "To locate your order and help you, I need your order number. "
                        "You can find it in your purchase confirmation email. Could you share it?"
                    )
                elif "screenshot_or_description" in missing:
                    self.state.response = (
                        "Para diagnosticar o problema técnico, pode me fornecer:\n\n"
                        "1. Um screenshot do erro (se possível)\n"
                        "2. Qual navegador e dispositivo está usando\n"
                        "3. Mensagem de erro exata (se aparecer)\n\n"
                        "Com essas informações consigo ajudá-la melhor!"
                    ) if pt else (
                        "To diagnose the technical issue, could you provide:\n\n"
                        "1. A screenshot of the error (if possible)\n"
                        "2. Which browser and device you're using\n"
                        "3. The exact error message (if any)\n\n"
                        "This will help me assist you better!"
                    )

            elif self.state.routing_action in ("step_by_step", "resolve"):
                self.state.escalation_required = False

        # ── Append escalation notice to response ──
        # Skip for refund_denied — the choice card (FIX 3) handles escalation messaging
        # after the customer explicitly contests, not in the initial denial response.
        _is_refund_denied = "refund_denied" in self.state.api_tags
        if (self.state.escalation_required and
                self.state.routing_action != "awaiting" and
                not _is_refund_denied):
            if _is_portuguese(self.state.inquiry):
                self.state.response += (
                    "\n\nSua solicitação foi encaminhada para análise humana. "
                    "Um agente de suporte entrará em contato em até 24 horas."
                )
                if self.state.reference_id:
                    self.state.response += f"\n\nID de Referência: {self.state.reference_id}"
            else:
                self.state.response += (
                    "\n\nYour inquiry has been flagged for human review. "
                    "A support agent will contact you within 24 hours to assist you further."
                )
                if self.state.reference_id:
                    self.state.response += f"\n\nReference ID: {self.state.reference_id}"

        # Store in memory if enabled
        if ENABLE_MEMORY:
            memory_result = await asyncio.to_thread(
                _svc.tool_registry.execute_tool, "Memory Management Tool", "store",
                inquiry=self.state.inquiry,
                category=self.state.category,
                sentiment=self.state.sentiment,
                escalation_required=self.state.escalation_required,
                response=self.state.response,
                reference_id=self.state.reference_id,
            )
            self.state.memory_saved = memory_result.get("success", False)
            self.state.tools_used.append("Memory Management Tool")

        self.state.execution_mode = DEFAULT_CONFIG["execution_mode"]
        self.state.steps = self.steps

        # Populate response cache for identical future inquiries.
        # Skip caching when external tools were called — refund status,
        # CEP/logistics and weather return real-time data that changes between calls.
        if self._inquiry_hash and not self._cache_hit and not self.state.external_context:
            _svc.response_cache[self._inquiry_hash] = {
                'ts': time.time(),
                'state': {
                    'category': self.state.category,
                    'category_confidence': self.state.category_confidence,
                    'sentiment': self.state.sentiment,
                    'sentiment_confidence': self.state.sentiment_confidence,
                    'urgency': self.state.urgency,
                    'articles': list(self.state.articles),
                    'response': self.state.response,
                    'response_confidence': self.state.response_confidence,
                    'escalation_required': self.state.escalation_required,
                    'escalation_reason': self.state.escalation_reason,
                },
            }

        return f"Escalation {'required' if self.state.escalation_required else 'not required'}"

    @listen(evaluate_escalation)
    async def evaluate_and_summarize(self):
        """
        Run evaluation in background — don't block response.
        Customer gets answer faster; operator sees quality
        metrics shortly after in the dashboard.
        """
        if self.state.routing_action not in ("resolve", "step_by_step"):
            return "Skipped"

        if not self.state.response:
            return "Skipped"

        # Import here so closures below don't need module-level refs
        from ..agents.crews import run_evaluation_crew
        from ..data_store import data_store as _ds

        # Snapshot state values — self may be GC'd before the task runs
        # Use run_id (always set at flow start) not reference_id (generated
        # in routes.py after kickoff_async returns for non-escalated tickets)
        run_id = self.state.run_id
        inquiry = self.state.inquiry
        response = self.state.response
        category = self.state.category
        knowledge_context = self.state.knowledge_context
        external_context = self.state.external_context

        async def _run_background():
            try:
                result = await asyncio.to_thread(
                    run_evaluation_crew,
                    inquiry=inquiry,
                    response=response,
                    category=category,
                    knowledge_context=knowledge_context,
                    external_context=external_context,
                )
                summary = result.get("summary", {})
                quality = result.get("quality", {})
                _ds.update_ticket_quality(
                    run_id=run_id,
                    ticket_summary=summary.get("summary", ""),
                    action_needed=summary.get("action_needed", ""),
                    key_facts=summary.get("key_facts", []),
                    quality_evaluation=quality,
                )
                logger.info(
                    "Background evaluation complete: run_id=%s grade=%s",
                    run_id,
                    quality.get("grade", "?"),
                )
            except Exception as e:
                logger.error("Background evaluation failed: %s", e)

        # Fire and forget — response is already built, don't block it
        asyncio.create_task(_run_background())

        self.log_step("Evaluation Crew", {
            "status": "running_in_background",
            "run_id": run_id,
            "note": "Results saved to DB when complete",
            "execution_mode": "crewai_llm_sonnet",
        })

        return "Evaluation started in background"
