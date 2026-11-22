// Copyright (c) 2026 Axara Pty Ltd.
// SPDX-License-Identifier: AGPL-3.0-or-later
import { useQuery } from '@tanstack/react-query';
import { useState } from 'react';
import { AlertCircle, Activity, Server, TrendingUp } from 'lucide-react';
import MetricsChart from '../components/MetricsChart';
import IncidentsList from '../components/IncidentsList';
import ServerHealthGrid from '../components/ServerHealthGrid';
import { api } from '../services/api';
import { Link } from 'react-router-dom';

// Summary tile with a value, icon, trend and severity colour.
function StatCard({ title, value, icon: Icon, trend, severity }) {
  const getSeverityColor = () => {
    switch (severity) {
      case 'critical': return 'text-red-400 bg-red-500/20';
      case 'high': return 'text-orange-400 bg-orange-500/20';
      case 'medium': return 'text-yellow-400 bg-yellow-500/20';
      default: return 'text-green-400 bg-green-500/20';
    }
  };

  return (
    <div className="bg-gray-800 rounded-xl border border-gray-700 p-6">
      <div className="flex items-center justify-between">
        <div>
          <p className="text-sm font-medium text-gray-400">{title}</p>
          <p className="text-3xl font-semibold mt-2 text-white">{value}</p>
          {trend && (
            <p className="text-sm text-gray-400 mt-1 flex items-center gap-1">
              <TrendingUp className="w-4 h-4" />
              {trend}
            </p>
          )}
        </div>
        <div className={`p-3 rounded-full ${getSeverityColor()}`}>
          <Icon className="w-8 h-8" />
        </div>
      </div>
    </div>
  );
}

// Home page: summary tiles, server health and metric charts.
function Dashboard() {
  const [selectedServerId, setSelectedServerId] = useState('');

  // Fetch dashboard summary
  const { data: summary, isLoading: summaryLoading } = useQuery({
    queryKey: ['dashboard-summary'],
    queryFn: () => api.get('/dashboard/summary').then(res => res.data),
    refetchInterval: 30000, // Refresh every 30 seconds
  });

  // Fetch recent incidents
  const { data: incidents } = useQuery({
    queryKey: ['recent-incidents'],
    queryFn: () => api.get('/incidents/?limit=10&status=open').then(res => res.data),
    refetchInterval: 30000,
  });

  // Fetch servers
  const { data: serverList = [] } = useQuery({
    queryKey: ['servers-health-dashboard'],
    queryFn: () =>
      api.get('/dashboard/servers-health').then(res => {
        const d = res.data;
        return Array.isArray(d) ? d : (d?.servers ?? []);
      }),
    refetchInterval: 30000,
  });

  if (summaryLoading) {
    return (
      <div className="flex items-center justify-center h-96">
        <div className="animate-spin rounded-full h-12 w-12 border-b-2 border-blue-600"></div>
      </div>
    );
  }

  // API returns snake_case — map to display values
  const openIncidents    = summary?.open_incidents     ?? 0;
  const criticalCount    = summary?.critical_incidents ?? 0;
  const activeServers    = summary?.active_servers     ?? 0;
  const avgCpu           = summary?.avg_cpu            ?? null;
  const avgMemory        = summary?.avg_memory         ?? null;
  const avgDisk          = summary?.avg_disk           ?? null;

  return (
    <div className="space-y-6">
      {/* Page Header */}
      <div>
        <h1 className="text-3xl font-bold text-white">Dashboard</h1>
        <p className="text-gray-400 mt-1">Real-time monitoring overview</p>
      </div>

      {/* Stats Grid */}
      <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-4 gap-6">
        <StatCard
          title="Open Incidents"
          value={openIncidents}
          icon={AlertCircle}
          severity={criticalCount > 0 ? 'critical' : 'low'}
          trend={`${criticalCount} critical`}
        />
        <StatCard
          title="Active Servers"
          value={activeServers}
          icon={Server}
          severity="low"
        />
        <StatCard
          title="Avg CPU"
          value={avgCpu != null ? `${avgCpu}%` : 'N/A'}
          icon={Activity}
          severity={avgCpu > 80 ? 'critical' : avgCpu > 60 ? 'medium' : 'low'}
          trend="last 15 min"
        />
        <StatCard
          title="Avg Disk"
          value={avgDisk != null ? `${avgDisk}%` : 'N/A'}
          icon={TrendingUp}
          severity={avgDisk > 90 ? 'critical' : avgDisk > 75 ? 'medium' : 'low'}
          trend="last 15 min"
        />
      </div>

      {/* Server selector for charts */}
      <div className="flex items-center gap-3">
        <label className="text-sm text-gray-400 font-medium">Server:</label>
        <select
          value={selectedServerId}
          onChange={e => setSelectedServerId(e.target.value)}
          className="px-3 py-2 border border-gray-700 rounded-lg text-sm
             bg-gray-800 text-gray-300 focus:outline-hidden focus:border-blue-500"
        >
          <option value="" className="text-gray-300">All Servers</option>
          {serverList.map(s => (
            <option key={s.id} value={s.id} className="text-gray-300">{s.hostname}</option>
          ))}
        </select>
      </div>

      {/* Charts Row */}
      <div className="grid grid-cols-1 lg:grid-cols-2 gap-6">
        {/* CPU Usage Chart */}
        <div className="bg-gray-800 rounded-xl border border-gray-700 p-5">
          <h2 className="text-sm font-semibold text-white mb-4">CPU Usage (24h)</h2>
          <MetricsChart
            metricType="cpu"
            hours={24}
            serverId={selectedServerId || undefined}
            title="CPU Usage (%)"
          />
        </div>

        {/* Memory Usage Chart */}
        <div className="bg-gray-800 rounded-xl border border-gray-700 p-5">
          <h2 className="text-sm font-semibold text-white mb-4">Memory Usage (24h)</h2>
          <MetricsChart
            metricType="memory"
            hours={24}
            serverId={selectedServerId || undefined}
            title="Memory Usage (%)"
          />
        </div>
      </div>

      {/* Server Health Grid */}
      <div className="bg-gray-800 rounded-xl border border-gray-700 p-5">
        <h2 className="text-sm font-semibold text-white mb-4">Server Health Matrix</h2>
        <ServerHealthGrid
          servers={serverList}
          isLoading={false}
        />
      </div>

      {/* Recent Incidents */}
      <div className="bg-gray-800 rounded-xl border border-gray-700 p-5">
        <div className="flex justify-between items-center mb-4">
          <h2 className="text-sm font-semibold text-white">Recent Incidents</h2>
          <Link to="/incidents"
            className="text-blue-400 hover:text-blue-300 text-sm font-medium"
          >
            View All →
          </Link>
        </div>
        <IncidentsList incidents={incidents?.incidents || []} limit={5} />
      </div>
    </div>
  );
}

export default Dashboard;
