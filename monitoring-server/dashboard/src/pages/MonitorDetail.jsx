// Copyright (c) 2026 Axara Pty Ltd.
// SPDX-License-Identifier: AGPL-3.0-or-later
import React, { useState } from 'react';
import { useParams, useNavigate } from 'react-router-dom';
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query';
import { useAuth } from '../context/AuthContext';
import { api } from '../services/api';
import {
  ArrowLeft, Edit, Trash2, CheckCircle, XCircle,
  AlertTriangle, Shield, Clock, RefreshCw
} from 'lucide-react';

import { TYPE_LABELS, SEVERITY_COLORS } from '../constants';
import * as edition from '@edition';      // monitor types and cards the edition adds

// Titled card.
function Card({ title, children }) {
  return (
    <div className="bg-gray-800 rounded-xl border border-gray-700">
      <div className="px-5 py-4 border-b border-gray-700">
        <h2 className="text-sm font-semibold text-white">{title}</h2>
      </div>
      <div className="px-5 py-4 space-y-3">{children}</div>
    </div>
  );
}

// Label and value row.
function Row({ label, value }) {
  return (
    <div className="flex items-start justify-between gap-4 py-1.5
                    border-b border-gray-700/50 last:border-0">
      <span className="text-xs text-gray-400 shrink-0 w-40">{label}</span>
      <span className="text-sm text-white text-right break-all">{value ?? '—'}</span>
    </div>
  );
}

// Pretty-printed JSON ('None' when empty).
function JsonBlock({ data }) {
  if (!data || Object.keys(data).length === 0) return <span className="text-gray-500 text-sm">None</span>;
  return (
    <pre className="bg-gray-900 rounded-lg p-3 text-xs text-gray-300 overflow-x-auto">
      {JSON.stringify(data, null, 2)}
    </pre>
  );
}

