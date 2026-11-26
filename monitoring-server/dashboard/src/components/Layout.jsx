// Copyright (c) 2026 Axara Pty Ltd.
// SPDX-License-Identifier: AGPL-3.0-or-later
import React, { useState, useRef, useEffect } from 'react';
import { useQuery } from '@tanstack/react-query';
import { api } from '../services/api';
import * as edition from '@edition';      // Community: none; other editions: extra pages and banner
import { mergeNav } from '../edition/merge';

import { useBranding, productName, APP_VERSION } from '../hooks/useBranding';
import { Link, useLocation, Outlet, useNavigate } from 'react-router-dom';
import { useAuth } from '../context/AuthContext';
import {
  LayoutDashboard,
  AlertTriangle,
  Server,
  Layers,
  Activity,
  Settings,
  Shield,
  BarChart2,
  Lock,
  Bell,
  Menu,
  X,
  ChevronDown,
  LogOut,
  UserCheck,
  ShieldCheck,
  Ticket
} from 'lucide-react';

const coreNavItems = [
  { path: '/',          label: 'Dashboard',  icon: LayoutDashboard },
  { path: '/incidents', label: 'Incidents',  icon: AlertTriangle },
  { path: '/servers',   label: 'Servers',    icon: Server },
  { path: '/applications',   label: 'Applications',    icon: Layers },
  { path: '/monitors',  label: 'Monitors',   icon: Shield },
  { path: '/metrics',   label: 'Metrics',    icon: Activity },
  { path: '/monitor-report',    label: 'Monitor Report',     icon: BarChart2 },
  { path: '/ssl-report', label: 'SSL Certs',  icon: Lock },
  { path: '/settings',  label: 'Settings',   icon: Settings },
];
const navItems = mergeNav(coreNavItems, edition.navItems);

// One sidebar link, highlighted when its route is active.
function NavItem({ item, collapsed }) {
  const location = useLocation();
  const isActive = item.path === '/'
    ? location.pathname === '/'
    : location.pathname.startsWith(item.path);
  const Icon = item.icon;

  return (
    <Link
      to={item.path}
      className={`flex items-center gap-3 px-3 py-2.5 rounded-lg transition-colors text-sm font-medium
        ${isActive
          ? 'bg-blue-600 text-white'
          : 'text-gray-400 hover:bg-gray-700 hover:text-white'
        }`}
    >
      <Icon size={18} className="shrink-0" />
      {!collapsed && <span>{item.label}</span>}
    </Link>
  );
}

// Collapsible sidebar with the navigation sections and product branding.
function Sidebar({ collapsed, onToggle, branding }) {
  return (
    <aside
      className={`flex flex-col bg-gray-900 border-r border-gray-700 transition-all duration-300
        ${collapsed ? 'w-16' : 'w-60'}`}
    >
      {/* Logo — uses branding.logo_url if provided, else built-in text */}
      <div className="flex items-center justify-between px-3 py-4 border-b border-gray-700">
        {!collapsed && (
          branding.logo_url
            ? <img src={branding.logo_url} alt={productName(branding)}
                   className="h-7 max-w-[140px] object-contain" />
            : <span className="text-white font-bold text-sm">
                {productName(branding)}
              </span>
        )}
        <button
          onClick={onToggle}
          className="text-gray-400 hover:text-white p-1 rounded-sm"
        >
          {collapsed ? <Menu size={18} /> : <X size={18} />}
        </button>
      </div>

      {/* Nav Items */}
      <nav className="flex flex-col gap-1 p-2 flex-1">
        {navItems.map(item => (
          <NavItem key={item.path} item={item} collapsed={collapsed} />
        ))}
      </nav>

      {/* Version + copyright */}
      {!collapsed && (
        <div className="px-4 py-3 border-t border-gray-700 space-y-0.5">
          <span className="text-xs text-gray-500">v{APP_VERSION}</span>
          {branding.copyright && (
            <p className="text-xs text-gray-600 leading-tight">
              {branding.copyright}
            </p>
          )}
        </div>
      )}
    </aside>
  );
}

