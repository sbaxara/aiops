// Copyright (c) 2026 Axara Pty Ltd.
// SPDX-License-Identifier: AGPL-3.0-or-later
import React, { useState } from 'react';
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query';
import { api } from '../services/api';
import {
  Users, CheckCircle, RefreshCw, Clock, UserCheck, AlertTriangle,
  UserX, UserPlus, ShieldAlert
} from 'lucide-react';

// Badge classes for a user role.
const roleBadgeColor = (role) => ({
  admin:    'bg-red-500/20 text-red-300 border-red-500/30',
  operator: 'bg-yellow-500/20 text-yellow-300 border-yellow-500/30',
  viewer:   'bg-blue-500/20 text-blue-300 border-blue-500/30',
}[role] ?? 'bg-gray-500/20 text-gray-300 border-gray-500/30');

// Badge classes for a user status.
const statusBadgeColor = (status) => ({
  active:   'bg-green-500/20 text-green-300 border-green-500/30',
  pending:  'bg-yellow-500/20 text-yellow-300 border-yellow-500/30',
  disabled: 'bg-gray-500/20 text-gray-400 border-gray-500/30',
}[status] ?? 'bg-gray-500/20 text-gray-300 border-gray-500/30');

// Table header cell.
function Th({ children }) {
  return (
    <th className="px-4 py-3 text-xs font-medium text-gray-400 uppercase text-left">
      {children}
    </th>
  );
}

