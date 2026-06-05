#!/usr/bin/env python3
"""Test script to verify Anthropic API key is working."""

import os
import sys

def test_anthropic_api():
    """Test if Anthropic API key is configured and working."""
    
    # Check if key exists
    api_key = os.getenv("ANTHROPIC_API_KEY")
    
    if not api_key:
        print("❌ ANTHROPIC_API_KEY not found in environment variables")
        print("\nTo fix:")
        print("1. Create/edit .env file")
        print("2. Add: ANTHROPIC_API_KEY=your-key-here")
        print("3. Restart the server")
        return False
    
    print(f"✅ ANTHROPIC_API_KEY found: {api_key[:20]}...")
    
    # Test API call
    try:
        from anthropic import Anthropic
        
        print("\n🔄 Testing API call...")
        client = Anthropic(api_key=api_key)
        
        response = client.messages.create(
            model="claude-haiku-4-5-20251001",
            max_tokens=50,
            messages=[{
                "role": "user",
                "content": "Say 'API working' in JSON format: {\"status\": \"working\"}"
            }]
        )
        
        result = response.content[0].text
        print(f"✅ API Response: {result}")
        print(f"✅ Tokens used: input={response.usage.input_tokens}, output={response.usage.output_tokens}")
        
        # Calculate cost
        input_tokens = response.usage.input_tokens
        output_tokens = response.usage.output_tokens
        cost = (input_tokens * 0.0000008) + (output_tokens * 0.000004)
        print(f"✅ Cost: ${cost:.6f}")
        
        return True
        
    except ImportError:
        print("❌ anthropic package not installed")
        print("\nTo fix: pip install anthropic")
        return False
        
    except Exception as e:
        print(f"❌ API call failed: {e}")
        print("\nPossible causes:")
        print("1. Invalid API key")
        print("2. Network issues")
        print("3. API quota exceeded")
        return False

if __name__ == "__main__":
    print("=" * 60)
    print("🧪 Testing Anthropic API Configuration")
    print("=" * 60)
    
    success = test_anthropic_api()
    
    print("\n" + "=" * 60)
    if success:
        print("✅ All tests passed! LLM should work correctly.")
    else:
        print("❌ Tests failed. Fix the issues above.")
    print("=" * 60)
    
    sys.exit(0 if success else 1)

# Made with Bob
