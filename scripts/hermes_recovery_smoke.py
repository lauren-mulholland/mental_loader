"""Exercise the real patched Hermes recovery path with a synthetic error; no model calls."""

import copy
import sys
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / ".local/hermes-runtime"))


def main():
    import httpx
    from agent.error_classifier import FailoverReason, classify_api_error
    from agent.turn_recovery import (
        _recover_format_errors,
        _recover_openrouter_encrypted_reasoning,
    )
    from agent.turn_retry_state import TurnRetryState
    from openai import BadRequestError

    error = BadRequestError(
        "Provider returned error: invalid_encrypted_content",
        response=httpx.Response(400, request=httpx.Request("POST", "https://example.test")),
        body={"error": {"code": 400, "message": "Provider returned error", "metadata": {
            "provider_error_code": "invalid_encrypted_content",
            "raw": '{"error":{"code":"invalid_encrypted_content"}}',
        }}},
    )
    classified = classify_api_error(error)
    assert classified.reason == FailoverReason.invalid_encrypted_content
    agent = SimpleNamespace(
        provider="openrouter", api_mode="chat_completions",
        model="openai/gpt-6-luna", log_prefix="fixture: ",
    )
    history = [
        {"role": "user", "content": "Fixture task"},
        {"role": "assistant", "content": "Saved", "tool_calls": [{"id": "fixture"}],
         "reasoning_details": [
             {"type": "reasoning.summary", "summary": "fixture summary"},
             {"type": "reasoning.encrypted", "data": "fixture-openai",
              "format": "openai-responses-v1"},
         ]},
        {"role": "tool", "tool_call_id": "fixture", "content": "Fixture receipt"},
        {"role": "assistant", "content": "Done", "reasoning_details": [
            {"type": "reasoning.encrypted", "data": "fixture-azure",
             "format": "azure-openai-responses-v1"},
        ]},
    ]
    original = copy.deepcopy(history)
    wire = copy.deepcopy(history)
    retry = TurnRetryState()
    assert _recover_format_errors(agent, error, classified, retry, history, wire)
    for repaired in (history, wire):
        assert [m["content"] for m in repaired] == [m["content"] for m in original]
        assert repaired[1]["tool_calls"] == original[1]["tool_calls"]
        assert repaired[2] == original[2]
        assert repaired[1]["reasoning_details"] == [original[1]["reasoning_details"][0]]
        assert "reasoning_details" not in repaired[3]
    assert not _recover_openrouter_encrypted_reasoning(
        agent, retry, original, copy.deepcopy(original)
    )
    for provider, model, mode in (
        ("anthropic", "claude", "anthropic_messages"),
        ("openrouter", "anthropic/claude", "chat_completions"),
        ("openai", "gpt-6-luna", "codex_responses"),
    ):
        other = SimpleNamespace(provider=provider, model=model, api_mode=mode)
        untouched = copy.deepcopy(original)
        assert not _recover_openrouter_encrypted_reasoning(other, TurnRetryState(), untouched, [])
        assert untouched == original
    print("PASS: real Hermes classifier and recovery; both histories repaired, tool state "
          "preserved, retry bounded, unrelated routes untouched. No model calls.")


if __name__ == "__main__":
    main()
