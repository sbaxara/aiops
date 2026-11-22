// Copyright (c) 2026 Axara Pty Ltd.
// SPDX-License-Identifier: AGPL-3.0-or-later
// Applications page: applications with their servers, monitors and open incidents, and the
// per-application settings dialog (maintenance windows, plus the edition's tabs).
import React, { useState } from 'react';
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query';
import axios from 'axios';
import { api } from '../services/api';
import { Layers, Server, AlertTriangle, CheckCircle, Search, Settings, X, Save, Trash2, Wrench, Plus } from 'lucide-react';
import { useAuth } from '../context/AuthContext';
import * as edition from '@edition';      // which application settings tabs this edition has

// Fetch all applications with their aggregated counts.
// Returns: Promise resolving to the array from GET /api/v1/config/applications.
const fetchApplications = async () => (await axios.get('/api/v1/config/applications')).data;

// ─── Maintenance Tab ──────────────────────────────────────────────────────────
// Active section: windows with end_time >= now (in_effect or scheduled — labelled).
// Past section:   windows with end_time < now (ended).
// Admins can create (server-or-All + start/end + description + suppress_type)
// and delete each window. Viewers see the listings without any write controls.
function MaintenanceTab({ app, isAdmin }) {
  const queryClient = useQueryClient();
  const [creating,  setCreating]  = useState(false);
  const [err,       setErr]       = useState('');

  // Fetch maintenance windows for this application.
  const { data: windows = { active: [], past: [] }, isLoading } = useQuery({
    queryKey: ['maintenance', app.id],
    queryFn:  () => api.get(`/config/applications/${app.id}/maintenance`).then(r => r.data),
  });

  // Fetch servers for the dropdown — uses the existing /agents/ endpoint which
  // returns all registered servers with their linked application info.
  const { data: servers = [] } = useQuery({
    queryKey: ['servers-list-for-maintenance'],
    queryFn:  () => api.get('/agents/').then(r => r.data),
  });

  const deleteMutation = useMutation({
    mutationFn: (id) => api.delete(`/config/maintenance/${id}`),
    onSuccess:  () => queryClient.invalidateQueries({ queryKey: ['maintenance', app.id] }),
    onError:    (e) => setErr(e.response?.data?.detail ?? 'Delete failed'),
  });

  const fmtDt = (iso) => iso ? new Date(iso).toLocaleString() : '—';

  const stateBadge = (state) => {
    const map = {
      in_effect: { txt: 'In effect',  cls: 'bg-green-500/15 text-green-300'  },
      scheduled: { txt: 'Scheduled',  cls: 'bg-blue-500/15  text-blue-300'   },
      ended:     { txt: 'Ended',      cls: 'bg-gray-600/30  text-gray-400'   },
    };
    const it = map[state] ?? { txt: state, cls: 'bg-gray-600/30 text-gray-400' };
    return <span className={`text-xs px-1.5 py-0.5 rounded-sm ${it.cls}`}>{it.txt}</span>;
  };

  const suppressBadge = (t) => {
    const map = {
      incidents: 'bg-amber-500/15 text-amber-300',
      alerts:    'bg-purple-500/15 text-purple-300',
      both:      'bg-red-500/15 text-red-300',
    };
    return <span className={`text-xs px-1.5 py-0.5 rounded-sm ${map[t] ?? 'bg-gray-600/30 text-gray-300'}`}>{t}</span>;
  };

  const Row = ({ w }) => (
    <div className="bg-gray-700/40 rounded-lg p-3 space-y-1.5">
      <div className="flex items-center justify-between gap-2">
        <div className="flex items-center gap-2 flex-wrap">
          {stateBadge(w.state)}
          {edition.hasAlerting && suppressBadge(w.suppress_type)}
          <span className="text-xs text-gray-400">
            {w.scope === 'all_servers'
              ? 'All servers'
              : (servers.find(s => s.id === w.server_id)?.hostname ?? w.server_id)}
          </span>
        </div>
        {isAdmin && (
          <button
            onClick={() => deleteMutation.mutate(w.id)}
            disabled={deleteMutation.isPending}
            className="p-1 text-gray-400 hover:text-red-400 hover:bg-red-400/10 rounded-sm transition-colors"
            title="Delete maintenance window">
            <Trash2 size={13} />
          </button>
        )}
      </div>
      <div className="text-xs text-gray-300">
        <span className="text-gray-400">Start:</span> {fmtDt(w.start_time)}
        <span className="mx-2 text-gray-600">•</span>
        <span className="text-gray-400">End:</span> {fmtDt(w.end_time)}
      </div>
      {w.description && (
        <p className="text-xs text-gray-400 italic">{w.description}</p>
      )}
    </div>
  );

  return (
    <>
      {err && (
        <div className="bg-red-500/10 border border-red-500/30 text-red-400
                        rounded-lg px-3 py-2 text-xs">{err}</div>
      )}

      {/* Create button (admin only) */}
      {isAdmin && !creating && (
        <button
          onClick={() => { setCreating(true); setErr(''); }}
          className="flex items-center gap-2 px-3 py-2 bg-blue-600 hover:bg-blue-700
                     text-white rounded-lg text-sm transition-colors">
          <Plus size={14} /> Add Maintenance Window
        </button>
      )}

      {/* Inline create form */}
      {isAdmin && creating && (
        <MaintenanceCreateForm
          app={app}
          servers={servers}
          onCancel={() => { setCreating(false); setErr(''); }}
          onDone={() => {
            setCreating(false);
            queryClient.invalidateQueries({ queryKey: ['maintenance', app.id] });
          }}
          setErr={setErr}
        />
      )}

      {/* Active section: in-effect + scheduled */}
      <div>
        <div className="flex items-center gap-2 mb-2">
          <Wrench size={13} className="text-gray-400" />
          <p className="text-xs font-medium text-gray-300">
            Active ({windows.active.length})
          </p>
          <span className="text-[10px] text-gray-500">in effect or scheduled</span>
        </div>
        {isLoading ? (
          <p className="text-xs text-gray-500">Loading…</p>
        ) : windows.active.length === 0 ? (
          <p className="text-xs text-gray-500">No active or scheduled maintenance.</p>
        ) : (
          <div className="space-y-2">{windows.active.map(w => <Row key={w.id} w={w} />)}</div>
        )}
      </div>

      {/* Past section */}
      <div>
        <div className="flex items-center gap-2 mb-2">
          <p className="text-xs font-medium text-gray-300">
            Past ({windows.past.length})
          </p>
        </div>
        {windows.past.length === 0 ? (
          <p className="text-xs text-gray-500">No past maintenance.</p>
        ) : (
          <div className="space-y-2">{windows.past.map(w => <Row key={w.id} w={w} />)}</div>
        )}
      </div>
    </>
  );
}

