"""Test script to check CrewAI result structure for token usage."""
from crewai import Crew

# Check what attributes are available on CrewOutput
result = type('MockResult', (), {
    'tasks_output': [],
    'token_usage': None,
    'usage_metrics': None,
})()

print("Checking CrewAI result attributes...")
print(dir(result))

# Check if Crew has usage_metrics
crew = type('MockCrew', (), {})()
print("\nChecking Crew attributes...")
print([attr for attr in dir(crew) if 'usage' in attr.lower() or 'token' in attr.lower()])

# Made with Bob
