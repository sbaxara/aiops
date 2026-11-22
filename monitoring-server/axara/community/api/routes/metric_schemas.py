# Copyright (c) 2026 Axara Pty Ltd.
# SPDX-License-Identifier: AGPL-3.0-or-later
"""
metric_schemas.py  (candidate for api/routes or a schemas/ module)

Versioned, typed metric envelope for ingest validation (Pydantic v2).
Greenfield: schema_version is mandatory and must be "1.0". No legacy adapter.

Validation strategy:
  - MetricEnvelope is the single ingest contract.
  - details is validated by a model-level validator that:
      (1) applies the ERROR-KEY WAIVER first: if details contains an "error"
          key, only {error, error_type?} is required (covers collector
          timeout/error, monitor handle_error, and DB connection failures
          that report status='fail' with sparse {error}).
      (2) otherwise routes by `type` to the matching typed details model.
  - We keep details as a plain dict on the model (so the wire shape is
    unchanged and additive), but we *construct* the typed model to enforce it,
    raising ValidationError on non-conformance -> FastAPI returns 422.
"""
from typing import List, Dict, Any, Optional, Literal
from pydantic import BaseModel, ConfigDict, field_validator, model_validator


# ------------------------------------------------------------------ shared bits

class TopProcess(BaseModel):
    """A process in a CPU metric's top-process list."""
    pid: int
    name: str
    value_pct: float


# ------------------------------------------------------------------ typed details

class CpuDetails(BaseModel):
    """Details of a 'cpu' metric."""
    model_config = ConfigDict(extra="allow")
    cpu_percent: float
    per_cpu_percent: List[float] = []
    cpu_count_logical: Optional[int] = None
    cpu_count_physical: Optional[int] = None
    load_average: Optional[Dict[str, float]] = None
    platform: Optional[str] = None
    top_processes: List[TopProcess] = []
    failure_reason: Optional[str] = None


class MemoryDetails(BaseModel):
    """Details of a 'memory' metric."""
    model_config = ConfigDict(extra="allow")
    memory_percent: float
    total_mb: Optional[float] = None
    available_mb: Optional[float] = None
    used_mb: Optional[float] = None
    free_mb: Optional[float] = None
    swap_percent: Optional[float] = None
    swap_total_mb: Optional[float] = None
    swap_used_mb: Optional[float] = None
    swap_free_mb: Optional[float] = None
    top_processes: List[TopProcess] = []
    failure_reason: Optional[str] = None


class DiskPartition(BaseModel):
    """One partition in a 'disk' metric."""
    model_config = ConfigDict(extra="allow")
    device: Optional[str] = None
    mountpoint: Optional[str] = None
    fstype: Optional[str] = None
    total_gb: Optional[float] = None
    used_gb: Optional[float] = None
    free_gb: Optional[float] = None
    percent: Optional[float] = None


class DiskDetails(BaseModel):
    """Details of a 'disk' metric."""
    model_config = ConfigDict(extra="allow")
    max_usage_percent: float
    critical_partition: Optional[str] = None
    partition_count: Optional[int] = None
    ignore_readonly: Optional[bool] = None
    partitions: List[DiskPartition] = []
    failure_reason: Optional[str] = None


class NetworkDetails(BaseModel):
    """Details of a 'network' metric."""
    model_config = ConfigDict(extra="allow")
    bytes_sent_gb: Optional[float] = None
    bytes_recv_gb: Optional[float] = None
    packets_sent: Optional[int] = None
    packets_recv: Optional[int] = None
    errors_in: Optional[int] = None
    errors_out: Optional[int] = None
    drops_in: Optional[int] = None
    drops_out: Optional[int] = None
    rates: Dict[str, float] = {}
    interfaces: Dict[str, Any] = {}
    failure_reason: Optional[str] = None


class LogMatch(BaseModel):
    """One matching line in a 'log' metric, with context."""
    model_config = ConfigDict(extra="allow")
    line_number: Optional[int] = None
    timestamp: Optional[str] = None
    matched_text: str
    context_before: List[str] = []
    context_after: List[str] = []


class LogPattern(BaseModel):
    """Matches of one pattern in one log file."""
    model_config = ConfigDict(extra="allow")
    count: int
    threshold: Optional[int] = None
    threshold_breached: bool
    matches: List[LogMatch] = []


class LogFileChecked(BaseModel):
    """Results for one log file."""
    model_config = ConfigDict(extra="allow")
    file: str
    patterns: Dict[str, LogPattern] = {}


class LogDetails(BaseModel):
    """Details of a 'log' metric."""
    model_config = ConfigDict(extra="allow")
    files_checked: List[LogFileChecked] = []
    failure_reason: Optional[str] = None


class DatabaseDetails(BaseModel):
    """Details of a 'database' metric (engine-specific keys allowed)."""
    # subtype-specific keys (threads_connected, active_connections, ...) vary by
    # engine, so extra=allow; the common core is what we assert.
    model_config = ConfigDict(extra="allow")
    connection_time_ms: float
    version: Optional[str] = None
    database_size_mb: Optional[float] = None
    replication: Dict[str, Any] = {}
    failure_reason: Optional[str] = None


class UrlDetails(BaseModel):
    """Details of a 'url' metric."""
    model_config = ConfigDict(extra="allow")
    url: str
    method: Optional[str] = None
    http_status: Optional[int] = None
    expected_status: Optional[int] = None
    status_ok: Optional[bool] = None
    body_ok: Optional[bool] = None
    response_time_ms: Optional[float] = None
    response_length: Optional[int] = None
    failure_reason: Optional[str] = None


