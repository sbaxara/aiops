// Copyright (c) 2026 Axara Pty Ltd.
// SPDX-License-Identifier: AGPL-3.0-or-later
import React, { useState, useEffect } from 'react';
import { useParams, useNavigate } from 'react-router-dom';
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query';
import { useAuth } from '../context/AuthContext';
import { api } from '../services/api';
import { ArrowLeft, AlertTriangle, Save, Copy, CheckCircle, X } from 'lucide-react';
import * as edition from '@edition';      // monitor types the edition adds

// ── Type-specific config defaults ────────────────────────────────────────────
const TYPE_CONFIG_DEFAULTS = {
  cpu:      {},
  memory:   {},
  disk:     { ignore_readonly: true },
  network:  {},
  url: {
    url: '', method: 'GET', expected_status: 200,
    expected_body_contains: '', timeout: 10, verify_ssl: true,
  },
  log: {
    log_paths: [],
    include_context_lines: 5,
    patterns: [
      { name: 'exceptions', regex: '(exception|traceback|error)', level: 'ERROR', threshold: 0 },
    ],
  },
  database: { host: 'localhost', port: 5432, user: '', password: '', database: '', timeout_seconds: 10 },
  process:  { process_name: '', expected_min_count: 1, match_cmdline: false },
  port:     { port: 80, protocol: 'tcp' },
  browser:  { script_path: '', timeout: 120 },
  ssl:      { timeout: 10 },
};

const TYPE_THRESHOLD_DEFAULTS = {
  cpu:      { cpu_percent:       { warning: 70,   critical: 80   } },
  memory:   { memory_percent:    { warning: 80,   critical: 90   } },
  disk:     { disk_percent:      { warning: 75,   critical: 90   } },
  network:  { max_errors: 1000,  max_drops: 1000 },
  url:      { response_time_ms:  { warning: 1000, critical: 3000 } },
  log:      {},
  database: { connection_time_ms:{ warning: 500,  critical: 2000 } },
  process:  {},
  port:     {},
  browser:  { duration_ms:       { warning: 5000, critical: 10000} },
  ssl:      { days_remaining:    { warning: 30,   critical: 7    } },
};

const VALID_TYPES = [
  'cpu','memory','disk','network','url','log','database','process','port','browser','ssl',
  ...Object.keys(edition.extraMonitorTypes),
];

const TYPE_LABELS = {
  cpu:'CPU', memory:'Memory', disk:'Disk', network:'Network', url:'URL',
  log:'Log', database:'Database', process:'Process', port:'Port', browser:'Browser', ssl:'SSL',
  ...edition.extraMonitorTypes,
};

// Edition monitor types whose cadence the server sets: the form shows only
// name/application/server/enabled, with no thresholds or type-specific settings.
const FIXED_CADENCE_TYPES = edition.fixedCadenceMonitorTypes;

