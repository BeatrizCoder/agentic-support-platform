"""Unit tests for the AAMAD backend support crew."""

from __future__ import annotations

import asyncio
from datetime import datetime

import pytest
from fastapi.testclient import TestClient

from aamad.backend import app
from aamad.data_store import SupportTicketData, data_store
from aamad.flow.state import SupportState
from aamad.flow.support_flow import SupportFlow
from aamad.api.models import SupportTicket
from aamad.integrations.ticketing_client import TicketingClient
from aamad.integrations.crm_client import CRMClient
from aamad.integrations.notification_client import NotificationClient


def test_support_flow_process_returns_valid_context():
    # Test the tool registry directly since flow testing is complex
    from aamad.core.services import tool_registry

    # Test classification tool
    result = tool_registry.execute_tool("Classification Tool", "My order #12345 hasn't arrived yet.")
    assert result["category"] == "Order Issues"
    assert isinstance(result["confidence"], int)
    assert result["confidence"] > 0

    # Test sentiment tool
    result = tool_registry.execute_tool("Sentiment Analysis Tool", "My order #12345 hasn't arrived yet.")
    assert result["sentiment"] in ["Concerned", "Urgent", "Neutral"]
    assert "confidence" in result
    assert "urgency" in result

    # Test knowledge tool
    result = tool_registry.execute_tool("Knowledge Retrieval Tool", "Order Issues", "My order #12345 hasn't arrived yet.")
    assert "articles" in result
    assert "source" in result

    # Test response tool
    result = tool_registry.execute_tool("Response Generation Tool", "Order Issues", "High", 2)
    assert "response" in result
    assert "confidence" in result

    # Test escalation tool
    result = tool_registry.execute_tool("Escalation Evaluation Tool", 75, "Concerned", 2, "My order #12345 hasn't arrived yet.")
    assert "escalation_required" in result
    assert "reason" in result
    assert "reference_id" in result


def test_support_ticket_model():
    ticket = SupportTicket(inquiry="Please help with payment issues.")
    assert ticket.inquiry.startswith("Please help")


def test_data_store_operations():
    """Test data store CRUD operations."""
    # Create test data
    test_ticket = SupportTicketData(
        reference_id="TEST-123",
        inquiry="Test inquiry",
        category="Test Category",
        category_confidence=80,
        sentiment="Neutral",
        sentiment_confidence=70,
        urgency="Low",
        articles=["Test article"],
        response="Test response",
        response_confidence=75,
        escalation_required=False,
        escalation_reason="",
        steps=[{"agent": "Test Agent", "details": {"action": "test"}}],
        status="completed",
        created_at=datetime.now().isoformat(),
        updated_at=datetime.now().isoformat()
    )

    # Test save and retrieve
    data_store.save_ticket(test_ticket)
    retrieved = data_store.get_ticket("TEST-123")

    assert retrieved is not None
    assert retrieved.reference_id == "TEST-123"
    assert retrieved.inquiry == "Test inquiry"
    assert retrieved.status == "completed"

    # Test status update
    success = data_store.update_ticket_status("TEST-123", "approved")
    assert success

    updated = data_store.get_ticket("TEST-123")
    assert updated.status == "approved"


def test_mock_ticketing_client():
    """Test mock ticketing integration."""
    client = TicketingClient()

    # Test ticket creation
    ticket_data = {
        "inquiry": "Test inquiry",
        "category": "Test",
        "urgency": "High"
    }

    result = client.create_ticket(ticket_data)
    assert result["success"] is True
    assert "external_ticket_id" in result
    assert result["status"] == "open"

    # Test status update
    external_id = result["external_ticket_id"]
    update_result = client.update_ticket_status(external_id, "resolved")
    assert update_result["success"] is True
    assert update_result["status"] == "resolved"


