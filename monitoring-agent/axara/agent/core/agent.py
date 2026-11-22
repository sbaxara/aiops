# Copyright (c) 2026 Axara Pty Ltd.
# SPDX-License-Identifier: AGPL-3.0-or-later
"""
Main Monitoring Agent
Orchestrates registration, monitoring, and metric collection
"""
import os
import yaml
import time
import platform
import socket
import logging
import datetime
from pathlib import Path
from typing import Dict, Any, Optional
from .collector import MetricsCollector
from .sender import APISender
from .script_updater import ScriptUpdater
from .config_updater import ConfigUpdater
from . import extensions

logger = logging.getLogger(__name__)

class MonitorConfigUpdated(Exception):
    """
    Sentinel raised inside _run_monitoring_loop when monitors.yaml has been
    updated.  Caught in start() to trigger a clean loop restart.
    """
    pass

class MonitoringAgent:
    """
    Main agent orchestrator
    Handles registration, monitoring loop, and communication with central server
    """
    
    def __init__(self, config_dir: str = "config"):
        """
        Initialise monitoring agent
        
        Args:
            config_dir: Path to configuration directory
        """
        self.config_dir = Path(config_dir)
        self.bootstrap_file = self.config_dir / "bootstrap.yaml"
        self.config_file = self.config_dir / "agent_config.yaml"
        
        self.registered = False
        self.server_id = None
        self.application_id = None
        self.check_interval_seconds = 300
        
        self.api_sender = None
        self.registration_error = None
        self.collector = None
        self.script_updater = None
        self.config_updater = None
    
    def start(self):
        """Start the monitoring agent"""
        logger.info("Starting Monitoring Agent...")

        try:
            config = self._load_or_create_config()

            if config.get('agent', {}).get('server_id'):
                # Already registered — use saved credentials and monitors.yaml as-is
                logger.info(
                    f"Agent already registered "
                    f"(server_id={config['agent']['server_id']})"
                )
                self.server_id     = config['agent']['server_id']
                self.application_id = config['agent']['application_id']
                self.check_interval_seconds = config['agent'].get('check_interval_seconds', 300)
            else:
                # First run — register, save agent_config.yaml and monitors.yaml
                logger.info("Agent not registered, initiating registration...")
                if not self._register_and_configure():
                    logger.error(f"Registration failed, cannot proceed: {self.registration_error}")
                    return
                config = self._load_config()

            # Initialise collector (loads monitors.yaml) and other components
            self._initialize_components(config)

            # Check for script updates at startup — if scripts were updated,
            # re-initialise components so new code is loaded before the loop starts.
            if not self.api_sender.ping():
                # Server unreachable — lets not check monitor scripts update.
                logger.warning(
                    f'Server unreachable — skipping monitor scripts update')
            else:
                if self.script_updater.check_and_update_scripts():
                    logger.info("Scripts updated at startup — reloading monitor modules")
                    self._reload_monitor_modules()
                    self._initialize_components(self._load_config())

            # FIXED (real bug): config_updater had NO equivalent startup check —
            # only script_updater did. That meant a stale local monitors.yaml
            # (e.g. one still listing monitors already deleted server-side) got
            # loaded and run unconditionally on every restart, with no check
            # against the server until the periodic loop's first maintenance
            # pass — up to a full check_interval_seconds later. Mirrors the
            # script-update block immediately above, including its own
            # reload-modules-then-reinitialise pattern (matching what the
            # MonitorConfigUpdated handler already does for a config-only
            # change further down, for consistency).
            if not self.api_sender.ping():
                logger.warning(
                    f'Server unreachable — skipping monitor config update check at startup')
            else:
                if self.config_updater.check_and_update_config():
                    logger.info("monitors.yaml updated at startup — reloading monitor modules")
                    self._reload_monitor_modules()
                    self._initialize_components(self._load_config())

            # Outer restart loop
            # _run_monitoring_loop runs forever until:
            #   • MonitorConfigUpdated  → re-initialise collector and restart loop
            #   • KeyboardInterrupt     → clean shutdown
            #   • Any other exception   → bubble up to outer except
            while True:
                try:
                    self._run_monitoring_loop()
                    break  # only reached on normal (non-exception) exit
                except MonitorConfigUpdated:
                    logger.info(
                        "monitors.yaml updated — restarting monitoring loop "
                        "cleanly with new configuration…"
                    )
                    if self.collector:
                        self.collector.shutdown()
                    # Evict cached monitor modules so updated .py files are
                    # re-read from disk on the next import inside _initialize_components
                    self._reload_monitor_modules()                       
                    # Re-load config from disk (monitors.yaml already written
                    # by config_updater before raising the exception)
                    self._initialize_components(self._load_config())
                    logger.info("Monitoring loop restarted with updated config")
                    # Continue outer while → restart _run_monitoring_loop

        except KeyboardInterrupt:
            logger.info("Received shutdown signal")
        except Exception as e:
            logger.error(f"Agent error: {e}", exc_info=True)
        finally:
            self._shutdown()
    
    def _load_or_create_config(self) -> Dict:
        """Load existing config or create from bootstrap"""
        if self.config_file.exists():
            return self._load_config()
        else:
            logger.info("No agent config found, creating from bootstrap...")
            return self._create_config_from_bootstrap()
    
    def _load_config(self) -> Dict:
        """Load agent configuration"""
        with open(self.config_file, 'r') as f:
            return yaml.safe_load(f)
    
    def _create_config_from_bootstrap(self) -> Dict:
        """Create initial config from bootstrap"""
        if not self.bootstrap_file.exists():
            raise FileNotFoundError(
                f"Bootstrap configuration not found: {self.bootstrap_file}\n"
                "Please create bootstrap.yaml with application details"
            )
        
        with open(self.bootstrap_file, 'r') as f:
            bootstrap = yaml.safe_load(f)
        
        # Create minimal agent config
        config = {
            'agent': {
                'server_id': None,  # Will be filled after registration
                'application_id': None,
                'central_server_url': bootstrap['central_server']['url'],
                'ssl_verify': bootstrap['central_server'].get('ssl_verify', True),
                'api_key': None,
                'check_interval_seconds': 300,
                'batch_size': 100,
                'retry_attempts': 3,
                'timeout': 30
            },
            'logging': {
                'level': 'INFO',
                'file': 'logs/agent.log',
                'max_size_mb': 100,
                'backup_count': 5
            }
        }
        
        # Save initial config
        with open(self.config_file, 'w') as f:
            yaml.dump(config, f)
        
        return config

    # Registration

    def _register_and_configure(self, enrolment_token: Optional[str] = None) -> bool:
        """
        Register agent with central server and auto-update configuration.
        Normally done by `axara-agent install` (which passes the enrolment token); a
        development run can supply the token in the AXARA_ENROL_TOKEN environment variable.
        
        Returns:
            True if successful (else the reason is in self.registration_error)
        """
        token = (enrolment_token or os.environ.get('AXARA_ENROL_TOKEN') or '').strip()
        if not token:
            self.registration_error = (
                "this agent is not registered and no enrolment token was given. An administrator "
                "creates one in the dashboard (Agent enrolment); then run: axara-agent install "
                "--server-url <url> --server-ca <ca> --enrol-token-file <file>")
            return False
        # Load bootstrap config
        with open(self.bootstrap_file, 'r') as f:
            bootstrap = yaml.safe_load(f)
        
        # Prepare registration data
        registration_data = {
            'hostname':         platform.node(),
            'ip_address':       self._get_primary_ip(),
            'os_type':          platform.system().lower(),
            'os_version':       platform.release(),
            'agent_version':    self._get_agent_version(),
            'capabilities':     self._detect_capabilities(),
            'application_name': bootstrap['application']['name'],
            'application_id':   bootstrap['application'].get('id'),
            'support_team':     bootstrap.get('support_team', {}),
            'environment':      bootstrap['application'].get('environment', 'production'),
            'tags':             bootstrap['application'].get('tags', []),
        }
        
        # Initialise API sender (without API key for registration)
        _ssl_verify = bootstrap['central_server'].get('ssl_verify', True)
        temp_sender = APISender(
            base_url=bootstrap['central_server']['url'],
            timeout=30,
            ssl_verify=_ssl_verify,
        )
        
        # Register
        response = temp_sender.register_agent(registration_data, enrolment_token=token)
        
        if not response or not response.get('success'):
            self.registration_error = getattr(temp_sender, 'last_error', None) or "registration refused"
            return False
        
        # Auto-update configuration
        self._update_agent_config(response)
        self._update_monitor_configs(response.get('monitor_configs', []))
        
        self.registered = True
        return True
    
    def _update_agent_config(self, registration_response: Dict):
        """
        Automatically update agent_config.yaml with registration data
        
        Args:
            registration_response: Response from registration API
        """
        config = self._load_config()
        
        # Update agent section
        config['agent']['server_id'] = registration_response['server_id']
        config['agent']['application_id'] = registration_response['application_id']
        config['agent']['api_key'] = registration_response['api_key']
        config['agent']['check_interval_seconds'] = registration_response.get('check_interval_seconds', 300)
        
        # Save updated config
        with open(self.config_file, 'w') as f:
            yaml.dump(config, f)
        
        logger.info(f"Agent configuration updated with server_id: {registration_response['server_id']}")
        
        # Update instance variables
        self.server_id = registration_response['server_id']
        self.application_id = registration_response['application_id']
        self.check_interval_seconds = registration_response.get('check_interval_seconds', 300)
    
    def _update_monitor_configs(self, monitor_configs: list):
        """
        Save monitor configurations to monitors.yaml.
        Only called once during initial registration.
        Runtime changes to monitors.yaml are detected by the collector
        hot-reload and applied automatically without restarting the agent.

        Args:
            monitor_configs: List of monitor configurations from server
        """
        monitors_file = self.config_dir / "monitors.yaml"
        with open(monitors_file, 'w') as f:
            yaml.dump({'monitors': monitor_configs}, f)
        logger.info(f"monitors.yaml saved ({len(monitor_configs)} monitors)")

    # Component initialisation
    
    def _reload_monitor_modules(self):
        """
        Evict all monitor modules from sys.modules so the next import
        re-reads the .py files from disk.

        Python caches every imported module in sys.modules after the first
        load of axara.agent.monitors (core.monitor_loader).  Simply writing a new .py
        file and calling _initialize_components again does not help — the
        import statement returns the cached class without touching the file.
        Removing the entries forces a full re-import on the next reference.
        """
        from . import monitor_loader
        stale = monitor_loader.evict()
        if stale:
            logger.info(f"Evicted {len(stale)} monitor module(s) from sys.modules: {stale}")

    def _sync_server_settings(self, config: Dict) -> Dict:
        """bootstrap.yaml is the single source of the server URL and TLS trust (written by
        `axara-agent install`). Copy them into agent_config.yaml when they differ, so a config
        created from the example file, or an install re-run with a new URL or CA, takes effect."""
        try:
            with open(self.bootstrap_file, 'r') as f:
                server = (yaml.safe_load(f) or {}).get('central_server') or {}
        except FileNotFoundError:
            return config
        agent_cfg = config.setdefault('agent', {})
        changed = []
        if server.get('url') and agent_cfg.get('central_server_url') != server['url']:
            agent_cfg['central_server_url'] = server['url']
            changed.append('central_server_url')
        if 'ssl_verify' in server and agent_cfg.get('ssl_verify') != server['ssl_verify']:
            agent_cfg['ssl_verify'] = server['ssl_verify']
            changed.append('ssl_verify')
        if changed:
            with open(self.config_file, 'w') as f:
                yaml.dump(config, f)
            logger.info(f"agent_config.yaml: {', '.join(changed)} updated from bootstrap.yaml")
        return config

    def _initialize_components(self, config: Dict):
        """Initialise agent components"""
        config = self._sync_server_settings(config)
        agent_config = config['agent']

        self.api_sender = APISender(
            base_url=agent_config['central_server_url'],
            api_key=agent_config.get('api_key'),
            retry_attempts=agent_config.get('retry_attempts', 3),
            timeout=agent_config.get('timeout', 30),
            ssl_verify=agent_config.get('ssl_verify', True),
        )

        self.collector = MetricsCollector(
            max_workers=10,
            timeout=agent_config.get('timeout', 30)
        )

        monitors_file = self.config_dir / "monitors.yaml"
        if monitors_file.exists():
            with open(monitors_file, 'r') as f:
                monitors_data = yaml.safe_load(f)
            self.collector.load_monitor_configs(
                monitors_data.get('monitors', []),
                monitors_file=monitors_file   # ← enables hot-reload
            )
            logger.info(
                f"Loaded {len(monitors_data.get('monitors', []))} "
                f"monitor config(s) from monitors.yaml"
            )

        self.script_updater = ScriptUpdater(self)
        self.config_updater = ConfigUpdater(self)

        logger.info("Agent components initialised")

    # Monitoring loop

    def _run_monitoring_loop(self):
        """
        Main monitoring loop.

        Wakes every POLL_INTERVAL seconds to ask the collector which monitors
        are due.  This means a monitor with check_interval_seconds=60 fires within
        POLL_INTERVAL seconds of being due, without having to wait for the
        longest check_interval_seconds (300s) to elapse.
        """
        POLL_INTERVAL = 30   # seconds — how often the loop wakes to check due monitors

        logger.info(
            f"Starting monitoring loop "
            f"(poll_interval={POLL_INTERVAL}s, "
            f"agent_check_interval_seconds={self.check_interval_seconds}s)"
        )

        # RETRACTED a previous change here: this was briefly set to 0.0 to make
        # the maintenance block fire on the very first loop pass. That was a
        # mistake — start() now already runs the equivalent checks explicitly
        # before the loop ever begins (see the startup block above), so also
        # firing immediately on the loop's first pass just double-checks
        # everything within milliseconds of the startup check, for no benefit
        # (confirmed live: script_updater logging "Checking..." twice back to
        # back). Reverted to the original time.time() — the loop's own
        # maintenance cadence should start counting from when the LOOP begins,
        # not re-trigger instantly on top of checks that already just ran.
        last_maintenance_check = time.time()

        while True:
            try:
                loop_start = time.time()

                # Maintenance: script + config version checks
                if loop_start - last_maintenance_check >= self.check_interval_seconds:
                    # 1. Script updates — returns True if any script was installed.
                    # Restart the loop so new code is imported fresh from disk.
                    # (Python caches modules in sys.modules; overwriting the .py
                    # file has no effect on the class already in memory.)
                    if not self.api_sender.ping():
                        # Server unreachable — lets not check monitor scripts update.
                        logger.warning(
                            f'Server unreachable — skipping monitor scripts update')
                    else:
                        if self.script_updater.check_and_update_scripts():
                            raise MonitorConfigUpdated(
                                "monitor scripts updated by server, restarting loop"
                            )

                    # 2. monitors.yaml update check (new)
                    if not self.api_sender.ping():
                        # Server unreachable — lets not check monitors.yaml update.
                        logger.warning(
                            f'Server unreachable — skipping monitor config update')
                    else:
                        if self.config_updater.check_and_update_config():
                            # File has been updated — signal a clean restart
                            raise MonitorConfigUpdated(
                                "monitors.yaml updated by server, restarting loop"
                            )

                    # 3. Steps added by agent extensions (none in the standard agent).
                    extensions.run_cycle_steps(self)

                    last_maintenance_check = loop_start

                # Collect metrics — monitors always run regardless of
                # server availability so no data is lost.
                metrics = self.collector.collect_all_metrics()

                if metrics:
                    if not self.api_sender.ping():
                        # Server unreachable — queue immediately rather than
                        # exhausting all retry attempts on a known-dead server.
                        self.api_sender.queue_metrics(self.server_id, metrics)
                        logger.warning(
                            f'Server unreachable — queued {len(metrics)} '
                            f'metric(s) for later replay '
                            f'(queue depth: '
                            f'{len(list(self.api_sender.queue_dir.glob("*.json")))} file(s))'
                        )
                    else:
                        # Server reachable — replay any queued payloads
                        # FIFO first, then send the current batch.
                        self.api_sender.replay_and_send(
                            self.server_id, metrics
                        )

                elapsed   = time.time() - loop_start
                sleep_for = max(0, POLL_INTERVAL - elapsed)
                logger.debug(f"Loop pass {elapsed:.2f}s, sleeping {sleep_for:.2f}s")
                time.sleep(sleep_for)

            except MonitorConfigUpdated:
                raise  # propagate to outer restart loop in start()
            except Exception as e:
                logger.error(f"Monitoring loop error: {e}", exc_info=True)
                time.sleep(60)

    # Registration verification

    def _verify_registration(self) -> bool:
        """Verify registration is still valid"""
        if not self.server_id:
            return False
        
        return self.api_sender.send_heartbeat(self.server_id)
    
    # Shutdown

    def _shutdown(self):
        """Cleanup and shutdown"""
        logger.info("Shutting down agent...")
        try:
            if self.collector:
                self.collector.shutdown()
        except KeyboardInterrupt:
            # Second Ctrl+C during shutdown — exit immediately without traceback
            logger.info("Shutdown interrupted — forcing exit")
        logger.info("Agent shutdown complete")

    # System helpers

    def _get_primary_ip(self) -> str:
        """Get primary IP address"""
        try:
            # Create a socket to determine primary network interface
            s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            s.connect(("8.8.8.8", 80))
            ip = s.getsockname()[0]
            s.close()
            return ip
        except Exception:
            return "127.0.0.1"
    
    def _get_agent_version(self) -> str:
        """The agent version: the package's VERSION file when installed, else the source version.

        Returns:
            Version text.
        """
        v = Path("VERSION")
        if v.is_file():
            return v.read_text(encoding="utf-8").strip()
        from axara.agent import __version__
        return __version__
    
    def _detect_capabilities(self) -> list:
        """Monitor types this host can run, sent at registration.

        Returns:
            Capability names (monitor types).
        """
        capabilities = [
            "cpu", "memory", "disk", "network", "log",
            "url", "process", "port", "browser", "ssl",
        ]
        for cap in extensions.load_extensions().capabilities:
            if cap not in capabilities:
                capabilities.append(cap)
        for lib, cap in [("pymongo", "mongodb"), ("pymysql", "mysql"),
                         ("psycopg2", "postgres")]:
            try:
                __import__(lib)
                capabilities.append(cap)
            except ImportError:
                pass
        return capabilities


def main():
    """Run the agent in the foreground (console + rotating file logging)."""
    # Configure logging — console + rotating file
    from logging.handlers import RotatingFileHandler

    log_dir = Path("logs")
    log_dir.mkdir(parents=True, exist_ok=True)

    fmt = logging.Formatter('%(asctime)s - %(name)s - %(levelname)s - %(message)s')

    stream_handler = logging.StreamHandler()
    stream_handler.setFormatter(fmt)

    file_handler = RotatingFileHandler(
        filename=log_dir / "agent.log",
        maxBytes=10 * 1024 * 1024,   # 10 MB per file
        backupCount=5,               # keep 5 rotated files (50 MB total)
        encoding="utf-8",
    )
    file_handler.setFormatter(fmt)

    root_logger = logging.getLogger()
    root_logger.setLevel(logging.INFO)
    root_logger.addHandler(stream_handler)
    root_logger.addHandler(file_handler)

    # Start agent
    agent = MonitoringAgent()
    agent.start()


if __name__ == '__main__':
    main()