// ── YAML block builder ───────────────────────────────────────────────────────
function buildYamlBlock(monitor) {
  // Serialise a monitor config response dict into a monitors.yaml block.
  // Uses js-yaml-style manual building to avoid a library dependency.
  const indent = (str, n = 2) =>
    str.split('\n').map(l => ' '.repeat(n) + l).join('\n');

  const toYaml = (obj, level = 0) => {
    if (obj === null || obj === undefined) return 'null';
    if (typeof obj === 'boolean') return String(obj);
    if (typeof obj === 'number') return String(obj);
    if (typeof obj === 'string') {
      // Quote strings that contain special YAML characters
      if (/[:{}[\],&*#?|<>=!%@`]/.test(obj) || obj === '') return `'${obj.replace(/'/g, "''")}'`;
      return obj;
    }
    if (Array.isArray(obj)) {
      if (obj.length === 0) return '[]';
      return obj.map(v => `\n${' '.repeat(level)}- ${toYaml(v, level + 2)}`).join('');
    }
    if (typeof obj === 'object') {
      return Object.entries(obj)
        .map(([k, v]) => {
          const val = toYaml(v, level + 2);
          if (typeof v === 'object' && v !== null && !Array.isArray(v)) {
            return `\n${' '.repeat(level)}${k}:${val}`;
          }
          if (Array.isArray(v)) {
            return `\n${' '.repeat(level)}${k}:${val}`;
          }
          return `\n${' '.repeat(level)}${k}: ${val}`;
        })
        .join('');
    }
    return String(obj);
  };

  const m = monitor;
  const config        = m.config        && Object.keys(m.config).length        ? m.config        : null;
  const thresholds    = m.threshold_config && Object.keys(m.threshold_config).length ? m.threshold_config : null;
  const notifChannels = m.notification_override?.channels ?? [];

  let block = `- assignee: ${m.assignee_override ?? 'support@company.com'}\n`;
  block += `  check_interval: ${m.check_interval_seconds}\n`;

  if (config) {
    block += `  config:`;
    Object.entries(config).forEach(([k, v]) => {
      const val = toYaml(v, 4);
      if (typeof v === 'object' && v !== null) {
        block += `\n    ${k}:${val}`;
      } else if (Array.isArray(v)) {
        block += `\n    ${k}:${val}`;
      } else {
        block += `\n    ${k}: ${val}`;
      }
    });
    block += '\n';
  } else {
    block += `  config: {}\n`;
  }

  block += `  consecutive_failures: ${m.consecutive_failures}\n`;
  block += `  enabled: ${m.enabled}\n`;
  block += `  monitor_config_id: ${m.monitor_config_id}\n`;
  block += `  name: ${toYaml(m.name)}\n`;

  if (notifChannels.length > 0) {
    block += `  notification_channels:\n`;
    notifChannels.forEach(ch => {
      block += `  - type: ${ch.type}\n`;
      if (ch.channel) block += `    channel: ${ch.channel}\n`;
      if (ch.recipients?.length) {
        block += `    recipients:\n`;
        ch.recipients.forEach(r => { block += `    - ${r}\n`; });
      }
      if (ch.severity_filter?.length) {
        block += `    severity_filter:\n`;
        ch.severity_filter.forEach(s => { block += `    - ${s}\n`; });
      }
    });
  } else {
    block += `  notification_channels: []\n`;
  }

  block += `  severity: ${m.severity}\n`;
  if (m.subtype) block += `  subtype: ${m.subtype}\n`;

  if (thresholds) {
    block += `  thresholds:`;
    Object.entries(thresholds).forEach(([k, v]) => {
      if (typeof v === 'object' && v !== null) {
        block += `\n    ${k}:`;
        Object.entries(v).forEach(([sk, sv]) => {
          block += `\n      ${sk}: ${sv}`;
        });
      } else {
        block += `\n    ${k}: ${v}`;
      }
    });
    block += '\n';
  } else {
    block += `  thresholds: {}\n`;
  }

  block += `  type: ${m.type}\n`;
  return block;
}

// ── YAML modal ───────────────────────────────────────────────────────────────
function YamlModal({ monitor, onClose }) {
  const [copied, setCopied] = React.useState(false);
  const yaml = buildYamlBlock(monitor);

  const handleCopy = () => {
    navigator.clipboard.writeText(yaml).then(() => {
      setCopied(true);
      setTimeout(() => setCopied(false), 2000);
    });
  };

  return (
    <div className="fixed inset-0 bg-black/70 flex items-center justify-center z-50 px-4">
      <div className="bg-gray-800 border border-gray-700 rounded-xl w-full max-w-2xl
                      max-h-[90vh] flex flex-col shadow-2xl">

        {/* Header */}
        <div className="flex items-center justify-between px-5 py-4 border-b border-gray-700">
          <div>
            <h2 className="text-white font-semibold">Monitor Created Successfully</h2>
            <p className="text-xs text-gray-400 mt-0.5">
              Copy the block below and add it to your{' '}
              <code className="text-blue-400">monitors.yaml</code> on the agent.
              The agent hot-reloads within 30 seconds.
            </p>
          </div>
          <button onClick={onClose}
            className="text-gray-400 hover:text-white transition-colors">
            <X size={18} />
          </button>
        </div>

        {/* YAML content */}
        <div className="flex-1 overflow-y-auto px-5 py-4">
          <pre className="bg-gray-900 rounded-lg p-4 text-xs text-green-300 font-mono
                          whitespace-pre overflow-x-auto leading-relaxed">
            {yaml}
          </pre>
        </div>

        {/* Footer */}
        <div className="flex items-center justify-between px-5 py-4 border-t border-gray-700">
          <p className="text-xs text-gray-500">
            Monitor ID:{' '}
            <code className="text-gray-300">{monitor.monitor_config_id}</code>
          </p>
          <div className="flex items-center gap-3">
            <button onClick={handleCopy}
              className="flex items-center gap-2 px-4 py-2 bg-gray-700 hover:bg-gray-600
                         text-gray-300 rounded-lg text-sm transition-colors">
              {copied
                ? <><CheckCircle size={14} className="text-green-400" /> Copied!</>
                : <><Copy size={14} /> Copy to Clipboard</>}
            </button>
            <button onClick={onClose}
              className="px-4 py-2 bg-blue-600 hover:bg-blue-700 text-white
                         rounded-lg text-sm transition-colors">
              Go to Monitor
            </button>
          </div>
        </div>
      </div>
    </div>
  );
}

// ── Field components ──────────────────────────────────────────────────────────
function Field({ label, required, hint, children }) {
  return (
    <div>
      <label className="block text-xs font-medium text-gray-300 mb-1.5">
        {label}{required && <span className="text-red-400 ml-1">*</span>}
      </label>
      {children}
      {hint && <p className="text-xs text-gray-500 mt-1">{hint}</p>}
    </div>
  );
}

const inputCls = `w-full bg-gray-800 border border-gray-700 rounded-lg px-3 py-2
                  text-sm text-white placeholder-gray-500
                  focus:outline-hidden focus:border-blue-500 focus:ring-1 focus:ring-blue-500`;

const selectCls = `w-full bg-gray-800 border border-gray-700 rounded-lg px-3 py-2
                   text-sm text-white focus:outline-hidden focus:border-blue-500`;

// Titled form section.
function Section({ title, children }) {
  return (
    <div className="bg-gray-800 rounded-xl border border-gray-700 p-5 space-y-4">
      <h2 className="text-sm font-semibold text-white border-b border-gray-700 pb-3">{title}</h2>
      {children}
    </div>
  );
}

// ── Type-specific config editors ─────────────────────────────────────────────
function UrlConfig({ value, onChange }) {
  const set = (k, v) => onChange({ ...value, [k]: v });
  return (
    <div className="space-y-4">
      <Field label="URL" required>
        <input className={inputCls} value={value.url ?? ''} placeholder="https://example.com/health"
          onChange={e => set('url', e.target.value)} />
      </Field>
      <div className="grid grid-cols-2 gap-4">
        <Field label="Method">
          <select className={selectCls} value={value.method ?? 'GET'} onChange={e => set('method', e.target.value)}>
            {['GET','POST','PUT','HEAD'].map(m => <option key={m}>{m}</option>)}
          </select>
        </Field>
        <Field label="Expected Status">
          <input className={inputCls} type="number" value={value.expected_status ?? 200}
            onChange={e => set('expected_status', parseInt(e.target.value))} />
        </Field>
      </div>
      <Field label="Expected Body Contains" hint="Leave empty to skip body check">
        <input className={inputCls} value={value.expected_body_contains ?? ''}
          placeholder='e.g. "status":"ok"'
          onChange={e => set('expected_body_contains', e.target.value)} />
      </Field>
      <div className="grid grid-cols-2 gap-4">
        <Field label="Timeout (seconds)">
          <input className={inputCls} type="number" value={value.timeout ?? 10}
            onChange={e => set('timeout', parseInt(e.target.value))} />
        </Field>
        <Field label="Verify SSL">
          <select className={selectCls} value={String(value.verify_ssl ?? true)}
            onChange={e => set('verify_ssl', e.target.value === 'true')}>
            <option value="true">Yes</option>
            <option value="false">No</option>
          </select>
        </Field>
      </div>
    </div>
  );
}

// Settings of a database monitor (engine, connection, credentials).
function DatabaseConfig({ value, onChange, subtype, onSubtype }) {
  const set = (k, v) => onChange({ ...value, [k]: v });
  return (
    <div className="space-y-4">
      <Field label="Database Type" required>
        <select className={selectCls} value={subtype ?? 'postgres'} onChange={e => onSubtype(e.target.value)}>
          <option value="postgres">PostgreSQL</option>
          <option value="mysql">MySQL</option>
          <option value="mongodb">MongoDB</option>
        </select>
      </Field>
      {(subtype === 'postgres' || subtype === 'mysql' || subtype === 'mongodb') ? (
        <>
          <div className="grid grid-cols-2 gap-4">
            <Field label="Host" required>
              <input className={inputCls} value={value.host ?? 'localhost'}
                onChange={e => set('host', e.target.value)} />
            </Field>
            <Field label="Port">
              <input className={inputCls} type="number"
                value={value.port ?? (subtype === 'mysql' ? 3306 : (subtype === 'mongodb' ? 27017 : 5432))}
                onChange={e => set('port', parseInt(e.target.value))} />
            </Field>
          </div>
          <div className="grid grid-cols-2 gap-4">
            <Field label="Username">
              <input className={inputCls} value={value.user ?? ''}
                onChange={e => set('user', e.target.value)} />
            </Field>
            <Field label="Password">
              <input className={inputCls} type="password" value={value.password ?? ''}
                onChange={e => set('password', e.target.value)} />
            </Field>
          </div>
          <Field label="Database Name">
            <input className={inputCls} value={value.database ?? ''}
              onChange={e => set('database', e.target.value)} />
          </Field>
        </>
      ) : null}
      <Field label="Timeout (seconds)">
        <input className={inputCls} type="number" value={value.timeout_seconds ?? 10}
          onChange={e => set('timeout_seconds', parseInt(e.target.value))} />
      </Field>
    </div>
  );
}

// Settings of a process monitor.
function ProcessConfig({ value, onChange }) {
  const set = (k, v) => onChange({ ...value, [k]: v });
  return (
    <div className="space-y-4">
      <Field label="Process Name" required hint="e.g. nginx, java, python">
        <input className={inputCls} value={value.process_name ?? ''}
          onChange={e => set('process_name', e.target.value)} />
      </Field>
      <div className="grid grid-cols-2 gap-4">
        <Field label="Min Expected Count">
          <input className={inputCls} type="number" value={value.expected_min_count ?? 1}
            onChange={e => set('expected_min_count', parseInt(e.target.value))} />
        </Field>
        <Field label="Match Command Line">
          <select className={selectCls} value={String(value.match_cmdline ?? false)}
            onChange={e => set('match_cmdline', e.target.value === 'true')}>
            <option value="false">No</option>
            <option value="true">Yes</option>
          </select>
        </Field>
      </div>
    </div>
  );
}

// Settings of a port monitor.
function PortConfig({ value, onChange }) {
  const set = (k, v) => onChange({ ...value, [k]: v });
  return (
    <div className="grid grid-cols-2 gap-4">
      <Field label="Port" required>
        <input className={inputCls} type="number" value={value.port ?? 80}
          onChange={e => set('port', parseInt(e.target.value))} />
      </Field>
      <Field label="Protocol">
        <select className={selectCls} value={value.protocol ?? 'tcp'}
          onChange={e => set('protocol', e.target.value)}>
          <option value="tcp">TCP</option>
          <option value="udp">UDP</option>
        </select>
      </Field>
    </div>
  );
}

/**
 * SSL monitor settings: the handshake timeout (expiry thresholds are set under Thresholds).
 * @param {{value: object, onChange: Function}} props
 * @returns {JSX.Element}
 */
function SSLConfig({ value, onChange }) {
  const set = (k, v) => onChange({ ...value, [k]: v });
  return (
    <div className="space-y-4">
      <div className="grid grid-cols-2 gap-4">
        <Field label="Handshake Timeout (seconds)">
          <input className={inputCls} type="number" min={1} max={60}
            value={value.timeout ?? 10}
            onChange={e => set('timeout', parseInt(e.target.value))} />
        </Field>
      </div>
      <div className="bg-gray-900/50 rounded-lg p-3 text-xs text-gray-400 space-y-1">
        <p className="font-medium text-gray-300">How SSL scanning works</p>
        <p>The agent uses psutil to discover all listening TCP ports on this server,
           attempts a TLS handshake on each, and reports each certificate's validity and
           days to expiry. An expired certificate, or one inside the critical threshold,
           opens an incident; inside the warning threshold it is a warning.</p>
        <p>No port configuration is required — all listening ports are scanned automatically.</p>
      </div>
    </div>
  );
}

// Settings of a disk monitor.
function DiskConfig({ value, onChange }) {
  const set = (k, v) => onChange({ ...value, [k]: v });
  return (
    <div className="space-y-4">
      <div className="flex items-center gap-3">
        <input
          type="checkbox"
          id="ignore_readonly"
          checked={value.ignore_readonly ?? true}
          onChange={e => set('ignore_readonly', e.target.checked)}
          className="w-4 h-4 accent-blue-600"
        />
        <label htmlFor="ignore_readonly" className="text-sm text-gray-300 cursor-pointer">
          Ignore read-only partitions
        </label>
      </div>
      <p className="text-xs text-gray-500">
        When enabled, read-only filesystems (e.g. macOS CoreSimulator volumes, Linux
        bind-mounted read-only mounts) are excluded from disk usage checks. These
        partitions cannot be written to so their usage is always at 100% and will
        always trigger a false alert without this setting.
      </p>
    </div>
  );
}

// Settings of a browser (journey script) monitor.
function BrowserConfig({ value, onChange }) {
  const set = (k, v) => onChange({ ...value, [k]: v });
  return (
    <div className="space-y-4">
      <Field label="Script Path" required hint="Path to Playwright test script">
        <input className={inputCls} value={value.script_path ?? ''}
          placeholder="scripts/my_test.py"
          onChange={e => set('script_path', e.target.value)} />
      </Field>
      <div className="grid grid-cols-2 gap-4">
        <Field label="Timeout (seconds)">
          <input className={inputCls} type="number" value={value.timeout ?? 120}
            onChange={e => set('timeout', parseInt(e.target.value))} />
        </Field>
      </div>
    </div>
  );
}

// Settings of a log monitor: files and patterns with thresholds.
function LogConfig({ value, onChange }) {
  const set = (k, v) => onChange({ ...value, [k]: v });
  const paths = (value.log_paths ?? []).join('\n');
  const patterns = value.patterns ?? [];

  const setPattern = (idx, key, val) => {
    const next = patterns.map((p, i) => i === idx ? { ...p, [key]: val } : p);
    set('patterns', next);
  };
  const addPattern = () =>
    set('patterns', [...patterns, { name: '', regex: '', level: 'ERROR', threshold: 0 }]);
  const removePattern = (idx) =>
    set('patterns', patterns.filter((_, i) => i !== idx));

  return (
    <div className="space-y-4">
      <Field label="Log File Paths" hint="One path per line">
        <textarea className={`${inputCls} h-24 resize-none font-mono`}
          value={paths}
          placeholder="/var/log/app.log&#10;/var/log/app.error.log"
          onChange={e => set('log_paths', e.target.value.split('\n').filter(Boolean))} />
      </Field>
      <Field label="Context Lines">
        <input className={inputCls} type="number"
          value={value.include_context_lines ?? 5}
          onChange={e => set('include_context_lines', parseInt(e.target.value))} />
      </Field>

      <Field label="Patterns" hint="Regex patterns to match in the log; each raises findings at its level">
        <div className="space-y-2">
          {patterns.length === 0 && (
            <p className="text-xs text-gray-500">No patterns yet — add one below.</p>
          )}
          {patterns.map((p, idx) => (
            <div key={idx} className="grid grid-cols-12 gap-2 items-start bg-gray-900/40 rounded-lg p-2">
              <input className={`${inputCls} col-span-3`} type="text" placeholder="name"
                value={p.name ?? ''}
                onChange={e => setPattern(idx, 'name', e.target.value)} />
              <input className={`${inputCls} col-span-4 font-mono`} type="text" placeholder="regex"
                value={p.regex ?? ''}
                onChange={e => setPattern(idx, 'regex', e.target.value)} />
              <select className={`${inputCls} col-span-2`}
                value={p.level ?? 'ERROR'}
                onChange={e => setPattern(idx, 'level', e.target.value)}>
                <option value="CRITICAL">CRITICAL</option>
                <option value="ERROR">ERROR</option>
                <option value="WARNING">WARNING</option>
                <option value="INFO">INFO</option>
              </select>
              <input className={`${inputCls} col-span-2`} type="number" placeholder="threshold"
                title="Occurrences before alerting (0 = any)"
                value={p.threshold ?? 0}
                onChange={e => setPattern(idx, 'threshold', parseInt(e.target.value) || 0)} />
              <button type="button" onClick={() => removePattern(idx)}
                className="col-span-1 text-red-400 hover:text-red-300 text-sm py-2"
                title="Remove pattern">✕</button>
            </div>
          ))}
          <button type="button" onClick={addPattern}
            className="text-xs px-2 py-1 bg-gray-700 hover:bg-gray-600 text-white rounded-sm">
            + Add pattern
          </button>
        </div>
      </Field>
    </div>
  );
}

// ── Threshold editor ─────────────────────────────────────────────────────────
function ThresholdEditor({ type, value, onChange }) {
  const defaults = TYPE_THRESHOLD_DEFAULTS[type] ?? {};
  const keys = Object.keys(defaults);

  if (keys.length === 0) {
    return <p className="text-sm text-gray-500">No configurable thresholds for this monitor type.</p>;
  }

  return (
    <div className="space-y-4">
      {keys.map(key => {
        const entry = defaults[key];
        if (typeof entry === 'object' && ('warning' in entry || 'critical' in entry)) {
          const current = value?.[key] ?? entry;
          return (
            <div key={key}>
              <p className="text-xs font-medium text-gray-400 mb-2 uppercase">{key.replace(/_/g, ' ')}</p>
              <div className="grid grid-cols-2 gap-3">
                <Field label="Warning">
                  <input className={inputCls} type="number"
                    value={current.warning ?? entry.warning}
                    onChange={e => onChange({
                      ...value,
                      [key]: { ...(value?.[key] ?? entry), warning: Number(e.target.value) }
                    })} />
                </Field>
                <Field label="Critical">
                  <input className={inputCls} type="number"
                    value={current.critical ?? entry.critical}
                    onChange={e => onChange({
                      ...value,
                      [key]: { ...(value?.[key] ?? entry), critical: Number(e.target.value) }
                    })} />
                </Field>
              </div>
            </div>
          );
        }
        // Simple number threshold (e.g. max_errors)
        return (
          <Field key={key} label={key.replace(/_/g, ' ')}>
            <input className={inputCls} type="number"
              value={value?.[key] ?? defaults[key]}
              onChange={e => onChange({ ...value, [key]: Number(e.target.value) })} />
          </Field>
        );
      })}
    </div>
  );
}

// ── Main form ────────────────────────────────────────────────────────────────
// Create or edit a monitor (admins only; others see a notice). The admin check is done here so
// the editor's hooks always run in the same order.
export default function MonitorForm() {
  const navigate = useNavigate();
  const { user } = useAuth();
  if (user?.role !== 'admin') {
    return (
      <div className="flex flex-col items-center justify-center h-64 gap-3 text-gray-400">
        <AlertTriangle size={36} className="text-yellow-400" />
        <p className="font-medium">Admin access required</p>
        <button onClick={() => navigate('/monitors')}
          className="text-blue-400 hover:underline text-sm">Back to Monitors</button>
      </div>
    );
  }
  return <MonitorEditor />;
}

// The monitor editor: loads the monitor when editing, validates and saves.
function MonitorEditor() {
  const { id }        = useParams();           // undefined = create, else = edit
  const isEdit        = Boolean(id);
  const navigate      = useNavigate();
  const queryClient   = useQueryClient();

  const [form, setForm] = useState({
    application_id:        '',
    server_id:             '',
    name:                  '',
    type:                  'url',
    subtype:               null,
    enabled:               true,
    check_interval_seconds: 60,
    consecutive_failures:  3,
    severity:              'medium',
    assignee_override:     '',
    config:                TYPE_CONFIG_DEFAULTS['url'],
    threshold_config:      TYPE_THRESHOLD_DEFAULTS['url'],
  });

  const [error, setError]           = useState('');
  const [savedMonitor, setSavedMonitor] = useState(null); // triggers YAML modal on create

  // Fetch servers and applications for dropdowns
  const { data: servers = [] } = useQuery({
    queryKey: ['servers-list'],
    queryFn:  () => api.get('/agents/').then(r => r.data),
  });

  const { data: applications = [] } = useQuery({
    queryKey: ['applications-list'],
    queryFn:  () => api.get('/config/applications').then(r => r.data),
  });

  // Load existing monitor for edit
  const { data: existingMonitor, isLoading: loadingMonitor } = useQuery({
    queryKey: ['monitor-config', id],
    queryFn:  () => api.get(`/config/monitor-configs/${id}`).then(r => r.data),
    enabled:  isEdit,
  });

  // Populate form once existing monitor data loads.
  // useEffect is used instead of onSuccess because onSuccess was removed in
  // React Query v5 — relying on it silently did nothing, so the form always
  // rendered with the url type default regardless of the actual monitor type.
  useEffect(() => {
    if (!existingMonitor) return;
    setForm({
      application_id:         existingMonitor.application_id         ?? '',
      server_id:              existingMonitor.server_id              ?? '',
      name:                   existingMonitor.name                   ?? '',
      type:                   existingMonitor.type                   ?? 'url',
      subtype:                existingMonitor.subtype                ?? null,
      enabled:                existingMonitor.enabled                ?? true,
      check_interval_seconds: existingMonitor.check_interval_seconds ?? 60,
      consecutive_failures:   existingMonitor.consecutive_failures   ?? 3,
      severity:               existingMonitor.severity               ?? 'medium',
      assignee_override:      existingMonitor.assignee_override      ?? '',
      config: {
        ...(TYPE_CONFIG_DEFAULTS[existingMonitor.type] ?? {}),
        ...(existingMonitor.config ?? {}),
      },
      threshold_config:       existingMonitor.threshold_config
                                ?? TYPE_THRESHOLD_DEFAULTS[existingMonitor.type]
                                ?? {},
    });
  }, [existingMonitor]);

  const set = (k, v) => setForm(f => ({ ...f, [k]: v }));

  // When type changes, reset type-specific fields to defaults
  const handleTypeChange = (newType) => {
    setForm(f => ({
      ...f,
      type:             newType,
      subtype:          null,
      config:           TYPE_CONFIG_DEFAULTS[newType] ?? {},
      threshold_config: TYPE_THRESHOLD_DEFAULTS[newType] ?? {},
    }));
  };

  const saveMutation = useMutation({
    mutationFn: (payload) => isEdit
      ? api.patch(`/config/monitor-configs/${id}`, payload)
      : api.post('/config/monitor-configs', payload),
    onSuccess: (res) => {
      // Invalidate both the list and the individual detail cache entry.
      // Without invalidating ['monitor-config', id] the detail page reads
      // stale data until the user closes and reopens the monitor.
      queryClient.invalidateQueries({ queryKey: ['monitor-configs'] });
      queryClient.invalidateQueries({ queryKey: ['monitor-config', id] });
      if (isEdit) {
        // Edit — just navigate back to detail page
        navigate(`/monitors/${id}`);
      } else {
        // Create — show YAML modal so operator can copy the monitors.yaml block
        setSavedMonitor(res.data);
      }
    },
    onError: (err) => setError(err.response?.data?.detail ?? 'Save failed'),
  });

  const handleSubmit = (e) => {
    e.preventDefault();
    setError('');

    // Basic validation
    if (!form.name.trim())   return setError('Name is required');
    if (!form.type)          return setError('Type is required');
    if (!isEdit && !form.server_id)      return setError('Server is required');
    if (!isEdit && !form.application_id) return setError('Application is required');
    if (form.type === 'url' && !form.config?.url?.trim())
      return setError('URL is required for URL monitors');

    const payload = {
      ...(!isEdit && { application_id: form.application_id }),
      ...(!isEdit && { server_id:      form.server_id }),
      name:                  form.name,
      type:                  form.type,
      subtype:               form.subtype || null,
      enabled:               form.enabled,
      check_interval_seconds: form.check_interval_seconds,
      consecutive_failures:  form.consecutive_failures,
      severity:              form.severity,
      assignee_override:     form.assignee_override || null,
      config:                form.config,
      threshold_config:      form.threshold_config,
    };
    saveMutation.mutate(payload);
  };

  if (isEdit && loadingMonitor) return (
    <div className="flex items-center justify-center h-64">
      <div className="animate-spin rounded-full h-8 w-8 border-b-2 border-blue-500" />
    </div>
  );

  return (
    <div className="max-w-3xl mx-auto space-y-6">

      {/* Header */}
      <div className="flex items-center gap-4">
        <button onClick={() => navigate(isEdit ? `/monitors/${id}` : '/monitors')}
          className="text-gray-400 hover:text-white transition-colors">
          <ArrowLeft size={20} />
        </button>
        <h1 className="text-xl font-bold text-white">
          {isEdit ? 'Edit Monitor' : 'Add Monitor'}
        </h1>
      </div>

      {error && (
        <div className="flex items-center gap-2 bg-red-500/10 border border-red-500/30
                        text-red-400 rounded-lg px-4 py-3 text-sm">
          <AlertTriangle size={15} /> {error}
        </div>
      )}

      <form onSubmit={handleSubmit} className="space-y-6">

        {/* Core settings */}
        <Section title="General">
          <div className="grid grid-cols-2 gap-4">
            <Field label="Monitor Name" required>
              <input className={inputCls} value={form.name}
                placeholder="My URL Health Check"
                onChange={e => set('name', e.target.value)} />
            </Field>
            <Field label="Type" required>
              <select className={selectCls} value={form.type}
                onChange={e => handleTypeChange(e.target.value)}
                disabled={isEdit}>
                {VALID_TYPES.map(t => (
                  <option key={t} value={t}>{TYPE_LABELS[t]}</option>
                ))}
              </select>
            </Field>
          </div>

          {/* Application + Server — only shown on create */}
          {!isEdit && (
            <div className="grid grid-cols-2 gap-4">
              <Field label="Application" required>
                <select className={selectCls} value={form.application_id}
                  onChange={e => set('application_id', e.target.value)}>
                  <option value="">Select application...</option>
                  {applications.map(a => (
                    <option key={a.id} value={a.id}>{a.name}</option>
                  ))}
                </select>
              </Field>
              <Field label="Server" required>
                <select className={selectCls} value={form.server_id}
                  onChange={e => set('server_id', e.target.value)}>
                  <option value="">Select server...</option>
                  {servers.map(s => (
                    <option key={s.id} value={s.id}>{s.hostname}</option>
                  ))}
                </select>
              </Field>
            </div>
          )}

          <div className="grid grid-cols-2 gap-4">
            {!FIXED_CADENCE_TYPES.includes(form.type) && (
              <Field label="Severity">
                <select className={selectCls} value={form.severity}
                  onChange={e => set('severity', e.target.value)}>
                  {['critical','high','medium','low'].map(s => (
                    <option key={s} value={s}>{s.charAt(0).toUpperCase() + s.slice(1)}</option>
                  ))}
                </select>
              </Field>
            )}
            <Field label="Status">
              <select className={selectCls} value={String(form.enabled)}
                onChange={e => set('enabled', e.target.value === 'true')}>
                <option value="true">Enabled</option>
                <option value="false">Disabled</option>
              </select>
            </Field>
          </div>

          {FIXED_CADENCE_TYPES.includes(form.type) ? (
            edition.FixedCadenceNote && <edition.FixedCadenceNote />
          ) : (
            <div className="grid grid-cols-2 gap-4">
              <Field label="Check Interval (seconds)">
                <input className={inputCls} type="number" min="10" value={form.check_interval_seconds}
                  onChange={e => set('check_interval_seconds', parseInt(e.target.value))} />
              </Field>
              <Field label="Consecutive Failures to Alert">
                <input className={inputCls} type="number" min="1" value={form.consecutive_failures}
                  onChange={e => set('consecutive_failures', parseInt(e.target.value))} />
              </Field>
            </div>
          )}

          {!FIXED_CADENCE_TYPES.includes(form.type) && (
            <Field label="Assignee Override" hint="Leave empty to use application default">
              <input className={inputCls} value={form.assignee_override ?? ''}
                placeholder="ops-team@company.com"
                onChange={e => set('assignee_override', e.target.value)} />
            </Field>
          )}
        </Section>

        {/* Type-specific config */}
        {!['cpu','memory','network', ...FIXED_CADENCE_TYPES].includes(form.type) && (
          <Section title={`${TYPE_LABELS[form.type]} Settings`}>
            {form.type === 'disk'     && <DiskConfig     value={form.config} onChange={v => set('config', v)} />}
            {form.type === 'url'      && <UrlConfig      value={form.config} onChange={v => set('config', v)} />}
            {form.type === 'database' && <DatabaseConfig  value={form.config} onChange={v => set('config', v)}
                                           subtype={form.subtype} onSubtype={v => set('subtype', v)} />}
            {form.type === 'process'  && <ProcessConfig   value={form.config} onChange={v => set('config', v)} />}
            {form.type === 'port'     && <PortConfig      value={form.config} onChange={v => set('config', v)} />}
            {form.type === 'browser'  && <BrowserConfig   value={form.config} onChange={v => set('config', v)} />}
            {form.type === 'ssl'      && <SSLConfig       value={form.config} onChange={v => set('config', v)} />}
            {form.type === 'log'      && <LogConfig       value={form.config} onChange={v => set('config', v)} />}
          </Section>
        )}

        {/* Thresholds — not applicable to fixed-cadence monitors */}
        {!FIXED_CADENCE_TYPES.includes(form.type) && (
          <Section title="Alert Thresholds">
            <ThresholdEditor
              type={form.type}
              value={form.threshold_config}
              onChange={v => set('threshold_config', v)} />
          </Section>
        )}

        {/* Submit */}
        <div className="flex items-center justify-end gap-3 pb-6">
          <button type="button"
            onClick={() => navigate(isEdit ? `/monitors/${id}` : '/monitors')}
            className="px-4 py-2 bg-gray-700 hover:bg-gray-600 text-gray-300
                       rounded-lg text-sm transition-colors">
            Cancel
          </button>
          <button type="submit" disabled={saveMutation.isPending}
            className="flex items-center gap-2 px-4 py-2 bg-blue-600 hover:bg-blue-700
                       disabled:opacity-50 text-white rounded-lg text-sm transition-colors">
            <Save size={14} />
            {saveMutation.isPending ? 'Saving...' : isEdit ? 'Save Changes' : 'Create Monitor'}
          </button>
        </div>
      </form>

      {/* YAML modal — shown after successful create only */}
      {savedMonitor && (
        <YamlModal
          monitor={savedMonitor}
          onClose={() => navigate(`/monitors/${savedMonitor.monitor_config_id}`)}
        />
      )}
    </div>
  );
}
