// Copyright (c) 2026 Axara Pty Ltd.
// SPDX-License-Identifier: AGPL-3.0-or-later
import React from 'react';
import { useNavigate } from 'react-router-dom';
import { AlertTriangle, CheckCircle, Clock, ArrowRight } from 'lucide-react';

/**
 * Coloured severity pill.
 * @param {{severity: string}} props
 * @returns {JSX.Element}
 */
function SeverityBadge({ severity }) {
  const styles = {
    critical: 'bg-red-600 text-white',
    high:     'bg-orange-500 text-white',
    medium:   'bg-yellow-500 text-black',
    low:      'bg-blue-500 text-white',
  };
  return (
    <span className={`px-2 py-0.5 rounded-sm text-xs font-bold ${styles[severity] ?? styles.low}`}>
      {severity?.toUpperCase()}
    </span>
  );
}

/**
 * Coloured incident status pill.
 * @param {{status: string}} props
 * @returns {JSX.Element}
 */
function StatusBadge({ status }) {
  const styles = {
    open:          'bg-red-500/20 text-red-400 border border-red-500/30',
    investigating: 'bg-yellow-500/20 text-yellow-400 border border-yellow-500/30',
    resolved:      'bg-green-500/20 text-green-400 border border-green-500/30',
    closed:        'bg-gray-500/20 text-gray-400 border border-gray-500/30',
  };
  return (
    <span className={`px-2 py-0.5 rounded-full text-xs ${styles[status] ?? styles.open}`}>
      {status}
    </span>
  );
}

/**
 * Placeholder shown when there are no incidents.
 * @returns {JSX.Element}
 */
function EmptyState() {
  return (
    <div className="flex flex-col items-center justify-center py-12 text-gray-400">
      <CheckCircle size={36} className="text-green-400 mb-3" />
      <p className="text-sm font-medium text-white">No incidents</p>
      <p className="text-xs mt-1">All systems are operating normally</p>
    </div>
  );
}

/**
 * Skeleton rows shown while incidents load.
 * @returns {JSX.Element}
 */
function LoadingState() {
  return (
    <div className="space-y-3 p-4">
      {[1, 2, 3].map(i => (
        <div key={i} className="animate-pulse flex gap-3 items-center">
          <div className="w-4 h-4 bg-gray-700 rounded-full" />
          <div className="flex-1 space-y-1.5">
            <div className="h-3 bg-gray-700 rounded-sm w-3/4" />
            <div className="h-2.5 bg-gray-700 rounded-sm w-1/2" />
          </div>
        </div>
      ))}
    </div>
  );
}

/**
 * Compact incident list (dashboard widget): severity, status, title, host, application,
 * ticket reference and age; a row opens the incident.
 * @param {object} props - incidents, loading flag and optional title/limit.
 * @returns {JSX.Element}
 */
export default function IncidentsList({
  incidents = [],
  isLoading = false,
  showViewAll = false,
  maxItems = null,
  title = 'Recent Incidents',
}) {
  const navigate = useNavigate();

  const displayed = maxItems ? incidents.slice(0, maxItems) : incidents;

  return (
    <div className="bg-gray-800 rounded-xl border border-gray-700 overflow-hidden">

      {/* Header */}
      <div className="flex items-center justify-between px-5 py-4 border-b border-gray-700">
        <div className="flex items-center gap-2">
          <AlertTriangle size={16} className="text-red-400" />
          <h2 className="text-sm font-semibold text-white">{title}</h2>
          {incidents.length > 0 && (
            <span className="bg-red-500/20 text-red-400 border border-red-500/30
                             px-2 py-0.5 rounded-full text-xs">
              {incidents.length}
            </span>
          )}
        </div>
        {showViewAll && (
          <button
            onClick={() => navigate('/incidents/')}
            className="flex items-center gap-1 text-xs text-blue-400 hover:text-blue-300
                       transition-colors"
          >
            View all <ArrowRight size={12} />
          </button>
        )}
      </div>

      {/* Body */}
      {isLoading ? (
        <LoadingState />
      ) : displayed.length === 0 ? (
        <EmptyState />
      ) : (
        <ul className="divide-y divide-gray-700/50">
          {displayed.map(incident => (
            <li
              key={incident.id}
              onClick={() => navigate(`/incidents/${incident.id}`)}
              className="flex items-start gap-3 px-5 py-3.5 hover:bg-gray-700/30
                         cursor-pointer transition-colors"
            >
              {/* Severity indicator */}
              <div className={`mt-0.5 w-2 h-2 rounded-full shrink-0
                ${{
                  critical: 'bg-red-500',
                  high:     'bg-orange-500',
                  medium:   'bg-yellow-500',
                  low:      'bg-blue-500',
                }[incident.severity] ?? 'bg-gray-500'}`}
              />

              {/* Content */}
              <div className="flex-1 min-w-0">
                <div className="flex flex-wrap items-center gap-2 mb-1">
                  <SeverityBadge severity={incident.severity} />
                  <StatusBadge   status={incident.status} />
                </div>
                <p className="text-sm text-white font-medium truncate">
                  {incident.title}
                </p>
                <div className="flex items-center gap-3 mt-1 text-xs text-gray-400">
                  <span>{incident.server?.hostname ?? incident.server_hostname ?? 'Unknown server'}</span>
                  <span>·</span>
                  <span>{incident.application?.name ?? incident.application_name ?? 'Unknown app'}</span>
                  {incident.ticket_ref && (
                    <>
                      <span>·</span>
                      <span className="text-blue-400">{incident.ticket_ref}</span>
                    </>
                  )}
                </div>
              </div>

              {/* Timestamp */}
              <div className="flex items-center gap-1 text-xs text-gray-500 shrink-0">
                <Clock size={11} />
                {incident.created_at
                  ? new Date(incident.created_at).toLocaleTimeString([], {
                      hour: '2-digit', minute: '2-digit'
                    })
                  : '—'}
              </div>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}
