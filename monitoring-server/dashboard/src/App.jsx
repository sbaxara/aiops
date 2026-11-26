// Copyright (c) 2026 Axara Pty Ltd.
// SPDX-License-Identifier: AGPL-3.0-or-later
import { BrowserRouter as Router, Routes, Route, Navigate } from 'react-router-dom';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import Layout from './components/Layout';
import Dashboard from './pages/Dashboard';
import Incidents from './pages/Incidents';
import IncidentDetail from './pages/IncidentDetail';
import Servers from './pages/Servers';
import ServerDetail from './pages/ServerDetail';
import Applications from './pages/Applications';
import Settings from './pages/Configuration';
import Monitors from './pages/Monitors';
import MonitorDetail from './pages/MonitorDetail';
import MonitorForm from './pages/MonitorForm';
import Metrics     from './pages/Metrics';
import MonitorReport  from './pages/MonitorReport';
import SSLReport      from './pages/SSLReport';
import Login from './pages/Login';
import Register from './pages/Register';
import UserApprovals from './pages/UserApprovals';
import AuditLog from './pages/AuditLog';
import AgentEnrolment from './pages/AgentEnrolment';
import { AuthProvider, useAuth } from './context/AuthContext';
import * as edition from '@edition';      // Community: none; other editions: their pages
import { addErrorCodeHandler } from './services/api';
import './index.css';

// Let the edition module react to coded API errors (none in Community).
if (edition.onErrorCode) addErrorCodeHandler(edition.onErrorCode);

const queryClient = new QueryClient({
  defaultOptions: {
    queries: {
      refetchOnWindowFocus: false,
      retry: 1,
      staleTime: 30000,
    },
  },
});

// Render the page only when signed in; otherwise go to the login page.
function ProtectedRoute({ children }) {
  const { isAuthenticated } = useAuth();
  return isAuthenticated ? children : <Navigate to="/login" />;
}

// Application root: query client, auth context and the route table.
function App() {
  return (
    <QueryClientProvider client={queryClient}>
      <AuthProvider>
        <Router>
          <Routes>
            <Route path="/login"    element={<Login />} />
            <Route path="/register" element={<Register />} />
            {edition.publicRoutes.map(r => <Route key={r.path} path={r.path} element={r.element} />)}
            <Route
              path="/"
              element={
                <ProtectedRoute>
                  <Layout />
                </ProtectedRoute>
              }
            >
              <Route index element={<Dashboard />} />
              <Route path="incidents" element={<Incidents />} />
              <Route path="incidents/:id" element={<IncidentDetail />} />
              <Route path="servers" element={<Servers />} />
              <Route path="servers/:id" element={<ServerDetail />} />
              <Route path="applications" element={<Applications />} />
              <Route path="settings" element={<Settings />} />
              <Route path="metrics"           element={<Metrics />} />
              <Route path="monitors"          element={<Monitors />} />
              <Route path="monitors/new"      element={<MonitorForm />} />
              <Route path="monitors/:id"      element={<MonitorDetail />} />
              <Route path="monitors/:id/edit" element={<MonitorForm />} />
              <Route path="monitor-report"    element={<MonitorReport />} />
              <Route path="ssl-report"        element={<SSLReport />} />
              <Route path="user-approvals"    element={<UserApprovals />} />
              <Route path="audit-log"         element={<AuditLog />} />
              <Route path="agent-enrolment"   element={<AgentEnrolment />} />
              {edition.routes.map(r => <Route key={r.path} path={r.path} element={r.element} />)}
              <Route path="*" element={<Navigate to="/" replace />} />
            </Route>
          </Routes>
        </Router>
      </AuthProvider>
    </QueryClientProvider>
  );
}

export default App;