// Inline form to create a new maintenance window.
function MaintenanceCreateForm({ app, servers, onCancel, onDone, setErr }) {
  // Default to a 1-hour window starting now (rounded down to the minute).
  const nowIso = () => {
    const d = new Date(); d.setSeconds(0, 0);
    // datetime-local needs a "YYYY-MM-DDTHH:MM" string without timezone.
    const pad = n => String(n).padStart(2, '0');
    return `${d.getFullYear()}-${pad(d.getMonth()+1)}-${pad(d.getDate())}T${pad(d.getHours())}:${pad(d.getMinutes())}`;
  };
  const plusHoursIso = (h) => {
    const d = new Date(); d.setSeconds(0, 0); d.setHours(d.getHours() + h);
    const pad = n => String(n).padStart(2, '0');
    return `${d.getFullYear()}-${pad(d.getMonth()+1)}-${pad(d.getDate())}T${pad(d.getHours())}:${pad(d.getMinutes())}`;
  };

  const [serverId,     setServerId]     = useState('');      // '' means All servers
  const [startTime,    setStartTime]    = useState(nowIso());
  const [endTime,      setEndTime]      = useState(plusHoursIso(1));
  const [description,  setDescription]  = useState('');
  const [suppressType, setSuppressType] = useState('both');

  const inputCls = 'w-full bg-gray-900 border border-gray-700 rounded-lg px-3 py-2 '
                 + 'text-sm text-white placeholder-gray-500 focus:outline-hidden focus:border-blue-500';

  const submit = async () => {
    setErr('');
    if (!startTime || !endTime) { setErr('Start and end times are required'); return; }
    if (new Date(endTime) <= new Date(startTime)) {
      setErr('End time must be after start time'); return;
    }
    try {
      await api.post(`/config/applications/${app.id}/maintenance`, {
        application_id: app.id,
        server_id:      serverId || null,         // '' -> null -> all servers
        start_time:     new Date(startTime).toISOString(),
        end_time:       new Date(endTime).toISOString(),
        description,
        suppress_type:  suppressType,
      });
      onDone();
    } catch (e) {
      setErr(e.response?.data?.detail ?? 'Create failed');
    }
  };

  return (
    <div className="bg-gray-700/40 rounded-lg p-3 space-y-3 border border-gray-600">
      <p className="text-xs font-semibold text-gray-300">New Maintenance Window</p>

      <div>
        <label className="block text-xs text-gray-400 mb-1">Server</label>
        <select className={inputCls}
          value={serverId}
          onChange={e => setServerId(e.target.value)}>
          <option value="">All servers ({app.name})</option>
          {servers.map(s => (
            <option key={s.id} value={s.id}>{s.hostname}</option>
          ))}
        </select>
      </div>

      <div className="grid grid-cols-2 gap-2">
        <div>
          <label className="block text-xs text-gray-400 mb-1">Start time</label>
          <input type="datetime-local" className={inputCls}
            value={startTime} onChange={e => setStartTime(e.target.value)} />
        </div>
        <div>
          <label className="block text-xs text-gray-400 mb-1">End time</label>
          <input type="datetime-local" className={inputCls}
            value={endTime} onChange={e => setEndTime(e.target.value)} />
        </div>
      </div>

      {/* Notifications and escalation exist only in the Enterprise edition; without them a
          window suppresses new incidents (stored as 'both', which covers that). */}
      {edition.hasAlerting ? (
        <div>
          <label className="block text-xs text-gray-400 mb-1">Suppress</label>
          <select className={inputCls}
            value={suppressType}
            onChange={e => setSuppressType(e.target.value)}>
            <option value="incidents">Incidents (no new incidents created)</option>
            <option value="alerts">Alerts (notifications & escalation only)</option>
            <option value="both">Both (recommended)</option>
          </select>
        </div>
      ) : (
        <p className="text-xs text-gray-400">No new incidents are created for the selected servers while the window is in effect.</p>
      )}

      <div>
        <label className="block text-xs text-gray-400 mb-1">Description (optional)</label>
        <input type="text" className={inputCls}
          placeholder="e.g. Quarterly OS patching"
          value={description} onChange={e => setDescription(e.target.value)} />
      </div>

      <div className="flex justify-end gap-2 pt-1">
        <button onClick={onCancel}
          className="px-3 py-1.5 bg-gray-700 hover:bg-gray-600 text-gray-300
                     rounded-lg text-xs transition-colors">
          Cancel
        </button>
        <button onClick={submit}
          className="px-3 py-1.5 bg-blue-600 hover:bg-blue-700 text-white
                     rounded-lg text-xs transition-colors">
          Create
        </button>
      </div>
    </div>
  );
}

