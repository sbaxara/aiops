# Copyright (c) 2026 Axara Pty Ltd.
# SPDX-License-Identifier: AGPL-3.0-or-later
"""axara-agent install registration (enrolment token) and uninstall --purge (server-side
removal first, then local files), with the server replaced by a fake requests.post."""
import importlib.util
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest
import yaml

AGENT = Path(__file__).resolve().parents[1]
if str(AGENT) not in sys.path:
    sys.path.insert(0, str(AGENT))
spec = importlib.util.spec_from_file_location("axara_agent_under_test", AGENT / "axara_agent.py")
aa = importlib.util.module_from_spec(spec)
spec.loader.exec_module(aa)

SID = "610df774-0cf2-4e77-876b-76199e04b5c7"


@pytest.fixture
def pkg(tmp_path, monkeypatch):
    """An unpacked agent package: <target>/axara-agent/{VERSION,EULA.md,config/...}."""
    target = tmp_path / "Axara" / "agent"
    base = target / "axara-agent"
    (base / "config").mkdir(parents=True)
    (base / "logs").mkdir()
    (base / "VERSION").write_text("1.0\n")
    (base / "EULA.md").write_text("eula\n")
    (target / "EULA-ACCEPTED.json").write_text("{}\n")
    (base / "config" / "bootstrap.yaml").write_text(yaml.dump(
        {"central_server": {"url": "https://srv:9090", "ssl_verify": "/x/ca.crt"}}))
    monkeypatch.setattr(aa, "BASE", base)
    monkeypatch.setattr(aa, "CONFIG", base / "config")
    monkeypatch.setattr(aa, "EXE", None)
    monkeypatch.setattr(aa, "IS_WINDOWS", False)
    monkeypatch.setattr(aa, "_is_admin", lambda: True)
    monkeypatch.setenv("AXARA_SYSTEMD_DIR", str(tmp_path / "systemd"))
    monkeypatch.delenv("AXARA_ENROL_TOKEN", raising=False)
    monkeypatch.chdir(tmp_path)
    return SimpleNamespace(target=target, base=base)


def registered(pkg):
    """Write an agent_config.yaml that marks the package as registered."""
    (pkg.base / "config" / "agent_config.yaml").write_text(yaml.dump(
        {"agent": {"server_id": SID, "api_key": "agent_k", "central_server_url": "https://old:9090"}}))


def fake_post(monkeypatch, status=200, body=None, exc=None):
    """Replace requests.post with a stub.

    Args:
        monkeypatch: pytest monkeypatch fixture.
        status: HTTP status the stub returns.
        body: JSON body the stub returns.
        exc: Exception the stub raises instead, if any.

    Returns:
        List that collects (url, kwargs) of each call.
    """
    calls = []
    import requests

    def post(url, **kw):
        """Record the call and answer with the configured response."""
        calls.append((url, kw))
        if exc:
            raise exc
        return SimpleNamespace(status_code=status, text=str(body), json=lambda: body or {})
    monkeypatch.setattr(requests, "post", post)
    return calls


def args(**kw):
    """Uninstall arguments for a confirmed purge."""
    return SimpleNamespace(purge=True, force=False, yes=True, **kw)


def test_purge_removes_server_side_then_files(pkg, monkeypatch):
    """--purge deregisters at the bootstrap server URL with the agent key and CA, then deletes the files."""
    registered(pkg)
    calls = fake_post(monkeypatch, 200, {"monitors_archived": 7})
    assert aa.cmd_uninstall(args()) == 0
    url, kw = calls[0]
    assert url == "https://srv:9090/api/v1/agents/deregister"          # bootstrap URL wins
    assert kw["headers"]["Authorization"] == "Bearer agent_k" and kw["verify"] == "/x/ca.crt"
    assert not pkg.base.exists() and not pkg.target.exists()           # installer folder too


def test_parent_with_other_files_is_kept(pkg, monkeypatch):
    """The installer's parent folder is kept when it holds other files."""
    registered(pkg)
    (pkg.target / "notes.txt").write_text("mine")
    fake_post(monkeypatch, 200, {})
    aa.cmd_uninstall(args())
    assert not pkg.base.exists() and (pkg.target / "notes.txt").exists()


