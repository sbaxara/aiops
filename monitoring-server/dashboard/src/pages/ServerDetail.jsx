// Copyright (c) 2026 Axara Pty Ltd.
// SPDX-License-Identifier: AGPL-3.0-or-later
import React, { useState } from 'react';
import { useParams, useNavigate } from 'react-router-dom';
import { useQuery, useQueryClient } from '@tanstack/react-query';
import axios from 'axios';
import { ArrowLeft, Server, Cpu, HardDrive, Wifi, Activity, Trash2, Archive } from 'lucide-react';
import MetricsChart from '../components/MetricsChart';
import { useAuth } from '../context/AuthContext';
import { api } from '../services/api';
import * as edition from '@edition';      // extra notes for the Remove agent dialog

// Load one agent.
const fetchServer  = async (id) => (await axios.get(`/api/v1/agents/${id}`)).data;
// Load an agent's recent metrics.
const fetchMetrics = async (id) => (await axios.get(`/api/v1/metrics/recent/${id}`)).data;
// Load an agent's network metrics for the last 6 hours.
const fetchNetMetrics = async (id) => (await axios.get(`/api/v1/metrics/recent/${id}?metric_type=network&hours=6`)).data;

const METRIC_LABELS = { cpu: 'CPU', memory: 'Memory', disk: 'Disk', network: 'Network' };

// Tile with one metric value coloured by status.
function StatCard({ icon: Icon, label, value, unit, status }) {
  const colors = { pass: 'text-green-400', warning: 'text-yellow-400', fail: 'text-red-400' };
  return (
    <div className="bg-gray-800 rounded-xl border border-gray-700 p-4">
      <div className="flex items-center gap-2 mb-3">
        <Icon size={16} className="text-gray-400" />
        <span className="text-xs text-gray-400">{label}</span>
      </div>
      <p className={`text-2xl font-bold ${colors[status] ?? 'text-white'}`}>
        {value}<span className="text-sm font-normal text-gray-400 ml-1">{unit}</span>
      </p>
    </div>
  );
}

// Tile with two related values.
function TwoStatCard({ icon: Icon, label, left, right }) {
  return (
    <div className="bg-gray-800 rounded-xl border border-gray-700 p-4">
      <div className="flex items-center gap-2 mb-3">
        <Icon size={16} className="text-gray-400" />
        <span className="text-xs text-gray-400">{label}</span>
      </div>
      <div className="grid grid-cols-2 gap-2">
        <div>
          <p className="text-xs text-gray-500 mb-0.5">{left.label}</p>
          <p className="text-xl font-bold text-white">
            {left.value}<span className="text-xs font-normal text-gray-400 ml-1">{left.unit}</span>
          </p>
        </div>
        <div>
          <p className="text-xs text-gray-500 mb-0.5">{right.label}</p>
          <p className="text-xl font-bold text-white">
            {right.value}<span className="text-xs font-normal text-gray-400 ml-1">{right.unit}</span>
          </p>
        </div>
      </div>
    </div>
  );
}

// Confirm removing an agent by typing its hostname.
function RemoveAgentDialog({ server, onClose, onRemoved }) {
  const [typed, setTyped] = useState('');
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState(null);
  const remove = async () => {
    setBusy(true); setError(null);
    try {
      const r = await api.delete(`/agents/${server.id}`);
      onRemoved(r.data);
    } catch (err) {
      const d = err.response?.data?.detail;
      setError(typeof d === 'string' ? d : d?.message ?? err.message);
      setBusy(false);
    }
  };
  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/60 px-4">
      <div className="w-full max-w-lg rounded-xl border border-gray-700 bg-gray-800 p-6 space-y-4">
        <h2 className="text-lg font-semibold text-white">Remove agent on {server.hostname}?</h2>
        <ul className="list-disc pl-5 text-sm text-gray-300 space-y-1">
          <li>Its monitors are archived: they stop, and are hidden from monitor lists.</li>
          <li>Its server-side configuration is deleted and its key stops working.</li>
          {edition.removeAgentNotes.map(n => <li key={n}>{n}</li>)}
          <li>Past metrics and incidents are kept for reports.</li>
        </ul>
        <p className="text-sm text-gray-400">
          Use this when the host can no longer run <code>axara-agent uninstall --purge</code> itself.
          The agent software stays on the host until it is uninstalled there.
        </p>
        <label className="block text-sm text-gray-300">
          Type the hostname to confirm
          <input value={typed} onChange={(e) => setTyped(e.target.value)} autoFocus
            className="mt-1 w-full rounded-lg bg-gray-900 border border-gray-700 px-3 py-2 text-white" />
        </label>
        {error && <p className="text-sm text-red-300">{error}</p>}
        <div className="flex justify-end gap-3">
          <button onClick={onClose} className="px-4 py-2 rounded-lg bg-gray-700 hover:bg-gray-600 text-sm text-white">Cancel</button>
          <button onClick={remove} disabled={busy || typed.trim() !== server.hostname}
            className="px-4 py-2 rounded-lg bg-red-600 hover:bg-red-500 disabled:opacity-40 text-sm font-medium text-white">
            {busy ? 'Removing…' : 'Remove agent'}
          </button>
        </div>
      </div>
    </div>
  );
}

