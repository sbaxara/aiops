# Copyright (c) 2026 Axara Pty Ltd.
# SPDX-License-Identifier: AGPL-3.0-or-later
"""
LLM Analyzer
Analyzes incidents using Qwen 3:30B for root cause and recommendations.

JSON Standardization (D2): uses the /api/chat endpoint with system+user messages,
a <|think|>-led system instruction that ALSO requires a single JSON object response
(C-merged incident_analysis_response@1.0 schema), hardened response parsing that
tolerates a reasoning preamble and markdown fences, one repair-retry, and a safe
fallback. A human-readable raw_analysis is synthesised from the structured fields so
Jira/ServiceNow/Teams/Slack render exactly as before.
"""
import logging
import os
import re
import json
import requests
from typing import Dict, Any, List, Optional
from datetime import datetime
from axara.community.incident_management.llm_schemas import (
    LLMRequest, IncidentAnalysisResponse, fallback_analysis,
)

logger = logging.getLogger(__name__)

RESPONSE_SCHEMA_REF = "incident_analysis_response@1.0"


class LLMAnalyzer:
    """LLM-powered incident analysis using Qwen 3:30B (chat + JSON)."""

    def __init__(
        self,
        ollama_url: str = None,
        model: str = None,
        temperature: float = None,
        top_p: float = None,
        top_k: int = None,
        num_predict: int = None,
        request_timeout: int = None,
    ):
        """Create the analyzer.

        Args:
            ollama_url: Ollama base URL.
            model: Model name.
            request_timeout: Seconds per call.
            *: Further options as in the signature.
        """
        self.ollama_url = ollama_url or os.getenv("OLLAMA_URL") or "http://ollama:11434"
        self.model = model

        def _int_env(key):
            """Integer from an environment variable.

            Args:
                key: Variable name.

            Returns:
                The value, or None when unset or invalid.
            """
            v = os.getenv(key)
            return int(v) if v else None

        def _float_env(key):
            """Float from an environment variable.

            Args:
                key: Variable name.

            Returns:
                The value, or None when unset or invalid.
            """
            v = os.getenv(key)
            return float(v) if v else None

        self.temperature  = temperature  if temperature  is not None else _float_env("LLM_TEMPERATURE")
        self.num_predict  = num_predict  if num_predict  is not None else _int_env("LLM_NUM_PREDICT")
        self.top_p        = top_p        if top_p        is not None else _float_env("LLM_TOP_P")
        self.top_k        = top_k        if top_k        is not None else _int_env("LLM_TOP_K")

        self.request_timeout = request_timeout

    # ------------------------------------------------------------------ public

    def analyze_incident(
        self,
        incident: Dict[str, Any],
        metrics: List[Dict[str, Any]] = None,
        similar_incidents: List[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        """Analyze an incident. Returns the C-merged structured dict plus a
        synthesised raw_analysis text for ticket/notification rendering."""
        try:
            system_msg, user_msg = self._build_messages(incident, metrics, similar_incidents)
            req = LLMRequest(
                task="incident_analysis",
                priority="high",
                response_schema=RESPONSE_SCHEMA_REF,
                messages=[
                    {"role": "system", "content": system_msg},
                    {"role": "user", "content": user_msg},
                ],
            )
            logger.info(f"Analyzing incident {incident.get('id')} with LLM (chat/json)...")
            #logger.info(f"Analysis prompt system message: [{system_msg}]")
            #logger.info(f"Analysis prompt user message: [{user_msg}]")

            raw_text = self._call_chat_gated(req)
            analysis = self._parse_chat_response(
                raw_text,
                retry_fn=lambda: self._call_chat_gated(self._with_retry_nudge(req)),
            )
            analysis["raw_analysis"] = self._render_raw_analysis(analysis)
            logger.info(f"LLM analysis completed for incident {incident.get('id')}")
            return analysis

        except Exception as e:
            logger.error(f"LLM analysis failed: {e}", exc_info=True)
            fb = fallback_analysis(str(e))
            fb["raw_analysis"] = self._render_raw_analysis(fb)
            fb["error"] = str(e)
            return fb

    # ------------------------------------------------------------- prompt build

    def _build_messages(
        self,
        incident: Dict[str, Any],
        metrics: List[Dict[str, Any]],
        similar_incidents: List[Dict[str, Any]],
    ):
        """Return (system_instruction, user_prompt). The system message carries
        the <|think|> directive AND the JSON-output contract; the user message
        carries the incident evidence."""

        system_instruction = (
            "You are an advanced, autonomous SRE Diagnostic Agent. Determine the TRUE "
            "root cause of the incident from its own evidence.\n"
            "\n"
            "CRITICAL INSTRUCTIONS:\n"
            "1. Determine the root cause from the current incident's description, "
            "failure reason, stdout, and error messages ALONE.\n"
            "2. Similar historical incidents are REFERENCE ONLY. For each, decide "
            "whether its failure signature matches; if not, DISCARD it.\n"
            "3. Never adopt a root cause from a similar incident unless its failure "
            "signature is consistent with the current incident's evidence.\n"
            "\n"
            "You MUST respond with a SINGLE valid JSON object and nothing else "
            "(no markdown fences). The object MUST contain exactly these fields:\n"
            "{\n"
            '  "reasoning": "your step-by-step evidence analysis",\n'
            '  "similar_incident_verdicts": [\n'
            '    {"incident_id": "id", "verdict": "RELEVANT|DISCARDED", "reason": "one line"}\n'
            "  ],\n"
            '  "summary": "concise operational title of the failure",\n'
            '  "component": "identified subsystem or file pathway",\n'
            '  "root_cause": "root cause from current-incident evidence",\n'
            '  "recommendations": ["step 1", "step 2", "step 3"],\n'
            '  "confidence": 0.0\n'
            "}\n"
            "confidence is a number from 0.0 to 1.0. Be specific: reference the actual "
            "error text, step name, and locator from the current incident.\n"
            "Keep \"reasoning\" to AT MOST 2 short sentences; do not restate the "
            "evidence verbatim. Keep \"recommendations\" to at most 3 concise items."
        )

        # ---- user message: deduplicated evidence ----
        # The incident.description is a human-readable ticket-body blob generated by
        # the state machine; it already restates app/server/monitor/details. We do
        # NOT render it verbatim (it duplicates the structured fields and the metrics
        # below ~6x, which is pure token/latency cost). We render compact structured
        # facts, the failure_reason ground-truth once, and the metrics as latest +
        # a one-line trend rather than N full snapshots.

        latest_metric = (metrics or [None])[0]

        # failure_reason moved to the envelope top level (JSON Standardization).
        # Read it there first; fall back to details (legacy) then to the incident.
        def _failure_reason(m):
            """Failure reason recorded on a metric, for the prompt.

            Args:
                m: Metric dict.

            Returns:
                The reason text, or ''.
            """
            if not m:
                return None
            return (m.get('failure_reason')
                    or (m.get('details') or m.get('metric_data') or {}).get('failure_reason'))

        failure_reason = _failure_reason(latest_metric)

        parts = [
            "=== CURRENT INCIDENT (PRIMARY EVIDENCE) ===",
            f"Title: {incident.get('title')}",
            f"Severity: {incident.get('severity')}",
            f"Server: {incident.get('server', {}).get('hostname', 'Unknown')}",
            f"Application: {incident.get('application', {}).get('name', 'Unknown')}",
            f"Created: {incident.get('created_at')}",
            "",
        ]

        if failure_reason:
            parts += [
                "=== CONFIRMED FAILURE REASON (monitor ground truth) ===",
                str(failure_reason),
                "Your root cause must be consistent with this specific error.",
                "",
            ]

        if metrics:
            mt = (latest_metric or {}).get('metric_type', 'metric')
            # One-line trend across the recent window (most recent first as received).
            trend = " -> ".join(
                f"{self._round(m.get('metric_value'))}({m.get('status')})"
                for m in metrics[:8]
            )
            parts.append("=== RECENT " + mt.upper() + " (latest first) ===")
            parts.append(f"Trend: {trend}")
            parts.append("")

            # Full detail for the LATEST reading only — earlier snapshots are
            # near-identical and add no diagnostic signal beyond the trend.
            latest_details = (latest_metric or {}).get('details') or (latest_metric or {}).get('metric_data') or {}
            if latest_details:
                parts.append("Latest reading detail:")
                SKIP_KEYS = {'failure_reason', 'screenshot_b64', 'screenshot_ref', 'stderr'}
                for k, v in latest_details.items():
                    if k in SKIP_KEYS:
                        continue
                    if k == 'stdout' and isinstance(v, str):
                        stdout_parsed = {}
                        for _line in reversed(v.splitlines()):
                            _line = _line.strip()
                            if _line.startswith('{'):
                                try:
                                    stdout_parsed = json.loads(_line)
                                except (ValueError, json.JSONDecodeError):
                                    pass
                                break
                        if stdout_parsed.get('status'):
                            parts.append(f"    status: {stdout_parsed.get('status')}")
                        if stdout_parsed.get('step'):
                            parts.append(f"    step: {stdout_parsed.get('step')}")
                        if stdout_parsed.get('error'):
                            parts.append(f"    error: {stdout_parsed['error'].strip()[:500]}")
                    else:
                        parts.append(f"    {k}: {self._round(v)}")
            parts.append("")

        if similar_incidents:
            parts += [
                "=== SIMILAR HISTORICAL INCIDENTS (secondary reference only) ===",
                "For each, decide RELEVANT or DISCARDED vs the current incident's "
                "failure signature and record it in similar_incident_verdicts.",
                "",
            ]
            for idx, similar in enumerate(similar_incidents[:3], 1):
                parts.append(f"{idx}. id={similar.get('incident_id')} | {similar.get('title')}")
                if similar.get('root_cause'):
                    parts.append(f"   Root Cause: {similar.get('root_cause')}")
                if similar.get('resolution'):
                    parts.append(f"   Resolution: {similar.get('resolution')}")
            parts.append("")

        return system_instruction, "\n".join(parts)

    @staticmethod
    def _round(v):
        """Trim float precision for token efficiency; recurse into lists/dicts."""
        if isinstance(v, float):
            return round(v, 1)
        if isinstance(v, list):
            return [LLMAnalyzer._round(x) for x in v]
        if isinstance(v, dict):
            return {k: LLMAnalyzer._round(x) for k, x in v.items()}
        return v

    def _with_retry_nudge(self, req: LLMRequest) -> LLMRequest:
        """Append a corrective instruction for the single repair-retry."""
        msgs = [m.model_dump() for m in req.messages]
        msgs.append({
            "role": "user",
            "content": "Your previous reply could not be parsed. Respond with ONLY a "
                       "single valid JSON object matching the required fields. No prose, "
                       "no markdown fences.",
        })
        return LLMRequest(
            task=req.task, priority=req.priority, response_schema=req.response_schema,
            constraints=req.constraints, messages=msgs,
        )

    # --------------------------------------------------------------- llm call
    def _call_chat_gated(self, req: "LLMRequest") -> str:
        """Call the LLM inside the registry's LLM slot (a no-op unless a plugin bounds
        concurrent LLM calls), then delegate to _call_chat."""
        from axara.community.plugins.loader import get_registry
        with get_registry().llm_slot("high"):
            return self._call_chat(req)
        
    def _call_chat(self, req: LLMRequest) -> str:
        """Call Ollama /api/chat. Returns message.content (str)."""
        url = f"{self.ollama_url}/api/chat"
        options: Dict[str, Any] = {
            "temperature": self.temperature,
            "num_predict": self.num_predict,
            "top_p": self.top_p,
            "top_k": self.top_k
        }

        options = {k: v for k, v in options.items() if v is not None}   # strip unset options to avoid unintended defaults in Ollama

        payload = {
            "model": self.model,
            "messages": [m.model_dump() for m in req.messages],
            "stream": False,
            "format": "json",
            "think": False,
            "options": options,
        }
        try:
            logger.info(f"LLM payload: {payload}")
            resp = requests.post(url, json=payload, timeout=self.request_timeout)
            resp.raise_for_status()
            data = resp.json()
            logger.info(f"LLM response: [{data}]")
            # /api/chat returns {"message": {"role": "...", "content": "..."}}
            return (data.get("message", {}) or {}).get("content", "") or ""
        except requests.exceptions.Timeout:
            logger.error("LLM request timed out")
            raise Exception(f"LLM inference timed out after {self.request_timeout}s")
        except requests.exceptions.RequestException as e:
            logger.error(f"LLM API request failed: {e}")
            raise Exception(f"Failed to communicate with LLM: {e}")

    # ------------------------------------------------------------- parse + render

    @staticmethod
    def _strip_fences(text: str) -> str:
        """Remove Markdown code fences around a JSON answer.

        Args:
            text: LLM output.

        Returns:
            The text without fences.
        """
        t = text.strip()
        t = re.sub(r"^```(?:json)?\s*", "", t, flags=re.IGNORECASE)
        t = re.sub(r"\s*```$", "", t)
        return t.strip()

    @classmethod
    def _extract_last_json_object(cls, text: str) -> dict:
        """Extract the last balanced {...} block, tolerating a reasoning
        preamble and markdown fences. Raises ValueError if none parses."""
        cleaned = cls._strip_fences(text)
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

    def _parse_chat_response(self, raw_text: str, retry_fn=None) -> Dict[str, Any]:
        """Parse + validate to the C-merged schema. One repair-retry, then fallback."""
        def _try(text: str) -> Dict[str, Any]:
            """Parse JSON, returning {} when it does not parse.

            Args:
                text: Candidate JSON text.

            Returns:
                The parsed dict, or {}.
            """
            obj = self._extract_last_json_object(text)
            return IncidentAnalysisResponse(**obj).model_dump()

        try:
            return _try(raw_text)
        except Exception as e1:
            logger.warning(f"LLM response parse failed: {e1}; attempting one repair-retry")
            if retry_fn is not None:
                try:
                    return _try(retry_fn())
                except Exception as e2:
                    logger.error(f"Repair-retry also failed: {e2}; using fallback")
                    return fallback_analysis(f"parse failed after retry: {e2}")
            return fallback_analysis(f"parse failed: {e1}")

    @staticmethod
    def _render_raw_analysis(analysis: Dict[str, Any]) -> str:
        """Render a human-readable text from the structured fields, so external
        tickets/notifications read as before. Reasoning and verdicts are kept in
        the structured fields (DB/dashboard), not in the ticket body."""
        lines = []
        if analysis.get("summary"):
            lines.append(str(analysis["summary"]))
            lines.append("")
        if analysis.get("component"):
            lines.append(f"Component: {analysis['component']}")
        lines.append("ROOT CAUSE:")
        lines.append(str(analysis.get("root_cause", "")).strip())
        lines.append("")
        recs = analysis.get("recommendations") or []
        if recs:
            lines.append("RECOMMENDATIONS:")
            for i, r in enumerate(recs, 1):
                lines.append(f"{i}. {r}")
            lines.append("")
        conf = analysis.get("confidence", 0.0)
        try:
            conf_pct = f"{float(conf) * 100:.0f}%"
        except (TypeError, ValueError):
            conf_pct = str(conf)
        lines.append(f"CONFIDENCE: {conf_pct}")
        return "\n".join(lines)

    # --------------------------------------------------------------- summary

    def generate_summary(self, incident: Dict[str, Any]) -> str:
        """Brief incident summary for notifications (free text, no JSON contract)."""
        system = "You write concise 2-3 sentence operational incident summaries."
        user = (
            f"Title: {incident.get('title')}\n"
            f"Description: {incident.get('description')}\n"
            f"Severity: {incident.get('severity')}\n"
            "Summarise the key issue and impact."
        )
        try:
            req = LLMRequest(
                task="incident_analysis", priority="low",
                response_schema=RESPONSE_SCHEMA_REF,
                messages=[{"role": "system", "content": system}, {"role": "user", "content": user}],
            )
            return self._call_chat(req).strip()
        except Exception as e:
            logger.error(f"Failed to generate summary: {e}")
            return incident.get('title', 'Incident occurred')
