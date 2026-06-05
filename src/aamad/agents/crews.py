

"""Three CrewAI Crews that map to the three pipeline phases."""

import io
import json
import logging
import os
import re
import sys

from crewai import Crew, Process
import litellm

logger = logging.getLogger(__name__)

# Thread-local token tracking
_crew_tokens = {"input": 0, "output": 0, "total": 0}

def _reset_tokens():
    """Reset token counter before crew execution."""
    global _crew_tokens
    _crew_tokens = {"input": 0, "output": 0, "total": 0}

def _track_tokens(kwargs, completion_response, start_time, end_time):
    """Callback to track token usage from LiteLLM."""
    try:
        usage = getattr(completion_response, 'usage', None)
        if usage:
            _crew_tokens["input"] += getattr(usage, 'prompt_tokens', 0)
            _crew_tokens["output"] += getattr(usage, 'completion_tokens', 0)
            _crew_tokens["total"] += getattr(usage, 'total_tokens', 0)
    except Exception:
        pass


def _kickoff_silent(crew: Crew):
    """Run crew.kickoff() suppressing rich/console TTY errors (non-TTY servers)."""
    old_stdout, old_stderr = sys.stdout, sys.stderr
    try:
        sys.stdout = io.StringIO()
        sys.stderr = io.StringIO()
        return crew.kickoff()
    finally:
        sys.stdout = old_stdout
        sys.stderr = old_stderr


def _extract_json(raw: str) -> dict:
    """Strip markdown fences and parse JSON; on failure, extract via regex."""
    cleaned = raw.replace("```json", "").replace("```", "").strip()
    try:
        return json.loads(cleaned)
    except json.JSONDecodeError:
        # Try to extract the first {...} block even if the string is truncated
        m = re.search(r'\{.*', cleaned, re.DOTALL)
        if m:
            fragment = m.group(0)
            # Attempt auto-close of truncated JSON
            try:
                return json.loads(fragment)
            except json.JSONDecodeError:
                pass
        raise


def run_analysis_crew(inquiry: str) -> dict:
    """
    Crew 1: Classification + Sentiment sequential.
    Returns combined category + sentiment result + token usage.
    """
    from .tasks import make_classification_task, make_sentiment_task
    from .definitions import classification_agent, sentiment_agent

    # Reset and register callback
    _reset_tokens()
    old_callbacks = litellm.success_callback
    litellm.success_callback = [_track_tokens]

    try:
        crew = Crew(
            agents=[classification_agent, sentiment_agent],
            tasks=[
                make_classification_task(inquiry),
                make_sentiment_task(inquiry),
            ],
            process=Process.sequential,
            verbose=False,
        )

        result = _kickoff_silent(crew)

        # Get tracked tokens
        token_usage = {
            "input_tokens": _crew_tokens["input"],
            "output_tokens": _crew_tokens["output"],
            "total_tokens": _crew_tokens["total"],
        }
    finally:
        # Restore old callbacks
        litellm.success_callback = old_callbacks

    try:
        tasks_output = result.tasks_output

        classification = _extract_json(tasks_output[0].raw)
        sentiment = _extract_json(tasks_output[1].raw)

        return {
            "category": classification.get("category", "General Support"),
            "confidence": classification.get("confidence", 70),
            "language": classification.get("language", "pt"),
            "scores": classification.get("scores", {}),
            "sentiment": sentiment.get("sentiment", "Neutral"),
            "urgency": sentiment.get("urgency", "Low"),
            "sentiment_confidence": sentiment.get("confidence", 70),
            "token_usage": token_usage,
        }

    except Exception as e:
        logger.error("Analysis crew parsing failed: %s", e)
        return {
            "category": "General Support",
            "confidence": 70,
            "language": "pt",
            "sentiment": "Neutral",
            "urgency": "Low",
            "sentiment_confidence": 70,
            "token_usage": {},
        }


def run_response_crew(
    inquiry: str,
    category: str,
    sentiment: str,
    urgency: str,
    routing_action: str,
    knowledge_context: str,
    external_context: str,
    detected_language: str,
) -> dict:
    """
    Crew 2: Knowledge + Response sequential.
    Knowledge agent summarises KB snippets; Response agent
    uses that summary + external context to write the reply.
    """
    from .tasks import make_knowledge_task, make_response_task
    from .definitions import knowledge_agent, response_agent

    # Reset and register callback
    _reset_tokens()
    old_callbacks = litellm.success_callback
    litellm.success_callback = [_track_tokens]

    try:
        crew = Crew(
            agents=[knowledge_agent, response_agent],
            tasks=[
                make_knowledge_task(inquiry, category, knowledge_context),
                make_response_task(
                    inquiry, category, sentiment, urgency,
                    routing_action, knowledge_context,
                    external_context, detected_language,
                ),
            ],
            process=Process.sequential,
            verbose=False,
        )

        result = _kickoff_silent(crew)

        # Get tracked tokens
        token_usage = {
            "input_tokens": _crew_tokens["input"],
            "output_tokens": _crew_tokens["output"],
            "total_tokens": _crew_tokens["total"],
        }
    finally:
        # Restore old callbacks
        litellm.success_callback = old_callbacks

    try:
        response_text = result.tasks_output[-1].raw.strip()
        if response_text.startswith("{"):
            data = json.loads(response_text)
            response_text = data.get("response", response_text)
        return {"response": response_text, "token_usage": token_usage}
    except Exception as e:
        logger.error("Response crew failed: %s", e)
        return {"response": "", "token_usage": {}}


def run_evaluation_crew(
    inquiry: str,
    response: str,
    category: str,
    knowledge_context: str,
    external_context: str,
) -> dict:
    """
    Crew 3: Summary + Quality Evaluator sequential.
    Sonnet acts as judge for quality evaluation (cross-model).
    """
    from .tasks import make_summary_task, make_quality_task
    from .definitions import summary_agent, quality_agent

    # Reset and register callback
    _reset_tokens()
    old_callbacks = litellm.success_callback
    litellm.success_callback = [_track_tokens]

    try:
        crew = Crew(
            agents=[summary_agent, quality_agent],
            tasks=[
                make_summary_task(inquiry, category, "", response),
                make_quality_task(
                    inquiry, response, category,
                    knowledge_context, external_context,
                ),
            ],
            process=Process.sequential,
            verbose=False,
        )

        result = _kickoff_silent(crew)

        # Get tracked tokens
        token_usage = {
            "input_tokens": _crew_tokens["input"],
            "output_tokens": _crew_tokens["output"],
            "total_tokens": _crew_tokens["total"],
        }
    finally:
        # Restore old callbacks
        litellm.success_callback = old_callbacks

    try:
        tasks_output = result.tasks_output

        summary = _extract_json(tasks_output[0].raw)
        quality = _extract_json(tasks_output[1].raw)

        return {"summary": summary, "quality": quality, "token_usage": token_usage}

    except Exception as e:
        logger.error("Evaluation crew failed: %s", e)
        return {"summary": {}, "quality": {}, "token_usage": {}}
