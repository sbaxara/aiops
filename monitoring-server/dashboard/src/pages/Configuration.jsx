// Copyright (c) 2026 Axara Pty Ltd.
// SPDX-License-Identifier: AGPL-3.0-or-later
import React, { useState } from 'react';
import { useEffect } from 'react';
import { useQuery } from '@tanstack/react-query';
import axios from 'axios';
import { Settings, Save, RefreshCw, AlertTriangle } from 'lucide-react';
import { useAuth } from '../context/AuthContext';
import * as edition from '@edition';      // extra Settings sections of the edition

// Load the server configuration.
const fetchConfig = async () => (await axios.get('/api/v1/config/server')).data;

// Titled settings card.
function Section({ title, children }) {
  return (
    <div className="bg-gray-800 rounded-xl border border-gray-700">
      <div className="px-5 py-4 border-b border-gray-700">
        <h2 className="text-sm font-semibold text-white">{title}</h2>
      </div>
      <div className="px-5 py-4 space-y-4">{children}</div>
    </div>
  );
}

// Hint for a setting that comes from monitoring-server/.env (read-only here).
const ENV_HINT = (v) => `Set by ${v} in monitoring-server/.env - change it there and restart the server.`;

// Labelled input with an optional hint.
function Field({ label, value, onChange, type = 'text', hint, disabled = false }) {
  return (
    <div>
      <label className="block text-xs font-medium text-gray-300 mb-1.5">{label}</label>
      <input
        type={type}
        value={value ?? ''}
        onChange={e => onChange(e.target.value)}
        disabled={disabled}
        className="w-full bg-gray-700 border border-gray-600 rounded-lg px-3 py-2.5
                   text-sm text-white focus:outline-hidden focus:border-blue-500
                   disabled:opacity-60 disabled:cursor-not-allowed"
      />
      {hint && <p className="text-xs text-gray-500 mt-1">{hint}</p>}
    </div>
  );
}

// Comma-separated list input stored as an array (edited as free text, split on save).
function AllowlistField({ label, value, onChange, hint, disabled = false }) {
  // Stored value is an array. Keep a local string so the user can type commas
  // and spaces freely. The old field derived its value from the array on every
  // keystroke; a trailing comma became an empty element that filter(Boolean)
  // dropped before it could render, so commas were impossible to type.
  const [text, setText] = React.useState(() => (value || []).join(', '));
  const [focused, setFocused] = React.useState(false);
  // Resync from the array when it changes externally (config load/refetch),
  // but never while focused or we'd clobber in-progress typing.
  React.useEffect(() => {
    if (!focused) setText((value || []).join(', '));
  }, [value, focused]);
  const parse = s => s.split(',').map(x => x.trim()).filter(Boolean);
  return (
    <div>
      <label className="block text-xs font-medium text-gray-300 mb-1.5">{label}</label>
      <input
        type="text"
        value={text}
        onChange={e => { setText(e.target.value); onChange(parse(e.target.value)); }}
        onFocus={() => setFocused(true)}
        onBlur={() => setFocused(false)}
        disabled={disabled}
        className="w-full bg-gray-700 border border-gray-600 rounded-lg px-3 py-2.5
                   text-sm text-white focus:outline-hidden focus:border-blue-500
                   disabled:opacity-60 disabled:cursor-not-allowed"
      />
      {hint && <p className="text-xs text-gray-500 mt-1">{hint}</p>}
    </div>
  );
}

