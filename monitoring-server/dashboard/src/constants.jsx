// Copyright (c) 2026 Axara Pty Ltd.
// SPDX-License-Identifier: AGPL-3.0-or-later
/**
 * Shared dashboard constants
 * Single source of truth for monitor types, colours, and status styles.
 * Import from this file rather than defining locally in each component.
 */

// ── Monitor type labels ────────────────────────────────────────────────────
export const TYPE_LABELS = {
  cpu:      'CPU',
  memory:   'Memory',
  disk:     'Disk',
  network:  'Network',
  url:      'URL',
  log:      'Log',
  database: 'Database',
  process:  'Process',
  port:     'Port',
  browser:  'Browser',
  ssl:      'SSL',
};

// ── Monitor type badge colours ─────────────────────────────────────────────
export const TYPE_COLORS = {
  cpu:      'bg-blue-500/20 text-blue-300 border-blue-500/30',
  memory:   'bg-green-500/20 text-green-300 border-green-500/30',
  disk:     'bg-yellow-500/20 text-yellow-300 border-yellow-500/30',
  network:  'bg-purple-500/20 text-purple-300 border-purple-500/30',
  url:      'bg-cyan-500/20 text-cyan-300 border-cyan-500/30',
  log:      'bg-orange-500/20 text-orange-300 border-orange-500/30',
  database: 'bg-pink-500/20 text-pink-300 border-pink-500/30',
  process:  'bg-indigo-500/20 text-indigo-300 border-indigo-500/30',
  port:     'bg-teal-500/20 text-teal-300 border-teal-500/30',
  browser:  'bg-rose-500/20 text-rose-300 border-rose-500/30',
  ssl:      'bg-emerald-500/20 text-emerald-300 border-emerald-500/30',
};

// ── Severity badge colours ─────────────────────────────────────────────────
export const SEVERITY_COLORS = {
  critical: 'bg-red-500/20 text-red-400 border-red-500/30',
  high:     'bg-orange-500/20 text-orange-400 border-orange-500/30',
  medium:   'bg-yellow-500/20 text-yellow-400 border-yellow-500/30',
  low:      'bg-blue-500/20 text-blue-400 border-blue-500/30',
};

// ── Metric/check status badge colours ─────────────────────────────────────
export const STATUS_STYLES = {
  pass:    'bg-green-500/20 text-green-400 border-green-500/30',
  warning: 'bg-yellow-500/20 text-yellow-400 border-yellow-500/30',
  fail:    'bg-red-500/20 text-red-400 border-red-500/30',
  error:   'bg-orange-500/20 text-orange-400 border-orange-500/30',
};

// ── Incident status badge colours ──────────────────────────────────────────
export const INCIDENT_STATUS_STYLES = {
  open:        'bg-blue-500/20 text-blue-400 border-blue-500/30',
  in_progress: 'bg-purple-500/20 text-purple-400 border-purple-500/30',
  resolved:    'bg-green-500/20 text-green-400 border-green-500/30',
  closed:      'bg-gray-500/20 text-gray-400 border-gray-500/30',
};

// ── Status ranking (higher = worse) for worst-of comparisons ─────────────
export const STATUS_RANK = { pass: 0, warning: 1, fail: 2, error: 3 };

// ── Shared reusable badge components ──────────────────────────────────────

export function TypeBadge({ type }) {
  return (
    <span className={`px-2 py-0.5 rounded-full text-xs border font-medium
                      ${TYPE_COLORS[type] ?? 'bg-gray-500/20 text-gray-300 border-gray-500/30'}`}>
      {TYPE_LABELS[type] ?? type}
    </span>
  );
}

/**
 * Severity badge in the shared colours.
 * @param {{severity: string}} props
 * @returns {JSX.Element}
 */
export function SeverityBadge({ severity }) {
  return (
    <span className={`px-2 py-0.5 rounded-full text-xs border font-medium
                      ${SEVERITY_COLORS[severity] ?? SEVERITY_COLORS.low}`}>
      {severity ?? 'low'}
    </span>
  );
}

/**
 * Generic status badge in the shared colours.
 * @param {{status: string, label?: string}} props
 * @returns {JSX.Element}
 */
export function StatusBadge({ status, label }) {
  const text = label ?? status;
  return (
    <span className={`px-2 py-0.5 rounded-full text-xs border font-medium
                      ${STATUS_STYLES[status] ?? 'bg-gray-500/20 text-gray-400 border-gray-500/30'}`}>
      {text}
    </span>
  );
}

/**
 * Incident status badge (open, in progress, resolved, ...).
 * @param {{status: string}} props
 * @returns {JSX.Element}
 */
export function IncidentStatusBadge({ status }) {
  return (
    <span className={`px-2 py-0.5 rounded-full text-xs border font-medium
                      ${INCIDENT_STATUS_STYLES[status?.toLowerCase()] ?? 'bg-gray-500/20 text-gray-400 border-gray-500/30'}`}>
      {status?.replace('_', ' ').toUpperCase()}
    </span>
  );
}

// ── Common select/input class strings ─────────────────────────────────────
export const SELECT_CLS =
  'bg-gray-800 border border-gray-700 rounded-lg px-3 py-2 ' +
  'text-sm text-gray-300 focus:outline-hidden focus:border-blue-500';

export const INPUT_CLS =
  'w-full bg-gray-900 border border-gray-700 rounded-lg px-3 py-2 ' +
  'text-sm text-white placeholder-gray-500 ' +
  'focus:outline-hidden focus:border-blue-500';

// Display name of an ITSM ticket provider (incident.ticket_provider).
export function ticketToolName(provider) {
  const known = { jira: 'Jira', servicenow: 'ServiceNow' };
  if (!provider) return 'Ticket';
  return known[provider] ?? provider.charAt(0).toUpperCase() + provider.slice(1);
}
