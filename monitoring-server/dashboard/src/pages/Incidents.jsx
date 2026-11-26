// Copyright (c) 2026 Axara Pty Ltd.
// SPDX-License-Identifier: AGPL-3.0-or-later
import { useState } from 'react';
import { useQuery } from '@tanstack/react-query';
import { Link } from 'react-router-dom';
import { AlertCircle, Clock, ExternalLink, Filter } from 'lucide-react';
import * as edition from '@edition';
import { formatDistanceToNow } from 'date-fns';
import { endpoints } from '../services/api';

// Badges

import { SeverityBadge, IncidentStatusBadge as StatusBadge, ticketToolName } from '../constants';

// Table row

function IncidentRow({ incident }) {
  const severityDot = {
    critical: 'bg-red-500',
    high:     'bg-orange-500',
    medium:   'bg-yellow-500',
    low:      'bg-green-500',
  }[incident.severity?.toLowerCase()] ?? 'bg-gray-500';

  return (
    <tr className="border-b border-gray-700/50 hover:bg-gray-700/30 transition-colors last:border-0">
      <td className="px-4 py-3">
        <div className="flex items-start gap-3">
          <span className={`mt-1.5 w-2 h-2 rounded-full shrink-0 ${severityDot}`} />
          <div className="min-w-0">
            <div className="flex items-center gap-2 flex-wrap">
              <Link
                to={`/incidents/${incident.id}`}
                className="text-sm font-medium text-white hover:text-blue-400 transition-colors"
              >
                {incident.title}
              </Link>
              {edition.IncidentBadges && <edition.IncidentBadges incident={incident} size="sm" />}
            </div>
            <p className="text-xs text-gray-400 mt-0.5">
              {incident.application_name} · {incident.server_hostname}
            </p>
          </div>
        </div>
      </td>
      <td className="px-4 py-3 whitespace-nowrap">
        <SeverityBadge severity={incident.severity} />
      </td>
      <td className="px-4 py-3 whitespace-nowrap">
        <StatusBadge status={incident.status} />
      </td>
      <td className="px-4 py-3 whitespace-nowrap text-sm text-gray-300">
        {incident.assignee || <span className="text-gray-500">Unassigned</span>}
      </td>
      <td className="px-4 py-3 whitespace-nowrap">
        <div className="text-xs text-gray-400">
          <div className="flex items-center gap-1">
            <Clock size={12} />
            {new Date(incident.created_at).toLocaleString()}
          </div>
        </div>
      </td>
      <td className="px-4 py-3 whitespace-nowrap">
        <div className="flex gap-3">
          {incident.ticket_ref && (incident.ticket_url ? (
            <a
              href={incident.ticket_url}
              target="_blank"
              rel="noopener noreferrer"
              title={ticketToolName(incident.ticket_provider)}
              className="flex items-center gap-1 text-xs text-blue-400 hover:text-blue-300 transition-colors"
            >
              {incident.ticket_ref} <ExternalLink size={11} />
            </a>
          ) : (
            <span className="text-xs text-gray-300" title={ticketToolName(incident.ticket_provider)}>
              {incident.ticket_ref}
            </span>
          ))}
          {!incident.ticket_ref && (
            <span className="text-xs text-gray-500">—</span>
          )}
        </div>
      </td>
    </tr>
  );
}

// Page

