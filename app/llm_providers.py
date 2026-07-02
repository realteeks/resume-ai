"""Unified LLM layer over Gemini/Gemma (Google AI) and Groq.

Builds a flat pool of "backends" — one per (provider, model, api_key) — and
serves JSON-returning chat calls with:
  * round-robin load balancing across all keys/models, and
  * automatic failover: if one backend is rate-limited or errors, the request
    retries on the next backend until one succeeds.

This lets several free-tier keys (and two providers) act like one bigger quota.
"""

import itertools
import json
import logging
import threading
from dataclasses import dataclass
from typing import Callable

from app.config import settings

logger = logging.getLogger(__name__)


class AllBackendsFailed(RuntimeError):
    """Raised when every configured provider/key failed for one request."""


@dataclass
class Backend:
    name: str  # e.g. "gemini:gemma-4-31b-it#2"
    call: Callable[[str, str, float], str]  # (system, user, temperature) -> raw text


def _loads(raw: str) -> dict:
    """Parse JSON from a model response, tolerating code fences / prose."""
    if raw is None:
        raise ValueError("Empty LLM response")
    text = raw.strip()
    if text.startswith("```"):
        text = text.strip("`")
        if text[:4].lower() == "json":
            text = text[4:]
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        start, end = text.find("{"), text.rfind("}")
        if start != -1 and end > start:
            return json.loads(text[start : end + 1])
        raise


def _build_gemini_backends() -> list[Backend]:
    keys = settings.gemini_keys
    models = settings.gemini_model_list
    if not keys or not models:
        return []

    from google import genai
    from google.genai import types

    backends: list[Backend] = []
    for model in models:
        for idx, key in enumerate(keys, 1):
            client = genai.Client(api_key=key)

            def _make(client=client, model=model):
                def _call(system: str, user: str, temperature: float) -> str:
                    # Gemma models don't support a system role, so inline it.
                    prompt = f"{system}\n\n{user}"
                    resp = client.models.generate_content(
                        model=model,
                        contents=prompt,
                        config=types.GenerateContentConfig(temperature=temperature),
                    )
                    return resp.text

                return _call

            backends.append(Backend(f"gemini:{model}#{idx}", _make()))
    return backends


def _build_groq_backends() -> list[Backend]:
    keys = settings.groq_keys
    if not keys:
        return []

    from groq import Groq

    backends: list[Backend] = []
    for idx, key in enumerate(keys, 1):
        client = Groq(api_key=key)

        def _make(client=client):
            def _call(system: str, user: str, temperature: float) -> str:
                completion = client.chat.completions.create(
                    model=settings.groq_model,
                    messages=[
                        {"role": "system", "content": system},
                        {"role": "user", "content": user},
                    ],
                    temperature=temperature,
                    response_format={"type": "json_object"},
                )
                return completion.choices[0].message.content

            return _call

        backends.append(Backend(f"groq:{settings.groq_model}#{idx}", _make()))
    return backends


_backends: list[Backend] | None = None
_cursor: "itertools.cycle | None" = None
_lock = threading.Lock()


def _ensure_pool() -> None:
    global _backends, _cursor
    if _backends is not None:
        return
    gemini = _build_gemini_backends()
    groq = _build_groq_backends()
    backends = gemini + groq if settings.primary_provider == "gemini" else groq + gemini
    if not backends:
        raise RuntimeError(
            "No LLM provider configured. Set GEMINI_API_KEYS and/or GROQ_API_KEYS "
            "in your .env."
        )
    _backends = backends
    _cursor = itertools.cycle(range(len(backends)))
    logger.info(
        "LLM pool ready: %d backend(s) [%s]",
        len(backends),
        ", ".join(b.name for b in backends),
    )


def generate_json(system: str, user: str, temperature: float = 0.4) -> dict:
    """Call the LLM pool and return parsed JSON, with rotation + failover."""
    _ensure_pool()
    assert _backends is not None and _cursor is not None
    with _lock:
        start = next(_cursor)
    ordered = _backends[start:] + _backends[:start]

    last_error: Exception | None = None
    for backend in ordered:
        try:
            raw = backend.call(system, user, temperature)
            return _loads(raw)
        except Exception as e:  # noqa: BLE001 - any failure -> try next backend
            last_error = e
            logger.warning("Backend %s failed (%s); failing over.", backend.name, e)
            continue

    raise AllBackendsFailed(
        f"All {len(ordered)} LLM backend(s) failed. Last error: {last_error}"
    )


def pool_status() -> dict:
    """Lightweight introspection for /healthz (no network calls)."""
    return {
        "gemini_keys": len(settings.gemini_keys),
        "gemini_models": settings.gemini_model_list,
        "groq_keys": len(settings.groq_keys),
        "primary_provider": settings.primary_provider,
    }
