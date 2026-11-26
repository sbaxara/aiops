// Copyright (c) 2026 Axara Pty Ltd.
// SPDX-License-Identifier: AGPL-3.0-or-later
// Community edition: no extra pages, menu entries or banner. Another edition's build can
// replace this module (see vite.config.js, alias @edition).

export const EDITION = 'community';
export const publicRoutes = [];
export const routes = [];
export const navItems = [];
export const adminMenuItems = [];
export const Banner = null;
export const SettingsSections = null;          // extra Settings page sections
export const hasAlerting = false;              // notifications and escalation
export const appSettingsTabs = [];             // extra tabs of the application settings dialog
export const extraMonitorTypes = {};           // {monitor type: label} added to the monitor form
export const fixedCadenceMonitorTypes = [];    // types whose interval the server sets
export const FixedCadenceNote = null;          // monitor form note for those types
export const MonitorDetailCard = null;         // extra card on the monitor page
export const removeAgentNotes = [];            // extra lines in the Remove agent dialog
export const onErrorCode = null;               // handler for coded API errors
export const IncidentBadges = null;            // extra badges on an incident ({incident, size})
export const IncidentDetailCards = null;       // extra cards on the incident page ({incident, Card})

/**
 * Message for the login page taken from its URL (none in this edition).
 *
 * Args:
 *   searchParams: URLSearchParams of the login page.
 *
 * Returns:
 *   '' (no message)
 */
export function loginMessage(searchParams) {   // eslint-disable-line no-unused-vars
  return '';
}

/**
 * Extra tabs of the application settings dialog (none in this edition).
 *
 * Returns:
 *   {tabs: [], render: () => null, save: null}
 */
export function useAppSettings() {
  return { tabs: [], render: () => null, save: null };
}

/**
 * Sign-in options shown on the login page (local accounts only).
 *
 * Returns:
 *   {providers: [], localRegistration: true, usernamePlaceholder: '', passwordHint: ''}
 */
export function useLoginOptions() {
  return { providers: [], localRegistration: true, usernamePlaceholder: '', passwordHint: '' };
}
