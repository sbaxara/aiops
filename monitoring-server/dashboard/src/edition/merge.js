// Copyright (c) 2026 Axara Pty Ltd.
// SPDX-License-Identifier: AGPL-3.0-or-later
// Helpers that place an edition's menu entries among the core ones.

/**
 * Insert edition menu entries into the core list, each after the entry named by `after`.
 *
 * Args:
 *   core: Core sidebar entries.
 *   extra: Edition entries with an optional `after` path.
 *
 * Returns:
 *   The merged list (entries whose `after` is unknown go at the end).
 */
export function mergeNav(core, extra) {
  const out = [...core];
  for (const item of extra) {
    const i = out.findIndex(x => x.path === item.after);
    if (i < 0) out.push(item); else out.splice(i + 1, 0, item);
  }
  return out;
}
