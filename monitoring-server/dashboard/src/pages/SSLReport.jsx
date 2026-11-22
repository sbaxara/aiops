// Copyright (c) 2026 Axara Pty Ltd.
// SPDX-License-Identifier: AGPL-3.0-or-later
import React, { useState, useMemo } from 'react';
import { useQuery } from '@tanstack/react-query';
import { api } from '../services/api';
import { Lock, RefreshCw, ChevronDown, ChevronRight } from 'lucide-react';

// ── Status helpers ────────────────────────────────────────────────────────

import { STATUS_STYLES, StatusBadge } from '../constants';

// Format an ISO timestamp as a UTC date ('—' when empty).
const fmtUtcDate = (iso) => {
  if (!iso) return '—';
  return new Date(iso).toLocaleDateString('en-GB', {
    day: '2-digit', month: 'short', year: 'numeric', timeZone: 'UTC',
  });
};

// Text style for days to expiry (red when expired or within 7 days, amber within 30).
const DAYS_STYLE = (days) => {
  if (days === null || days === undefined) return 'text-gray-500';
  if (days < 0)   return 'text-red-400 font-bold';
  if (days <= 7)  return 'text-red-400 font-semibold';
  if (days <= 30) return 'text-yellow-400 font-semibold';
  return 'text-green-400';
};

// ── Certificate row ───────────────────────────────────────────────────────

function CertRow({ cert }) {
  const noSsl = !cert.ssl;

  if (noSsl) {
    return (
      <tr className="border-b border-gray-700/30 hover:bg-gray-700/20 transition-colors">
        <td className="px-3 py-2.5 text-xs text-gray-400 font-mono">{cert.ip}</td>
        <td className="px-3 py-2.5 text-xs text-gray-300">{cert.port}</td>
        <td className="px-3 py-2.5" colSpan={4}>
          <span className="text-xs text-gray-500 italic">No SSL</span>
        </td>
        <td className="px-3 py-2.5">
          <span className="px-2 py-0.5 rounded-full text-xs border font-medium
                           bg-gray-500/20 text-gray-400 border-gray-500/30">
            No SSL
          </span>
        </td>
        <td className="px-3 py-2.5 text-xs text-gray-500">—</td>
      </tr>
    );
  }

  const days = cert.days_remaining;
  const daysLabel = days === null || days === undefined
    ? '—'
    : days < 0 ? `Expired ${Math.abs(days)}d ago` : `${days}d`;

  return (
    <tr className="border-b border-gray-700/30 hover:bg-gray-700/20 transition-colors">
      <td className="px-3 py-2.5 text-xs text-gray-400 font-mono">{cert.ip}</td>
      <td className="px-3 py-2.5 text-xs text-gray-300 font-medium">{cert.port}</td>
      <td className="px-3 py-2.5 text-xs text-gray-300 max-w-36 truncate" title={cert.subject}>
        {cert.subject ?? '—'}
      </td>
      <td className="px-3 py-2.5 text-xs text-gray-400 max-w-36 truncate" title={cert.issuer}>
        {cert.issuer ?? '—'}
      </td>
      <td className="px-3 py-2.5 text-xs text-gray-400 whitespace-nowrap">
        {fmtUtcDate(cert.valid_from)}
      </td>
      <td className="px-3 py-2.5 text-xs text-gray-400 whitespace-nowrap">
       {fmtUtcDate(cert.valid_to)}
      </td>
      <td className="px-3 py-2.5">
        <StatusBadge
          status={cert.status ?? 'pass'}
          label={cert.error ? 'Handshake Failed' : undefined}
        />
      </td>
      <td className={`px-3 py-2.5 text-xs whitespace-nowrap ${DAYS_STYLE(days)}`}>
        {daysLabel}
      </td>
    </tr>
  );
}

// ── Server section ────────────────────────────────────────────────────────

