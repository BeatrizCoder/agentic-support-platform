import logging
import re
import unicodedata
from dataclasses import dataclass
from typing import Optional

logger = logging.getLogger(__name__)


def normalize(text: str) -> str:
    text = text.lower().strip()
    text = unicodedata.normalize("NFD", text)
    return "".join(c for c in text if unicodedata.category(c) != "Mn")


ORDER_NUMBER_PATTERNS = [
    r'pedido\s*[:#]?\s*(\d{4,8})',
    r'order\s*[:#]?\s*(\d{4,8})',
    r'n[uú]mero\s+(?:do\s+)?pedido\s*[:#]?\s*(\d{4,8})',
    r'#(\d{4,8})',
    r'\border\s+#?(\d{4,8})\b',
]

EMAIL_PATTERNS = [
    r'[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}'
]

INVOICE_PATTERNS = [
    r'fatura\s*[:#]?\s*\d+',
    r'invoice\s*[:#]?\s*\d+',
    r'nota\s*fiscal\s*[:#]?\s*\d+',
    r'\b(nf|nfe)\s*[:#]?\s*\d+',
]

EXPLICIT_ESCALATION = [
    # PT
    "gerente", "supervisor", "responsavel", "responsável",
    "procon", "reclame aqui", "tribunal", "advogado",
    "processo", "absurdo", "inaceitavel", "inaceitável",
    "vou processar", "vou no procon", "quero meu dinheiro",
    "exijo", "indignado", "indignada", "revoltado",
    # EN
    "manager", "supervisor", "lawsuit", "lawyer", "attorney",
    "demand", "outrageous", "unacceptable", "worst service",
    "i demand", "legal action", "report you",
    # Security / hacking — always escalate
    "hackeada", "hackeado", "hack", "hacked", "invadida", "invadido",
    "acesso nao autorizado", "acesso não autorizado",
    "nao fui eu", "não fui eu", "estranha", "estranho",
    "suspicious", "compromised", "unauthorized",
    "someone else", "not me", "strange activity",
    "atividade suspeita", "atividade estranha",
    "roubaram", "roubou", "stolen",
]

STEP_BY_STEP_CATEGORIES = ["Account Access", "Technical Issue"]
ALWAYS_ESCALATE_CATEGORIES = ["Billing"]

EXCHANGE_APPROVED_PATTERNS = [
    r'troca\s+(foi\s+)?aprovada',
    r'minha\s+troca\s+foi\s+aprovada',
    r'devolução\s+(foi\s+)?aprovada',
    r'devolucao\s+(foi\s+)?aprovada',
    r'exchange\s+(was\s+)?approved',
    r'return\s+(was\s+)?approved',
    r'troca\s+aprovada',
]

# HOW-TO patterns: informational questions that should resolve directly,
# never require order number or email.
HOW_TO_PATTERNS = [
    r'\bhow (do|can|to)\b',
    r'\bhow (do i|can i|should i)\b',
    r'\bwhat is (your|the)\b',
    r'\bwhat are (your|the)\b',
    r'\bwhere (do|can) i\b',
    r'\bcan you (explain|tell me|describe)\b',
    r'^como (fa[cç]o|posso|funciona|rastreio|rastrear)',
    r'\bcomo (fa[cç]o|posso|funciona|rastreio|rastrear)\b',
    r'\bqual [eéa] (a|o|política|prazo|processo|regra)\b',
    r'\bqual a (pol[ií]tica|regra|forma|maneira|condi[cç][aã]o)\b',
    r'\bquais s[aã]o\b',
    r'\bonde (posso|devo|fico)\b',
]


LOGISTICS_ALERTS = {
    "norte_nordeste": {
        "active": True,
        "states": [
            # Norte
            "AM", "PA", "AC", "RO", "RR", "AP", "TO",
            # Nordeste
            "MA", "PI", "CE", "RN", "PB", "PE", "AL", "SE", "BA",
        ],
        "message_pt": (
            "Identificamos que sua região (Norte/Nordeste) está sendo "
            "afetada por um atraso em nossa frota logística. "
            "Nossos caminhões estão operando com capacidade "
            "reduzida devido a manutenção programada da frota. "
            "Sua entrega pode ter um atraso adicional de "
            "3 dias úteis além do prazo original. Pedimos desculpas pelo "
            "transtorno."
        ),
        "message_en": (
            "We've identified that your region (North/Northeast Brazil) "
            "is affected by a logistics delay in our fleet. Our trucks "
            "are operating at reduced capacity due to scheduled "
            "fleet maintenance. Your delivery may have an additional "
            "3 business days delay beyond the original "
            "estimated date. We apologize for the inconvenience."
        ),
        "eta_additional_days": 3,
    }
}

