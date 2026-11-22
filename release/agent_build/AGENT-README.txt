Axara AIOps agent
=================

This folder is the complete agent: no Python, Docker or other software is needed on the host.
Supported: Windows Server 2016+ / Windows 10+ (x64) and Linux x86-64 (RHEL/CentOS 7+, Ubuntu 18.04+,
Debian 9+). Browser monitors on Linux need RHEL 8+, Ubuntu 20.04+ or Debian 10+.

Install (as Administrator on Windows, as root on Linux), from this folder:

  Windows:  axara-agent.exe install --server-url https://<server>:9090 --server-ca <server.crt> --enrol-token-file enrol-token.txt
  Linux:    ./axara-agent install --server-url https://<server>:9090 --server-ca <server.crt> --enrol-token-file enrol-token.txt

The first install needs an enrolment token: an administrator creates one in the dashboard (user menu >
Agent enrolment). Save it in a file, pass --enrol-token-file, and delete the file afterwards; without the
option you are asked for it. A host that already has a registered agent is refused - remove the old agent
first.

This writes the settings to config/, registers the agent with the server and registers it as a service
that starts at boot (Windows service "AxaraAgent", systemd unit "axara-agent"). The Community agent is
licensed under the GNU AGPL v3 or later (LICENSE). Enterprise packages also contain EULA.md, which the
install asks you to accept (--accept-eula for unattended installs). Options:
  --with-browser [DIR]   install Chromium for browser monitors (from the chromium-<os>-x64 package,
                         extracted next to this folder, or the folder given)
  --app-name NAME        application name used at first registration
  --environment ENV      production / staging / dev
  --no-service           configure only; start it yourself with "axara-agent run"

Other commands: status, selftest, version.

Uninstall: "axara-agent uninstall --purge" removes the agent from the server (monitors archived, licence
slot freed in the Enterprise edition, key revoked, recorded in the audit log), removes the service and deletes this folder. It
changes nothing if the server cannot be reached; --force removes it from this host anyway (then remove it
in the dashboard: Servers > host > Remove agent). "axara-agent uninstall" alone only removes the service.

Upgrade: stop the service, extract the new package over this folder (your settings in config/ are
kept; the package only contains *example* config files), start the service.
  Windows:  sc stop AxaraAgent   ...   sc start AxaraAgent
  Linux:    systemctl stop axara-agent   ...   systemctl start axara-agent

Browser monitor scripts read URLs and credentials from environment variables set on the host;
see scripts/browser/example_journey.py.