// App Config Modal
// Tabs: the edition's (Enterprise: ticketing, escalation, notification), then maintenance.
function AppConfigModal({ app, onClose }) {
  const { user } = useAuth();
  const isAdmin  = user?.role === 'admin';
  const ext  = edition.useAppSettings(app, isAdmin);
  const tabs = [...ext.tabs, 'maintenance'];
  const [tab, setTab]   = useState(tabs[0]);
  const [saved,  setSaved]  = useState(false);
  const [errMsg, setErrMsg] = useState('');

  const saveMutation = useMutation({
    mutationFn: () => ext.save(),
    onSuccess: () => {
      setSaved(true);
      setErrMsg('');
      setTimeout(() => setSaved(false), 3000);
    },
    onError: (e) => setErrMsg(e.response?.data?.detail ?? 'Save failed'),
  });

  return (
    <div className="fixed inset-0 bg-black/60 flex items-center justify-center z-50 px-4">
      <div className="bg-gray-800 border border-gray-700 rounded-xl w-full max-w-xl
                      flex flex-col max-h-[90vh]">

        {/* Header */}
        <div className="flex items-center justify-between px-5 py-4 border-b border-gray-700">
          <div>
            <h2 className="text-white font-semibold">{app.name}</h2>
            <p className="text-xs text-gray-400 mt-0.5 capitalize">{tabs.join(' · ')}</p>
          </div>
          <button onClick={onClose} className="text-gray-400 hover:text-white">
            <X size={18} />
          </button>
        </div>

        {/* Tabs */}
        <div className="flex border-b border-gray-700">
          {tabs.map(t => (
            <button key={t} onClick={() => setTab(t)}
              className={`px-5 py-2.5 text-sm font-medium capitalize transition-colors
                ${tab === t ? 'text-white border-b-2 border-blue-500'
                           : 'text-gray-400 hover:text-white'}`}>
              {t}
            </button>
          ))}
        </div>

        {/* Body */}
        <div className="flex-1 overflow-y-auto px-5 py-4 space-y-4">

          {tab !== 'maintenance' && ext.render(tab)}

          {tab === 'maintenance' && (
            <MaintenanceTab app={app} isAdmin={isAdmin} />
          )}
        </div>

        {/* Footer */}
        <div className="px-5 py-4 border-t border-gray-700 flex items-center justify-between gap-3">
          {saved && (
            <span className="text-xs text-green-400">Saved successfully.</span>
          )}
          {errMsg && (
            <span className="text-xs text-red-400">{errMsg}</span>
          )}
          {!saved && !errMsg && <span />}
          <div className="flex gap-2">
            <button onClick={onClose}
              className="px-3 py-2 bg-gray-700 hover:bg-gray-600 text-gray-300
                         rounded-lg text-sm transition-colors">
              Cancel
            </button>
            {tab !== 'maintenance' && (
              isAdmin && ext.save && (
                <button onClick={() => saveMutation.mutate()}
                  disabled={saveMutation.isPending}
                  className="flex items-center gap-1.5 px-3 py-2 bg-blue-600 hover:bg-blue-700
                             disabled:opacity-50 text-white rounded-lg text-sm transition-colors">
                  <Save size={13} />
                  {saveMutation.isPending ? 'Saving…' : 'Save'}
                </button>
              )
            )}
          </div>
        </div>
      </div>
    </div>
  );
}

