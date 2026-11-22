# Copyright (c) 2026 Axara Pty Ltd.
# SPDX-License-Identifier: AGPL-3.0-or-later
"""
llm_schemas.py

Versioned, typed LLM request/response contracts (JSON Standardization Design, D2 6.2).
Server holds the authoritative models.

REQUEST envelope: task-discriminated, priority-tagged, carries chat messages and a
                  response_schema reference. The gateway (future) renders/route by task.
RESPONSE schema (incident_analysis, "C-merged"): preserves the <|think|> false-positive
                  discipline as structured fields (reasoning + similar_incident_verdicts)
                  AND the operational richness (summary/component) AND confidence.
"""
from typing import List, Dict, Any, Optional, Literal
from pydantic import BaseModel, ConfigDict, field_validator


# --------------------------------------------------------------- request side

class ChatMessage(BaseModel):
    """One chat message sent to the LLM."""
    role: Literal["system", "user", "assistant"]
    content: str


class LLMConstraints(BaseModel):
    """Sampling settings for an LLM request."""
    model_config = ConfigDict(extra="allow")
    temperature: float = 1.0
    top_p: float = 0.95
    top_k: int = 64
    max_tokens: int = 4096
    # NOTE: prompt-instructed JSON (not Ollama format:json), per the agreed design —
    # the system message instructs the model to return a single JSON object.


VALID_TASKS = {
    "incident_analysis",
}
VALID_PRIORITY = {"high", "normal", "low"}


class LLMRequest(BaseModel):
    """Request envelope sent to the LLM provider (version 1.0)."""
    model_config = ConfigDict(extra="forbid")
    request_version: str = "1.0"
    task: str
    request_id: Optional[str] = None
    priority: str = "normal"
    response_schema: str                 # e.g. "incident_analysis_response@1.0"
    constraints: LLMConstraints = LLMConstraints()
    messages: List[ChatMessage]          # system + user, /api/chat shape

    @field_validator("request_version")
    @classmethod
    def _ver(cls, v):
        """Only request_version 1.0 is accepted."""
        if v != "1.0":
            raise ValueError(f"unsupported request_version {v!r}")
        return v

    @field_validator("task")
    @classmethod
    def _task(cls, v):
        """The task must be one of VALID_TASKS."""
        if v not in VALID_TASKS:
            raise ValueError(f"unknown task {v!r}")
        return v

    @field_validator("priority")
    @classmethod
    def _prio(cls, v):
        """The priority must be one of VALID_PRIORITY."""
        if v not in VALID_PRIORITY:
            raise ValueError(f"invalid priority {v!r}")
        return v


# --------------------------------------------------------------- response side

class SimilarIncidentVerdict(BaseModel):
    """The LLM's verdict on one similar past incident."""
    model_config = ConfigDict(extra="allow")
    incident_id: Optional[str] = None
    verdict: str                          # RELEVANT | DISCARDED
    reason: Optional[str] = None

    @field_validator("verdict")
    @classmethod
    def _verdict(cls, v):
        """Normalise the verdict to RELEVANT or DISCARDED."""
        u = (v or "").strip().upper()
        if u not in {"RELEVANT", "DISCARDED"}:
            raise ValueError(f"verdict must be RELEVANT or DISCARDED, got {v!r}")
        return u


class IncidentAnalysisResponse(BaseModel):
    """C-merged response schema (incident_analysis_response@1.0)."""
    model_config = ConfigDict(extra="allow")
    reasoning: Optional[str] = None                       # <|think|> discipline, captured
    similar_incident_verdicts: List[SimilarIncidentVerdict] = []
    summary: Optional[str] = None                         # from pseudo-code
    component: Optional[str] = None                       # from pseudo-code
    root_cause: str                                       # required
    recommendations: List[str] = []
    confidence: float = 0.5                               # preserved (0..1)

    @field_validator("confidence")
    @classmethod
    def _conf(cls, v):
        """Clamp the confidence to 0..1 (0.5 when it is not a number)."""
        try:
            f = float(v)
        except (TypeError, ValueError):
            return 0.5
        return max(0.0, min(1.0, f))


# Safe fallback object (used by the analyzer when the model output cannot be parsed).
def fallback_analysis(reason: str) -> Dict[str, Any]:
    """The analysis stored when the LLM call fails.

    Args:
        reason: Why the analysis failed.

    Returns:
        An analysis dict with zero confidence that asks for manual investigation.
    """
    return {
        "reasoning": "",
        "similar_incident_verdicts": [],
        "summary": "Automated analysis unavailable",
        "component": "Unknown subsystem",
        "root_cause": f"Analysis failed - unable to determine root cause ({reason})",
        "recommendations": ["Manual investigation required"],
        "confidence": 0.0,
    }
