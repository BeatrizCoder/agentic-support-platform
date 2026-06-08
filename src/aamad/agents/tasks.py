"""CrewAI Task factories — one function per agent."""

from crewai import Task
from .definitions import (
    classification_agent,
    sentiment_agent,
    knowledge_agent,
    response_agent,
    summary_agent,
    quality_agent,
)


def make_classification_task(inquiry: str) -> Task:
    return Task(
        description=f"""
Classify this customer support inquiry and detect language.

Inquiry: {inquiry}

Return ONLY valid JSON:
{{
  "category": "Order Issues|Billing|Account Access|Technical Issue|General Support",
  "confidence": 0-100,
  "language": "pt|en",
  "language_confidence": 0-100,
  "scores": {{
    "Order Issues": 0-100,
    "Billing": 0-100,
    "Account Access": 0-100,
    "Technical Issue": 0-100,
    "General Support": 0-100
  }}
}}
""",
        expected_output="Valid JSON with category, confidence, language and scores",
        agent=classification_agent,
    )


def make_sentiment_task(inquiry: str) -> Task:
    return Task(
        description=f"""
Analyze the sentiment and urgency of this customer inquiry.

Inquiry: {inquiry}

Return ONLY valid JSON:
{{
  "sentiment": "Neutral|Concerned|Urgent",
  "urgency": "Low|Medium|High",
  "confidence": 0-100,
  "emotional_indicators": ["frustration", "confusion"]
}}
""",
        expected_output="Valid JSON with sentiment, urgency and confidence",
        agent=sentiment_agent,
    )


def make_knowledge_task(
    inquiry: str,
    category: str,
    knowledge_context: str,
) -> Task:
    return Task(
        description=f"""
You have retrieved these knowledge base snippets for a
customer support inquiry. Review them and identify the
most relevant information.

Category: {category}
Customer Inquiry: {inquiry}

Knowledge Base Snippets:
{knowledge_context}

Return a concise summary of the most relevant
knowledge points (max 300 words).
""",
        expected_output="Concise summary of relevant knowledge",
        agent=knowledge_agent,
    )


