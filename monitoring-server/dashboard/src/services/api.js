// Copyright (c) 2026 Axara Pty Ltd.
// SPDX-License-Identifier: AGPL-3.0-or-later
import axios from 'axios';

// Base axios instance — all API calls go through this
export const api = axios.create({
  baseURL: '/api/v1',
  timeout: 15000,
  headers: {
    'Content-Type': 'application/json',
  },
});

// Attach auth token to every request if present
api.interceptors.request.use((config) => {
  const stored = localStorage.getItem('auth_user');
  if (stored) {
    try {
      const { token } = JSON.parse(stored);
      if (token) config.headers.Authorization = `Bearer ${token}`;
    } catch {
      // ignore malformed storage
    }
  }
  return config;
});

// Extra handlers for coded API errors, added by the edition module (see App.jsx).
const errorCodeHandlers = [];

/**
 * Add a handler called for every API error whose detail is {code, message}.
 * @param {(code: string, error: object) => void} fn - Handler.
 */
export function addErrorCodeHandler(fn) {
  errorCodeHandlers.push(fn);
}

// Global response error handling
api.interceptors.response.use(
  (response) => response,
  (error) => {
    // Coded errors carry {code, message}. Flatten to a string so every page that renders
    // `detail` keeps working, keep the code on error.errorCode and pass it to the handlers.
    const detail = error.response?.data?.detail;
    if (detail && typeof detail === 'object' && detail.code) {
      error.errorCode = detail.code;
      error.response.data.detail = detail.message || detail.code;
      errorCodeHandlers.forEach(fn => { try { fn(detail.code, error); } catch { /* ignore */ } });
    }
    if (error.response?.status === 401) {
      // Token expired or invalid — clear session and redirect to login
      localStorage.removeItem('auth_user');
      window.location.href = '/login';
    }
    return Promise.reject(error);
  }
);

// Convenience helpers
export const endpoints = {
  // Dashboard
  dashboardSummary: ()              => api.get('/dashboard/summary'),

  // Incidents
  incidents:        (params = {})   => api.get('/incidents/',         { params }),
  incident:         (id)            => api.get(`/incidents/${id}`),
  incidentAttachmentDownload: (id)  => api.get(`/incidents/${id}/attachments/download`, { responseType: 'blob' }),

  // Servers / Agents
  servers:          ()              => api.get('/agents/'),
  server:           (id)            => api.get(`/agents/${id}`),

  // Metrics
  metricsRecent:    (serverId)      => api.get(`/metrics/recent/${serverId}`),
  metricsHistory:   (params = {})   => api.get('/metrics/history',   { params }),

  // Applications
  applications:     ()              => api.get('/config/applications'),

  // Config
  serverConfig:     ()              => api.get('/config/server'),
  saveServerConfig: (data)          => api.put('/config/server', data),

  // Monitor Configs
  monitorConfigs:       (params = {})  => api.get('/config/monitor-configs',      { params }),
  monitorConfig:        (id)           => api.get(`/config/monitor-configs/${id}`),
  createMonitorConfig:  (data)         => api.post('/config/monitor-configs',      data),
  updateMonitorConfig:  (id, data)     => api.patch(`/config/monitor-configs/${id}`, data),
  deleteMonitorConfig:  (id, hard)     => api.delete(`/config/monitor-configs/${id}${hard ? '?hard=true' : ''}`),

  // Auth
  login:            (credentials)   => api.post('/auth/login',    credentials),
  register:         (data)          => api.post('/auth/register', data),
  logout:           (username)      => api.post('/auth/logout',   { username }),
};
