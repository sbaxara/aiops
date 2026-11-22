# Copyright (c) 2026 Axara Pty Ltd.
# SPDX-License-Identifier: AGPL-3.0-or-later
"""
Shared low-level LLM chat helpers.

Extracted from llm_analyzer.py so LLMAnalyzer (incident analysis, env-driven recipe)
and plugins (their own recipe) call one implementation instead of copies. The
functions are incident-agnostic: they do the Ollama round-trip and JSON extraction.

The one deliberate change from the original `_call_chat`: it took recipe values
from instance state (self.temperature, ...) and hard-coded format/think. The
extracted `call_chat` takes `options`, `messages`, `fmt`, and `think` as explicit
arguments, so each caller supplies its own recipe. LLMAnalyzer passes its
env-derived options. Behaviour
for the incident path is preserved exactly (same payload shape, same parsing).
"""
import os
import re
import json
import logging
from typing import Any, Dict, List

import requests

logger = logging.getLogger(__name__)


def _tls_verify(url: str) -> bool:
    """TLS verification policy for the LLM endpoint. Default: verify for https,
    skip for plain http (internal air-gapped Ollama, where verify is moot).
    Override with LLM_TLS_VERIFY=true/false (e.g. false for a self-signed https
    Ollama). Replaces the previous unconditional verify=False."""
    env = os.getenv("LLM_TLS_VERIFY")
    if env is not None:
        return env.strip().lower() in ("1", "true", "yes")
    return url.lower().startswith("https://")


def call_chat(
    ollama_url: str,
    model: str,
    messages: List[Dict[str, str]],
    options: Dict[str, Any],
    request_timeout: int,
    *,
    fmt: str = "json",
    think: bool = False,
) -> str:
    """
    Call Ollama /api/chat. Returns message.content (str).

    Args:
        ollama_url:      base URL, e.g. http://127.0.0.1:11434
        model:           model tag, e.g. gemma4:26b
        messages:        list of {"role","content"} dicts (already plain dicts)
        options:         Ollama options dict (temperature/top_p/top_k/num_predict);
                         unset (None) entries are stripped to avoid unintended
                         Ollama defaults — same behaviour as the original.
        request_timeout: seconds
        fmt:             response format (default "json")
        think:           Ollama think flag (default False)

    Raises Exception on timeout or transport error (same messages as before).
    """
    url = f"{ollama_url}/api/chat"

    # strip unset options to avoid unintended defaults in Ollama
    options = {k: v for k, v in (options or {}).items() if v is not None}

    payload = {
        "model": model,
        "messages": messages,
        "stream": False,
        "format": fmt,
        "think": think,
        "options": options,
    }
    try:
        logger.info(f"LLM payload: {payload}")
        #print(f"LLM payload: {payload}")
        resp = requests.post(url, json=payload, timeout=request_timeout, verify=_tls_verify(ollama_url))
        resp.raise_for_status()
        data = resp.json()
        #print(f"LLM response: [{data}]")
        logger.info(f"LLM response: [{data}]")
        # /api/chat returns {"message": {"role": "...", "content": "..."}}
        return (data.get("message", {}) or {}).get("content", "") or ""
    except requests.exceptions.Timeout:
        logger.error("LLM request timed out")
        raise Exception(f"LLM inference timed out after {request_timeout}s")
    except requests.exceptions.RequestException as e:
        logger.error(f"LLM API request failed: {e}")
        raise Exception(f"Failed to communicate with LLM: {e}")


def strip_fences(text: str) -> str:
    """Strip markdown code fences from an LLM response. Verbatim from the
    original LLMAnalyzer._strip_fences."""
    t = text.strip()
    t = re.sub(r"^```(?:json)?\s*", "", t, flags=re.IGNORECASE)
    t = re.sub(r"\s*```$", "", t)
    return t.strip()


def extract_last_json_object(text: str) -> dict:
    """
    Extract the last balanced {...} block, tolerating a reasoning preamble and
    markdown fences. Raises ValueError if none parses. Verbatim (modulo the
    cls->module-function change) from LLMAnalyzer._extract_last_json_object.
    """
    cleaned = strip_fences(text)
    try:
        return json.loads(cleaned)
    except json.JSONDecodeError:
        pass
    depth, start, candidates = 0, None, []
    for i, ch in enumerate(cleaned):
        if ch == "{":
            if depth == 0:
                start = i
            depth += 1
        elif ch == "}":
            if depth > 0:
                depth -= 1
                if depth == 0 and start is not None:
                    candidates.append(cleaned[start:i + 1])
                    start = None
    for blob in reversed(candidates):
        try:
            return json.loads(blob)
        except json.JSONDecodeError:
            continue
    raise ValueError("no parseable JSON object found in LLM response")