// One monitor's settings, state and recent results; enable, disable or delete it.
export default function MonitorDetail() {
  const { id }        = useParams();
  const navigate      = useNavigate();
  const queryClient   = useQueryClient();
  const { user }      = useAuth();
  const isAdmin       = user?.role === 'admin';
  const [showDelete,  setShowDelete]  = useState(false);
  const [hardDelete,  setHardDelete]  = useState(false);
  const [deleteError, setDeleteError] = useState('');

  const { data: monitor, isLoading, isError } = useQuery({
    queryKey: ['monitor-config', id],
    queryFn:  () => api.get(`/config/monitor-configs/${id}`).then(r => r.data),
  });

  // Monitors whose cadence the server sets (edition types) show a reduced view, with no
  // thresholds or type-specific settings.
  const isFixedCadence = monitor && edition.fixedCadenceMonitorTypes.includes(monitor.type);

  const deleteMutation = useMutation({
    mutationFn: () =>
      api.delete(`/config/monitor-configs/${id}${hardDelete ? '?hard=true' : ''}`),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['monitor-configs'] });
      navigate('/monitors');
    },
    onError: (err) => setDeleteError(err.response?.data?.detail ?? 'Delete failed'),
  });

  if (isLoading) return (
    <div className="flex items-center justify-center h-64">
      <div className="animate-spin rounded-full h-8 w-8 border-b-2 border-blue-500" />
    </div>
  );

  if (isError || !monitor) return (
    <div className="flex flex-col items-center justify-center h-64 gap-3 text-gray-400">
      <XCircle size={40} className="text-red-400" />
      <p>Monitor not found.</p>
      <button onClick={() => navigate('/monitors')}
        className="text-blue-400 hover:underline text-sm">Back to Monitors</button>
    </div>
  );

  return (
    <div className="max-w-4xl mx-auto space-y-6">

      {/* Header */}
      <div className="flex items-start justify-between gap-4">
        <div className="flex items-start gap-4">
          <button onClick={() => navigate('/monitors')}
            className="mt-1 text-gray-400 hover:text-white transition-colors">
            <ArrowLeft size={20} />
          </button>
          <div>
            <div className="flex items-center gap-2 mb-1">
              {monitor.enabled
                ? <CheckCircle size={16} className="text-green-400" />
                : <XCircle     size={16} className="text-gray-500"  />}
              <span className={`px-2 py-0.5 rounded-full text-xs border
                ${SEVERITY_COLORS[monitor.severity] ?? SEVERITY_COLORS.low}`}>
                {monitor.severity?.toUpperCase()}
              </span>
            </div>
            <h1 className="text-xl font-bold text-white">{monitor.name}</h1>
            <p className="text-sm text-gray-400 mt-0.5">
              {TYPE_LABELS[monitor.type] ?? monitor.type}
              {monitor.subtype && ` — ${monitor.subtype}`}
            </p>
          </div>
        </div>
        {isAdmin && (
          <div className="flex items-center gap-2 shrink-0">
            <button onClick={() => navigate(`/monitors/${id}/edit`)}
              className="flex items-center gap-2 px-3 py-2 bg-gray-700 hover:bg-gray-600
                         text-gray-300 rounded-lg text-sm transition-colors">
              <Edit size={14} /> Edit
            </button>
            <button onClick={() => { setShowDelete(true); setDeleteError(''); }}
              className="flex items-center gap-2 px-3 py-2 bg-red-600/20 hover:bg-red-600/30
                         text-red-400 border border-red-500/30 rounded-lg text-sm transition-colors">
              <Trash2 size={14} /> Delete
            </button>
          </div>
        )}
      </div>

      <div className="grid grid-cols-1 lg:grid-cols-2 gap-6">

        {isFixedCadence ? (
          <>
            {/* Reduced configuration view */}
            <Card title="Configuration">
              <Row label="Monitor ID"   value={<span className="font-mono text-xs">{monitor.monitor_config_id}</span>} />
              <Row label="Name"         value={monitor.name} />
              <Row label="Type"         value={TYPE_LABELS[monitor.type] ?? edition.extraMonitorTypes[monitor.type] ?? monitor.type} />
              <Row label="Server"       value={monitor.server_id} />
              <Row label="Application"  value={monitor.application_id} />
              <Row label="Status"       value={monitor.enabled ? 'Enabled' : 'Disabled'} />
              <Row label="Created"      value={monitor.created_at ? new Date(monitor.created_at).toLocaleString() : null} />
              <Row label="Last Updated" value={monitor.updated_at ? new Date(monitor.updated_at).toLocaleString() : null} />
            </Card>

            {edition.MonitorDetailCard && (
              <edition.MonitorDetailCard monitor={monitor} Card={Card} Row={Row} />
            )}
          </>
        ) : (
          <>
            {/* Core settings */}
            <Card title="Configuration">
              <Row label="Monitor ID"         value={<span className="font-mono text-xs">{monitor.monitor_config_id}</span>} />
              <Row label="Type"               value={TYPE_LABELS[monitor.type] ?? monitor.type} />
              <Row label="Status"             value={monitor.enabled ? 'Enabled' : 'Disabled'} />
              <Row label="Severity"           value={monitor.severity} />
              <Row label="Check Interval"     value={`${monitor.check_interval_seconds}s`} />
              <Row label="Consecutive Failures" value={monitor.consecutive_failures} />
              <Row label="Assignee"           value={monitor.assignee_override} />
              <Row label="Created"            value={monitor.created_at ? new Date(monitor.created_at).toLocaleString() : null} />
              <Row label="Last Updated"       value={monitor.updated_at ? new Date(monitor.updated_at).toLocaleString() : null} />
            </Card>

            {/* Type-specific config */}
            <Card title="Type-Specific Settings">
              <JsonBlock data={monitor.config} />
            </Card>

            {/* Thresholds */}
            <Card title="Thresholds">
              <JsonBlock data={monitor.threshold_config} />
            </Card>

            {/* Notification override */}
            <Card title="Notification Override">
              <JsonBlock data={monitor.notification_override} />
            </Card>
          </>
        )}

      </div>

      {/* Delete modal */}
      {showDelete && (
        <div className="fixed inset-0 bg-black/60 flex items-center justify-center z-50 px-4">
          <div className="bg-gray-800 border border-gray-700 rounded-xl p-6 w-full max-w-sm space-y-4">
            <div className="flex items-center gap-3">
              <AlertTriangle size={20} className="text-red-400 shrink-0" />
              <h2 className="text-white font-semibold">Delete Monitor</h2>
            </div>
            <p className="text-sm text-gray-300">
              Are you sure you want to delete <span className="text-white font-medium">
              {monitor.name}</span>?
            </p>
            <label className="flex items-center gap-2 text-sm text-gray-400 cursor-pointer">
              <input type="checkbox" checked={hardDelete}
                onChange={e => setHardDelete(e.target.checked)}
                className="rounded-sm border-gray-600 bg-gray-700" />
              Permanently delete (removes all history)
            </label>
            {deleteError && <p className="text-sm text-red-400">{deleteError}</p>}
            <div className="flex gap-3 pt-1">
              <button onClick={() => setShowDelete(false)}
                className="flex-1 px-4 py-2 bg-gray-700 hover:bg-gray-600
                           text-gray-300 rounded-lg text-sm transition-colors">Cancel</button>
              <button onClick={() => deleteMutation.mutate()}
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
