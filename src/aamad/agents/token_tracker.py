"""Global token usage tracker for CrewAI/LiteLLM calls."""
import logging
from threading import local
from typing import Dict, Any

logger = logging.getLogger(__name__)

# Thread-local storage for token tracking
_thread_local = local()


def get_current_tokens() -> Dict[str, int]:
    """Get accumulated tokens for current thread/crew execution."""
    if not hasattr(_thread_local, 'tokens'):
        _thread_local.tokens = {"input_tokens": 0, "output_tokens": 0, "total_tokens": 0}
    return _thread_local.tokens


def reset_tokens():
    """Reset token counter for new crew execution."""
    _thread_local.tokens = {"input_tokens": 0, "output_tokens": 0, "total_tokens": 0}


def track_completion(kwargs: Dict[str, Any], completion_response: Any, start_time: float, end_time: float):
    """
    Callback function for LiteLLM to track token usage.
    This is called after each LLM completion.
    """
    try:
        # Extract usage from response
        usage = None
        if hasattr(completion_response, 'usage'):
            usage = completion_response.usage
        elif isinstance(completion_response, dict) and 'usage' in completion_response:
            usage = completion_response['usage']
        
        if usage:
            # Get token counts
            if hasattr(usage, 'prompt_tokens'):
                input_tokens = usage.prompt_tokens
                output_tokens = usage.completion_tokens
                total_tokens = usage.total_tokens
            elif isinstance(usage, dict):
                input_tokens = usage.get('prompt_tokens', 0)
                output_tokens = usage.get('completion_tokens', 0)
                total_tokens = usage.get('total_tokens', 0)
            else:
                return
            
            # Accumulate in thread-local storage
            tokens = get_current_tokens()
            tokens['input_tokens'] += input_tokens
            tokens['output_tokens'] += output_tokens
            tokens['total_tokens'] += total_tokens
            
            logger.debug(
                "Token usage tracked: input=%d output=%d total=%d (accumulated: %d)",
                input_tokens, output_tokens, total_tokens, tokens['total_tokens']
            )
    except Exception as e:
        logger.warning("Failed to track token usage: %s", e)

# Made with Bob
