"""Debug script to check CrewAI result structure."""
import sys
sys.path.insert(0, '/home/beatriz/AAMAD/src')

from aamad.agents.crews import run_analysis_crew

# Test with a simple inquiry
result = run_analysis_crew("Meu pedido não chegou")

print("=== CrewAI Result Structure ===")
print(f"Type: {type(result)}")
print(f"Keys: {result.keys() if isinstance(result, dict) else 'N/A'}")
print(f"\nFull result: {result}")

if 'token_usage' in result:
    print(f"\nToken usage found: {result['token_usage']}")
else:
    print("\n❌ No token_usage in result!")

# Check what attributes the raw CrewAI result has
print("\n=== Checking raw CrewAI result object ===")
print("This will show what's actually available from CrewAI...")

# Made with Bob
