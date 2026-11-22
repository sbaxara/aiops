// Copyright (c) 2026 Axara Pty Ltd.
// SPDX-License-Identifier: AGPL-3.0-or-later
import React, { useState } from 'react';
import { useQuery, useQueryClient } from '@tanstack/react-query';
import { Ticket, Copy, Check, Ban, RefreshCw } from 'lucide-react';
import { api } from '../services/api';

const STATE_STYLE = {
  active:  'bg-green-500/20 text-green-300 border-green-500/30',
  used_up: 'bg-gray-500/20 text-gray-300 border-gray-500/30',
  expired: 'bg-gray-500/20 text-gray-400 border-gray-500/30',
  revoked: 'bg-red-500/20 text-red-300 border-red-500/30',
};
const STATE_LABEL = { active: 'Active', used_up: 'Used up', expired: 'Expired', revoked: 'Revoked' };

// Readable error text from an API error (string detail, detail.message, or fallback).
function errText(err, fallback) {
  const d = err.response?.data?.detail;
  if (typeof d === 'string') return d;
  return d?.message ?? err.message ?? fallback;
}

// Format an ISO timestamp in local time ('—' when empty).
const fmt = (iso) => (iso ? new Date(iso).toLocaleString() : '—');

// Button that copies text to the clipboard and confirms briefly.
function CopyButton({ text }) {
  const [done, setDone] = useState(false);
  return (
    <button
      type="button"
      onClick={() => { navigator.clipboard?.writeText(text); setDone(true); setTimeout(() => setDone(false), 1500); }}
      className="inline-flex items-center gap-1.5 px-3 py-1.5 rounded-lg bg-gray-700 hover:bg-gray-600 text-sm text-white"
    >
      {done ? <Check size={14} /> : <Copy size={14} />} {done ? 'Copied' : 'Copy'}
    </button>
  );
}

