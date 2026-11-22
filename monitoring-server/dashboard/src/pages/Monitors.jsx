// Copyright (c) 2026 Axara Pty Ltd.
// SPDX-License-Identifier: AGPL-3.0-or-later
import React, { useState } from 'react';
import { useNavigate } from 'react-router-dom';
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query';
import { useAuth } from '../context/AuthContext';
import { api } from '../services/api';
import {
  Shield, Plus, Search, RefreshCw, Trash2, Edit,
  CheckCircle, XCircle, AlertTriangle, ChevronDown
} from 'lucide-react';

import { TYPE_LABELS, TypeBadge, SeverityBadge } from '../constants';

// Enabled or disabled icon.
function StatusIcon({ enabled }) {
  return enabled
    ? <CheckCircle size={15} className="text-green-400" />
    : <XCircle     size={15} className="text-gray-500"  />;
}

// Monitor list with search and enable, disable and delete actions.
export default function Monitors() {
  const navigate     = useNavigate();
  const queryClient  = useQueryClient();
  const { user }     = useAuth();
  const isAdmin      = user?.role === 'admin';

  const [search,         setSearch]         = useState('');
  const [filterType,     setFilterType]     = useState('');
  const [filterEnabled,  setFilterEnabled]  = useState('');
  const [deleteTarget,   setDeleteTarget]   = useState(null); // {id, name, hard}
  const [deleteError,    setDeleteError]    = useState('');
  const [sortField,      setSortField]      = useState('updated_at');
  const [sortDir,        setSortDir]        = useState('desc');  // most recent first

  const toggleSort = (field) => {
    if (sortField === field) setSortDir(d => d === 'asc' ? 'desc' : 'asc');
    else { setSortField(field); setSortDir('asc'); }
  };

  const SortIcon = ({ field }) => {
    if (sortField !== field) return <span className="ml-1 text-gray-600">⇅</span>;
    return <span className="ml-1 text-blue-400">{sortDir === 'asc' ? '↑' : '↓'}</span>;
  };

  // Fetch servers for filter dropdown
  const { data: servers = [] } = useQuery({
    queryKey: ['servers-list'],
    queryFn:  () => api.get('/agents/').then(r => r.data),
  });

  const [filterServer, setFilterServer] = useState('');

  // Fetch monitors
  const { data: monitors = [], isLoading, refetch } = useQuery({
    queryKey: ['monitor-configs', filterServer, filterType, filterEnabled],
    queryFn:  () => {
      const params = {};
      if (filterServer)  params.server_id = filterServer;
      if (filterType)    params.type      = filterType;
      if (filterEnabled !== '') params.enabled = filterEnabled === 'true';
      return api.get('/config/monitor-configs', { params }).then(r => r.data);
    },
  });

  const deleteMutation = useMutation({
    mutationFn: ({ id, hard }) =>
      api.delete(`/config/monitor-configs/${id}${hard ? '?hard=true' : ''}`),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['monitor-configs'] });
      setDeleteTarget(null);
      setDeleteError('');
    },
    onError: (err) => {
      setDeleteError(err.response?.data?.detail ?? 'Delete failed');
    },
  });

  // Build a lookup map from server_id → hostname using the already-fetched
  // servers list. This avoids a separate API call and works for the hostname
  // column without changing the monitor-configs endpoint.
  const serverMap = Object.fromEntries(servers.map(s => [s.id, s.hostname]));

  const filtered = monitors
    .filter(m => {
      if (!search) return true;
      const q = search.toLowerCase();
      return (
        m.name?.toLowerCase().includes(q) ||
        m.type?.toLowerCase().includes(q) ||
        m.severity?.toLowerCase().includes(q)
      );
    })
    .sort((a, b) => {
      // 'hostname' is not a direct monitor field — look it up from serverMap
      const get = m => sortField === 'hostname'
        ? (serverMap[m.server_id] ?? '')
        : (m[sortField] ?? '');
      const cmp = String(get(a)).localeCompare(String(get(b)), undefined, { numeric: true });
      return sortDir === 'asc' ? cmp : -cmp;
    });

  return (
    <div className="space-y-6">

      {/* Header */}
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-3xl font-bold text-white">Monitors</h1>
          <p className="text-gray-400 mt-1">
            {monitors.length} monitor{monitors.length !== 1 ? 's' : ''} configured
          </p>
        </div>
        <div className="flex items-center gap-2">
          <button onClick={() => refetch()}
            className="flex items-center gap-2 px-3 py-2 bg-gray-700 hover:bg-gray-600
                       text-gray-300 rounded-lg text-sm transition-colors">
            <RefreshCw size={14} /> Refresh
          </button>
          {isAdmin && (
            <button onClick={() => navigate('/monitors/new')}
              className="flex items-center gap-2 px-3 py-2 bg-blue-600 hover:bg-blue-700
                         text-white rounded-lg text-sm transition-colors">
              <Plus size={14} /> Add Monitor
            </button>
          )}
        </div>
      </div>

      {/* Filters */}
      <div className="flex flex-wrap gap-3">
        {/* Search */}
        <div className="relative flex-1 min-w-48">
          <Search size={15} className="absolute left-3 top-1/2 -translate-y-1/2 text-gray-400" />
          <input type="text" placeholder="Search by name or type..."
            value={search} onChange={e => setSearch(e.target.value)}
            className="w-full bg-gray-800 border border-gray-700 rounded-lg pl-9 pr-4 py-2
                       text-sm text-white placeholder-gray-500
                       focus:outline-hidden focus:border-blue-500" />
        </div>

        {/* Server filter */}
        <select value={filterServer} onChange={e => setFilterServer(e.target.value)}
          className="bg-gray-800 border border-gray-700 rounded-lg px-3 py-2
                     text-sm text-gray-300 focus:outline-hidden focus:border-blue-500">
          <option value="">All Servers</option>
          {servers.map(s => (
            <option key={s.id} value={s.id}>{s.hostname}</option>
          ))}
        </select>

        {/* Type filter */}
        <select value={filterType} onChange={e => setFilterType(e.target.value)}
          className="bg-gray-800 border border-gray-700 rounded-lg px-3 py-2
                     text-sm text-gray-300 focus:outline-hidden focus:border-blue-500">
          <option value="">All Types</option>
          {Object.entries(TYPE_LABELS).map(([v, l]) => (
            <option key={v} value={v}>{l}</option>
          ))}
        </select>

        {/* Status filter */}
        <select value={filterEnabled} onChange={e => setFilterEnabled(e.target.value)}
          className="bg-gray-800 border border-gray-700 rounded-lg px-3 py-2
                     text-sm text-gray-300 focus:outline-hidden focus:border-blue-500">
          <option value="">All Statuses</option>
          <option value="true">Enabled</option>
          <option value="false">Disabled</option>
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
            <Shield size={32} className="mb-2 opacity-40" />
            <p className="text-sm">No monitors found</p>
          </div>
        ) : (
          <table className="w-full">
            <thead>
              <tr className="border-b border-gray-700 text-left">
                <th className="px-4 py-3 text-xs font-medium text-gray-400 uppercase" />
                {[['name','Name'],['hostname','Host'],['type','Type'],['severity','Severity'],
                  ['check_interval_seconds','Interval'],['consecutive_failures','Failures'],
                  ['updated_at','Updated']].map(([field, label]) => (
                  <th key={field}
                    onClick={() => toggleSort(field)}
                    className="px-4 py-3 text-xs font-medium text-gray-400 uppercase
                               cursor-pointer hover:text-white select-none">
                    {label}<SortIcon field={field} />
                  </th>
                ))}
                <th className="px-4 py-3 text-xs font-medium text-gray-400 uppercase" />
              </tr>
            </thead>
            <tbody>
              {filtered.map(m => (
                <tr key={m.monitor_config_id}
                  onClick={() => navigate(`/monitors/${m.monitor_config_id}`)}
                  className="border-b border-gray-700/50 hover:bg-gray-700/30
                             cursor-pointer transition-colors last:border-0">
                  <td className="px-4 py-3">
                    <StatusIcon enabled={m.enabled} />
                  </td>
                  <td className="px-4 py-3">
                    <p className="text-sm font-medium text-white">{m.name}</p>
                    {m.subtype && (
                      <p className="text-xs text-gray-400 mt-0.5">{m.subtype}</p>
                    )}
                  </td>
                  <td className="px-4 py-3 text-sm text-gray-300">
                    {serverMap[m.server_id] ?? '—'}
                  </td>
                  <td className="px-4 py-3"><TypeBadge type={m.type} /></td>
                  <td className="px-4 py-3"><SeverityBadge severity={m.severity} /></td>
                  <td className="px-4 py-3 text-sm text-gray-300">
                    {m.check_interval_seconds}s
                  </td>
                  <td className="px-4 py-3 text-sm text-gray-300">
                    {m.consecutive_failures}
                  </td>
                  <td className="px-4 py-3 text-xs text-gray-400">
                    {m.updated_at ? new Date(m.updated_at).toLocaleString() : '—'}
                  </td>
                  {/* Admin actions */}
                  <td className="px-4 py-3">
                    {isAdmin && (
                      <div className="flex items-center gap-2"
                        onClick={e => e.stopPropagation()}>
                        <button
                          onClick={() => navigate(`/monitors/${m.monitor_config_id}/edit`)}
                          className="p-1.5 text-gray-400 hover:text-blue-400 hover:bg-blue-400/10
                                     rounded transition-colors">
                          <Edit size={14} />
                        </button>
                        <button
                          onClick={() => { setDeleteTarget(m); setDeleteError(''); }}
                          className="p-1.5 text-gray-400 hover:text-red-400 hover:bg-red-400/10
                                     rounded transition-colors">
                          <Trash2 size={14} />
                        </button>
                      </div>
                    )}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </div>

      {/* Delete confirmation modal */}
      {deleteTarget && (
        <div className="fixed inset-0 bg-black/60 flex items-center justify-center z-50 px-4">
          <div className="bg-gray-800 border border-gray-700 rounded-xl p-6 w-full max-w-sm space-y-4">
            <div className="flex items-center gap-3">
              <AlertTriangle size={20} className="text-red-400 shrink-0" />
              <h2 className="text-white font-semibold">Delete Monitor</h2>
            </div>
            <p className="text-sm text-gray-300">
              Are you sure you want to delete <span className="text-white font-medium">
              {deleteTarget.name}</span>?
            </p>
            <label className="flex items-center gap-2 text-sm text-gray-400 cursor-pointer">
              <input type="checkbox"
                checked={deleteTarget.hard ?? false}
                onChange={e => setDeleteTarget(t => ({ ...t, hard: e.target.checked }))}
                className="rounded-sm border-gray-600 bg-gray-700" />
              Permanently delete (removes all history)
            </label>
            {deleteError && (
              <p className="text-sm text-red-400">{deleteError}</p>
            )}
            <div className="flex gap-3 pt-1">
              <button onClick={() => setDeleteTarget(null)}
                className="flex-1 px-4 py-2 bg-gray-700 hover:bg-gray-600
                           text-gray-300 rounded-lg text-sm transition-colors">
                Cancel
              </button>
              <button
                onClick={() => deleteMutation.mutate({
                  id: deleteTarget.monitor_config_id,
                  hard: deleteTarget.hard ?? false,
                })}
                disabled={deleteMutation.isPending}
                className="flex-1 px-4 py-2 bg-red-600 hover:bg-red-700
                           disabled:opacity-50 text-white rounded-lg text-sm transition-colors">
                {deleteMutation.isPending ? 'Deleting...' : 'Delete'}
              </button>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
