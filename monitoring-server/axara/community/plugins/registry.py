# Copyright (c) 2026 Axara Pty Ltd.
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Plugin registry: the extension points between the core and optional plugins.

The core builds one Registry at start-up (plugins.loader.get_registry). The core and each
plugin call register(registry) to add ticket providers, notifiers, sign-in hooks, API routers,
start-up/shutdown hooks, agent-registered hooks, incident-field providers, monitor-template
folders, migration folders and settings defaults. A registration may name a licence feature; it is then active only while
`feature_allowed(feature)` is true, checked each time it is used, so a licence change takes
effect without a restart. Without a licence gate installed (Community), every registered
feature is allowed and every stage-gated function is on.
"""
import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

logger = logging.getLogger(__name__)

# Stage-gated functions the core asks about (licence stages in Enterprise; always on otherwise).
FUNCTIONS = ("analysis", "notifications", "incidents")
# Request checks and core events plugins can hook (see add_check / add_event_hook).
CHECKS = ("login", "user", "agent_registration", "agent_intake")
EVENTS = ("agents_changed", "agent_report", "agent_decommissioned", "monitors_yaml",
          "server_config_saved", "monitor_defaults", "application_created")


class TicketProvider:
    """Interface for a ticketing (ITSM) tool. Methods return False/None on failure, never raise.

    Subclasses set `name`: the value stored in Application.itsm_provider and in
    Incident.ticket_provider next to the ticket reference (Incident.ticket_ref).
    """

    name = ""

    def ticket_url(self, ticket: str) -> Optional[str]:
        """Link to the ticket in the ITSM tool's web UI.

        Args:
            ticket: Ticket reference returned by create_ticket.

        Returns:
            Absolute URL, or None when the tool's base URL is not configured.
        """
        return None

    def create_ticket(self, incident: Dict[str, Any]) -> Optional[str]:
        """Open a ticket for an incident.

        Args:
            incident: Incident as a dict (orchestrator._incident_to_dict).

        Returns:
            The ticket reference to store on the incident, or None if it was not created.
        """
        raise NotImplementedError

    def add_attachment(self, ticket: str, file_path: str) -> bool:
        """Attach a file (e.g. a browser screenshot) to a ticket.

        Args:
            ticket: Ticket reference returned by create_ticket.
            file_path: Path of the file on the server.

        Returns:
            True when attached.
        """
        return False

    def add_comment(self, ticket: str, text: str) -> bool:
        """Add a comment visible on the ticket.

        Args:
            ticket: Ticket reference.
            text: Comment text.

        Returns:
            True when added.
        """
        raise NotImplementedError

    def add_analysis_note(self, ticket: str, text: str) -> bool:
        """Add the LLM analysis to the ticket (defaults to a normal comment).

        Args:
            ticket: Ticket reference.
            text: Analysis text.

        Returns:
            True when added.
        """
        return self.add_comment(ticket, text)

    def resolve(self, ticket: str, notes: str) -> bool:
        """Resolve the ticket because the AIOps incident recovered or was resolved.

        Args:
            ticket: Ticket reference.
            notes: Resolution notes.

        Returns:
            True when resolved.
        """
        raise NotImplementedError

    def fetch_resolution(self, ticket: str) -> Optional[str]:
        """Check whether the ticket was resolved in the ITSM tool (used by the ITSM sync job).

        Args:
            ticket: Ticket reference.

        Returns:
            The resolution notes when the ticket is resolved or closed, else None.
        """
        return None


class Notifier:
    """Interface for an outbound notification channel (Email, Slack, Teams, ...)."""

    name = ""

    def incident(self, incident: Dict[str, Any]) -> None:
        """Announce a new incident or its analysis (incident['event_type'] says which).

        Args:
            incident: Incident as a dict.
        """

    def resolution(self, incident: Dict[str, Any]) -> None:
        """Announce that an incident was resolved.

        Args:
            incident: Incident as a dict.
        """


@dataclass
class _Entry:
    """One registration: the item, its licence feature, a name and options."""
    item: Any
    feature: Optional[str] = None
    name: str = ""
    options: Dict[str, Any] = field(default_factory=dict)


# Monitors enabled at registration in the Community edition (no secrets or settings needed).
DEFAULT_ENABLED_MONITORS = ("cpu", "memory", "disk", "network", "ssl")

class Registry:
    """Holds every extension registered by the core and by plugins."""

    def __init__(self):
        """Create an empty registry (no gate: everything allowed)."""
        self._ticket_providers: Dict[str, _Entry] = {}
        self._notifiers: List[_Entry] = []
        self._password_logins: List[_Entry] = []
        self._routers: List[_Entry] = []
        self._startup: List[_Entry] = []
        self._shutdown: List[_Entry] = []
        self._agent_registered: List[_Entry] = []
        self._template_dirs: List[_Entry] = []
        self._migration_dirs: List[Path] = []
        self._incident_fields: List[_Entry] = []
        self._metric_types: Dict[str, Any] = {}
        self._metric_info: Dict[str, Dict[str, Any]] = {}
        self._llm_slot: Optional[Callable[[str], Any]] = None
        # Monitor types switched on when an agent registers: the basic resource monitors and
        # SSL start on; a plugin may change the set.
        self._default_enabled_monitors = set(DEFAULT_ENABLED_MONITORS)
        self._creatable: Dict[str, Optional[Callable[[], bool]]] = {}
        self._config_defaults: Dict[str, Dict[str, Any]] = {}
        self._feature_gate: Optional[Callable[[str], bool]] = None
        self._function_gate: Optional[Callable[[str], bool]] = None
        self._local_registration: Optional[Callable[[], bool]] = None
        self._feature_guard_factory: Optional[Callable[[str], Callable]] = None
        self._checks: Dict[str, List[Callable]] = {k: [] for k in CHECKS}
        self._events: Dict[str, List[_Entry]] = {k: [] for k in EVENTS}
        self._audit_sinks: List[Callable] = []
        self._audit_skip: List[str] = []
        self._commands: Dict[str, _Entry] = {}
        self.plugins: List[str] = []

    # ------------------------------------------------------------- transactions
    def snapshot(self) -> Dict[str, Any]:
        """Copy of every registration, taken before a plugin registers (see restore).

        Returns:
            Opaque snapshot.
        """
        import copy
        return {k: copy.copy(v) if isinstance(v, (list, dict)) else v
                for k, v in self.__dict__.items()} | {
            "_checks": {k: list(v) for k, v in self._checks.items()},
            "_events": {k: list(v) for k, v in self._events.items()}}

    def restore(self, snap: Dict[str, Any]) -> None:
        """Undo every registration made since snapshot() (a plugin that failed to register).

        Args:
            snap: Value returned by snapshot().
        """
        self.__dict__.clear()
        self.__dict__.update(snap)

    # ------------------------------------------------------------- gates
    def set_licence_gates(self, feature_gate: Callable[[str], bool],
                          function_gate: Callable[[str], bool]) -> None:
        """Install the licence checks (Enterprise). Only one plugin may do this.

        Args:
            feature_gate: f(feature) -> bool, e.g. licensing.feature_enabled.
            function_gate: f(function) -> bool, e.g. licensing.function_allowed.

        Raises:
            RuntimeError: when gates were already installed.
        """
        if self._feature_gate or self._function_gate:
            raise RuntimeError("licence gates are already installed")
        self._feature_gate, self._function_gate = feature_gate, function_gate

    def set_feature_guard_factory(self, factory: Callable[[str], Callable]) -> None:
        """Install the dependency factory used for feature-gated plugin routers (Enterprise).

        Args:
            factory: f(feature) -> FastAPI dependency refusing requests while not licensed.
        """
        self._feature_guard_factory = factory

    def feature_guard(self, feature: str) -> Callable:
        """Dependency refusing requests (403) while a feature is not allowed.

        Args:
            feature: Licence feature name.

        Returns:
            The plugin's dependency when installed, else one built on feature_allowed().
        """
        if self._feature_guard_factory is not None:
            return self._feature_guard_factory(feature)

        def _guard():
            """Refuse the request while the feature is not allowed.

            Raises:
                HTTPException: 403 feature_not_licensed.
            """
            if not self.feature_allowed(feature):
                from fastapi import HTTPException
                raise HTTPException(status_code=403, detail={"code": "feature_not_licensed", "feature": feature,
                                                             "message": f"'{feature}' is not available"})
        return _guard

    def feature_allowed(self, feature: Optional[str]) -> bool:
        """Whether a licence feature is available now. Fails closed on a gate error.

        Args:
            feature: Feature name, or None for an ungated registration.

        Returns:
            True when the feature is ungated, no gate is installed, or the gate allows it.
        """
        if not feature or self._feature_gate is None:
            return True
        try:
            return bool(self._feature_gate(feature))
        except Exception:
            logger.exception("feature gate failed for %s", feature)
            return False

    def function_allowed(self, function: str) -> bool:
        """Whether a stage-gated function (analysis, notifications, incidents) may run now.

        Args:
            function: One of FUNCTIONS.

        Returns:
            True when no gate is installed or the gate allows it; False on a gate error.

        Raises:
            ValueError: for an unknown function name.
        """
        if function not in FUNCTIONS:
            raise ValueError(f"unknown function {function!r}")
        if self._function_gate is None:
            return True
        try:
            return bool(self._function_gate(function))
        except Exception:
            logger.exception("function gate failed for %s", function)
            return False

    def function_guard(self, function: str) -> Callable:
        """FastAPI dependency refusing a request (403) while a stage-gated function is off.

        Args:
            function: One of FUNCTIONS.

        Returns:
            The dependency.
        """
        def _guard():
            """Refuse the request while the function is off.

            Raises:
                HTTPException: 403 licence_restricted.
            """
            if not self.function_allowed(function):
                from fastapi import HTTPException
                raise HTTPException(status_code=403, detail={
                    "code": "licence_restricted", "function": function,
                    "message": f"'{function}' is not available under the current licence"})
        return _guard

    # ------------------------------------------------------------- request checks
    def add_check(self, kind: str, check: Callable) -> None:
        """Register a check that may refuse a request (Enterprise licence enforcement).

        Kinds and how they are called:
            "login":              check(user) when a session token is issued;
            "user":               check(user) on every request of a signed-in user;
            "agent_registration": check() before a new agent registers;
            "agent_intake":       check(server_id) on agent calls (server_id None on agent
                                  routers that carry none).

        A check refuses by raising fastapi.HTTPException. Any other exception also refuses the
        request (fail closed): it is logged and answered with 403.

        Args:
            kind: One of CHECKS.
            check: The callable.

        Raises:
            ValueError: for an unknown kind.
        """
        if kind not in CHECKS:
            raise ValueError(f"unknown check kind {kind!r}")
        self._checks[kind].append(check)

    def run_checks(self, kind: str, *args) -> None:
        """Run every check of a kind.

        Args:
            kind: One of CHECKS.
            *args: Arguments for the checks.

        Raises:
            HTTPException: the first refusal (403 when a check failed unexpectedly).
        """
        from fastapi import HTTPException
        for check in self._checks[kind]:
            try:
                check(*args)
            except HTTPException:
                raise
            except Exception:
                logger.exception("%s check %s failed; refusing the request", kind,
                                 getattr(check, "__name__", check))
                raise HTTPException(status_code=403, detail={
                    "code": "check_failed", "message": "The request could not be verified"})

    # ------------------------------------------------------------- events
    def add_event_hook(self, event: str, hook: Callable, feature: Optional[str] = None) -> None:
        """Register a hook told about a core event.

        Events and arguments:
            "agents_changed":       hook() after an agent registered or was removed;
            "agent_report":         hook(server_id, agent_timestamp) for each metrics batch;
            "agent_decommissioned": hook(db, server_id) -> rows removed, while an agent is
                                    removed (inside its transaction; may raise to abort);
            "monitors_yaml":        hook(server_id, monitors) before an agent's monitors.yaml is
                                    written; may change the monitor dicts in place;
            "server_config_saved":  hook(db, old_config, new_config) after the settings are saved;
            "monitor_defaults":     hook(monitor_type, config) -> None, or a dict with any of
                                    check_interval_seconds, threshold_config, config, severity
                                    that replaces the request's values for a new monitor;
            "application_created":  hook(db, application, support_team) when agent registration
                                    creates an application (support_team: the agent's
                                    bootstrap support_team settings, or {}).

        Args:
            event: One of EVENTS.
            hook: The callable.
            feature: Licence feature it needs, if any.

        Raises:
            ValueError: for an unknown event.
        """
        if event not in EVENTS:
            raise ValueError(f"unknown event {event!r}")
        self._events[event].append(_Entry(hook, feature, getattr(hook, "__name__", "")))

    def emit(self, event: str, *args, strict: bool = False) -> List[Any]:
        """Call the hooks of an event.

        Args:
            event: One of EVENTS.
            *args: Arguments for the hooks.
            strict: Re-raise a hook's exception (otherwise it is logged and skipped).

        Returns:
            The hooks' return values.

        Raises:
            Exception: a hook's exception when strict is True.
        """
        out = []
        for e in self._events[event]:
            if not self.feature_allowed(e.feature):
                continue
            try:
                out.append(e.item(*args))
            except Exception:
                if strict:
                    raise
                logger.exception("%s hook %s failed", event, e.name)
        return out

    def add_audit_skip_prefix(self, prefix: str) -> None:
        """Leave requests under a path prefix out of the request audit (e.g. frequent agent
        calls that are not configuration changes).

        Args:
            prefix: Path prefix such as '/api/v1/example/agent/'.

        Raises:
            ValueError: unless the prefix starts with '/api/' and ends with '/'.
        """
        if not (prefix.startswith("/api/") and prefix.endswith("/")):
            raise ValueError(f"audit skip prefix must start with /api/ and end with /: {prefix!r}")
        if prefix not in self._audit_skip:
            self._audit_skip.append(prefix)

    def audit_skip_prefixes(self) -> List[str]:
        """Path prefixes plugins left out of the request audit.

        Returns:
            Prefixes.
        """
        return list(self._audit_skip)

    def add_audit_sink(self, sink: Callable[[Dict[str, Any]], None]) -> None:
        """Register a receiver of every audit event after it is stored (e.g. SIEM forwarding).

        Args:
            sink: f(event dict); exceptions are logged and never affect the audit log.
        """
        self._audit_sinks.append(sink)

    def audit_sinks(self) -> List[Callable]:
        """The registered audit sinks.

        Returns:
            Callables.
        """
        return list(self._audit_sinks)

    # ------------------------------------------------------------- admin commands
    def add_command(self, name: str, func: Callable[[List[str]], int], help_text: str) -> None:
        """Register a `python manage.py <name>` command.

        Args:
            name: Command name.
            func: f(args after the name) -> exit status.
            help_text: One-line description shown in the help.

        Raises:
            ValueError: when the name is already registered.
        """
        if name in self._commands:
            raise ValueError(f"command {name!r} already registered")
        self._commands[name] = _Entry(func, None, name, {"help": help_text})

    def commands(self) -> Dict[str, _Entry]:
        """Registered admin commands by name.

        Returns:
            {name: entry} (entry.item is the function, entry.options["help"] its help).
        """
        return dict(self._commands)

    # ------------------------------------------------------------- ticketing
    def add_ticket_provider(self, provider: TicketProvider, feature: Optional[str] = None) -> None:
        """Register a ticketing tool.

        Args:
            provider: TicketProvider instance (or a zero-argument factory returning one).
            feature: Licence feature it needs, if any.

        Raises:
            ValueError: when a provider with the same name is already registered.
        """
        name = provider.name if isinstance(provider, TicketProvider) else getattr(provider, "name", "")
        if not name:
            raise ValueError("ticket provider needs a name")
        if name in self._ticket_providers:
            raise ValueError(f"ticket provider {name!r} already registered")
        self._ticket_providers[name] = _Entry(provider, feature, name)

    def ticket_provider(self, name: str) -> Optional[TicketProvider]:
        """The active provider for a name, or None when unknown or not licensed.

        Args:
            name: Provider name (Application.itsm_provider).

        Returns:
            The TicketProvider, or None.
        """
        e = self._ticket_providers.get((name or "").lower())
        if not e or not self.feature_allowed(e.feature):
            return None
        if not isinstance(e.item, TicketProvider):            # lazy factory
            try:
                e.item = e.item()
            except Exception:
                logger.exception("ticket provider %s could not be created", e.name)
                return None
        return e.item

    def ticket_provider_names(self) -> List[str]:
        """Names of the ticket providers active now.

        Returns:
            Sorted provider names.
        """
        return sorted(n for n, e in self._ticket_providers.items() if self.feature_allowed(e.feature))

    # ------------------------------------------------------------- notifications
    def add_notifier(self, notifier: Any, feature: Optional[str] = None) -> None:
        """Register a notification channel.

        Args:
            notifier: Notifier instance or a zero-argument factory returning one.
            feature: Licence feature it needs, if any.
        """
        self._notifiers.append(_Entry(notifier, feature, getattr(notifier, "name", "")))

    def notifiers(self) -> List[Notifier]:
        """The notification channels active now.

        Returns:
            Notifier instances (factories are created on first use).
        """
        out = []
        for e in self._notifiers:
            if not self.feature_allowed(e.feature):
                continue
            if not isinstance(e.item, Notifier):
                try:
                    e.item = e.item()
                except Exception:
                    logger.exception("notifier %s could not be created", e.name or e.item)
                    continue
            out.append(e.item)
        return out

    # ------------------------------------------------------------- sign-in
    def add_password_login(self, hook: Callable, feature: Optional[str] = None) -> None:
        """Register an alternative password sign-in (e.g. LDAP).

        Args:
            hook: async f(request, db, user, ip, ua) -> LoginResponse | None. Return None to
                fall through to local sign-in; raise HTTPException to refuse.
            feature: Licence feature it needs, if any.
        """
        self._password_logins.append(_Entry(hook, feature))

    def password_logins(self) -> List[Callable]:
        """Alternative password sign-ins active now, in registration order.

        Returns:
            Hook callables.
        """
        return [e.item for e in self._password_logins if self.feature_allowed(e.feature)]

    def set_local_registration_check(self, check: Callable[[], bool]) -> None:
        """Let a plugin decide whether local self-registration is allowed (e.g. SSO settings).

        Args:
            check: f() -> bool.
        """
        self._local_registration = check

    def local_registration_enabled(self) -> bool:
        """Whether users may register local accounts themselves.

        Returns:
            The plugin's answer, else LOCAL_REGISTRATION_ENABLED (default on).
        """
        if self._local_registration is not None:
            return bool(self._local_registration())
        import os
        return (os.getenv("LOCAL_REGISTRATION_ENABLED", "1") or "1").strip().lower() not in ("0", "false", "no")

    # ------------------------------------------------------------- API
    def add_router(self, router: Any, kind: str, feature: Optional[str] = None) -> None:
        """Register a FastAPI router mounted at start-up.

        Args:
            router: fastapi.APIRouter.
            kind: 'public' (handles its own auth), 'agent' (agent-facing; licence intake
                check added) or 'user' (signed-in, licensed user required).
            feature: Licence feature; requests are refused (403) while it is not allowed.

        Raises:
            ValueError: for an unknown kind.
        """
        if kind not in ("public", "agent", "user"):
            raise ValueError(f"unknown router kind {kind!r}")
        self._routers.append(_Entry(router, feature, options={"kind": kind}))

    def routers(self) -> List[_Entry]:
        """All registered routers (gating happens per request).

        Returns:
            Entries with .item (router), .feature and .options['kind'].
        """
        return list(self._routers)

    # ------------------------------------------------------------- lifecycle
    def add_startup(self, name: str, hook: Callable, feature: Optional[str] = None) -> None:
        """Register a start-up hook (scheduler start etc.), run in registration order.

        Args:
            name: Short name for logs.
            hook: f(app) -> None; exceptions are logged, never stop the server.
            feature: Licence feature; the hook is skipped when not allowed at start-up.
        """
        self._startup.append(_Entry(hook, feature, name))

    def add_shutdown(self, name: str, hook: Callable) -> None:
        """Register a shutdown hook.

        Args:
            name: Short name for logs.
            hook: f(app) -> None; exceptions are ignored.
        """
        self._shutdown.append(_Entry(hook, None, name))

    def run_startup(self, app: Any) -> None:
        """Run every allowed start-up hook; a failing hook is logged and skipped.

        Args:
            app: The FastAPI app.
        """
        for e in self._startup:
            if not self.feature_allowed(e.feature):
                logger.info("start-up hook %s skipped (feature %s not licensed)", e.name, e.feature)
                continue
            try:
                e.item(app)
            except Exception as exc:
                logger.warning("start-up hook %s failed: %s", e.name, exc)

    def run_shutdown(self, app: Any) -> None:
        """Run every shutdown hook, ignoring failures.

        Args:
            app: The FastAPI app.
        """
        for e in self._shutdown:
            try:
                e.item(app)
            except Exception:
                logger.debug("shutdown hook %s failed", e.name, exc_info=True)

    # ------------------------------------------------------------- agents
    def set_default_enabled_monitors(self, monitor_types) -> None:
        """Choose which monitor types start enabled when an agent registers.

        Args:
            monitor_types: Iterable of monitor types.
        """
        self._default_enabled_monitors = set(monitor_types)

    def default_enabled_monitors(self) -> set:
        """Monitor types enabled at agent registration.

        Returns:
            Set of monitor types.
        """
        return set(self._default_enabled_monitors)

    def add_agent_registered(self, hook: Callable, feature: Optional[str] = None) -> None:
        """Register a hook run when a new agent registers.

        Args:
            hook: f(db, monitor_repo, application, server, capabilities) -> list of MonitorConfig
                rows it created (they are sent to the agent with the core's monitors).
            feature: Licence feature it needs, if any.
        """
        self._agent_registered.append(_Entry(hook, feature))

    def run_agent_registered(self, db, monitor_repo, application, server, capabilities) -> List[Any]:
        """Run the agent-registered hooks.

        Args:
            db: SQLAlchemy session.
            monitor_repo: MonitorRepository for the session.
            application: Application row.
            server: Server row of the new agent.
            capabilities: Capability names the agent declared.

        Returns:
            Monitor configs created by the hooks.

        Raises:
            Exception: whatever a hook raises (registration then fails and is rolled back).
        """
        out = []
        for e in self._agent_registered:
            if self.feature_allowed(e.feature):
                out.extend(e.item(db, monitor_repo, application, server, capabilities) or [])
        return out

    # ------------------------------------------------------------- metric types
    def add_metric_type(self, metric_type: str, details_model: Any = None,
                        creatable: Optional[Callable[[], bool]] = None, alertable: bool = True,
                        stands_in_for: Optional[str] = None, in_health_summary: bool = True) -> None:
        """Accept a monitor type the core does not know (e.g. 'tls').

        Its metrics are always accepted (agents may already run the script); new monitor
        configs of the type can be created only while `creatable` returns True.

        Args:
            metric_type: Value of the metric's `type` field.
            details_model: Pydantic model that validates the metric's `details`, or None to
                store the details without validation.
            creatable: Whether monitors of this type may be created now (default: always).
            alertable: False for observation-only metrics: stored, never turned into incidents.
            stands_in_for: A core type (e.g. 'cpu') whose charts and tiles show this type's
                data while that type itself has no data.
            in_health_summary: False to keep the type out of the per-server health summary.

        Raises:
            ValueError: when another plugin already registered the type, or when two types
                stand in for the same core type.
        """
        if metric_type in self._metric_types:
            raise ValueError(f"metric type {metric_type!r} is already registered")
        if stands_in_for and self.metric_fallback(stands_in_for):
            raise ValueError(f"a metric type already stands in for {stands_in_for!r}")
        self._metric_types[metric_type] = details_model
        self._creatable[metric_type] = creatable
        self._metric_info[metric_type] = {"alertable": bool(alertable), "stands_in_for": stands_in_for,
                                          "in_health_summary": bool(in_health_summary)}

    def non_alertable_metric_types(self) -> set:
        """Plugin metric types that never create incidents.

        Returns:
            Set of metric types.
        """
        return {t for t, i in self._metric_info.items() if not i["alertable"]}

    def metric_fallback(self, metric_type: str) -> Optional[str]:
        """The plugin type that stands in for a core type while that type has no data.

        Args:
            metric_type: Core metric type (e.g. 'cpu').

        Returns:
            The stand-in type, or None.
        """
        for t, i in self._metric_info.items():
            if i["stands_in_for"] == metric_type:
                return t
        return None

    def metric_base_type(self, metric_type: str) -> str:
        """The core type a stand-in type is shown as (the type itself otherwise).

        Args:
            metric_type: Metric type.

        Returns:
            Metric type to show it under.
        """
        info = self._metric_info.get(metric_type)
        return (info or {}).get("stands_in_for") or metric_type

    def health_summary_excluded_types(self) -> set:
        """Plugin metric types left out of the per-server health summary.

        Returns:
            Set of metric types.
        """
        return {t for t, i in self._metric_info.items() if not i["in_health_summary"]}

    def monitor_type_creatable(self, metric_type: str) -> bool:
        """Whether a monitor of a plugin-added type may be created now.

        Args:
            metric_type: Monitor type.

        Returns:
            False for types no plugin added, or whose condition is false or fails.
        """
        if metric_type not in self._metric_types:
            return False
        cond = self._creatable.get(metric_type)
        try:
            return cond is None or bool(cond())
        except Exception as e:
            logger.warning(f"monitor type {metric_type!r} condition failed: {e}")
            return False

    def metric_types(self) -> Dict[str, Any]:
        """Metric types added by plugins, with their details model (or None).

        Returns:
            Metric type to details model.
        """
        return dict(self._metric_types)

    # ------------------------------------------------------------- LLM
    def set_llm_slot(self, factory: Callable[[str], Any]) -> None:
        """Bound concurrent LLM calls (e.g. a gateway shared with other LLM users).

        Args:
            factory: f(priority) -> context manager held for the duration of one LLM call.
        """
        self._llm_slot = factory

    def llm_slot(self, priority: str = "high"):
        """Context manager to hold around an LLM call; a no-op unless a plugin set one.

        A provider that fails to start is logged and the call goes ahead without it, so
        incident analysis is never blocked by it.

        Args:
            priority: 'high' for incident analysis.

        Returns:
            A context manager.
        """
        import contextlib
        if self._llm_slot is None:
            return contextlib.nullcontext()
        try:
            return self._llm_slot(priority)
        except Exception:
            logger.exception("LLM slot provider failed; calling the LLM directly")
            return contextlib.nullcontext()

    # ------------------------------------------------------------- incidents
    def add_incident_fields(self, provider: Callable, feature: Optional[str] = None) -> None:
        """Register a provider of extra fields shown with incidents (e.g. correlation).

        Args:
            provider: f(db, incident_ids, detail) -> {incident_id: {field: value}}. `detail`
                is True for the single-incident view, where costlier fields may be added.
            feature: Licence feature it needs, if any.
        """
        self._incident_fields.append(_Entry(provider, feature))

    def incident_fields(self, db, incident_ids: List[str], detail: bool = False) -> Dict[str, Dict[str, Any]]:
        """Collect the extra incident fields from every active provider.

        A provider that fails is logged and skipped, so the incident still loads.

        Args:
            db: SQLAlchemy session.
            incident_ids: Incidents being returned.
            detail: True for the single-incident view.

        Returns:
            Extra fields by incident id (only incidents that have any).
        """
        out: Dict[str, Dict[str, Any]] = {}
        if not incident_ids:
            return out
        for e in self._incident_fields:
            if not self.feature_allowed(e.feature):
                continue
            try:
                for iid, fields in (e.item(db, list(incident_ids), detail) or {}).items():
                    out.setdefault(iid, {}).update(fields)
            except Exception:
                logger.exception("incident field provider %s failed", getattr(e.item, "__name__", e.item))
                try:
                    db.rollback()
                except Exception:
                    pass
        return out

    # ------------------------------------------------------------- database
    def add_migration_dir(self, folder: Path) -> None:
        """Register a folder of Alembic migrations for a plugin's own tables.

        Not licence-gated: a plugin's tables are created and kept whether or not its
        feature is licensed at the moment, so a licence change never loses data.

        Args:
            folder: Folder of Alembic revision files (a branch depending on the core baseline).

        Raises:
            ValueError: when the folder does not exist.
        """
        folder = Path(folder).resolve()
        if not folder.is_dir():
            raise ValueError(f"migration folder {folder} does not exist")
        if folder not in self._migration_dirs:
            self._migration_dirs.append(folder)

    def migration_dirs(self) -> List[Path]:
        """Plugin migration folders, in registration order.

        Returns:
            Folder paths.
        """
        return list(self._migration_dirs)

    # ------------------------------------------------------------- monitors and settings
    def add_monitor_templates(self, folder: Path, feature: Optional[str] = None,
                              condition: Optional[Callable[[], bool]] = None) -> None:
        """Register an extra folder of monitor scripts served to agents.

        Args:
            folder: Folder holding *_monitor.py files.
            feature: Licence feature; the folder is served only while allowed.
            condition: Extra f() -> bool checked each time (e.g. "licence not blocked");
                an error counts as False.
        """
        self._template_dirs.append(_Entry(Path(folder), feature, options={"condition": condition}))

    def monitor_template_dirs(self) -> List[Path]:
        """Extra monitor-script folders active now.

        Returns:
            Folder paths.
        """
        out = []
        for e in self._template_dirs:
            if not self.feature_allowed(e.feature):
                continue
            cond = e.options.get("condition")
            try:
                if cond is not None and not cond():
                    continue
            except Exception:
                logger.exception("monitor template condition failed for %s", e.item)
                continue
            out.append(e.item)
        return out

    def add_config_defaults(self, section: str, defaults: Dict[str, Any]) -> None:
        """Register default values for a Settings section.

        Args:
            section: Top-level key in the server configuration.
            defaults: Default values.

        Raises:
            ValueError: when the section is already registered.
        """
        if section in self._config_defaults:
            raise ValueError(f"config section {section!r} already registered")
        self._config_defaults[section] = dict(defaults)

    def config_defaults(self) -> Dict[str, Dict[str, Any]]:
        """Defaults registered by plugins, by section.

        Returns:
            A copy of the defaults.
        """
        return {k: dict(v) for k, v in self._config_defaults.items()}