def test_unreachable_server_changes_nothing_unless_forced(pkg, monkeypatch):
    """An unreachable server leaves everything in place unless --force is given."""
    registered(pkg)
    fake_post(monkeypatch, exc=OSError("connection refused"))
    with pytest.raises(SystemExit):
        aa.cmd_uninstall(args())
    assert (pkg.base / "config" / "agent_config.yaml").exists()
    aa.cmd_uninstall(SimpleNamespace(purge=True, force=True, yes=True))
    assert not pkg.base.exists()


def test_already_removed_in_dashboard_continues(pkg, monkeypatch):
    """An agent already removed in the dashboard (401) is still purged locally."""
    registered(pkg)
    fake_post(monkeypatch, 401, {"detail": "Invalid"})
    aa.cmd_uninstall(args())
    assert not pkg.base.exists()


def test_server_error_refuses(pkg, monkeypatch):
    """A server error refuses the purge."""
    registered(pkg)
    fake_post(monkeypatch, 500, {"detail": "boom"})
    with pytest.raises(SystemExit):
        aa.cmd_uninstall(args())
    assert pkg.base.exists()


def test_unregistered_agent_purges_without_server(pkg, monkeypatch):
    """An unregistered agent is purged without calling the server."""
    calls = fake_post(monkeypatch, 200, {})
    aa.cmd_uninstall(args())
    assert calls == [] and not pkg.base.exists()


def test_purge_refused_outside_a_package(pkg, monkeypatch):
    """--purge refuses a folder that is not an installed agent package."""
    (pkg.base / "VERSION").unlink()
    with pytest.raises(SystemExit):
        aa.cmd_uninstall(args())
    assert pkg.base.exists()


def test_purge_needs_confirmation_when_not_interactive(pkg, monkeypatch):
    """--purge without --yes refuses when not interactive."""
    monkeypatch.setattr(sys, "stdin", None)
    with pytest.raises(SystemExit):
        aa.cmd_uninstall(SimpleNamespace(purge=True, force=False, yes=False))
    assert pkg.base.exists()


def test_plain_uninstall_keeps_files(pkg, monkeypatch):
    """Uninstall without --purge keeps the files and does not call the server."""
    registered(pkg)
    calls = fake_post(monkeypatch, 200, {})
    aa.cmd_uninstall(SimpleNamespace(purge=False, force=False, yes=False))
    assert calls == [] and pkg.base.exists()


# ------------------------------------------------------------------ install registration

def iargs(**kw):
    """Install arguments with no enrolment token unless given."""
    d = dict(enrol_token=None, enrol_token_file=None)
    d.update(kw)
    return SimpleNamespace(**d)


def test_already_registered_needs_no_token(pkg, monkeypatch):
    """A registered agent does not register again or need a token."""
    registered(pkg)
    import axara.agent.core.agent as ca
    monkeypatch.setattr(ca.MonitoringAgent, "_register_and_configure",
                        lambda *a, **k: pytest.fail("must not register again"))
    aa._ensure_registered(iargs())


def test_missing_token_fails_non_interactive(pkg, monkeypatch):
    """No token and no terminal: the install stops."""
    monkeypatch.setattr(sys, "stdin", None)
    with pytest.raises(SystemExit):
        aa._ensure_registered(iargs())


@pytest.mark.parametrize("source", ["file", "arg", "env"])
def test_token_sources_and_registration(pkg, monkeypatch, tmp_path, source):
    """The token is taken from a file, an argument or the environment and passed to registration."""
    seen = {}
    import axara.agent.core.agent as ca

    def reg(self, enrolment_token=None):
        """Stub registration that records the token and succeeds."""
        seen["tok"] = enrolment_token
        self.server_id = SID
        return True
    monkeypatch.setattr(ca.MonitoringAgent, "_register_and_configure", reg)
    kw = {}
    if source == "file":
        f = tmp_path / "tok.txt"
        f.write_text("axe_secret\n")
        kw["enrol_token_file"] = str(f)
    elif source == "arg":
        kw["enrol_token"] = "axe_secret"
    else:
        monkeypatch.setenv("AXARA_ENROL_TOKEN", "axe_secret")
    aa._ensure_registered(iargs(**kw))
    assert seen["tok"] == "axe_secret"
    assert "axe_secret" not in (pkg.base / "config" / "agent_config.yaml").read_text()