def make_response_task(
    inquiry: str,
    category: str,
    sentiment: str,
    urgency: str,
    routing_action: str,
    knowledge_summary: str,
    external_context: str,
    detected_language: str,
) -> Task:
    external_instructions = ""

    if "WEATHER DELAY ALERT" in external_context:
        _is_severe_weather = "SEVERE" in external_context
        if _is_severe_weather:
            external_instructions = """
SEVERE WEATHER ALERT — AUTO-RESOLVED.
DO NOT ask for order number or tracking info.
DO NOT escalate. DO NOT say a human will contact them.
Explain: severe weather (storm/flood/snow/extreme conditions) is causing
broad operational disruptions in the region.
All deliveries in the affected area are being monitored automatically.
Give estimated recovery timeline: 2-3 business days after conditions improve.
Use ⛈️ emoji. Be reassuring and professional.
"""
        else:
            if routing_action == "escalate":
                external_instructions = """
MODERATE WEATHER ALERT + ORDER NUMBER PROVIDED.
The customer has already supplied the order number, so this specific
order should be escalated for specialist review.
Explain that weather may be contributing, but since the order details are
available, the ticket is now routed to a human investigator.
Do NOT ask for the order number again.
Use 🌧️ emoji. Be contextual, direct, and reassuring.
"""
            else:
                external_instructions = """
MODERATE WEATHER ALERT.
Explain that weather conditions in the customer's city may be contributing to delays.
Mention the REAL temperature and conditions from the External Context.
Ask for the order number to investigate their specific delivery.
Use 🌧️ emoji. Be contextual and helpful.
"""
    elif "WEATHER CHECK" in external_context:
        external_instructions = """
WEATHER CHECKED - NORMAL CONDITIONS.
Briefly mention weather was checked and is normal.
Explain that since weather is not the cause,
the team will investigate.
"""
    elif "LOGISTICS ALERT" in external_context:
        external_instructions = """
LOGISTICS ALERT ACTIVE. This ticket is FULLY RESOLVED by the logistics explanation.
CRITICAL RULE — failure to follow this will break the customer experience:
  - DO NOT say "vou encaminhar", "vou escalar", "equipe especializada", or ANY transfer language.
  - DO NOT suggest a human will follow up.
  - The response ends HERE — no escalation path exists.
Explain: fleet maintenance is causing +3 business days delay in the validated region.
Mention the city/address from the external context.
Apologise sincerely for the delay and give an adjusted delivery window.
Use 🚛 emoji once.
"""
    elif "REFUND DATA FROM DATABASE" in external_context:
        import re

        order_match = re.search(r'Order: #?(\w+)', external_context)
        product_match = re.search(r'Product: (.+?)(?:\n|$)', external_context)
        amount_match = re.search(r'Amount: R\$?([\d,.]+)', external_context)
        status_match = re.search(r'Status: (.+?)(?:\n|$)', external_context)
        approved_match = re.search(r'Approved: (.+?)(?:\n|$)', external_context)
        bank_match = re.search(r'Bank processed: (.+?)(?:\n|$)', external_context)
        expected_match = re.search(r'Expected credit: (.+?)(?:\n|$)', external_context)

        order = order_match.group(1) if order_match else "?"
        product = product_match.group(1).strip() if product_match else "?"
        amount = amount_match.group(1) if amount_match else "?"
        status = status_match.group(1).strip() if status_match else "?"
        approved = approved_match.group(1).strip() if approved_match else ""
        bank_processed = bank_match.group(1).strip() if bank_match else ""
        expected = expected_match.group(1).strip() if expected_match else ""

        mandatory_fields = f"""  ✓ Order number: #{order}
  ✓ Product name: {product}
  ✓ Amount: R${amount}
  ✓ Status: {status}"""
        if approved and approved != "not yet":
            mandatory_fields += f"\n  ✓ Approved on: {approved}"
        if bank_processed:
            mandatory_fields += f"\n  ✓ Bank processed: {bank_processed}"
        if expected and expected != "TBD":
            mandatory_fields += f"\n  ✓ Expected credit: {expected}"

        external_instructions = f"""
REFUND DATA RETRIEVED FROM DATABASE. This ticket is FULLY RESOLVED.

CRITICAL RULES:
  - DO NOT say "vou encaminhar", "equipe especializada" or ANY transfer language
  - DO NOT suggest a human will follow up
  - The response ends HERE — no escalation path exists

MANDATORY — your response MUST mention ALL of these:
{mandatory_fields}

Use this exact data — do not invent or omit any field.
Adapt tone to customer sentiment.
If bank has NOT processed yet: explain banking timeline (5-10 days).
If bank HAS processed: confirm money was returned.
"""
    elif "PENDING ACTION FOUND" in external_context:
        import re as _re
        _status_m = _re.search(r'Status: ([^\n]+)', external_context)
        _pa_status = _status_m.group(1).strip() if _status_m else ""
        if _pa_status == "DELIVERY_FAILED":
            external_instructions = """
DELIVERY FAILED — AUTO-RESOLVED. DO NOT escalate.
Explain: we attempted delivery but no one was available to receive it.
Provide both options from the Details field:
- Reschedule via the reschedule_url link
- Pick up at the cd_address location
Mention the return_deadline — the item will be returned to stock after that date.
Use 🏠 emoji. Be helpful and action-oriented.
"""
        elif _pa_status == "LABEL_EXPIRED":
            external_instructions = """
RETURN LABEL EXPIRED — AUTO-RESOLVED. DO NOT escalate.
Explain: the return shipping label has expired.
Provide the new_label_url from the Details field so the customer can generate a new one.
Use 📦 emoji. Be clear and proactive.
"""
        elif _pa_status == "UNDER_TECHNICAL_ANALYSIS":
            external_instructions = """
PRODUCT UNDER TECHNICAL ANALYSIS — AUTO-RESOLVED. DO NOT escalate.
Explain: the product is currently with our technical team for analysis.
Reference the report_deadline and protocol from the Details field.
Reassure the customer: we will contact them as soon as the report is ready.
Use 🔧 emoji. Be reassuring.
"""
        else:
            external_instructions = """
EXISTING TICKET WITH PENDING ACTION FOUND.
Reference the existing ticket ID.
Clearly explain what action the customer needs to take.
Provide specific next steps from the description and action_required fields.
"""

    # Exchange/return already approved — add numbered steps
    _inq_lower = inquiry.lower()
    _exchange_keywords = [
        'troca aprovada', 'troca foi aprovada', 'devolução aprovada',
        'devolucao aprovada', 'exchange approved', 'return approved',
    ]
    if routing_action == "resolve" and any(kw in _inq_lower for kw in _exchange_keywords):
        external_instructions += """

EXCHANGE/RETURN ALREADY APPROVED — AUTO-RESOLVED.
Your response MUST include numbered steps for the exchange process:
1. Embale o produto com segurança (use a embalagem original se possível)
2. Coloque a etiqueta de devolução fornecida na embalagem
3. Leve ao ponto de coleta da transportadora mais próximo
4. Aguarde o novo produto ser enviado após recebimento (3-5 dias úteis)

Use 📦 emoji. Be encouraging and specific.
"""

    return Task(
        description=f"""
Generate a helpful customer support response.

Customer Inquiry: {inquiry}
Category: {category}
Sentiment: {sentiment}
Urgency: {urgency}
Routing Action: {routing_action}
Customer Language: {detected_language}

Knowledge Base Summary:
{knowledge_summary or "No specific knowledge articles found."}

External Context (APIs/Database):
{external_context or "No external data retrieved."}

{external_instructions}

ROUTING PHILOSOPHY — PROGRESSIVE ESCALATION:
Follow progressive escalation. Your response must:
1. Use ALL available context from External Context (CEP, weather, refund data, pending actions)
2. Provide a contextual, operationally-aware response that explains WHY something happened
3. Request missing info if routing_action is "awaiting"
4. Only reference human review if routing_action is "escalate"

Your response should be:
✅ Smart — references specific data from External Context
✅ Contextual — explains WHY something happened (logistics, weather, system issue)
✅ Operationally aware — mentions real data (city, temperature, order status)
✅ Enterprise-grade — professional and reassuring
✅ Explainable — gives the customer a clear reason

NEVER:
❌ Say "I'll connect you with a human agent" for auto-resolvable issues (resolve/step_by_step)
❌ Use escalation language when routing_action is "resolve" or "step_by_step"
❌ Ignore available operational context in External Context

FORMATTING RULES:
- Respond in {detected_language.upper()} only
- Use appropriate emojis (1-3 max)
- Use line breaks between paragraphs
- Numbered steps each on own line
- Max 130 words
- Warm, professional tone
- Match urgency to customer's emotional state
""",
        expected_output=(
            f"A helpful support response in {detected_language}, "
            "properly formatted with emojis and line breaks"
        ),
        agent=response_agent,
    )


