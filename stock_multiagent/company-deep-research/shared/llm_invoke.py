from __future__ import annotations

import time
from typing import Any


def is_token_limit_exceeded(e: Exception) -> bool:
    """
    OpenAI 컨텍스트 길이 초과 에러를 감지합니다.
    rate limit(429)이 아니라 입력 자체가 너무 긴 경우(400)를 탐지합니다.
    (open_deep_research의 is_token_limit_exceeded 패턴)
    """
    msg = str(e)
    return (
        "context_length_exceeded" in msg
        or "maximum context length" in msg.lower()
        or "prompt is too long" in msg.lower()
        or "reduce the length of the messages" in msg.lower()
        or ("Error code: 400" in msg and "context" in msg.lower())
        or ("Error code: 400" in msg and "token" in msg.lower())
    )


def invoke_with_backoff(
    llm,
    messages: list[dict[str, Any]],
    *,
    max_attempts: int = 6,
    **invoke_kwargs,
):
    """
    Retries transient OpenAI/transport failures (notably 429 TPM) with exponential backoff.
    Keeps dependencies minimal by catching broad exceptions and pattern-matching known cases.
    """

    base_sleep_s = 0.75
    last_err: Exception | None = None

    for attempt in range(1, max_attempts + 1):
        try:
            return llm.invoke(messages, **invoke_kwargs)
        except Exception as e:  # noqa: BLE001
            last_err = e
            msg = str(e)
            is_rate_limit = (
                "Rate limit reached" in msg
                or "rate_limit_exceeded" in msg
                or ("Error code: 429" in msg and "insufficient_quota" not in msg)
            )
            is_insufficient_quota = "insufficient_quota" in msg

            # Quota/billing exhaustion is not recoverable via retries.
            if is_insufficient_quota:
                raise RuntimeError(
                    "OpenAI quota exhausted (`insufficient_quota`). "
                    "Check billing/quota for the org tied to your `OPENAI_API_KEY`."
                ) from e

            # Context length exceeded: re-raise immediately so caller can truncate input.
            # Retrying with the same payload would just fail again.
            if is_token_limit_exceeded(e):
                raise

            # Only retry rate-limit-ish errors (avoid hiding real bugs).
            if not is_rate_limit or attempt >= max_attempts:
                raise

            # Small exponential backoff; OpenAI errors often include a short "try again in Xs" window.
            sleep_s = min(8.0, base_sleep_s * (2 ** (attempt - 1)))
            time.sleep(sleep_s)

    # Should never reach here, but keep mypy happy.
    raise last_err or RuntimeError("LLM invoke failed")

