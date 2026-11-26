// Copyright (c) 2026 Axara Pty Ltd.
// SPDX-License-Identifier: AGPL-3.0-or-later
import React, { useState } from 'react';
import { useNavigate, Navigate, Link, useLocation } from 'react-router-dom';
import * as edition from '@edition';
import { useAuth } from '../context/AuthContext';
import { useBranding, productName, APP_VERSION } from '../hooks/useBranding';
import { AlertTriangle, Eye, EyeOff, Activity, KeyRound } from 'lucide-react';

// Sign-in page: local accounts, plus any sign-in options the edition module adds.
export default function Login() {
  const branding = useBranding();
  const { login, isAuthenticated } = useAuth();
  const navigate = useNavigate();
  const location = useLocation();

  const [username, setUsername] = useState('');
  const [password, setPassword] = useState('');
  const [showPass, setShowPass] = useState(false);
  // A message passed in the URL by the edition module (none in Community).
  const [error, setError]       = useState(() => edition.loginMessage(new URLSearchParams(location.search)));
  const [loading, setLoading]   = useState(false);
  const {
    providers, localRegistration: localReg, usernamePlaceholder, passwordHint,
  } = edition.useLoginOptions();

  if (isAuthenticated) return <Navigate to="/" replace />;

  const redirectProviders = providers.filter(p => p.type === 'redirect');

  const handleSubmit = async (e) => {
    e.preventDefault();
    setError('');
    setLoading(true);
    try {
      await login(username, password);
      navigate('/');
    } catch (err) {
      const d = err.response?.data?.detail;
      setError((d && typeof d === 'object' ? d.message : d) ?? 'Login failed. Check your credentials.');
    } finally {
      setLoading(false);
    }
  };

  return (
    <div className="min-h-screen bg-gray-950 flex items-center justify-center px-4">
      <div className="w-full max-w-sm">

        <div className="flex flex-col items-center mb-8">
          <div className="w-12 h-12 bg-blue-600 rounded-xl flex items-center justify-center mb-3">
            <Activity size={24} className="text-white" />
          </div>
          <h1 className="text-white text-xl font-bold">{productName(branding)}</h1>
          <p className="text-gray-400 text-sm mt-1">Sign in to your account</p>
        </div>

        <div className="bg-gray-800 rounded-xl border border-gray-700 p-6">
          {error && (
            <div className="flex items-center gap-2 bg-red-500/10 border border-red-500/30
                            text-red-400 rounded-lg px-3 py-2.5 mb-4 text-sm">
              <AlertTriangle size={15} />
              {error}
            </div>
          )}

          <form onSubmit={handleSubmit} className="space-y-4">
            <div>
              <label className="block text-xs font-medium text-gray-300 mb-1.5">Username</label>
              <input
                type="text"
                value={username}
                onChange={e => setUsername(e.target.value)}
                required
                autoFocus
                placeholder={usernamePlaceholder || 'admin'}
                autoComplete="username"
                className="w-full bg-gray-700 border border-gray-600 rounded-lg px-3 py-2.5
                           text-sm text-white placeholder-gray-500
                           focus:outline-hidden focus:border-blue-500 focus:ring-1 focus:ring-blue-500"
              />
            </div>

            <div>
              <label className="block text-xs font-medium text-gray-300 mb-1.5">Password</label>
              <div className="relative">
                <input
                  type={showPass ? 'text' : 'password'}
                  value={password}
                  onChange={e => setPassword(e.target.value)}
                  required
                  placeholder="••••••••"
                  autoComplete="current-password"
                  className="w-full bg-gray-700 border border-gray-600 rounded-lg px-3 py-2.5
                             text-sm text-white placeholder-gray-500 pr-10
                             focus:outline-hidden focus:border-blue-500 focus:ring-1 focus:ring-blue-500"
                />
                <button
                  type="button"
                  onClick={() => setShowPass(s => !s)}
                  className="absolute right-3 top-1/2 -translate-y-1/2 text-gray-400 hover:text-white"
                >
                  {showPass ? <EyeOff size={15} /> : <Eye size={15} />}
                </button>
              </div>
              {passwordHint && (
                <p className="text-xs text-gray-500 mt-1.5">{passwordHint}</p>
              )}
            </div>

            <button
              type="submit"
              disabled={loading}
              className="w-full bg-blue-600 hover:bg-blue-700 disabled:opacity-50
                         disabled:cursor-not-allowed text-white font-medium text-sm
                         rounded-lg py-2.5 transition-colors"
            >
              {loading ? 'Signing in...' : 'Sign In'}
            </button>
          </form>

          {redirectProviders.length > 0 && (
            <>
              <div className="flex items-center gap-3 my-4">
                <div className="flex-1 h-px bg-gray-700" />
                <span className="text-xs text-gray-500">or</span>
                <div className="flex-1 h-px bg-gray-700" />
              </div>
              <div className="space-y-2">
                {redirectProviders.map(p => (
                  <button
                    key={p.id}
                    type="button"
                    onClick={() => window.location.assign(p.login_url)}
                    className="w-full flex items-center justify-center gap-2 bg-gray-700 hover:bg-gray-600
                               border border-gray-600 text-white text-sm rounded-lg py-2.5 transition-colors"
                  >
                    <KeyRound size={15} />
                    {p.label}
                  </button>
                ))}
              </div>
            </>
          )}
        </div>

        {localReg && (
          <p className="text-center text-xs text-gray-400 mt-4">
            Don't have an account?{' '}
            <Link to="/register" className="text-blue-400 hover:text-blue-300">Request access</Link>
          </p>
        )}

        <p className="text-center text-xs text-gray-500 mt-3">{productName(branding)} v{APP_VERSION}</p>
      </div>
    </div>
  );
}
