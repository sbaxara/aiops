// Copyright (c) 2026 Axara Pty Ltd.
// SPDX-License-Identifier: AGPL-3.0-or-later
// ─────────────────────────────────────────────────────────────────────────────
// FILE: src/utils/time.js
// The server stores UTC timestamps without a "Z" suffix.
// Appending "Z" makes new Date() treat them as UTC; the browser then
// converts to the user's local OS timezone automatically via toLocaleString().
// ─────────────────────────────────────────────────────────────────────────────

/** Format a UTC ISO string as a full local date+time, e.g. "08 Mar 2026, 21:00" */
export const localDt = (iso) => {
  if (!iso) return '—';
  const d = new Date(iso.endsWith('Z') ? iso : iso + 'Z');
  return isNaN(d) ? '—' : d.toLocaleString([], {
    day: '2-digit', month: 'short', year: 'numeric',
    hour: '2-digit', minute: '2-digit',
  });
};

/** Format as time only, e.g. "21:00" */
export const localTime = (iso) => {
  if (!iso) return '—';
  const d = new Date(iso.endsWith('Z') ? iso : iso + 'Z');
  return isNaN(d) ? '—' : d.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' });
};

/** Relative time, e.g. "3 minutes ago" */
export const timeAgo = (iso) => {
  if (!iso) return '—';
  const d = new Date(iso.endsWith('Z') ? iso : iso + 'Z');
  if (isNaN(d)) return '—';
  const diffSec = Math.round((Date.now() - d.getTime()) / 1000);
  const rtf = new Intl.RelativeTimeFormat('en', { numeric: 'auto' });
  if (diffSec < 60)    return rtf.format(-diffSec,                'second');
  if (diffSec < 3600)  return rtf.format(-Math.round(diffSec/60),  'minute');
  if (diffSec < 86400) return rtf.format(-Math.round(diffSec/3600),'hour');
  return                      rtf.format(-Math.round(diffSec/86400),'day');
};

// REPLACE formatXAxis:
const formatXAxis = (ms) =>
  new Date(ms).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' });