export default function Incidents() {
  const [filters, setFilters] = useState({
    status:      'all',
    severity:    'all',
    application: 'all',
  });
  const [sortField, setSortField] = useState('created_at');
  const [sortDir,   setSortDir]   = useState('desc'); // most recent first

  const toggleSort = (field) => {
    if (sortField === field) setSortDir(d => d === 'asc' ? 'desc' : 'asc');
    else { setSortField(field); setSortDir('asc'); }
  };

  const SortIcon = ({ field }) => {
    if (sortField !== field) return <span className="ml-1 text-gray-600">⇅</span>;
    return <span className="ml-1 text-blue-400">{sortDir === 'asc' ? '↑' : '↓'}</span>;
  };

  const { data, isLoading } = useQuery({
    queryKey: ['incidents', filters],
    queryFn: async () => {
      const params = new URLSearchParams();
      if (filters.status      !== 'all') params.append('status',         filters.status);
      if (filters.severity    !== 'all') params.append('severity',       filters.severity);
      if (filters.application !== 'all') params.append('application_id', filters.application);
      return endpoints.incidents(params.toString()).then(r => r.data);
    },
    refetchInterval: 30000,
  });

  const { data: applications = [] } = useQuery({
    queryKey: ['applications'],
    queryFn:  () => endpoints.applications().then(r => r.data),
  });

  const incidents = [...(data?.incidents ?? [])].sort((a, b) => {
    const av = a[sortField] ?? '';
    const bv = b[sortField] ?? '';
    const cmp = String(av).localeCompare(String(bv), undefined, { numeric: true });
    return sortDir === 'asc' ? cmp : -cmp;
  });

  const hasFilters =
    filters.status !== 'all' ||
    filters.severity !== 'all' ||
    filters.application !== 'all';

  const selectCls = `bg-gray-800 border border-gray-700 rounded-lg px-3 py-2
                     text-sm text-gray-300 focus:outline-hidden focus:border-blue-500`;

  return (
    <div className="space-y-6">

      {/* Header */}
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-3xl font-bold text-white">Incidents</h1>
          <p className="text-gray-400 mt-1">
            {(data?.total ?? incidents.length).toLocaleString()} incident{(data?.total ?? incidents.length) !== 1 ? 's' : ''}
          </p>
        </div>
      </div>

      {/* Filters */}
      <div className="flex flex-wrap items-center gap-3">
        <Filter size={15} className="text-gray-400 shrink-0" />

        <select value={filters.status}
          onChange={e => setFilters(f => ({ ...f, status: e.target.value }))}
          className={selectCls}>
          <option value="all">All Statuses</option>
          <option value="open">Open</option>
          <option value="in_progress">In Progress</option>
          <option value="resolved">Resolved</option>
          <option value="closed">Closed</option>
        </select>

        <select value={filters.severity}
          onChange={e => setFilters(f => ({ ...f, severity: e.target.value }))}
          className={selectCls}>
          <option value="all">All Severities</option>
          <option value="critical">Critical</option>
          <option value="high">High</option>
          <option value="medium">Medium</option>
          <option value="low">Low</option>
        </select>

        <select value={filters.application}
          onChange={e => setFilters(f => ({ ...f, application: e.target.value }))}
          className={selectCls}>
          <option value="all">All Applications</option>
          {applications.map(app => (
            <option key={app.id} value={app.id}>{app.name}</option>
          ))}
        </select>

        {hasFilters && (
          <button
            onClick={() => setFilters({ status: 'all', severity: 'all', application: 'all' })}
            className="text-xs text-blue-400 hover:text-blue-300 transition-colors">
            Clear filters
          </button>
        )}
      </div>

      {/* Table */}
      <div className="bg-gray-800 rounded-xl border border-gray-700 overflow-hidden">
        {isLoading ? (
          <div className="flex items-center justify-center h-48">
            <div className="animate-spin rounded-full h-8 w-8 border-b-2 border-blue-500" />
          </div>
        ) : incidents.length === 0 ? (
          <div className="flex flex-col items-center justify-center h-48 text-gray-400">
            <AlertCircle size={32} className="mb-2 opacity-40" />
            <p className="text-sm">No incidents found</p>
          </div>
        ) : (
          <div className="overflow-x-auto">
            <table className="w-full">
              <thead>
                <tr className="border-b border-gray-700 text-left">
                  <th className="px-4 py-3 text-xs font-medium text-gray-400 uppercase tracking-wide whitespace-nowrap">
                    Incident
                  </th>
                  {[['severity','Severity'],['status','Status'],
                    ['assignee','Assignee'],['created_at','Created'],
                    ['ticket_ref','Ticket']
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
                {incidents.map(incident => (
                  <IncidentRow key={incident.id} incident={incident} />
                ))}
              </tbody>
            </table>
          </div>
        )}

        {/* Pagination — show count footer when there are more results than displayed */}
        {data?.total > incidents.length && (
          <div className="flex items-center justify-between px-4 py-3 border-t border-gray-700">
            <p className="text-xs text-gray-400">
              Showing {incidents.length} of {data.total.toLocaleString()} incidents
            </p>
          </div>
        )}
      </div>
    </div>
  );
}