function ServerSection({ server, filterStatus }) {
  const [expanded, setExpanded] = useState(true);
  const certs = server.certificates ?? [];

  const filtered = useMemo(() => {
    if (!filterStatus) return certs;
    return certs.filter(c => {
      if (filterStatus === 'no_ssl') return !c.ssl;
      return c.ssl && c.status === filterStatus;
    });
  }, [certs, filterStatus]);

  if (filtered.length === 0) return null;

  const summary = server.summary;
  const status  = server.overall_status;

  return (
    <div className="bg-gray-800 rounded-xl border border-gray-700 overflow-hidden">
      {/* Server header */}
      <button
        onClick={() => setExpanded(e => !e)}
        className="w-full flex items-center justify-between px-5 py-3.5
                   hover:bg-gray-700/30 transition-colors"
      >
        <div className="flex items-center gap-3">
          {expanded
            ? <ChevronDown size={14} className="text-gray-400" />
            : <ChevronRight size={14} className="text-gray-400" />}
          <Lock size={15} className="text-teal-400" />
          <span className="text-sm font-semibold text-white">{server.hostname}</span>
          <span className="text-xs text-gray-400">{server.ip_address}</span>
          {status && <StatusBadge status={status} />}
        </div>
        <div className="flex items-center gap-4 text-xs text-gray-400">
          {summary && (
            <>
              <span>{summary.total_ports_scanned} ports</span>
              <span className="text-green-400">{summary.ssl_ports} SSL</span>
              {summary.expired   > 0 && <span className="text-red-400">{summary.expired} expired</span>}
              {summary.critical  > 0 && <span className="text-red-400">{summary.critical} critical</span>}
              {summary.warning   > 0 && <span className="text-yellow-400">{summary.warning} warning</span>}
            </>
          )}
          {server.scanned_at && (
            <span className="text-gray-500">
              {server.scanned_at
                ? new Date(server.scanned_at).toLocaleString()
                : '—'}
            </span>
          )}
        </div>
      </button>

      {/* Certificate table */}
      {expanded && (
        <div className="overflow-x-auto">
          <table className="w-full min-w-max">
            <thead>
              <tr className="border-t border-b border-gray-700 text-left bg-gray-900/40">
                {['IP', 'Port', 'Subject', 'Issuer', 'Valid From', 'Valid To',
                  'Status', 'Days'].map(h => (
                  <th key={h}
                    className="px-3 py-2 text-xs font-medium text-gray-400 uppercase
                               tracking-wide whitespace-nowrap">
                    {h}
                  </th>
                ))}
              </tr>
            </thead>
            <tbody>
              {filtered.map((cert, i) => (
                <CertRow key={`${cert.ip}-${cert.port}-${i}`} cert={cert} />
              ))}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
}

// ── Page ──────────────────────────────────────────────────────────────────

export default function SSLReport() {
  const [filterStatus,   setFilterStatus]   = useState('');
  const [filterHostname, setFilterHostname] = useState('');

  const {
    data,
    isLoading,
    refetch,
  } = useQuery({
    queryKey: ['ssl-report'],
    queryFn:  () => api.get('/ssl-certificates/').then(r => r.data),
    staleTime: 60000,
  });

  const servers = data?.servers ?? [];

  // Unique hostnames for filter dropdown
  const hostnames = useMemo(() => [...new Set(servers.map(s => s.hostname))], [servers]);

  // Summary totals across all servers
  const totals = useMemo(() => {
    const t = { expired:0, critical:0, warning:0, healthy:0, handshake_failed:0 };
    servers.forEach(s => {
      const sm = s.summary;
      if (!sm) return;
      t.expired         += sm.expired         ?? 0;
      t.critical        += sm.critical        ?? 0;
      t.warning         += sm.warning         ?? 0;
      t.healthy         += sm.healthy         ?? 0;
      t.handshake_failed+= sm.handshake_failed?? 0;
    });
    return t;
  }, [servers]);

  const filteredServers = useMemo(() => {
    return servers.filter(s => {
      if (filterHostname && s.hostname !== filterHostname) return false;
      return true;
    });
  }, [servers, filterHostname]);

  const selectCls = `bg-gray-800 border border-gray-700 rounded-lg px-3 py-2
                     text-sm text-gray-300 focus:outline-hidden focus:border-blue-500`;

  return (
    <div className="space-y-6">

      {/* Header */}
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-3xl font-bold text-white">SSL Certificate Report</h1>
          <p className="text-gray-400 mt-1">
            {servers.length} server{servers.length !== 1 ? 's' : ''} scanned
          </p>
        </div>
        <button onClick={() => refetch()}
          className="flex items-center gap-2 px-3 py-2 bg-gray-700 hover:bg-gray-600
                     text-gray-300 rounded-lg text-sm transition-colors">
          <RefreshCw size={14} /> Refresh
        </button>
      </div>

      {/* Summary KPI cards */}
      {!isLoading && servers.length > 0 && (
        <div className="grid grid-cols-2 sm:grid-cols-3 lg:grid-cols-5 gap-3">
          {[
            { label: 'Expired',          value: totals.expired,          color: 'text-red-400',    status: 'fail'    },
            { label: 'Critical (≤7d)',    value: totals.critical,         color: 'text-red-400',    status: 'fail'    },
            { label: 'Warning (≤30d)',    value: totals.warning,          color: 'text-yellow-400', status: 'warning' },
            { label: 'Healthy',          value: totals.healthy,          color: 'text-green-400',  status: 'pass'    },
            { label: 'Handshake Failed', value: totals.handshake_failed, color: 'text-red-400',    status: 'fail'    },
          ].map(card => (
            <button
              key={card.label}
              onClick={() => setFilterStatus(f => f === card.status ? '' : card.status)}
              className={`bg-gray-800 border rounded-xl p-4 text-left transition-colors
                          hover:bg-gray-700/50 cursor-pointer
                          ${filterStatus === card.status
                            ? 'border-blue-500'
                            : 'border-gray-700'}`}
            >
              <p className={`text-2xl font-bold ${card.color}`}>{card.value}</p>
              <p className="text-xs text-gray-400 mt-1">{card.label}</p>
            </button>
          ))}
        </div>
      )}

      {/* Filters */}
      <div className="flex flex-wrap gap-3">
        <select value={filterHostname} onChange={e => setFilterHostname(e.target.value)}
          className={selectCls}>
          <option value="">All Servers</option>
          {hostnames.map(h => <option key={h} value={h}>{h}</option>)}
        </select>

        <select value={filterStatus} onChange={e => setFilterStatus(e.target.value)}
          className={selectCls}>
          <option value="">All Statuses</option>
          <option value="pass">Healthy</option>
          <option value="warning">Warning</option>
          <option value="fail">Fail</option>
          <option value="no_ssl">No SSL</option>
        </select>

        {(filterHostname || filterStatus) && (
          <button
            onClick={() => { setFilterHostname(''); setFilterStatus(''); }}
            className="text-xs text-blue-400 hover:text-blue-300 transition-colors">
            Clear filters
          </button>
        )}
      </div>

      {/* Server sections */}
      {isLoading ? (
        <div className="flex items-center justify-center h-48">
          <div className="animate-spin rounded-full h-8 w-8 border-b-2 border-blue-500" />
        </div>
      ) : filteredServers.length === 0 ? (
        <div className="flex flex-col items-center justify-center h-48 text-gray-400
                        bg-gray-800 rounded-xl border border-gray-700">
          <Lock size={32} className="mb-2 opacity-40" />
          <p className="text-sm">No SSL scan data available</p>
          <p className="text-xs mt-1 text-gray-500">
            Add an SSL monitor to a server to see results here
          </p>
        </div>
      ) : (
        <div className="space-y-4">
          {filteredServers.map(server => (
            <ServerSection
              key={server.server_id}
              server={server}
              filterStatus={filterStatus}
            />
          ))}
        </div>
      )}
    </div>
  );
}
