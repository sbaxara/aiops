// Copyright (c) 2026 Axara Pty Ltd.
// SPDX-License-Identifier: AGPL-3.0-or-later
import React, { useState } from 'react';
import { useQuery } from '@tanstack/react-query';
import { api } from '../services/api';
import { ShieldCheck, Download, RefreshCw, Search, ChevronLeft, ChevronRight } from 'lucide-react';

const PAGE = 50;

// Badge classes for an audit outcome.
const outcomeColor = (o) => ({
  success: 'bg-green-500/20 text-green-300 border-green-500/30',
  denied:  'bg-yellow-500/20 text-yellow-300 border-yellow-500/30',
  failure: 'bg-red-500/20 text-red-300 border-red-500/30',
}[o] ?? 'bg-gray-500/20 text-gray-300 border-gray-500/30');

// Table header cell.
function Th({ children }) {
  return <th className="px-3 py-2 text-xs font-medium text-gray-400 uppercase text-left">{children}</th>;
}

// Admin page: filter, page through, export and verify the audit log.
export default function AuditLog() {
  const [filters, setFilters] = useState({ actor: '', action: '', outcome: '' });
  const [applied, setApplied] = useState({ actor: '', action: '', outcome: '' });
  const [page, setPage] = useState(0);
  const [expanded, setExpanded] = useState(null);
  const [verify, setVerify] = useState(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');

  const cleanFilters = Object.fromEntries(Object.entries(applied).filter(([, v]) => v));
  const params = { ...cleanFilters, limit: PAGE, offset: page * PAGE };

  const { data, isLoading, refetch } = useQuery({
    queryKey: ['audit-events', params],
    queryFn: async () => (await api.get('/audit/events', { params })).data,
  });
  const rows = data?.events ?? [];
  const total = data?.total ?? 0;
  const pages = Math.max(1, Math.ceil(total / PAGE));

  const apply = (e) => { e.preventDefault(); setPage(0); setApplied(filters); };

  const runVerify = async () => {
    setBusy(true); setError('');
    try { setVerify((await api.get('/audit/verify')).data); }
    catch (e) { setError(e.response?.data?.detail ?? 'Verification failed'); }
    finally { setBusy(false); }
  };

  const exportCsv = async () => {
    setBusy(true); setError('');
    try {
      const res = await api.get('/audit/export', { params: cleanFilters, responseType: 'blob' });
      const url = URL.createObjectURL(res.data);
      const a = document.createElement('a');
      a.href = url;
      a.download = `audit-log-${new Date().toISOString().replace(/[:.]/g, '-')}.csv`;
      document.body.appendChild(a); a.click(); a.remove();
      URL.revokeObjectURL(url);
    } catch (e) { setError(e.response?.status === 403 ? 'Admin access required' : 'Export failed'); }
    finally { setBusy(false); }
  };

  return (
    <div className="space-y-5">
      <div className="flex items-center justify-between flex-wrap gap-3">
        <div>
          <h1 className="text-xl font-semibold text-white">Audit Log</h1>
          <p className="text-sm text-gray-400">Tamper-evident record of security-relevant actions.</p>
        </div>
        <div className="flex gap-2">
          <button onClick={runVerify} disabled={busy}
            className="flex items-center gap-2 px-3 py-2 rounded-lg bg-gray-700 hover:bg-gray-600 text-sm text-white disabled:opacity-60">
            <ShieldCheck size={15} /> Verify integrity
          </button>
          <button onClick={exportCsv} disabled={busy}
            className="flex items-center gap-2 px-3 py-2 rounded-lg bg-blue-600 hover:bg-blue-500 text-sm text-white disabled:opacity-60">
            <Download size={15} /> Export CSV
          </button>
          <button onClick={() => refetch()}
            className="flex items-center gap-2 px-3 py-2 rounded-lg bg-gray-700 hover:bg-gray-600 text-sm text-white">
            <RefreshCw size={15} />
          </button>
        </div>
      </div>

      {verify && (
        <div className={`rounded-lg border px-4 py-3 text-sm ${verify.ok
          ? 'border-green-500/30 bg-green-500/10 text-green-300'
          : 'border-red-500/30 bg-red-500/10 text-red-300'}`}>
          {verify.ok
            ? `Chain intact — ${verify.checked} event(s) verified.`
            : `Integrity check FAILED at event #${verify.first_bad_id}: ${verify.reason} (${verify.checked} verified before it).`}
        </div>
      )}
      {error && <div className="rounded-lg border border-red-500/30 bg-red-500/10 px-4 py-3 text-sm text-red-300">{error}</div>}

      <form onSubmit={apply} className="flex flex-wrap gap-2 items-end">
        {['actor', 'action'].map((k) => (
          <input key={k} value={filters[k]} placeholder={k === 'actor' ? 'Actor' : 'Action'}
            onChange={(e) => setFilters({ ...filters, [k]: e.target.value })}
            className="bg-gray-700 border border-gray-600 rounded-lg px-3 py-2 text-sm text-white focus:outline-hidden focus:border-blue-500" />
        ))}
        <select value={filters.outcome} onChange={(e) => setFilters({ ...filters, outcome: e.target.value })}
          className="bg-gray-700 border border-gray-600 rounded-lg px-3 py-2 text-sm text-white">
          <option value="">Any outcome</option>
          <option value="success">success</option>
          <option value="denied">denied</option>
          <option value="failure">failure</option>
        </select>
        <button type="submit" className="flex items-center gap-2 px-3 py-2 rounded-lg bg-gray-700 hover:bg-gray-600 text-sm text-white">
          <Search size={15} /> Filter
        </button>
      </form>

      <div className="bg-gray-800 rounded-xl border border-gray-700 overflow-x-auto">
        <table className="w-full text-sm">
          <thead className="border-b border-gray-700">
            <tr><Th>#</Th><Th>Time (UTC)</Th><Th>Actor</Th><Th>Action</Th><Th>Resource</Th><Th>Outcome</Th><Th>Source IP</Th></tr>
          </thead>
          <tbody>
            {isLoading && <tr><td colSpan={7} className="px-3 py-6 text-center text-gray-400">Loading…</td></tr>}
            {!isLoading && rows.length === 0 && <tr><td colSpan={7} className="px-3 py-6 text-center text-gray-400">No events.</td></tr>}
            {rows.map((r) => (
              <React.Fragment key={r.id}>
                <tr onClick={() => setExpanded(expanded === r.id ? null : r.id)}
                  className="border-b border-gray-700/50 hover:bg-gray-700/40 cursor-pointer">
                  <td className="px-3 py-2 text-gray-500">{r.id}</td>
                  <td className="px-3 py-2 text-gray-300 whitespace-nowrap">{r.ts?.replace('T', ' ').slice(0, 19)}</td>
                  <td className="px-3 py-2 text-white">{r.actor ?? <span className="text-gray-500">{r.actor_type}</span>}</td>
                  <td className="px-3 py-2 text-gray-200 font-mono text-xs">{r.action}</td>
                  <td className="px-3 py-2 text-gray-400 text-xs">{[r.resource_type, r.resource_id].filter(Boolean).join(' / ')}</td>
                  <td className="px-3 py-2">
                    <span className={`px-2 py-0.5 rounded-sm border text-xs ${outcomeColor(r.outcome)}`}>{r.outcome}</span>
                  </td>
                  <td className="px-3 py-2 text-gray-400 text-xs">{r.source_ip}</td>
                </tr>
                {expanded === r.id && (
                  <tr className="border-b border-gray-700/50 bg-gray-900/40">
                    <td colSpan={7} className="px-3 py-3">
                      <pre className="text-xs text-gray-300 whitespace-pre-wrap">{JSON.stringify(
                        { actor_type: r.actor_type, actor_id: r.actor_id, user_agent: r.user_agent,
                          details: r.details, hash: r.hash, prev_hash: r.prev_hash }, null, 2)}</pre>
                    </td>
                  </tr>
                )}
              </React.Fragment>
            ))}
          </tbody>
        </table>
      </div>

      <div className="flex items-center justify-between text-sm text-gray-400">
        <span>{total} event(s)</span>
        <div className="flex items-center gap-2">
          <button disabled={page === 0} onClick={() => setPage(page - 1)}
            className="p-1.5 rounded-sm bg-gray-700 disabled:opacity-40"><ChevronLeft size={15} /></button>
          <span>Page {page + 1} / {pages}</span>
          <button disabled={page + 1 >= pages} onClick={() => setPage(page + 1)}
            className="p-1.5 rounded-sm bg-gray-700 disabled:opacity-40"><ChevronRight size={15} /></button>
        </div>
      </div>
    </div>
  );
}
