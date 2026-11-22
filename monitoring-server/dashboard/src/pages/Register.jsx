// Copyright (c) 2026 Axara Pty Ltd.
// SPDX-License-Identifier: AGPL-3.0-or-later
import React, { useState } from 'react';
import { Link, Navigate } from 'react-router-dom';
import { useAuth } from '../context/AuthContext';
import { useBranding, productName, APP_VERSION } from '../hooks/useBranding';
import { AlertTriangle, CheckCircle, Eye, EyeOff, Activity } from 'lucide-react';
import { api } from '../services/api';

const ROLES = [
  { value: 'viewer',   label: 'Viewer   — read-only access' },
  { value: 'operator', label: 'Operator — can manage incidents and monitors' },
  { value: 'admin',    label: 'Admin    — full system access' },
];

// Self-registration page (the account waits for admin approval).
export default function Register() {
  const branding = useBranding();
  const { isAuthenticated } = useAuth();

  const [form, setForm] = useState({
    username: '', email: '', password: '', confirmPassword: '', role: 'viewer',
  });
  const [showPass,    setShowPass]    = useState(false);
  const [showConfirm, setShowConfirm] = useState(false);
  const [error,       setError]       = useState('');
  const [success,     setSuccess]     = useState(false);
  const [loading,     setLoading]     = useState(false);

  if (isAuthenticated) return <Navigate to="/" replace />;

  const set = (field) => (e) => setForm(f => ({ ...f, [field]: e.target.value }));

  const handleSubmit = async (e) => {
    e.preventDefault();
    setError('');

    if (form.password !== form.confirmPassword) {
      setError('Passwords do not match.');
      return;
    }
    if (form.password.length < 8) {
      setError('Password must be at least 8 characters.');
      return;
    }

    setLoading(true);
    try {
      await api.post('/auth/register', {
        username: form.username,
        email:    form.email,
        password: form.password,
        role:     form.role,
      });
      setSuccess(true);
    } catch (err) {
      setError(err.response?.data?.detail ?? 'Registration failed. Please try again.');
    } finally {
      setLoading(false);
    }
  };

  return (
    <div className="min-h-screen bg-gray-950 flex items-center justify-center px-4">
      <div className="w-full max-w-sm">

        {/* Logo */}
        <div className="flex flex-col items-center mb-8">
          <div className="w-12 h-12 bg-blue-600 rounded-xl flex items-center justify-center mb-3">
            <Activity size={24} className="text-white" />
          </div>
          <h1 className="text-white text-xl font-bold">{productName(branding)}</h1>
          <p className="text-gray-400 text-sm mt-1">Request access</p>
        </div>

        <div className="bg-gray-800 rounded-xl border border-gray-700 p-6">

          {/* Success state */}
          {success ? (
            <div className="space-y-4">
              <div className="flex items-start gap-3 bg-green-500/10 border border-green-500/30
                              text-green-400 rounded-lg px-4 py-3 text-sm">
                <CheckCircle size={16} className="shrink-0 mt-0.5" />
                <div>
                  <p className="font-medium">Registration submitted</p>
                  <p className="mt-1 text-green-300/80">
                    Your account is pending admin approval. You will be able to
                    sign in once your request has been reviewed.
                  </p>
                </div>
              </div>
              <Link
                to="/login"
                className="block text-center w-full bg-gray-700 hover:bg-gray-600
                           text-white text-sm font-medium rounded-lg py-2.5 transition-colors"
              >
                Back to Sign In
              </Link>
            </div>
          ) : (
            <>
              {error && (
                <div className="flex items-center gap-2 bg-red-500/10 border border-red-500/30
                                text-red-400 rounded-lg px-3 py-2.5 mb-4 text-sm">
                  <AlertTriangle size={15} />
                  {error}
                </div>
              )}

              <form onSubmit={handleSubmit} className="space-y-4">

                {/* Username */}
                <div>
                  <label className="block text-xs font-medium text-gray-300 mb-1.5">
                    Username
                  </label>
                  <input
                    type="text" value={form.username} onChange={set('username')}
                    required autoFocus placeholder="johndoe"
                    className="w-full bg-gray-700 border border-gray-600 rounded-lg px-3 py-2.5
                               text-sm text-white placeholder-gray-500
                               focus:outline-hidden focus:border-blue-500 focus:ring-1 focus:ring-blue-500"
                  />
                </div>

                {/* Email */}
                <div>
                  <label className="block text-xs font-medium text-gray-300 mb-1.5">
                    Email
                  </label>
                  <input
                    type="email" value={form.email} onChange={set('email')}
                    required placeholder="john@example.com"
                    className="w-full bg-gray-700 border border-gray-600 rounded-lg px-3 py-2.5
                               text-sm text-white placeholder-gray-500
                               focus:outline-hidden focus:border-blue-500 focus:ring-1 focus:ring-blue-500"
                  />
                </div>

                {/* Role */}
                <div>
                  <label className="block text-xs font-medium text-gray-300 mb-1.5">
                    Role
                  </label>
                  <select
                    value={form.role} onChange={set('role')} required
                    className="w-full bg-gray-700 border border-gray-600 rounded-lg px-3 py-2.5
                               text-sm text-white
                               focus:outline-hidden focus:border-blue-500 focus:ring-1 focus:ring-blue-500"
                  >
                    {ROLES.map(r => (
                      <option key={r.value} value={r.value}>{r.label}</option>
                    ))}
                  </select>
                </div>

                {/* Password */}
                <div>
                  <label className="block text-xs font-medium text-gray-300 mb-1.5">
                    Password
                  </label>
                  <div className="relative">
                    <input
                      type={showPass ? 'text' : 'password'}
                      value={form.password} onChange={set('password')}
                      required minLength={8} placeholder="Min. 8 characters"
                      className="w-full bg-gray-700 border border-gray-600 rounded-lg px-3 py-2.5
                                 text-sm text-white placeholder-gray-500 pr-10
                                 focus:outline-hidden focus:border-blue-500 focus:ring-1 focus:ring-blue-500"
                    />
                    <button type="button" onClick={() => setShowPass(s => !s)}
                      className="absolute right-3 top-1/2 -translate-y-1/2 text-gray-400 hover:text-white">
                      {showPass ? <EyeOff size={15} /> : <Eye size={15} />}
                    </button>
                  </div>
                </div>

                {/* Confirm Password */}
                <div>
                  <label className="block text-xs font-medium text-gray-300 mb-1.5">
                    Confirm Password
                  </label>
                  <div className="relative">
                    <input
                      type={showConfirm ? 'text' : 'password'}
                      value={form.confirmPassword} onChange={set('confirmPassword')}
                      required placeholder="Re-enter password"
                      className="w-full bg-gray-700 border border-gray-600 rounded-lg px-3 py-2.5
                                 text-sm text-white placeholder-gray-500 pr-10
                                 focus:outline-hidden focus:border-blue-500 focus:ring-1 focus:ring-blue-500"
                    />
                    <button type="button" onClick={() => setShowConfirm(s => !s)}
                      className="absolute right-3 top-1/2 -translate-y-1/2 text-gray-400 hover:text-white">
                      {showConfirm ? <EyeOff size={15} /> : <Eye size={15} />}
                    </button>
                  </div>
                </div>

                <button
                  type="submit" disabled={loading}
                  className="w-full bg-blue-600 hover:bg-blue-700 disabled:opacity-50
                             disabled:cursor-not-allowed text-white font-medium text-sm
                             rounded-lg py-2.5 transition-colors"
                >
                  {loading ? 'Submitting...' : 'Request Access'}
                </button>
              </form>

              <p className="text-center text-xs text-gray-500 mt-4">
                Already have an account?{' '}
                <Link to="/login" className="text-blue-400 hover:text-blue-300">
                  Sign in
                </Link>
              </p>
            </>
          )}
        </div>

        <p className="text-center text-xs text-gray-500 mt-6">
          {productName(branding)} v{APP_VERSION}
        </p>
      </div>
    </div>
  );
}
