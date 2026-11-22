// Copyright (c) 2026 Axara Pty Ltd.
// SPDX-License-Identifier: AGPL-3.0-or-later
import React from 'react';
import { useNavigate } from 'react-router-dom';
import {
  Server, CheckCircle, XCircle, AlertTriangle,
  Cpu, MemoryStick, HardDrive, Clock
} from 'lucide-react';

// Icon for a server health status (healthy, failed, else degraded).
function StatusIcon({ status }) {
  if (status === 'healthy')  return <CheckCircle  size={14} className="text-green-400" />;
  if (status === 'failed')   return <XCircle      size={14} className="text-red-400"   />;
  return                            <AlertTriangle size={14} className="text-yellow-400" />;
}

// Percentage bar coloured by metric status.
function MetricBar({ value, status }) {
  const color = {
    pass:    'bg-green-500',
    warning: 'bg-yellow-500',
    fail:    'bg-red-500',
  }[status] ?? 'bg-gray-500';

  return (
    <div className="w-full bg-gray-700 rounded-full h-1.5">
      <div
        className={`h-1.5 rounded-full transition-all ${color}`}
        style={{ width: `${Math.min(value ?? 0, 100)}%` }}
      />
    </div>
  );
}

// Card with one server's status and latest CPU, memory and disk; opens the server.
function ServerCard({ server }) {
  const navigate = useNavigate();
  const m = server.latest_metrics ?? {};

  const borderColor = {
    healthy:  'border-gray-700 hover:border-green-500/50',
    degraded: 'border-yellow-500/40 hover:border-yellow-500/70',
    failed:   'border-red-500/40   hover:border-red-500/70',
  }[server.status] ?? 'border-gray-700';

  return (
    <div
      onClick={() => navigate(`/servers/${server.id}`)}
      className={`bg-gray-800 rounded-xl border ${borderColor}
                  p-4 cursor-pointer transition-all space-y-3`}
    >
      {/* Header */}
      <div className="flex items-start justify-between gap-2">
        <div className="flex items-center gap-2 min-w-0">
          <Server size={15} className="text-gray-400 shrink-0" />
          <div className="min-w-0">
            <p className="text-sm font-semibold text-white truncate">
              {server.hostname}
            </p>
            <p className="text-xs text-gray-400 truncate">
              {server.application?.name ?? 'No application'}
            </p>
          </div>
        </div>
        <StatusIcon status={server.status} />
      </div>

      {/* Metrics */}
      <div className="space-y-2">
        {/* CPU */}
        <div className="flex items-center gap-2">
          <Cpu size={12} className="text-gray-500 shrink-0" />
          <div className="flex-1 min-w-0">
            <MetricBar value={m.cpu?.value} status={m.cpu?.status} />
          </div>
          <span className="text-xs text-gray-400 w-9 text-right shrink-0">
            {m.cpu?.value != null ? `${m.cpu.value}%` : '—'}
          </span>
        </div>

        {/* Memory */}
        <div className="flex items-center gap-2">
          <MemoryStick size={12} className="text-gray-500 shrink-0" />
          <div className="flex-1 min-w-0">
            <MetricBar value={m.memory?.value} status={m.memory?.status} />
          </div>
          <span className="text-xs text-gray-400 w-9 text-right shrink-0">
            {m.memory?.value != null ? `${m.memory.value}%` : '—'}
          </span>
        </div>

        {/* Disk */}
        <div className="flex items-center gap-2">
          <HardDrive size={12} className="text-gray-500 shrink-0" />
          <div className="flex-1 min-w-0">
            <MetricBar value={m.disk?.value} status={m.disk?.status} />
          </div>
          <span className="text-xs text-gray-400 w-9 text-right shrink-0">
            {m.disk?.value != null ? `${m.disk.value}%` : '—'}
          </span>
        </div>
      </div>

      {/* Footer */}
      <div className="flex items-center gap-1 text-xs text-gray-500 pt-1 border-t border-gray-700">
        <Clock size={11} />
        {server.last_heartbeat
          ? new Date(server.last_heartbeat).toLocaleTimeString([], {
              hour: '2-digit', minute: '2-digit'
            })
          : 'Never'}
      </div>
    </div>
  );
}

// Placeholder cards shown while servers load.
function LoadingGrid() {
  return (
    <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 xl:grid-cols-4 gap-4">
      {[1, 2, 3, 4].map(i => (
        <div key={i} className="bg-gray-800 rounded-xl border border-gray-700 p-4 space-y-3 animate-pulse">
          <div className="flex items-center gap-2">
            <div className="w-4 h-4 bg-gray-700 rounded-sm" />
            <div className="flex-1 space-y-1">
              <div className="h-3 bg-gray-700 rounded-sm w-2/3" />
              <div className="h-2.5 bg-gray-700 rounded-sm w-1/2" />
            </div>
          </div>
          <div className="space-y-2">
            <div className="h-1.5 bg-gray-700 rounded-full" />
            <div className="h-1.5 bg-gray-700 rounded-full" />
            <div className="h-1.5 bg-gray-700 rounded-full" />
          </div>
        </div>
      ))}
    </div>
  );
}

// Grid of server health cards (or an empty-state message).
export default function ServerHealthGrid({ servers = [], isLoading = false }) {
  if (isLoading) return <LoadingGrid />;

  if (servers.length === 0) {
    return (
      <div className="flex flex-col items-center justify-center py-16 text-gray-400
                      bg-gray-800 rounded-xl border border-gray-700">
        <Server size={36} className="mb-3 opacity-40" />
        <p className="text-sm font-medium text-white">No servers registered</p>
        <p className="text-xs mt-1">Deploy a monitoring agent to get started</p>
      </div>
    );
  }

  return (
    <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 xl:grid-cols-4 gap-4">
      {servers.map(server => (
        <ServerCard key={server.id} server={server} />
      ))}
    </div>
  );
}
