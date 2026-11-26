// Copyright (c) 2026 Axara Pty Ltd.
// SPDX-License-Identifier: AGPL-3.0-or-later
import React, { createContext, useContext, useState, useEffect } from 'react';
import axios from 'axios';

const AuthContext = createContext(null);

/**
 * Whether a JWT has passed its `exp` time (or cannot be read).
 *
 * Args:
 *   token: JWT string.
 *
 * Returns:
 *   True when the token should not be used.
 */
function tokenExpired(token) {
  try {
    const b64 = token.split('.')[1].replace(/-/g, '+').replace(/_/g, '/');
    const { exp } = JSON.parse(atob(b64));
    return !exp || exp * 1000 <= Date.now();
  } catch {
    return true;
  }
}

// Pages that call axios directly: an expired or rejected session sends the user to sign in
// (services/api.js does the same for its own instance). Sign-in calls handle 401 themselves.
axios.interceptors.response.use(
  (response) => response,
  (error) => {
    const url = error.config?.url ?? '';
    if (error.response?.status === 401 && !url.includes('/auth/login') && !url.includes('/auth/me')) {
      localStorage.removeItem('auth_user');
      delete axios.defaults.headers.common['Authorization'];
      if (!window.location.pathname.startsWith('/login')) window.location.href = '/login';
    }
    return Promise.reject(error);
  }
);

// Provide the signed-in user, login and logout; restores the session on load.
export function AuthProvider({ children }) {
  const [user, setUser]       = useState(null);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    // Restore a saved session only after checking it, so pages never call the API with a
    // token the server will refuse (expired, or issued before the server was reinstalled).
    const clear = () => localStorage.removeItem('auth_user');
    let parsed = null;
    try { parsed = JSON.parse(localStorage.getItem('auth_user') || 'null'); } catch { clear(); }
    if (!parsed?.token || tokenExpired(parsed.token)) {
      if (parsed) clear();
      setLoading(false);
      return;
    }
    const accept = () => {
      setUser(parsed);
      axios.defaults.headers.common['Authorization'] = `Bearer ${parsed.token}`;
    };
    axios.get('/api/v1/auth/me', { headers: { Authorization: `Bearer ${parsed.token}` } })
      .then(accept)
      .catch(err => {
        if (err.response?.status === 401 || err.response?.status === 403) clear();
        else accept();                    // server unreachable: keep the session, pages retry
      })
      .finally(() => setLoading(false));
  }, []);

  const login = async (username, password) => {
    const { data } = await axios.post('/api/v1/auth/login', { username, password });
    setUser(data);
    localStorage.setItem('auth_user', JSON.stringify(data));
    axios.defaults.headers.common['Authorization'] = `Bearer ${data.token}`;
    return data;
  };

  // Sign in with a token issued elsewhere (e.g. by a plugin's sign-in page); fetch the profile.
  const loginWithToken = async (token) => {
    const { data } = await axios.get('/api/v1/auth/me', {
      headers: { Authorization: `Bearer ${token}` },
    });
    const authUser = { token, username: data.username, email: data.email, role: data.role };
    setUser(authUser);
    localStorage.setItem('auth_user', JSON.stringify(authUser));
    axios.defaults.headers.common['Authorization'] = `Bearer ${token}`;
    return authUser;
  };

  const logout = async () => {
    // Tell the server to invalidate the token (sets token_invalidated_at)
    const stored = localStorage.getItem('auth_user');
    if (stored) {
      try {
        const { username } = JSON.parse(stored);
        await axios.post('/api/v1/auth/logout', { username });
      } catch {
        // Ignore network errors on logout — clear local state regardless
      }
    }
    setUser(null);
    localStorage.removeItem('auth_user');
    delete axios.defaults.headers.common['Authorization'];
  };

  return (
    <AuthContext.Provider value={{ user, login, loginWithToken, logout, loading, isAuthenticated: !!user }}>
      {!loading && children}
    </AuthContext.Provider>
  );
}

// The auth context (must be used inside AuthProvider).
export function useAuth() {
  const ctx = useContext(AuthContext);
  if (!ctx) throw new Error('useAuth must be used inside AuthProvider');
  return ctx;
}