// Server configuration page (editable by admins).
export default function Configuration() {
  const { user } = useAuth();
  const isAdmin  = user?.role === 'admin';
  const [saved,   setSaved]   = useState(false);
  const [error,   setError]   = useState('');
  const [config,  setConfig]  = useState(null);

  const { data: configData, isLoading, refetch } = useQuery({
    queryKey: ['server-config'],
    queryFn: fetchConfig,
  });
  
  useEffect(() => {
    if (configData) setConfig(configData);
  }, [configData]);

  // Settings pinned by the server's .env (returned by GET /config/server as _env_locked).
  const envLocked = (name) => (config?._env_locked ?? []).includes(name);

  const set = (section, key, value) => {
    setConfig(prev => ({
      ...prev,
      [section]: { ...prev?.[section], [key]: value }
    }));
  };

  const handleSave = async () => {
    if (!isAdmin) return;
    setError('');
    try {
      await axios.put('/api/v1/config/server', config);
      setSaved(true);
      setTimeout(() => setSaved(false), 3000);
    } catch (err) {
      setError(err.response?.data?.detail ?? 'Failed to save configuration');
    }
  };

  if (isLoading) return (
    <div className="flex items-center justify-center h-64">
      <div className="animate-spin rounded-full h-8 w-8 border-b-2 border-blue-500" />
    </div>
  );

  return (
    <div className="space-y-6 max-w-3xl">

      {/* Header */}
      <div className="flex items-center justify-between">
        <div className="flex items-center gap-3">
          <Settings size={20} className="text-blue-400" />
          <h1 className="text-3xl font-bold text-white">Configuration</h1>
        </div>
        <div className="flex items-center gap-2">
          <button
            onClick={() => refetch()}
            className="flex items-center gap-2 px-3 py-2 bg-gray-700 hover:bg-gray-600
                       text-gray-300 rounded-lg text-sm transition-colors"
          >
            <RefreshCw size={14} /> Reload
          </button>
          {isAdmin && (
            <button
              onClick={handleSave}
              className="flex items-center gap-2 px-4 py-2 bg-blue-600 hover:bg-blue-700
                         text-white rounded-lg text-sm transition-colors"
            >
              <Save size={14} /> Save Changes
            </button>
          )}
        </div>
      </div>

      {/* Feedback */}
      {saved && (
        <div className="bg-green-500/10 border border-green-500/30 text-green-400
                        rounded-lg px-4 py-2.5 text-sm">
          Configuration saved successfully.
        </div>
      )}
      {error && (
        <div className="flex items-center gap-2 bg-red-500/10 border border-red-500/30
                        text-red-400 rounded-lg px-4 py-2.5 text-sm">
          <AlertTriangle size={14} /> {error}
        </div>
      )}

      {/* Incident Settings */}
      <Section title="Incident Management">
        <Field
          label="Deduplication Window (minutes)"
          type="number"
          value={config?.incident?.deduplication_window_minutes}
          onChange={v => set('incident', 'deduplication_window_minutes', Number(v))}
          hint="Incidents with the same key within this window are merged"
          disabled={!isAdmin}
        />
        <Field
          label="Consecutive Failures before Incident"
          type="number"
          value={config?.incident?.consecutive_failures}
          onChange={v => set('incident', 'consecutive_failures', Number(v))}
          hint="Number of consecutive monitor failures required to open an incident"
          disabled={!isAdmin}
        />
        <div className="flex items-center gap-3">
          <input
            type="checkbox"
            id="auto_resolve"
            checked={config?.incident?.auto_resolve_enabled ?? true}
            onChange={e => set('incident', 'auto_resolve_enabled', e.target.checked)}
            disabled={!isAdmin}
            className="w-4 h-4 accent-blue-600 disabled:opacity-60 disabled:cursor-not-allowed"
          />
          <label htmlFor="auto_resolve" className="text-sm text-gray-300">
            Enable auto-resolution
          </label>
        </div>
      </Section>

      {/* Retention */}
      <Section title="Data Retention">
        <div className="grid grid-cols-3 gap-4">
          {[
            { key: 'metrics_days',   label: 'Metrics (days)'   },
            { key: 'incidents_days', label: 'Incidents (days)' },
            { key: 'logs_days',      label: 'Logs (days)'      },
          ].map(({ key, label }) => (
            <Field
              key={key}
              label={label}
              type="number"
              value={config?.retention?.[key]}
              onChange={v => set('retention', key, Number(v))}
              disabled={!isAdmin}
        />
          ))}
        </div>
      </Section>

      {/* LLM */}
      <Section title="LLM / AI Settings">
        <Field
          label="Ollama URL"
          value={config?.llm?.base_url}
          onChange={v => set('llm', 'base_url', v)}
          hint={envLocked('llm.base_url') ? ENV_HINT('OLLAMA_URL') : undefined}
          disabled={!isAdmin || envLocked('llm.base_url')}
        />
        <Field
          label="Model Name"
          value={config?.llm?.model}
          onChange={v => set('llm', 'model', v)}
          hint="e.g. gemma4:26b"
          disabled={!isAdmin}
        />
        <Field
          label="LLM Request Timeout (seconds)"
          type="number"
          value={config?.llm?.timeout}
          onChange={v => set('llm', 'timeout', Number(v))}
          hint="Maximum seconds to wait for Ollama to respond"
          disabled={!isAdmin}
        />
        <Field
          label="RAG Top-K Results"
          type="number"
          value={config?.rag?.top_k}
          onChange={v => set('rag', 'top_k', Number(v))}
          hint="Number of similar historical incidents to retrieve"
          disabled={!isAdmin}
        />
        <Field
          label="RAG Similarity Threshold"
          type="number"
          value={config?.rag?.similarity_threshold}
          onChange={v => set('rag', 'similarity_threshold', Number(v))}
          hint="Minimum similarity score 0–1 for a historical incident to be included"
          disabled={!isAdmin}
        />
        <Field
          label="ChromaDB URL"
          value={config?.rag?.chroma_url}
          onChange={v => set('rag', 'chroma_url', v)}
          hint={envLocked('rag.chroma_url') ? ENV_HINT('CHROMA_URL') : undefined}
          disabled={!isAdmin || envLocked('rag.chroma_url')}
        />
      </Section>

      {/* Edition settings sections */}
      {edition.SettingsSections && (
        <edition.SettingsSections config={config} set={set} isAdmin={isAdmin}
                                  Section={Section} Field={Field} AllowlistField={AllowlistField} />
      )}
    </div>
  );
}