// One agent: status, metrics, monitors and the remove action.
export default function ServerDetail() {
  const { id } = useParams();
  const navigate = useNavigate();
  const qc = useQueryClient();
  const { user } = useAuth();
  const [confirmRemove, setConfirmRemove] = useState(false);

  const { data: server,  isLoading: loadingServer  } = useQuery({ queryKey: ['server',  id], queryFn: () => fetchServer(id)  });
  const { data: metrics, isLoading: loadingMetrics } = useQuery({ queryKey: ['metrics', id], queryFn: () => fetchMetrics(id), refetchInterval: 30000 });
  const { data: netMetrics } = useQuery({ queryKey: ['net-metrics', id], queryFn: () => fetchNetMetrics(id), refetchInterval: 30000 });

  if (loadingServer) return (
    <div className="flex items-center justify-center h-64">
      <div className="animate-spin rounded-full h-8 w-8 border-b-2 border-blue-500" />
    </div>
  );

  const latest = metrics?.latest ?? {};

  // Compute TX and RX rates from consecutive cumulative GB counter readings.
  // bytes_recv_gb / bytes_sent_gb are totals since boot — the delta between
  // two consecutive readings divided by elapsed seconds is the true rate.
  const netRaw = netMetrics?.metrics ?? [];
  const computeNetRate = () => {
    if (netRaw.length < 2) return null;
    const curr = netRaw[0];
    const prev = netRaw[1];
    const dt   = (new Date(curr.timestamp) - new Date(prev.timestamp)) / 1000;
    if (dt <= 0) return null;
    const recvDelta = ((curr.details?.bytes_recv_gb ?? 0) - (prev.details?.bytes_recv_gb ?? 0)) * 1024; // MB
    const sentDelta = ((curr.details?.bytes_sent_gb ?? 0) - (prev.details?.bytes_sent_gb ?? 0)) * 1024;
    return {
      rxKbps: Math.max(0, recvDelta / dt * 1024),
      txKbps: Math.max(0, sentDelta / dt * 1024),
      rxGb:   curr.details?.bytes_recv_gb ?? null,
      txGb:   curr.details?.bytes_sent_gb ?? null,
    };
  };
  const netRate = computeNetRate();

  // Format a KB/s value: show MB/s above 1024, else KB/s
  const fmtKbps = (kbps) => {
    if (kbps === null || kbps === undefined) return { value: '—', unit: '' };
    if (kbps >= 1024) return { value: (kbps / 1024).toFixed(2), unit: 'MB/s' };
    return             { value: kbps.toFixed(1),               unit: 'KB/s' };
  };
  const txFmt = fmtKbps(netRate?.txKbps);
  const rxFmt = fmtKbps(netRate?.rxKbps);

  return (
    <div className="space-y-6 max-w-6xl mx-auto">

      {/* Header */}
      <div className="flex items-center gap-4">
        <button onClick={() => navigate('/servers')} className="text-gray-400 hover:text-white">
          <ArrowLeft size={20} />
        </button>
        <Server size={20} className="text-blue-400" />
        <div className="flex-1">
          <h1 className="text-xl font-bold text-white">{server?.hostname}</h1>
          <p className="text-sm text-gray-400">{server?.ip_address} · {server?.os_type}</p>
        </div>
        {user?.role === 'admin' && server && server.status !== 'decommissioned' && (
          <button onClick={() => setConfirmRemove(true)}
            className="inline-flex items-center gap-2 px-3 py-2 rounded-lg border border-red-500/40 text-sm text-red-300 hover:bg-red-500/10">
            <Trash2 size={15} /> Remove agent
          </button>
        )}
      </div>

      {server?.status === 'decommissioned' && (
        <div className="flex items-start gap-3 rounded-xl border border-gray-600 bg-gray-800/60 px-4 py-3 text-sm text-gray-300">
          <Archive size={16} className="mt-0.5 text-gray-400" />
          <div>
            This agent was removed{server.decommissioned_at ? ` on ${new Date(server.decommissioned_at).toLocaleString()}` : ''}
            {server.decommissioned_by ? ` by ${server.decommissioned_by}` : ''}. Its monitors are archived; its past
            metrics and incidents are kept for reports.
          </div>
        </div>
      )}

      {confirmRemove && server && (
        <RemoveAgentDialog
          server={server}
          onClose={() => setConfirmRemove(false)}
          onRemoved={() => {
            setConfirmRemove(false);
            qc.invalidateQueries({ queryKey: ['servers'] });
            qc.invalidateQueries({ queryKey: ['server', id] });
            navigate('/servers');
          }}
        />
      )}

      {/* Stat cards */}
      <div className="grid grid-cols-2 lg:grid-cols-4 gap-4">
        <StatCard icon={Cpu}      label="CPU Usage"    value={latest.cpu    ?? '—'} unit="%" status={latest.cpu_status} />
        <StatCard icon={Activity} label="Memory Usage" value={latest.memory ?? '—'} unit="%" status={latest.memory_status} />
        <StatCard icon={HardDrive}label="Disk Usage"   value={latest.disk   ?? '—'} unit="%" status={latest.disk_status} />
        <TwoStatCard
          icon={Wifi}
          label="Network throughput (current interval)"
          left={{  label: '↑ TX', value: txFmt.value, unit: txFmt.unit }}
          right={{ label: '↓ RX', value: rxFmt.value, unit: rxFmt.unit }}
        />
      </div>

      {/* Metrics chart */}
      <div className="bg-gray-800 rounded-xl border border-gray-700 p-5">
        <h2 className="text-sm font-semibold text-white mb-4">Metrics History (1h)</h2>
        <div className="grid grid-cols-1 lg:grid-cols-2 gap-6">
          {['cpu', 'memory', 'disk', 'network'].map(type => (
            <div key={type}>
              <p className="text-xs font-medium text-gray-400 mb-2">{METRIC_LABELS[type] ?? type}</p>
              <MetricsChart metricType={type} serverId={id} hours={1} />
            </div>
          ))}
        </div>
      </div>

      {/* Server info */}
      <div className="bg-gray-800 rounded-xl border border-gray-700 p-5">
        <h2 className="text-sm font-semibold text-white mb-4">Server Details</h2>
        <dl className="grid grid-cols-2 gap-4 text-sm">
          {[
            ['Application',    server?.application?.name],
            ['Environment',    server?.application?.environment],
            ['OS Version',     server?.os_version],
            ['Agent Version',  server?.agent_version],
            ['Registered At',  server?.created_at ? new Date(server.created_at).toLocaleString() : '—'],
            ['Last Heartbeat', server?.last_heartbeat ? new Date(server.last_heartbeat).toLocaleString() : '—'],
            ['Total TX (since boot)', netRate?.txGb != null ? `${netRate.txGb.toFixed(1)} GB` : '—'],
            ['Total RX (since boot)', netRate?.rxGb != null ? `${netRate.rxGb.toFixed(1)} GB` : '—'],
          ].map(([label, value]) => (
            <div key={label}>
              <dt className="text-gray-400">{label}</dt>
              <dd className="text-white mt-0.5">{value ?? '—'}</dd>
            </div>
          ))}
        </dl>
      </div>
    </div>
  );
}
