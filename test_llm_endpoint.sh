#!/bin/bash
# Test if LLM is working in production by creating a test ticket

echo "=========================================="
echo "Testing LLM in Production"
echo "=========================================="
echo ""

# Test 1: Simple inquiry without escalation keywords
echo "Test 1: Simple order inquiry (should use LLM)"
echo "---"

curl -X POST "https://web-production-126e2.up.railway.app/api/support" \
  -H "Content-Type: application/json" \
  -H "X-API-Key: dev-key-change-in-production" \
  -d '{
    "inquiry": "Gostaria de saber o prazo de entrega para São Paulo",
    "customer_email": "test@example.com"
  }' | python3 -m json.tool | grep -A 5 "reference_id\|execution_mode\|cache_used"

echo ""
echo "=========================================="
echo "Check the reference_id above and query:"
echo "curl -H \"X-API-Key: dev-key-change-in-production\" \"https://web-production-126e2.up.railway.app/api/observability/tickets/REF-ID-HERE\""
echo "=========================================="

# Made with Bob