// Admin page to approve, disable and change the role of users.
export default function UserApprovals() {
  const queryClient = useQueryClient();
  const [tab, setTab] = useState('pending');
  const [busyUser, setBusyUser] = useState(null);        // username of the row mid-action
  const [confirmUser, setConfirmUser] = useState(null);  // user object awaiting confirmation
  const [successMsg, setSuccessMsg] = useState('');
  const [errorMsg, setErrorMsg]   = useState('');

  const flashOk = (msg) => { setSuccessMsg(msg); setTimeout(() => setSuccessMsg(''), 4000); };
  const resetMsgs = () => { setErrorMsg(''); setSuccessMsg(''); };

  const { data: pending = [], isLoading: pendingLoading, refetch: refetchPending } = useQuery({
    queryKey: ['pending-users'],
    queryFn:  () => api.get('/auth/pending-users').then(r => r.data),
  });

  // Current user — used only to disable the self-deactivate control. The server
  // enforces this too (hiding a button stops nothing); this just avoids
  // offering an action that is guaranteed to fail.
  const { data: me } = useQuery({
    queryKey: ['me'],
    queryFn:  () => api.get('/auth/me').then(r => r.data),
  });

  const { data: allUsers = [], isLoading: usersLoading, refetch: refetchUsers } = useQuery({
    queryKey: ['all-users'],
    queryFn:  () => api.get('/auth/users').then(r => r.data),
  });

  const invalidateAll = () => {
    queryClient.invalidateQueries({ queryKey: ['pending-users'] });
    queryClient.invalidateQueries({ queryKey: ['all-users'] });
  };

  const approveMutation = useMutation({
    mutationFn: (username) => api.post(`/auth/approve/${username}`),
    onMutate:   (username) => { setBusyUser(username); resetMsgs(); },
    onSuccess:  (_, username) => {
      invalidateAll();
      flashOk(`${username} approved successfully.`);
      setBusyUser(null);
    },
    onError: (err, username) => {
      setErrorMsg(err.response?.data?.detail ?? `Failed to approve ${username}.`);
      setBusyUser(null);
    },
  });

  const deactivateMutation = useMutation({
    mutationFn: (username) => api.post(`/auth/users/${username}/deactivate`),
    onMutate:   (username) => { setBusyUser(username); resetMsgs(); },
    onSuccess:  (_, username) => {
      invalidateAll();
      flashOk(`${username} deactivated. Their active session has been terminated.`);
      setBusyUser(null); setConfirmUser(null);
    },
    onError: (err, username) => {
      setErrorMsg(err.response?.data?.detail ?? `Failed to deactivate ${username}.`);
      setBusyUser(null); setConfirmUser(null);
    },
  });

  const reactivateMutation = useMutation({
    mutationFn: (username) => api.post(`/auth/users/${username}/reactivate`),
    onMutate:   (username) => { setBusyUser(username); resetMsgs(); },
    onSuccess:  (_, username) => {
      invalidateAll();
      flashOk(`${username} reactivated.`);
      setBusyUser(null);
    },
    onError: (err, username) => {
      setErrorMsg(err.response?.data?.detail ?? `Failed to reactivate ${username}.`);
      setBusyUser(null);
    },
  });

  const pendingCount = pending.length;
  const activeCount  = allUsers.filter(u => u.status === 'active').length;

  const tabCls = (name) =>
    `px-4 py-2 text-sm font-medium rounded-lg transition-colors ${
      tab === name
        ? 'bg-blue-600 text-white'
        : 'text-gray-400 hover:text-white hover:bg-gray-700'
    }`;

  return (
    <div className="space-y-6">

      {/* Header */}
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-3xl font-bold text-white">User Management</h1>
          <p className="text-gray-400 mt-1">
            {pendingCount} pending · {activeCount} active · {allUsers.length} total
          </p>
        </div>
        <button
          onClick={() => { refetchPending(); refetchUsers(); }}
          className="flex items-center gap-2 px-3 py-2 bg-gray-700 hover:bg-gray-600
                     text-gray-300 rounded-lg text-sm transition-colors"
        >
          <RefreshCw size={14} />
          Refresh
        </button>
      </div>

      {/* Tabs */}
      <div className="flex items-center gap-2">
        <button onClick={() => { setTab('pending'); resetMsgs(); }} className={tabCls('pending')}>
          Pending Approvals
          {pendingCount > 0 && (
            <span className="ml-2 px-1.5 py-0.5 rounded-full bg-yellow-500/30 text-yellow-200 text-xs">
              {pendingCount}
            </span>
          )}
        </button>
        <button onClick={() => { setTab('all'); resetMsgs(); }} className={tabCls('all')}>
          All Users
        </button>
      </div>

      {/* Feedback banners */}
      {successMsg && (
        <div className="flex items-center gap-2 bg-green-500/10 border border-green-500/30
                        text-green-400 rounded-lg px-4 py-2.5 text-sm">
          <CheckCircle size={15} />
          {successMsg}
        </div>
      )}
      {errorMsg && (
        <div className="flex items-center gap-2 bg-red-500/10 border border-red-500/30
                        text-red-400 rounded-lg px-4 py-2.5 text-sm">
          <AlertTriangle size={15} />
          {errorMsg}
        </div>
      )}

      {/* Confirmation prompt for deactivation — deliberately explicit, since this
          terminates the user's session immediately. */}
      {confirmUser && (
        <div className="bg-gray-800 border border-red-500/40 rounded-xl px-5 py-4 space-y-3">
          <div className="flex items-start gap-3">
            <ShieldAlert size={18} className="text-red-400 mt-0.5 shrink-0" />
            <div>
              <p className="text-sm text-white font-medium">
                Deactivate {confirmUser.username}?
              </p>
              <p className="text-xs text-gray-400 mt-1">
                They will be signed out immediately and unable to log in until reactivated.
                Their account and history are preserved.
              </p>
            </div>
          </div>
          <div className="flex items-center gap-2">
            <button
              onClick={() => deactivateMutation.mutate(confirmUser.username)}
              disabled={busyUser === confirmUser.username}
              className="px-3 py-1.5 bg-red-600 hover:bg-red-700 text-white rounded-lg
                         text-xs font-medium transition-colors disabled:opacity-50"
            >
              {busyUser === confirmUser.username ? 'Deactivating…' : 'Deactivate'}
            </button>
            <button
              onClick={() => setConfirmUser(null)}
              className="px-3 py-1.5 bg-gray-700 hover:bg-gray-600 text-gray-300
                         rounded-lg text-xs font-medium transition-colors"
            >
              Cancel
            </button>
          </div>
        </div>
      )}

      {/* Pending approvals tab */}
      {tab === 'pending' && (
        <div className="bg-gray-800 rounded-xl border border-gray-700 overflow-hidden">
          {pendingLoading ? (
            <div className="flex items-center justify-center h-48">
              <div className="animate-spin rounded-full h-8 w-8 border-b-2 border-blue-500" />
            </div>
          ) : pending.length === 0 ? (
            <div className="flex flex-col items-center justify-center h-48 text-gray-400">
              <Users size={32} className="mb-2 opacity-40" />
              <p className="text-sm">No pending requests</p>
            </div>
          ) : (
            <table className="w-full">
              <thead>
                <tr className="border-b border-gray-700">
                  {['Username', 'Email', 'Role', 'Requested', 'Action'].map(h => (
                    <Th key={h}>{h}</Th>
                  ))}
                </tr>
              </thead>
              <tbody>
                {pending.map(u => (
                  <tr key={u.username} className="border-b border-gray-700/50 last:border-0">
                    <td className="px-4 py-3 text-sm font-medium text-white">{u.username}</td>
                    <td className="px-4 py-3 text-sm text-gray-300">{u.email ?? '—'}</td>
                    <td className="px-4 py-3">
                      <span className={`px-2 py-0.5 rounded-full text-xs border font-medium
                                       ${roleBadgeColor(u.role)}`}>
                        {u.role ?? 'viewer'}
                      </span>
                    </td>
                    <td className="px-4 py-3 text-sm text-gray-400">
                      <div className="flex items-center gap-1">
                        <Clock size={12} />
                        {u.created_at ? new Date(u.created_at).toLocaleString() : '—'}
                      </div>
                    </td>
                    <td className="px-4 py-3">
                      <button
                        onClick={() => approveMutation.mutate(u.username)}
                        disabled={busyUser === u.username}
                        className="flex items-center gap-1.5 px-3 py-1.5 bg-green-600/20
                                   hover:bg-green-600/30 text-green-400 border border-green-500/30
                                   rounded-lg text-xs font-medium transition-colors
                                   disabled:opacity-50 disabled:cursor-not-allowed"
                      >
                        <UserCheck size={13} />
                        {busyUser === u.username ? 'Approving…' : 'Approve'}
                      </button>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
        </div>
      )}

      {/* All users tab */}
      {tab === 'all' && (
        <div className="bg-gray-800 rounded-xl border border-gray-700 overflow-hidden">
          {usersLoading ? (
            <div className="flex items-center justify-center h-48">
              <div className="animate-spin rounded-full h-8 w-8 border-b-2 border-blue-500" />
            </div>
          ) : allUsers.length === 0 ? (
            <div className="flex flex-col items-center justify-center h-48 text-gray-400">
              <Users size={32} className="mb-2 opacity-40" />
              <p className="text-sm">No users found</p>
            </div>
          ) : (
            <table className="w-full">
              <thead>
                <tr className="border-b border-gray-700">
                  {['Username', 'Email', 'Role', 'Status', 'Last Login', 'Action'].map(h => (
                    <Th key={h}>{h}</Th>
                  ))}
                </tr>
              </thead>
              <tbody>
                {allUsers.map(u => {
                  const isSelf = me && u.username === me.username;
                  const isBusy = busyUser === u.username;
                  return (
                    <tr key={u.username} className="border-b border-gray-700/50 last:border-0">
                      <td className="px-4 py-3 text-sm font-medium text-white">
                        {u.username}
                        {isSelf && <span className="ml-2 text-xs text-gray-500">(you)</span>}
                      </td>
                      <td className="px-4 py-3 text-sm text-gray-300">{u.email ?? '—'}</td>
                      <td className="px-4 py-3">
                        <span className={`px-2 py-0.5 rounded-full text-xs border font-medium
                                         ${roleBadgeColor(u.role)}`}>
                          {u.role ?? 'viewer'}
                        </span>
                      </td>
                      <td className="px-4 py-3">
                        <span className={`px-2 py-0.5 rounded-full text-xs border font-medium
                                         ${statusBadgeColor(u.status)}`}>
                          {u.status ?? 'unknown'}
                        </span>
                      </td>
                      <td className="px-4 py-3 text-sm text-gray-400">
                        {u.last_login ? new Date(u.last_login).toLocaleString() : 'Never'}
                      </td>
                      <td className="px-4 py-3">
                        {u.status === 'pending' ? (
                          <button
                            onClick={() => approveMutation.mutate(u.username)}
                            disabled={isBusy}
                            className="flex items-center gap-1.5 px-3 py-1.5 bg-green-600/20
                                       hover:bg-green-600/30 text-green-400 border border-green-500/30
                                       rounded-lg text-xs font-medium transition-colors
                                       disabled:opacity-50 disabled:cursor-not-allowed"
                          >
                            <UserCheck size={13} />
                            {isBusy ? 'Approving…' : 'Approve'}
                          </button>
                        ) : u.status === 'disabled' ? (
                          <button
                            onClick={() => reactivateMutation.mutate(u.username)}
                            disabled={isBusy}
                            className="flex items-center gap-1.5 px-3 py-1.5 bg-blue-600/20
                                       hover:bg-blue-600/30 text-blue-400 border border-blue-500/30
                                       rounded-lg text-xs font-medium transition-colors
                                       disabled:opacity-50 disabled:cursor-not-allowed"
                          >
                            <UserPlus size={13} />
                            {isBusy ? 'Reactivating…' : 'Reactivate'}
                          </button>
                        ) : (
                          <button
                            onClick={() => { resetMsgs(); setConfirmUser(u); }}
                            disabled={isBusy || isSelf}
                            title={isSelf ? 'You cannot deactivate your own account' : undefined}
                            className="flex items-center gap-1.5 px-3 py-1.5 bg-red-600/20
                                       hover:bg-red-600/30 text-red-400 border border-red-500/30
                                       rounded-lg text-xs font-medium transition-colors
                                       disabled:opacity-40 disabled:cursor-not-allowed"
                          >
                            <UserX size={13} />
                            Deactivate
                          </button>
                        )}
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          )}
        </div>
      )}
    </div>
  );
}