// Admin page to create, list and revoke agent enrolment tokens.
export default function AgentEnrolment() {
  const qc = useQueryClient();
  const [label, setLabel] = useState('');
  const [maxUses, setMaxUses] = useState(10);
  const [days, setDays] = useState(7);
  const [created, setCreated] = useState(null);
  const [msg, setMsg] = useState(null);
  const [busy, setBusy] = useState(false);

  const { data: tokens = [], isLoading, refetch } = useQuery({
    queryKey: ['enrolment-tokens'],
    queryFn: () => api.get('/agents/enrolment-tokens').then((r) => r.data),
  });

  const create = async (e) => {
    e.preventDefault();
    setBusy(true); setMsg(null); setCreated(null);
    try {
      const r = await api.post('/agents/enrolment-tokens', {
        label: label.trim(), max_uses: Number(maxUses), expires_in_days: Number(days),
      });
      setCreated(r.data);
      setLabel('');
      qc.invalidateQueries({ queryKey: ['enrolment-tokens'] });
    } catch (err) {
      setMsg({ ok: false, text: errText(err, 'Could not create the token') });
    } finally {
      setBusy(false);
    }
  };

  const revoke = async (t) => {
    if (!window.confirm(`Revoke "${t.label}"? Agents can no longer register with it. Agents already registered keep working.`)) return;
    setMsg(null);
    try {
      await api.post(`/agents/enrolment-tokens/${t.id}/revoke`);
      if (created?.id === t.id) setCreated(null);
      qc.invalidateQueries({ queryKey: ['enrolment-tokens'] });
    } catch (err) {
      setMsg({ ok: false, text: errText(err, 'Could not revoke the token') });
    }
  };

  const serverUrl = `https://${window.location.hostname}:9090`;
  const winCmd = `axara-agent.exe install --server-url ${serverUrl} --server-ca server.crt --enrol-token-file enrol-token.txt --accept-eula`;
  const linuxCmd = `sudo ./axara-agent install --server-url ${serverUrl} --server-ca server.crt --enrol-token-file enrol-token.txt --accept-eula`;

  return (
    <div className="space-y-6 max-w-6xl mx-auto">
      <div className="flex items-center gap-3">
        <Ticket size={20} className="text-blue-400" />
        <div>
          <h1 className="text-xl font-bold text-white">Agent enrolment</h1>
          <p className="text-sm text-gray-400">
            A new agent registers only with an enrolment token. Create one for a rollout, give it to whoever
            installs the agents, and revoke it when you are done.
          </p>
        </div>
      </div>

      {msg && (
        <div className={`rounded-lg border px-4 py-3 text-sm ${msg.ok
          ? 'border-green-500/30 bg-green-500/10 text-green-300'
          : 'border-red-500/30 bg-red-500/10 text-red-300'}`}>{msg.text}</div>
      )}

      {/* Create */}
      <form onSubmit={create} className="bg-gray-800 rounded-xl border border-gray-700 p-5 space-y-4">
        <h2 className="text-sm font-semibold text-white">Create a token</h2>
        <div className="grid grid-cols-1 md:grid-cols-4 gap-4">
          <label className="md:col-span-2 text-sm text-gray-300">
            Label
            <input
              value={label} onChange={(e) => setLabel(e.target.value)} required maxLength={100}
              placeholder="e.g. Payments servers - November rollout"
              className="mt-1 w-full rounded-lg bg-gray-900 border border-gray-700 px-3 py-2 text-white"
            />
          </label>
          <label className="text-sm text-gray-300">
            Number of agents
            <input
              type="number" min={1} max={1000} value={maxUses} required
              onChange={(e) => setMaxUses(e.target.value)}
              className="mt-1 w-full rounded-lg bg-gray-900 border border-gray-700 px-3 py-2 text-white"
            />
          </label>
          <label className="text-sm text-gray-300">
            Expires after (days)
            <input
              type="number" min={1} max={30} value={days} required
              onChange={(e) => setDays(e.target.value)}
              className="mt-1 w-full rounded-lg bg-gray-900 border border-gray-700 px-3 py-2 text-white"
            />
          </label>
        </div>
        <button
          type="submit" disabled={busy}
          className="px-4 py-2 rounded-lg bg-blue-600 hover:bg-blue-500 disabled:opacity-50 text-sm font-medium text-white"
        >
          {busy ? 'Creating…' : 'Create token'}
        </button>
      </form>

      {/* Shown once */}
      {created && (
        <div className="rounded-xl border border-amber-500/40 bg-amber-500/10 p-5 space-y-4">
          <div>
            <h2 className="text-sm font-semibold text-amber-200">Token for “{created.label}”</h2>
            <p className="text-sm text-amber-100/80 mt-1">
              Copy it now - it is shown only once. Valid for {created.max_uses} agent(s) until {fmt(created.expires_at)}.
            </p>
          </div>
          <div className="flex flex-wrap items-center gap-3">
            <code className="flex-1 min-w-0 break-all rounded-lg bg-gray-900 border border-gray-700 px-3 py-2 text-sm text-white">
              {created.secret}
            </code>
            <CopyButton text={created.secret} />
          </div>
          <div className="text-sm text-gray-300 space-y-2">
            <p>On each host, save the token in a file (for example <code>enrol-token.txt</code>), install the agent,
              then delete the file:</p>
            <p className="text-xs text-gray-400">Windows (Administrator prompt, in the agent folder)</p>
            <code className="block break-all rounded-lg bg-gray-900 border border-gray-700 px-3 py-2 text-xs text-gray-200">{winCmd}</code>
            <p className="text-xs text-gray-400">Linux (in the agent folder)</p>
            <code className="block break-all rounded-lg bg-gray-900 border border-gray-700 px-3 py-2 text-xs text-gray-200">{linuxCmd}</code>
            <p className="text-xs text-gray-400">
              Without --enrol-token-file the installer asks for the token. A host that already has an agent registered
              is refused - remove the old agent first.
            </p>
          </div>
        </div>
      )}

      {/* List */}
      <div className="bg-gray-800 rounded-xl border border-gray-700">
        <div className="flex items-center justify-between px-5 py-4 border-b border-gray-700">
          <h2 className="text-sm font-semibold text-white">Tokens</h2>
          <button onClick={() => refetch()} className="text-gray-400 hover:text-white" title="Refresh">
            <RefreshCw size={16} />
          </button>
        </div>
        {isLoading ? (
          <p className="px-5 py-6 text-sm text-gray-400">Loading…</p>
        ) : tokens.length === 0 ? (
          <p className="px-5 py-6 text-sm text-gray-400">No enrolment tokens yet.</p>
        ) : (
          <div className="overflow-x-auto">
            <table className="w-full text-sm">
              <thead className="text-xs text-gray-400 uppercase">
                <tr className="border-b border-gray-700">
                  <th className="text-left px-5 py-3 font-medium">Label</th>
                  <th className="text-left px-3 py-3 font-medium">Token</th>
                  <th className="text-left px-3 py-3 font-medium">Agents</th>
                  <th className="text-left px-3 py-3 font-medium">Expires</th>
                  <th className="text-left px-3 py-3 font-medium">Created by</th>
                  <th className="text-left px-3 py-3 font-medium">Status</th>
                  <th className="px-5 py-3" />
                </tr>
              </thead>
              <tbody>
                {tokens.map((t) => (
                  <tr key={t.id} className="border-b border-gray-700/60 last:border-0">
                    <td className="px-5 py-3 text-white">{t.label}</td>
                    <td className="px-3 py-3 font-mono text-xs text-gray-300">{t.hint}</td>
                    <td className="px-3 py-3 text-gray-300">{t.use_count} / {t.max_uses}</td>
                    <td className="px-3 py-3 text-gray-300">{fmt(t.expires_at)}</td>
                    <td className="px-3 py-3 text-gray-300">{t.created_by}<div className="text-xs text-gray-500">{fmt(t.created_at)}</div></td>
                    <td className="px-3 py-3">
                      <span className={`px-2 py-0.5 rounded-full text-xs border ${STATE_STYLE[t.state] ?? STATE_STYLE.expired}`}>
                        {STATE_LABEL[t.state] ?? t.state}
                      </span>
                    </td>
                    <td className="px-5 py-3 text-right">
                      {t.state === 'active' && (
                        <button onClick={() => revoke(t)}
                          className="inline-flex items-center gap-1 text-xs text-red-300 hover:text-red-200">
                          <Ban size={13} /> Revoke
                        </button>
                      )}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </div>
    </div>
  );
}