def make_summary_task(
    inquiry: str,
    category: str,
    sentiment: str,
    response: str,
) -> Task:
    return Task(
        description=f"""
Create a 2-line operator summary for this support ticket.

Customer Inquiry: {inquiry}
Category: {category}
Sentiment: {sentiment}
AI Response: {response}

Return ONLY valid JSON:
{{
  "summary": "One sentence describing the issue",
  "action_needed": "One sentence for operator action",
  "key_facts": ["fact1", "fact2", "fact3"]
}}
""",
        expected_output="Valid JSON with summary, action_needed and key_facts",
        agent=summary_agent,
    )


def make_quality_task(
    inquiry: str,
    response: str,
    category: str,
    knowledge_context: str,
    external_context: str,
) -> Task:
    return Task(
        description=f"""
Evaluate this AI-generated support response as an
independent quality judge. Be critical and objective.

Customer Inquiry: {inquiry}
AI Response to Evaluate: {response}
Category: {category}

Context available to the AI:
Knowledge: {knowledge_context[:300] if knowledge_context else "none"}
External data: {external_context[:300] if external_context else "none"}

Score each dimension 0-10:
1. FAITHFULNESS: Used real info or hallucinated?
2. RELEVANCE: Actually answered what was asked?
3. EMPATHY: Warm and appropriate tone?
4. COMPLETENESS: Enough info to help the customer?

Return ONLY valid JSON:
{{
  "faithfulness": 0-10,
  "relevance": 0-10,
  "empathy": 0-10,
  "completeness": 0-10,
  "overall": 0-10,
  "grade": "A|B|C|D|F",
  "hallucination_detected": true,
  "hallucination_details": "what was hallucinated or none",
  "issues": ["issue1"],
  "strengths": ["strength1"],
  "suggestion": "one improvement"
}}
""",
        expected_output="Valid JSON quality evaluation with scores and grade",
        agent=quality_agent,
    )