// Main page
export default function Applications() {
  const [search, setSearch] = useState('');
  const [editApp, setEditApp] = useState(null);

  const { data: apps = [], isLoading } = useQuery({
    queryKey: ['applications'],
    queryFn: fetchApplications,
    refetchInterval: 60000,
  });

  const filtered = apps.filter(a =>
    a.name?.toLowerCase().includes(search.toLowerCase()) ||
    a.environment?.toLowerCase().includes(search.toLowerCase())
  );

  return (
    <div className="space-y-6">

      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-3xl font-bold text-white-900">Applications</h1>
          <p className="text-gray-400 mt-1">{apps.length} application{apps.length !== 1 ? 's' : ''} registered</p>
        </div>
      </div>

      <div className="relative">
        <Search size={16} className="absolute left-3 top-1/2 -translate-y-1/2 text-gray-400" />
        <input
          type="text"
          placeholder="Search applications..."
          value={search}
          onChange={e => setSearch(e.target.value)}
          className="w-full bg-gray-800 border border-gray-700 rounded-lg pl-9 pr-4 py-2.5
                     text-sm text-white placeholder-gray-500 focus:outline-hidden focus:border-blue-500"
        />
      </div>

      {isLoading ? (
        <div className="flex items-center justify-center h-48">
          <div className="animate-spin rounded-full h-8 w-8 border-b-2 border-blue-500" />
        </div>
      ) : (
        <div className="grid grid-cols-1 md:grid-cols-2 xl:grid-cols-3 gap-4">
          {filtered.map(app => (
            <div key={app.id} className="bg-gray-800 rounded-xl border border-gray-700 p-5 space-y-4">

              {/* App header */}
              <div className="flex items-start justify-between">
                <div className="flex items-center gap-3">
                  <div className="w-9 h-9 bg-blue-600/20 rounded-lg flex items-center justify-center">
                    <Layers size={16} className="text-blue-400" />
                  </div>
                  <div>
                    <p className="text-white font-semibold text-sm">{app.name}</p>
                    <p className="text-xs text-gray-400 capitalize">{app.environment}</p>
                  </div>
                </div>
                <div className="flex items-center gap-2 shrink-0">
                  <button
                    onClick={e => { e.stopPropagation(); setEditApp(app); }}
                    className="p-1 text-gray-500 hover:text-blue-400 hover:bg-blue-400/10 rounded-sm transition-colors"
                    title={`Application settings: ${[...edition.appSettingsTabs, 'maintenance'].join(', ')}`}>
                    <Settings size={14} />
                  </button>
                  {app.open_incidents > 0
                    ? <AlertTriangle size={16} className="text-red-400" />
                    : <CheckCircle   size={16} className="text-green-400" />}
                </div>
              </div>

              {/* Stats */}
              <div className="grid grid-cols-3 gap-2 text-center">
                {[
                  { label: 'Servers',   value: app.server_count   ?? 0 },
                  { label: 'Monitors',  value: app.monitor_count  ?? 0 },
                  { label: 'Incidents', value: app.open_incidents ?? 0 },
                ].map(({ label, value }) => (
                  <div key={label} className="bg-gray-700/50 rounded-lg p-2">
                    <p className="text-white font-bold text-lg">{value}</p>
                    <p className="text-gray-400 text-xs">{label}</p>
                  </div>
                ))}
              </div>

              {/* Assignee */}
              <div className="flex items-center gap-2 text-xs text-gray-400 pt-1 border-t border-gray-700">
                <Server size={12} />
                <span className="truncate">{app.primary_assignee ?? 'No assignee'}</span>
              </div>
            </div>
          ))}

          {filtered.length === 0 && (
            <div className="col-span-3 flex flex-col items-center justify-center h-48 text-gray-400">
              <Layers size={32} className="mb-2 opacity-40" />
              <p className="text-sm">No applications found</p>
            </div>
          )}
        </div>
      )}

      {editApp && (
        <AppConfigModal app={editApp} onClose={() => setEditApp(null)} />
      )}
    </div>
  );
}
