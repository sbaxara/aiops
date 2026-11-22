// Copyright (c) 2026 Axara Pty Ltd.
// SPDX-License-Identifier: AGPL-3.0-or-later
import React, { useState } from 'react';
import { useNavigate } from 'react-router-dom';
import { useQuery } from '@tanstack/react-query';
import axios from 'axios';
import {
  Server, CheckCircle, XCircle, AlertTriangle,
  RefreshCw, Search, Clock
} from 'lucide-react';

// Load all agents.
const fetchServers = async () => {
  const { data } = await axios.get('/api/v1/agents/');
  return data;
};

// Icon for a server status.
function StatusIcon({ status }) {
  if (status === 'healthy')  return <CheckCircle  size={16} className="text-green-400" />;
  if (status === 'failed')   return <XCircle      size={16} className="text-red-400" />;
  return                            <AlertTriangle size={16} className="text-yellow-400" />;
}

// Badge for a server status.
function StatusBadge({ status }) {
  const styles = {
    healthy:  'bg-green-500/20  text-green-400  border-green-500/30',
    degraded: 'bg-yellow-500/20 text-yellow-400 border-yellow-500/30',
    failed:   'bg-red-500/20    text-red-400    border-red-500/30',
    unknown:  'bg-gray-500/20   text-gray-400   border-gray-500/30',
  };
  return (
    <span className={`px-2 py-0.5 rounded-full text-xs border ${styles[status] ?? styles.unknown}`}>
      {status ?? 'unknown'}
    </span>
  );
}

// Agent list with search and sorting.
export default function Servers() {
  const navigate = useNavigate();
  const [search,    setSearch]    = useState('');
  const [sortField,  setSortField]  = useState('last_heartbeat');
  const [sortDir,    setSortDir]    = useState('desc'); // most recent first

  const toggleSort = (field) => {
    if (sortField === field) setSortDir(d => d === 'asc' ? 'desc' : 'asc');
    else { setSortField(field); setSortDir('asc'); }
  };

  const SortIcon = ({ field }) => {
    if (sortField !== field) return <span className="ml-1 text-gray-600">⇅</span>;
    return <span className="ml-1 text-blue-400">{sortDir === 'asc' ? '↑' : '↓'}</span>;
  };

  const { data: servers = [], isLoading, refetch } = useQuery({
    queryKey: ['servers'],
    queryFn: fetchServers,
    refetchInterval: 60000,
  });

  const filtered = servers
    .filter(s =>
      s.hostname?.toLowerCase().includes(search.toLowerCase()) ||
      s.ip_address?.includes(search) ||
      s.application?.name?.toLowerCase().includes(search.toLowerCase())
    )
    .sort((a, b) => {
      // Nested field support: application.name
      const get = (obj, field) =>
        field === 'application' ? (obj.application?.name ?? '') : (obj[field] ?? '');
      const av = get(a, sortField);
      const bv = get(b, sortField);
      const cmp = String(av).localeCompare(String(bv), undefined, { numeric: true });
      return sortDir === 'asc' ? cmp : -cmp;
    });

  return (
    <div className="space-y-6">

      {/* Header */}
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-3xl font-bold text-white">Servers</h1>
          <p className="text-gray-400 mt-1">
            {servers.length} registered agent{servers.length !== 1 ? 's' : ''}
          </p>
        </div>
        <button
          onClick={() => refetch()}
          className="flex items-center gap-2 px-3 py-2 bg-gray-700 hover:bg-gray-600
                     text-gray-300 rounded-lg text-sm transition-colors"
        >
          <RefreshCw size={14} />
          Refresh
        </button>
      </div>

      {/* Search */}
      <div className="relative">
        <Search size={16} className="absolute left-3 top-1/2 -translate-y-1/2 text-gray-400" />
        <input
          type="text"
          placeholder="Search by hostname, IP, or application..."
          value={search}
          onChange={e => setSearch(e.target.value)}
          className="w-full bg-gray-800 border border-gray-700 rounded-lg
                     pl-9 pr-4 py-2.5 text-sm text-white placeholder-gray-500
                     focus:outline-hidden focus:border-blue-500"
        />
      </div>

      {/* Table */}
      <div className="bg-gray-800 rounded-xl border border-gray-700 overflow-hidden">
        {isLoading ? (
          <div className="flex items-center justify-center h-48 text-gray-400">
            <div className="animate-spin rounded-full h-8 w-8 border-b-2 border-blue-500" />
          </div>
        ) : filtered.length === 0 ? (
          <div className="flex flex-col items-center justify-center h-48 text-gray-400">
            <Server size={32} className="mb-2 opacity-40" />
            <p className="text-sm">No servers found</p>
          </div>
        ) : (
          <table className="w-full">
            <thead>
              <tr className="border-b border-gray-700 text-left">
                {[['status','Status'],['hostname','Hostname'],['ip_address','IP Address'],
                  ['os_type','OS'],['application','Application'],
                  ['last_heartbeat','Last Heartbeat'],['agent_version','Agent Version']
                ].map(([field, label]) => (
                  <th key={field}
                    onClick={() => toggleSort(field)}
                    className="px-4 py-3 text-xs font-medium text-gray-400 uppercase
                               cursor-pointer hover:text-white select-none">
                    {label}<SortIcon field={field} />
                  </th>
                ))}
              </tr>
            </thead>
            <tbody>
              {filtered.map(server => (
                <tr
                  key={server.id}
                  onClick={() => navigate(`/servers/${server.id}`)}
                  className="border-b border-gray-700/50 hover:bg-gray-700/30
                             cursor-pointer transition-colors last:border-0"
                >
                  <td className="px-4 py-3">
                    <div className="flex items-center gap-2">
                      <StatusIcon status={server.status} />
                      <StatusBadge status={server.status} />
                    </div>
                  </td>
                  <td className="px-4 py-3 text-sm text-white font-medium">
                    {server.hostname}
                  </td>
                  <td className="px-4 py-3 text-sm text-gray-300">{server.ip_address}</td>
                  <td className="px-4 py-3 text-sm text-gray-300 capitalize">{server.os_type}</td>
                  <td className="px-4 py-3 text-sm text-gray-300">
                    {server.application?.name ?? '—'}
                  </td>
                  <td className="px-4 py-3 text-sm text-gray-400">
                    <div className="flex items-center gap-1">
                      <Clock size={12} />
                      {server.last_heartbeat
                        ? new Date(server.last_heartbeat).toLocaleString()
                        : 'Never'}
                    </div>
                  </td>
                  <td className="px-4 py-3 text-sm text-gray-400">
                    {server.agent_version ?? '—'}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </div>
    </div>
  );
}