// Signed-in user's menu (profile details and sign out).
function UserMenu() {
  const { user, logout } = useAuth();
  const navigate         = useNavigate();
  const [open, setOpen]  = useState(false);
  const ref              = useRef(null);

  // Close dropdown when clicking outside
  useEffect(() => {
    const handler = (e) => { if (ref.current && !ref.current.contains(e.target)) setOpen(false); };
    document.addEventListener('mousedown', handler);
    return () => document.removeEventListener('mousedown', handler);
  }, []);

  const handleSignOut = async () => {
    setOpen(false);
    await logout();
    navigate('/login');
  };

  const initials = user?.username
    ? user.username.slice(0, 2).toUpperCase()
    : 'OP';

  const roleBadgeColor = {
    admin:    'bg-red-500/20 text-red-300',
    operator: 'bg-yellow-500/20 text-yellow-300',
    viewer:   'bg-blue-500/20 text-blue-300',
  }[user?.role] ?? 'bg-gray-500/20 text-gray-300';

  return (
    <div className="relative" ref={ref}>
      <button
        onClick={() => setOpen(o => !o)}
        className="flex items-center gap-2 text-sm text-gray-300 hover:text-white"
      >
        <div className="w-7 h-7 rounded-full bg-blue-600 flex items-center justify-center
                        text-white text-xs font-bold">
          {initials}
        </div>
        <ChevronDown size={14} className={`transition-transform ${open ? 'rotate-180' : ''}`} />
      </button>

      {open && (
        <div className="absolute right-0 top-full mt-2 w-52 bg-gray-800 border border-gray-700
                        rounded-xl shadow-xl z-50 overflow-hidden">
          {/* User info */}
          <div className="px-4 py-3 border-b border-gray-700">
            <p className="text-sm font-semibold text-white">{user?.username ?? '—'}</p>
            <p className="text-xs text-gray-400 truncate">{user?.email ?? ''}</p>
            <span className={`inline-block mt-1.5 px-2 py-0.5 rounded-sm text-xs font-medium ${roleBadgeColor}`}>
              {user?.role ?? 'unknown'}
            </span>
          </div>
          {/* Manage Users — admin only */}
          {user?.role === 'admin' && (
            <button
              onClick={() => { setOpen(false); navigate('/user-approvals'); }}
              className="w-full flex items-center gap-2 px-4 py-3 text-sm text-gray-300
                         hover:bg-gray-700 hover:text-white transition-colors"
            >
              <UserCheck size={15} />
              Manage Users
            </button>
          )}
          {/* Agent enrolment — admin only */}
          {user?.role === 'admin' && (
            <button
              onClick={() => { setOpen(false); navigate('/agent-enrolment'); }}
              className="w-full flex items-center gap-2 px-4 py-3 text-sm text-gray-300
                         hover:bg-gray-700 hover:text-white transition-colors"
            >
              <Ticket size={15} />
              Agent enrolment
            </button>
          )}
          {/* Audit Log — admin only */}
          {user?.role === 'admin' && (
            <button
              onClick={() => { setOpen(false); navigate('/audit-log'); }}
              className="w-full flex items-center gap-2 px-4 py-3 text-sm text-gray-300
                         hover:bg-gray-700 hover:text-white transition-colors"
            >
              <ShieldCheck size={15} />
              Audit Log
            </button>
          )}
          {/* Edition admin entries */}
          {user?.role === 'admin' && edition.adminMenuItems.map(({ path, label, icon: Icon }) => (
            <button
              key={path}
              onClick={() => { setOpen(false); navigate(path); }}
              className="w-full flex items-center gap-2 px-4 py-3 text-sm text-gray-300
                         hover:bg-gray-700 hover:text-white transition-colors"
            >
              <Icon size={15} />
              {label}
            </button>
          ))}
          {/* Sign out */}
          <button
            onClick={handleSignOut}
            className="w-full flex items-center gap-2 px-4 py-3 text-sm text-gray-300
                       hover:bg-gray-700 hover:text-white transition-colors"
          >
            <LogOut size={15} />
            Sign Out
          </button>
        </div>
      )}
    </div>
  );
}

// Header bell showing the open-incident count; opens the incidents page.
function OpenIncidentsBell() {
  const navigate = useNavigate();
  const { data } = useQuery({
    queryKey: ['open-incidents-count'],
    queryFn:  () => api.get('/incidents/?limit=1&status=open,in_progress').then(r => r.data),
    refetchInterval: 30000,
  });
  const count = data?.total ?? 0;

  return (
    <button
      onClick={() => navigate('/incidents')}
      className="relative text-gray-400 hover:text-white"
      title="View open incidents"
    >
      <Bell size={18} />
      {count > 0 && (
        <span className="absolute -top-1 -right-1 min-w-4 h-4 px-0.5
                         bg-red-500 rounded-full text-white
                         text-xs flex items-center justify-center">
          {count > 99 ? '99+' : count}
        </span>
      )}
    </button>
  );
}

// Page header with the current page title, the incident bell and the user menu.
function Header() {
  const location = useLocation();

  return (
    <header className="flex items-center justify-between px-6 py-3 bg-gray-800 border-b border-gray-700">
      {/* Page title */}
      <h1 className="text-white font-semibold text-base">
        {navItems.find(i => i.path === location.pathname)?.label ?? 'Dashboard'}
      </h1>

      {/* Right side */}
      <div className="flex items-center gap-4">
        {/* Status indicator */}
        <div className="flex items-center gap-1.5 text-xs text-green-400">
          <span className="w-2 h-2 rounded-full bg-green-400 animate-pulse" />
          System Healthy
        </div>

        {/* Notifications — open incident count */}
        <OpenIncidentsBell />

        {/* User dropdown */}
        <UserMenu />
      </div>
    </header>
  );
}

// Shell around every signed-in page: sidebar, header and the routed page.
export default function Layout() {
  const [collapsed, setCollapsed] = useState(false);
  const branding = useBranding();

  return (
    <div className="flex h-screen bg-gray-950 text-white overflow-hidden">
      {/* Sidebar */}
      <Sidebar collapsed={collapsed} onToggle={() => setCollapsed(c => !c)} branding={branding} />

      {/* Main content */}
      <div className="flex flex-col flex-1 overflow-hidden">
        <Header />
        {edition.Banner && <edition.Banner />}

        {/* Page content — Outlet renders the matched child route */}
        <main className="flex-1 overflow-y-auto p-6">
          <Outlet />
        </main>
      </div>
    </div>
  );
}
