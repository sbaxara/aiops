// Copyright (c) 2026 Axara Pty Ltd.
// SPDX-License-Identifier: AGPL-3.0-or-later
import React, { useState, useMemo } from 'react';
import { useQuery } from '@tanstack/react-query';
import { api } from '../services/api';
import { BarChart2, RefreshCw } from 'lucide-react';

import { TYPE_LABELS, TypeBadge, StatusBadge } from '../constants';

const TIME_RANGES = [
  { label: 'All time',  value: 0    },
  { label: 'Last 1h',   value: 60   },
  { label: 'Last 6h',   value: 360  },
  { label: 'Last 1d',   value: 1440 },
  { label: 'Last 2d',   value: 2880 },
  { label: 'Last 3d',   value: 4320 },
  { label: 'Last 7d',   value: 10080 },
  { label: 'Last 30d',  value: 43200 },
  { label: 'Last 90d',  value: 129600 },  
];

// Report of monitors filtered by application, environment and host.
export default function MonitorReport() {
  const [filterApp,       setFilterApp]       = useState('');
  const [filterEnv,       setFilterEnv]       = useState('');
  const [filterHostname,  setFilterHostname]  = useState('');
  const [filterType,      setFilterType]      = useState('');
  const [filterStatus,    setFilterStatus]    = useState('');
  const [filterTimeRange, setFilterTimeRange] = useState(60);
  const [sortField,       setSortField]       = useState('timestamp');
  const [sortDir,         setSortDir]         = useState('desc'); // most recent first

  const toggleSort = (field) => {
    if (sortField === field) setSortDir(d => d === 'asc' ? 'desc' : 'asc');
    else { setSortField(field); setSortDir('asc'); }
  };

  const SortIcon = ({ field }) => {
    if (sortField !== field) return <span className="ml-1 text-gray-600">⇅</span>;
    return <span className="ml-1 text-blue-400">{sortDir === 'asc' ? '↑' : '↓'}</span>;
  };

  // Servers
  const {
    data: servers = [],
    isLoading: serversLoading,
    refetch: refetchServers,
  } = useQuery({
    queryKey: ['report-servers'],
    queryFn:  () => api.get('/agents/').then(r => r.data),
  });

  // Unique option lists derived from server list
  const applications = useMemo(() => {
    const seen = new Set();
    return servers
      .filter(s => s.application?.name && !seen.has(s.application.name) && seen.add(s.application.name))
      .map(s => s.application.name);
  }, [servers]);

  const environments = useMemo(() => {
    const seen = new Set();
    return servers
      .filter(s => s.application?.environment && !seen.has(s.application.environment) && seen.add(s.application.environment))
      .map(s => s.application.environment);
  }, [servers]);

  // Servers that pass the server-level filters (app / env / hostname)
  const targetServers = useMemo(() => servers.filter(s => {
    if (filterApp      && s.application?.name        !== filterApp)      return false;
    if (filterEnv      && s.application?.environment !== filterEnv)      return false;
    if (filterHostname && s.hostname                 !== filterHostname) return false;
    return true;
  }), [servers, filterApp, filterEnv, filterHostname]);

  // Metrics (parallel fetch for all target servers)
  const {
    data: allMetrics = [],
    isLoading: metricsLoading,
    refetch: refetchMetrics,
    dataUpdatedAt: metricsFetchedAt,
  } = useQuery({
    queryKey: ['report-metrics', targetServers.map(s => s.id), filterTimeRange],
    queryFn: async () => {
      if (targetServers.length === 0) return [];
      const params = filterTimeRange > 0
        ? { hours: filterTimeRange / 60 }   // convert minutes → hours
        : { limit: 10000 };

      const results = await Promise.all(
        targetServers.map(s =>
          api.get(`/metrics/recent/${s.id}`, { params })
            .then(r => (r.data.metrics ?? []).map(m => ({
              ...m,
              hostname:    s.hostname,
              application: s.application?.name        ?? '—',
              environment: s.application?.environment ?? '—',
            })))
            .catch(() => [])
        )
      );
      return results.flat();
    },
    enabled: targetServers.length > 0,
  });

  const isLoading = serversLoading || metricsLoading;

  const refetch = () => { refetchServers(); refetchMetrics(); };

  // Client-side filters (type / status / time range) + sort
  const filtered = useMemo(() => {
    // Measured from when the metrics were fetched (keeps this calculation pure).
    const cutoff = filterTimeRange > 0
      ? new Date(metricsFetchedAt - filterTimeRange * 60 * 1000)
      : null;

    const rows = allMetrics.filter(m => {
      if (filterType   && m.type   !== filterType)   return false;
      if (filterStatus && m.status !== filterStatus) return false;
      if (cutoff && new Date(m.timestamp) < cutoff)  return false;
      return true;
    });

    return [...rows].sort((a, b) => {
      const av = a[sortField] ?? '';
      const bv = b[sortField] ?? '';
      // Numeric sort for value column; lexicographic (with date-aware) for rest
      const cmp = sortField === 'value'
        ? (Number(av) - Number(bv))
        : String(av).localeCompare(String(bv), undefined, { numeric: true });
      return sortDir === 'asc' ? cmp : -cmp;
    });
  }, [allMetrics, metricsFetchedAt, filterType, filterStatus, filterTimeRange, sortField, sortDir]);

  // Render
  return (
    <div className="space-y-6">

      {/* Header */}
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-3xl font-bold text-white">Monitor Report</h1>
          <p className="text-gray-400 mt-1">
            {filtered.length} result{filtered.length !== 1 ? 's' : ''}
            {allMetrics.length !== filtered.length &&
              ` (filtered from ${allMetrics.length})`}
          </p>
        </div>
        <button onClick={refetch}
          className="flex items-center gap-2 px-3 py-2 bg-gray-700 hover:bg-gray-600
                     text-gray-300 rounded-lg text-sm transition-colors">
          <RefreshCw size={14} /> Refresh
        </button>
      </div>

      {/* Filters */}
      <div className="flex flex-wrap gap-3">

        <select value={filterApp} onChange={e => setFilterApp(e.target.value)}
          className="bg-gray-800 border border-gray-700 rounded-lg px-3 py-2
                     text-sm text-gray-300 focus:outline-hidden focus:border-blue-500">
          <option value="">All Applications</option>
          {applications.map(a => <option key={a} value={a}>{a}</option>)}
        </select>

        <select value={filterEnv} onChange={e => setFilterEnv(e.target.value)}
          className="bg-gray-800 border border-gray-700 rounded-lg px-3 py-2
                     text-sm text-gray-300 focus:outline-hidden focus:border-blue-500">
          <option value="">All Environments</option>
          {environments.map(e => <option key={e} value={e}>{e}</option>)}
        </select>

        <select value={filterHostname} onChange={e => setFilterHostname(e.target.value)}
          className="bg-gray-800 border border-gray-700 rounded-lg px-3 py-2
                     text-sm text-gray-300 focus:outline-hidden focus:border-blue-500">
          <option value="">All Hosts</option>
          {servers.map(s => (
            <option key={s.id} value={s.hostname}>{s.hostname}</option>
          ))}
        </select>

        <select value={filterType} onChange={e => setFilterType(e.target.value)}
          className="bg-gray-800 border border-gray-700 rounded-lg px-3 py-2
                     text-sm text-gray-300 focus:outline-hidden focus:border-blue-500">
          <option value="">All Types</option>
          {Object.entries(TYPE_LABELS).map(([v, l]) => (
            <option key={v} value={v}>{l}</option>
          ))}
        </select>

        <select value={filterStatus} onChange={e => setFilterStatus(e.target.value)}
          className="bg-gray-800 border border-gray-700 rounded-lg px-3 py-2
                     text-sm text-gray-300 focus:outline-hidden focus:border-blue-500">
          <option value="">All Statuses</option>
          <option value="pass">Pass</option>
          <option value="warning">Warning</option>
          <option value="fail">Fail</option>
          <option value="error">Error</option>
        </select>

        <select value={filterTimeRange}
          onChange={e => setFilterTimeRange(Number(e.target.value))}
          className="bg-gray-800 border border-gray-700 rounded-lg px-3 py-2
                     text-sm text-gray-300 focus:outline-hidden focus:border-blue-500">
          {TIME_RANGES.map(t => (
            <option key={t.value} value={t.value}>{t.label}</option>
          ))}
        </select>
      </div>

      {/* Table */}
      <div className="bg-gray-800 rounded-xl border border-gray-700 overflow-hidden">
        {isLoading ? (
          <div className="flex items-center justify-center h-48">
            <div className="animate-spin rounded-full h-8 w-8 border-b-2 border-blue-500" />
          </div>
        ) : filtered.length === 0 ? (
          <div className="flex flex-col items-center justify-center h-48 text-gray-400">
            <BarChart2 size={32} className="mb-2 opacity-40" />
            <p className="text-sm">No results found</p>
          </div>
        ) : (
          <div className="overflow-x-auto">
            <table className="w-full">
              <thead>
                <tr className="border-b border-gray-700 text-left">
                  {[['id','ID'],['hostname','Host'],['application','Application'],
                    ['environment','Environment'],['type','Type'],['monitor_name','Monitor Name'],
                    ['value','Value'],['status','Status'],['timestamp','Timestamp']
                  ].map(([field, label]) => (
                    <th key={field}
                      onClick={() => toggleSort(field)}
                      className="px-4 py-3 text-xs font-medium text-gray-400 uppercase
                                 tracking-wide whitespace-nowrap cursor-pointer
                                 hover:text-white select-none">
                      {label}<SortIcon field={field} />
                    </th>
                  ))}
                </tr>
              </thead>
              <tbody>
                {filtered.map(m => (
                  <tr key={`${m.id}-${m.hostname}`}
                    className="border-b border-gray-700/50 hover:bg-gray-700/30
                               transition-colors last:border-0">
                    <td className="px-4 py-3 text-xs text-gray-500 font-mono">
                      {m.id}
                    </td>
                    <td className="px-4 py-3 text-sm text-gray-300">
                      {m.hostname}
                    </td>
                    <td className="px-4 py-3 text-sm text-gray-300">
                      {m.application}
                    </td>
                    <td className="px-4 py-3 text-sm text-gray-300 capitalize">
                      {m.environment}
                    </td>
                    <td className="px-4 py-3">
                      <TypeBadge type={m.type} />
                    </td>
                    <td className="px-4 py-3 text-sm text-gray-300">
                      {m.monitor_name || '—'}
                    </td>
                    <td className="px-4 py-3 text-sm text-white font-medium tabular-nums">
                      {m.value}
                    </td>
                    <td className="px-4 py-3">
                      <StatusBadge status={m.status} />
                    </td>
                    <td className="px-4 py-3 text-xs text-gray-400 whitespace-nowrap">
                      {new Date(m.timestamp).toLocaleString()}
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