WEATHER_DELAY_REGIONS = {
    "sul_sudeste": {
        "active": True,
        "cities": [
            # Sul
            "Curitiba", "Porto Alegre", "Florianópolis", "Florianopolis",
            "Joinville", "Blumenau", "Caxias do Sul",
            # Sudeste
            "São Paulo", "Sao Paulo", "Rio de Janeiro",
            "Belo Horizonte", "Campinas", "Guarulhos", "Vitória", "Vitoria",
        ],
        "message_pt": (
            "Identificamos condições climáticas adversas "
            "em sua região ({city}: {conditions}, {temp}°C). "
            "As condições estão causando atrasos nas operações logísticas "
            "no Sul/Sudeste do Brasil. Sua entrega "
            "pode ter um atraso adicional de 1-2 dias úteis. "
            "Assim que as condições melhorarem, sua entrega "
            "será priorizada."
        ),
        "message_en": (
            "We've detected adverse weather conditions in "
            "your region ({city}: {conditions}, {temp}°C). "
            "These conditions are causing logistics delays "
            "in Southern/Southeastern Brazil. Your delivery may have an "
            "additional delay of 1-2 business days."
        ),
    }
}


def check_logistics_alert(state_code: str) -> dict | None:
    for alert_key, alert in LOGISTICS_ALERTS.items():
        if (alert.get("active") and
                state_code.upper() in alert.get("states", [])):
            return {
                "alert_active": True,
                "alert_key": alert_key,
                "message_pt": alert["message_pt"],
                "message_en": alert["message_en"],
                "eta_additional_days": alert["eta_additional_days"],
            }
    return None


def check_weather_delay(city: str, weather_result: dict) -> dict | None:
    if not weather_result.get("available"):
        return None
    if not weather_result.get("adverse_conditions"):
        return None
    conditions = weather_result.get("conditions", "")
    temp = weather_result.get("temperature_c", "")
    city_lower = city.lower()
    for region, config in WEATHER_DELAY_REGIONS.items():
        if config.get("active") and any(
            c.lower() in city_lower or city_lower in c.lower()
            for c in config["cities"]
        ):
            return {
                "delay_active": True,
                "region": region,
                "message_pt": config["message_pt"].format(
                    city=city, conditions=conditions, temp=temp,
                ),
                "message_en": config["message_en"].format(
                    city=city, conditions=conditions, temp=temp,
                ),
            }
    # Fallback: any city with adverse conditions gets a delay
    return {
        "delay_active": True,
        "region": "generic",
        "message_pt": (
            f"Identificamos condições climáticas adversas em {city} "
            f"({conditions}, {temp}°C). "
            f"As condições estão causando atrasos nas operações logísticas. "
            f"Sua entrega pode ter um atraso adicional de 1-2 dias úteis."
        ),
        "message_en": (
            f"We've detected adverse weather conditions in {city} "
            f"({conditions}, {temp}°C). "
            f"These conditions are causing logistics delays. "
            f"Your delivery may have an additional delay of 1-2 business days."
        ),
    }


def _is_how_to_question(text: str) -> bool:
    """Returns True if the inquiry is an informational HOW-TO question."""
    norm = normalize(text)
    for pattern in HOW_TO_PATTERNS:
        if re.search(pattern, norm, re.IGNORECASE):
            return True
    return False


@dataclass
class RoutingDecision:
    action: str           # "escalate", "awaiting", "resolve", "step_by_step"
    reason: str
    missing_info: list[str]
    has_order_number: bool
    has_email: bool
    has_invoice: bool
    explicit_escalation: bool
    triggered_keyword: Optional[str]
    confidence: float     # 0-1


def _clean_for_routing(inquiry: str) -> str:
    """Strip all appended intake metadata before pattern matching."""
    clean = inquiry
    clean = re.sub(r'\nPhone:\s*.+', '', clean, flags=re.IGNORECASE)
    clean = re.sub(r'\nEmail:\s*.+', '', clean, flags=re.IGNORECASE)
    clean = re.sub(r'\nName:\s*.+', '', clean, flags=re.IGNORECASE)
    clean = re.sub(r'\nCustomer Name:\s*.+', '', clean, flags=re.IGNORECASE)
    clean = re.sub(r'\n---.*$', '', clean, flags=re.DOTALL | re.IGNORECASE)
    clean = re.sub(r"',\s*\)$", '', clean)
    clean = re.sub(r"'\s*,?\s*\)$", '', clean)
    return clean.strip()


def _has_pattern(text: str, patterns: list[str]) -> bool:
    for p in patterns:
        if re.search(p, text, re.IGNORECASE):
            return True
    return False


def _find_explicit_escalation(text: str) -> Optional[str]:
    normalized = normalize(text)
    for kw in EXPLICIT_ESCALATION:
        if normalize(kw) in normalized:
            return kw
    return None


