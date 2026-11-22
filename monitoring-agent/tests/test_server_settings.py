# Copyright (c) 2026 Axara Pty Ltd.
# SPDX-License-Identifier: AGPL-3.0-or-later
"""The server URL and TLS trust come from bootstrap.yaml, even when agent_config.yaml was
created from the example file (the bug where a registered agent kept reporting
'Server unreachable' because it talked to the example address after registration)."""
import shutil
import sys
from pathlib import Path

import yaml

AGENT = Path(__file__).resolve().parents[1]
if str(AGENT) not in sys.path:
    sys.path.insert(0, str(AGENT))

from axara.agent.core.agent import MonitoringAgent  # noqa: E402

REAL_URL = "https://srv.corp.local:9090"


def _setup(tmp_path, ssl_verify="/opt/axara-agent/config/server-ca.crt"):
    """An agent whose config was copied from the example, with a bootstrap.yaml pointing at REAL_URL.

    Args:
        tmp_path: Temporary folder.
        ssl_verify: ssl_verify value written to bootstrap.yaml.

    Returns:
        (agent, config folder).
    """
    cfg = tmp_path / "config"
    cfg.mkdir()
    boot = yaml.safe_load((AGENT / "config" / "bootstrap.yaml").read_text(encoding="utf-8"))
    boot["central_server"]["url"] = REAL_URL
    boot["central_server"]["ssl_verify"] = ssl_verify
    (cfg / "bootstrap.yaml").write_text(yaml.dump(boot), encoding="utf-8")
    # what agent.bat / the old launcher did: copy the example config into place
    shutil.copyfile(AGENT / "config" / "agent_config_example.yaml", cfg / "agent_config.yaml")
    return MonitoringAgent(config_dir=str(cfg)), cfg


def test_example_config_takes_bootstrap_server(tmp_path):
    """The server URL and ssl_verify from bootstrap.yaml replace the example values and are saved."""
    agent, cfg = _setup(tmp_path)
    example = yaml.safe_load((cfg / "agent_config.yaml").read_text())
    assert example["agent"]["central_server_url"] != REAL_URL        # the broken starting point
    config = agent._sync_server_settings(agent._load_config())
    assert config["agent"]["central_server_url"] == REAL_URL
    assert config["agent"]["ssl_verify"] == "/opt/axara-agent/config/server-ca.crt"
    saved = yaml.safe_load((cfg / "agent_config.yaml").read_text())
    assert saved["agent"]["central_server_url"] == REAL_URL           # persisted
    assert saved["agent"]["ssl_verify"] == "/opt/axara-agent/config/server-ca.crt"


def test_registration_fields_are_kept(tmp_path):
    """Syncing server settings keeps the registration fields."""
    agent, cfg = _setup(tmp_path)
    config = agent._load_config()
    config["agent"].update(server_id="bdb050c1-976a-42dc-b491-90d48a221b82", api_key="k", application_id="a")
    (cfg / "agent_config.yaml").write_text(yaml.dump(config))
    synced = agent._sync_server_settings(agent._load_config())
    assert synced["agent"]["server_id"] == "bdb050c1-976a-42dc-b491-90d48a221b82"
    assert synced["agent"]["api_key"] == "k"


def test_no_rewrite_when_in_sync(tmp_path):
    """The config file is not rewritten when it already matches."""
    agent, cfg = _setup(tmp_path, ssl_verify=False)
    agent._sync_server_settings(agent._load_config())
    before = (cfg / "agent_config.yaml").stat().st_mtime_ns
    agent._sync_server_settings(agent._load_config())
    assert (cfg / "agent_config.yaml").stat().st_mtime_ns == before


def test_sender_uses_bootstrap_server(tmp_path):
    """The API sender uses the bootstrap server URL and ssl_verify."""
    agent, cfg = _setup(tmp_path, ssl_verify=False)
    agent._initialize_components(agent._load_config())
    assert agent.api_sender.base_url == REAL_URL
    assert agent.api_sender.session.verify is False


def test_configured_ca_wins_over_requests_ca_bundle(tmp_path, monkeypatch):
    """REQUESTS_CA_BUNDLE in the environment does not replace the agent's server CA."""
    from axara.agent.core.sender import APISender
    monkeypatch.setenv("REQUESTS_CA_BUNDLE", "/elsewhere/ca.pem")
    monkeypatch.setenv("AGENT_QUEUE_DIR", str(tmp_path / "q"))
    s = APISender("https://srv:9090", ssl_verify="/opt/axara-agent/config/server-ca.crt")
    st = s.session.merge_environment_settings("https://srv:9090/x", {}, None, None, None)
    assert st["verify"] == "/opt/axara-agent/config/server-ca.crt"
    assert s.session.merge_environment_settings("https://srv:9090/x", {}, None, False, None)["verify"] is False