def test_refused_registration_is_reported(pkg, monkeypatch):
    """A refused registration stops the install with the server's reason."""
    import axara.agent.core.agent as ca

    def reg(self, enrolment_token=None):
        """Stub registration that fails with an expired-token error."""
        self.registration_error = "401: The enrolment token has expired."
        return False
    monkeypatch.setattr(ca.MonitoringAgent, "_register_and_configure", reg)
    with pytest.raises(SystemExit):
        aa._ensure_registered(iargs(enrol_token="axe_x"))


def test_sender_sends_token_header_and_reads_error(monkeypatch, tmp_path):
    """The sender sends the token in X-Axara-Enrolment-Token and keeps the server's error message."""
    monkeypatch.setenv("AGENT_QUEUE_DIR", str(tmp_path / "q"))
    from axara.agent.core.sender import APISender
    s = APISender("https://srv:9090")
    sent = {}

    def post(url, json=None, headers=None, timeout=None):
        """Stub POST answering 409 hostname_registered."""
        sent.update(url=url, headers=headers)
        return SimpleNamespace(status_code=409, text="x", json=lambda: {
            "detail": {"code": "hostname_registered", "message": "An agent is already registered"}})
    monkeypatch.setattr(s.session, "post", post)
    assert s.register_agent({"hostname": "h"}, enrolment_token="axe_t") is None
    assert sent["headers"] == {"X-Axara-Enrolment-Token": "axe_t"}
    assert s.last_error == "409: An agent is already registered"


def test_agent_without_token_does_not_call_server(tmp_path, monkeypatch):
    """Without a token the agent does not try to register."""
    monkeypatch.delenv("AXARA_ENROL_TOKEN", raising=False)
    from axara.agent.core.agent import MonitoringAgent
    cfg = tmp_path / "config"
    cfg.mkdir()
    (cfg / "bootstrap.yaml").write_text(yaml.dump({"central_server": {"url": "https://srv:9090"},
                                                   "application": {"name": "a"}}))
    ag = MonitoringAgent(config_dir=str(cfg))
    assert ag._register_and_configure() is False
    assert "enrolment token" in ag.registration_error


def test_community_package_needs_no_eula(pkg, monkeypatch):
    """A Community package (LICENSE, no EULA.md) installs without an EULA prompt and can be purged."""
    (pkg.base / "EULA.md").unlink()
    with pytest.raises(SystemExit):
        aa._accept_eula(SimpleNamespace(accept_eula=False))                # neither file: refused
    (pkg.base / "LICENSE").write_text("GNU AFFERO GENERAL PUBLIC LICENSE\n")
    monkeypatch.setattr("builtins.input", lambda _p: pytest.fail("must not ask"))
    aa._accept_eula(SimpleNamespace(accept_eula=False))
    assert not (pkg.base / "EULA-ACCEPTED.json").exists()
    assert aa._is_installed_agent_folder()


def test_missing_packages_stop_before_any_change(pkg, monkeypatch, capsys):
    """From source, a missing package stops the command with the pip command and changes nothing."""
    import importlib.util
    real = importlib.util.find_spec
    monkeypatch.setattr(importlib.util, "find_spec", lambda name, *a: None if name == "yaml" else real(name, *a))
    before = sorted(p.name for p in pkg.base.rglob("*"))
    with pytest.raises(SystemExit):
        aa.main(["install", "--no-service", "--server-url", "https://srv:9090"])
    err = capsys.readouterr().err
    assert "PyYAML" in err and "pip install -r" in err and "nothing was changed" in err
    assert sorted(p.name for p in pkg.base.rglob("*")) == before
    assert aa.main(["version"]) == 0                      # version still works