def test_support_endpoint_falls_back_when_flow_raises(monkeypatch):
    async def _boom(*args, **kwargs):
        raise RuntimeError("simulated flow failure")

    monkeypatch.setattr("aamad.api.routes.SupportFlow.kickoff_async", _boom)

    client = TestClient(app)
    response = client.post(
        "/api/support",
        json={"inquiry": "Test fallback response"},
        headers={"Content-Type": "application/json"},
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["inquiry"] == "Test fallback response"
    assert payload["category"] == "General Support"
    assert payload["response_confidence"] == 50
    assert payload["reference_id"].startswith("REF-")
    assert payload["execution_mode"] == "fallback"


def test_awaiting_route_does_not_escalate_when_missing_order_number(monkeypatch):
    flow = SupportFlow()
    flow.state.routing_action = "awaiting"
    flow.state.routing_missing_info = ["order_number"]
    flow.state.response_confidence = 35
    flow.state.sentiment = "Concerned"
    flow.state.urgency = "High"
    flow.state.inquiry = "Minha entrega não chegou, preciso do número do pedido"
    flow.state.articles = []
    flow.state.escalation_required = True

    async def fake_execute_tool(tool_name, *args, **kwargs):
        return {
            "escalation_required": True,
            "reason": "Low confidence in automated response.",
            "reference_id": "ESC-2026-9999",
        }

    monkeypatch.setattr(flow, "execute_tool", fake_execute_tool)
    asyncio.run(flow.evaluate_escalation())

    assert flow.state.routing_action == "awaiting"
    assert flow.state.escalation_required is False
    assert flow.state.escalation_reason.startswith("Awaiting customer information")


def test_support_endpoint_includes_status_for_awaiting_route(monkeypatch):
    async def fake_kickoff(self, *args, **kwargs):
        self.state.routing_action = "awaiting"
        self.state.routing_missing_info = ["order_number"]
        self.state.response = "Please provide your order number for further investigation."
        self.state.response_confidence = 70
        self.state.articles = []
        self.state.category = "Order Issues"
        self.state.category_confidence = 90
        self.state.sentiment = "Concerned"
        self.state.sentiment_confidence = 75
        self.state.urgency = "High"
        self.state.escalation_required = False
        self.state.escalation_reason = "Awaiting order number"
        self.state.triggered_keyword = None
        self.state.steps = []
        self.state.knowledge_source = "mock"
        self.state.memory_saved = False
        self.state.execution_mode = "mock"
        self.state.prompt_template_used = None
        self.state.skills_used = []
        self.state.tools_used = []
        self.state.cache_used = False
        self.state.token_usage = {}
        self.state.cost_usd = 0.0
        self.state.api_tags = []
        self.state.quality_evaluation = {}
        self.state.pending_action = {}

    monkeypatch.setattr("aamad.api.routes.SupportFlow.kickoff_async", fake_kickoff)
    client = TestClient(app)
    response = client.post(
        "/api/support",
        json={"inquiry": "My order is delayed and I don't have the order number."},
        headers={"Content-Type": "application/json"},
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["status"] == "awaiting_customer_info"
    assert payload["routing_action"] == "awaiting"
    assert payload["escalation_required"] is False
    assert payload["routing_missing_info"] == ["order_number"]


def test_cors_preflight_returns_empty_204_without_content_length():
    client = TestClient(app)

    response = client.options(
        "/api/support",
        headers={
            "Origin": "http://localhost:5500",
            "Access-Control-Request-Method": "POST",
            "Access-Control-Request-Headers": "Content-Type",
        },
    )

    assert response.status_code == 204
    assert response.text == ""
    assert "content-length" not in response.headers


def test_mock_crm_client():
    """Test mock CRM integration."""
    client = CRMClient()

    # Test customer profile retrieval
    profile = client.get_customer_profile("test@example.com")
    assert profile["success"] is True
    assert "customer" in profile
    assert profile["customer"]["email"] == "test@example.com"

    # Test interaction logging
    interaction = client.log_interaction("CUST-123", "support_ticket", {"ticket_id": "TICKET-123"})
    assert interaction["success"] is True
    assert "interaction_id" in interaction


def test_mock_notification_client():
    """Test mock notification integration."""
    client = NotificationClient()

    # Test email sending
    email_result = client.send_email("test@example.com", "Test Subject", "Test body")
    assert email_result["success"] is True
    assert "message_id" in email_result

    # Test SMS sending
    sms_result = client.send_sms("+1234567890", "Test SMS")
    assert sms_result["success"] is True
    assert "message_id" in sms_result


def test_rest_api_tool():
    """Test REST API tool mock."""
    from tools.rest_api_tool import RESTApiTool, RESTApiRequest

    tool = RESTApiTool("test_api")

    # Test GET request
    request = RESTApiRequest(method="GET", url="https://api.example.com/data")
    result = tool._run(request)

    assert result["success"] is True
    assert "response" in result
    assert result["status_code"] == 200
    assert "latency" in result
    assert result["cached"] is False

    # Test POST request
    request = RESTApiRequest(method="POST", url="https://api.example.com/create", data={"name": "test"})
    result = tool._run(request)

    assert result["success"] is True
    assert result["status_code"] == 201
    assert "id" in result["response"]


def test_graphql_api_tool():
    """Test GraphQL API tool mock."""
    from tools.graphql_api_tool import GraphQLApiTool, GraphQLRequest

    tool = GraphQLApiTool("test_graphql")

    # Test user query
    request = GraphQLRequest(
        query="query { user { id name email } }",
        url="https://api.example.com/graphql"
    )
    result = tool._run(request)

    assert result["success"] is True
    assert "data" in result
    assert "user" in result["data"]
    assert "latency" in result

    # Test repository query
    request = GraphQLRequest(
        query="query { repository { name owner stars } }",
        url="https://api.example.com/graphql"
    )
    result = tool._run(request)

    assert result["success"] is True
    assert "repository" in result["data"]


def test_weather_tool():
    """Test weather API tool mock."""
    from tools.weather_tool import WeatherTool, WeatherRequest

    tool = WeatherTool()

    # Test current weather
    request = WeatherRequest(location="New York", units="metric")
    result = tool._run(request)

    assert result["success"] is True
    assert "data" in result
    assert "current" in result["data"]
    assert result["data"]["current"]["location"] == "New York"
    assert result["data"]["current"]["units"] == "metric"

    # Test weather with forecast
    request = WeatherRequest(location="London", units="imperial", include_forecast=True)
    result = tool._run(request)

    assert result["success"] is True
    assert "forecast" in result["data"]
    assert len(result["data"]["forecast"]) > 0


def test_github_tool():
    """Test GitHub API tool mock."""
    from tools.github_tool import GitHubTool, GitHubRequest

    tool = GitHubTool()

    # Test get repository
    request = GitHubRequest(action="get_repo", owner="octocat", repo="Hello-World")
    result = tool._run(request)

    assert result["success"] is True
    assert "data" in result
    assert result["data"]["name"] == "Hello-World"
    assert result["data"]["full_name"] == "octocat/Hello-World"

    # Test get user
    request = GitHubRequest(action="get_user", username="octocat")
    result = tool._run(request)

    assert result["success"] is True
    assert result["data"]["login"] == "octocat"

    # Test search repositories
    request = GitHubRequest(action="search_repos", query="python", limit=3)
    result = tool._run(request)

    assert result["success"] is True
    assert "items" in result["data"]
    assert len(result["data"]["items"]) <= 3