def route_ticket(
    inquiry: str,
    category: str,
    sentiment: str,
    urgency: str,
) -> RoutingDecision:
    logger.debug("route_ticket input: %r", inquiry[:200])

    norm_inquiry  = normalize(inquiry)
    clean_inquiry = _clean_for_routing(inquiry)

    logger.debug("route_ticket clean: %r", clean_inquiry[:200])

    has_order   = _has_pattern(clean_inquiry, ORDER_NUMBER_PATTERNS)
    has_email   = _has_pattern(clean_inquiry, EMAIL_PATTERNS)
    has_invoice = _has_pattern(clean_inquiry, INVOICE_PATTERNS)

    logger.debug("has_order=%s has_email=%s category=%s", has_order, has_email, category)

    esc_keyword = _find_explicit_escalation(clean_inquiry)

    missing = []

    # RULE 1: Explicit escalation always wins
    if esc_keyword:
        return RoutingDecision(
            action="escalate",
            reason=f"Customer explicitly requested escalation (keyword: '{esc_keyword}')",
            missing_info=[],
            has_order_number=has_order,
            has_email=has_email,
            has_invoice=has_invoice,
            explicit_escalation=True,
            triggered_keyword=esc_keyword,
            confidence=1.0,
        )

    # RULE 2: Billing always escalates
    if category == "Billing":
        return RoutingDecision(
            action="escalate",
            reason="Billing issues always require human review for financial security",
            missing_info=[],
            has_order_number=has_order,
            has_email=has_email,
            has_invoice=has_invoice,
            explicit_escalation=False,
            triggered_keyword=None,
            confidence=1.0,
        )

    # RULE 3a: HOW-TO questions resolve directly — never ask for order details
    if category == "Order Issues" and _is_how_to_question(inquiry):
        return RoutingDecision(
            action="resolve",
            reason="Informational HOW-TO question — no order details required",
            missing_info=[],
            has_order_number=has_order,
            has_email=has_email,
            has_invoice=has_invoice,
            explicit_escalation=False,
            triggered_keyword=None,
            confidence=0.85,
        )

    # RULE 2.5: Exchange/return already approved → auto-resolve with next steps
    if category == "Order Issues" and _has_pattern(norm_inquiry, EXCHANGE_APPROVED_PATTERNS):
        return RoutingDecision(
            action="resolve",
            reason="Approved exchange/return — providing operational next steps",
            missing_info=[],
            has_order_number=has_order,
            has_email=has_email,
            has_invoice=has_invoice,
            explicit_escalation=False,
            triggered_keyword=None,
            confidence=0.9,
        )

    # RULE 3: Order Issues
    if category == "Order Issues":
        if not has_order:
            missing.append("order_number")

        if missing:
            return RoutingDecision(
                action="awaiting",
                reason=f"Missing required information: {', '.join(missing)}",
                missing_info=missing,
                has_order_number=has_order,
                has_email=has_email,
                has_invoice=has_invoice,
                explicit_escalation=False,
                triggered_keyword=None,
                confidence=0.9,
            )
        else:
            return RoutingDecision(
                action="escalate",
                reason="Customer provided order details — routing to specialist for investigation",
                missing_info=[],
                has_order_number=has_order,
                has_email=has_email,
                has_invoice=has_invoice,
                explicit_escalation=False,
                triggered_keyword=None,
                confidence=0.95,
            )

    # RULE 3.5: Account security issues → always escalate
    SECURITY_KEYWORDS = [
        'hack', 'hackeada', 'hackeado', 'hacked', 'invadida', 'invadido',
        'acesso nao autorizado', 'acesso não autorizado',
        'nao fui eu', 'nao sou eu', 'suspicious activity',
        'unauthorized', 'compromised', 'atividade suspeita',
        'roubaram', 'stolen', 'someone else logged',
    ]
    if category == "Account Access" and any(
        normalize(kw) in norm_inquiry for kw in SECURITY_KEYWORDS
    ):
        return RoutingDecision(
            action="escalate",
            reason="Account security issue — requires immediate security team review",
            missing_info=[],
            has_order_number=has_order,
            has_email=has_email,
            has_invoice=has_invoice,
            explicit_escalation=True,
            triggered_keyword="security_issue",
            confidence=1.0,
        )

    # RULE 4: Account Access → step by step
    if category == "Account Access":
        return RoutingDecision(
            action="step_by_step",
            reason="Account access issues can be resolved with guided instructions",
            missing_info=[],
            has_order_number=has_order,
            has_email=has_email,
            has_invoice=has_invoice,
            explicit_escalation=False,
            triggered_keyword=None,
            confidence=0.85,
        )

    # RULE 5: Technical Issue → always step_by_step first
    # (awaiting only shown if customer clicks "Not Helpful" in the UI)
    if category == "Technical Issue":
        return RoutingDecision(
            action="step_by_step",
            reason="Technical issue — providing guided troubleshooting steps",
            missing_info=[],
            has_order_number=has_order,
            has_email=has_email,
            has_invoice=has_invoice,
            explicit_escalation=False,
            triggered_keyword=None,
            confidence=0.85,
        )

    # RULE 6: General Support → auto-resolve
    return RoutingDecision(
        action="resolve",
        reason="General inquiry — AI can provide complete answer",
        missing_info=[],
        has_order_number=has_order,
        has_email=has_email,
        has_invoice=has_invoice,
        explicit_escalation=False,
        triggered_keyword=None,
        confidence=0.9,
    )