class ProcessEntry(BaseModel):
    """One matching process in a 'process' metric."""
    model_config = ConfigDict(extra="allow")
    pid: int
    name: str
    status: Optional[str] = None


class ProcessDetails(BaseModel):
    """Details of a 'process' metric."""
    model_config = ConfigDict(extra="allow")
    process_name: str
    expected_min_count: Optional[int] = None
    found_count: int
    platform: Optional[str] = None
    processes: List[ProcessEntry] = []
    failure_reason: Optional[str] = None


class ListeningSocket(BaseModel):
    """A socket listening on the monitored port."""
    model_config = ConfigDict(extra="allow")
    ip: Optional[str] = None
    port: int
    pid: Optional[int] = None


class PortDetails(BaseModel):
    """Details of a 'port' metric."""
    model_config = ConfigDict(extra="allow")
    port: int
    protocol: Optional[str] = None
    is_listening: bool
    connection_ok: Optional[bool] = None
    platform: Optional[str] = None
    listening_sockets: List[ListeningSocket] = []
    failure_reason: Optional[str] = None


class BrowserDetails(BaseModel):
    """Details of a 'browser' metric (journey script result)."""
    model_config = ConfigDict(extra="allow")
    script_path: Optional[str] = None
    exit_code: Optional[int] = None
    duration_ms: Optional[float] = None
    platform: Optional[str] = None
    stdout: Optional[str] = None
    stderr: Optional[str] = None
    step: Optional[str] = None
    error: Optional[str] = None
    script_status: Optional[str] = None
    screenshot_ref: Optional[str] = None
    failure_reason: Optional[str] = None


class SslCertificate(BaseModel):
    """One scanned port in an 'ssl' metric (with or without a certificate)."""
    # four-variant union handled by extra=allow + required ip/port/ssl/status
    model_config = ConfigDict(extra="allow")
    ip: Optional[str] = None
    port: int
    ssl: bool
    status: str
    note: Optional[str] = None
    error: Optional[str] = None


class SslDetails(BaseModel):
    """Details of an 'ssl' metric."""
    model_config = ConfigDict(extra="allow")
    scanned_at: Optional[str] = None
    scanned_ports: Optional[int] = None
    ssl_ports: Optional[int] = None
    min_tls_version: Optional[str] = None
    certificates: List[SslCertificate] = []
    failure_reason: Optional[str] = None


_TYPED_DETAILS = {
    "cpu": CpuDetails, "memory": MemoryDetails, "disk": DiskDetails,
    "network": NetworkDetails, "log": LogDetails, "database": DatabaseDetails,
    "url": UrlDetails, "process": ProcessDetails, "port": PortDetails,
    "browser": BrowserDetails, "ssl": SslDetails,
}

VALID_STATUS = {"pass", "warning", "fail", "error", "timeout"}

# Plugins add further types (Registry.add_metric_type), each with its own details model or
# none (details then stored without validation).
VALID_TYPES = set(_TYPED_DETAILS.keys())
SCHEMA_VERSION = "1.0"


def _plugin_types() -> Dict[str, Any]:
    """Metric types added by plugins (e.g. Enterprise 'tls'), with their details model or None."""
    from axara.community.plugins.loader import get_registry
    return get_registry().metric_types()


class MetricEnvelope(BaseModel):
    """The v1.0 metric envelope an agent sends for each monitor result.

    The type must be a core type or one a plugin added; `details` is validated by the
    type's model unless it carries an `error` string (error results are always accepted).
    """
    model_config = ConfigDict(extra="forbid")

    schema_version: str
    monitor_config_id: str
    type: str
    monitor_version: Optional[str] = None
    metric_name: str
    value: Optional[float] = None
    unit: Optional[str] = None
    status: str
    failure_reason: Optional[str] = None
    timestamp: Optional[str] = None
    details: Dict[str, Any] = {}

    @field_validator("schema_version")
    @classmethod
    def _check_version(cls, v: str) -> str:
        """Only schema_version 1.0 is accepted."""
        if v != SCHEMA_VERSION:
            raise ValueError(f"unsupported schema_version {v!r}; expected {SCHEMA_VERSION!r}")
        return v

    @field_validator("type")
    @classmethod
    def _check_type(cls, v: str) -> str:
        """The type must be a core metric type or one a plugin registered."""
        if v not in VALID_TYPES and v not in _plugin_types():
            raise ValueError(f"unknown metric type {v!r}")
        return v

    @field_validator("status")
    @classmethod
    def _check_status(cls, v: str) -> str:
        """The status must be pass, warning, fail, error or timeout."""
        if v not in VALID_STATUS:
            raise ValueError(f"invalid status {v!r}")
        return v

    @model_validator(mode="after")
    def _validate_details(self):
        """Validate `details` with the type's model (skipped for error results and types without a model)."""
        d = self.details or {}
        # (1) error-key waiver
        if isinstance(d, dict) and "error" in d:
            if not isinstance(d["error"], str):
                raise ValueError("details.error must be a string")
            return self
        # (2) route by type
        model = _TYPED_DETAILS.get(self.type) or _plugin_types().get(self.type)
        if model is not None:
            model(**d)   # raises ValidationError if non-conforming
        return self
