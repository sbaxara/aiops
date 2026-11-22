// Copyright (c) 2026 Axara Pty Ltd.
// SPDX-License-Identifier: AGPL-3.0-or-later
// dashboard/src/pages/Metrics.jsx
import React, { useState } from 'react';
import { useQuery } from '@tanstack/react-query';
import { api } from '../services/api';
import MetricsChart from '../components/MetricsChart';
import { Activity } from 'lucide-react';

const METRIC_TYPES = ['cpu', 'memory', 'disk', 'network'];
const METRIC_LABELS = { cpu: 'CPU', memory: 'Memory', disk: 'Disk', network: 'Network' };
const HOURS_OPTIONS = [
  { label: 'Last 1h',  value: 1    },
  { label: 'Last 6h',  value: 6    },
  { label: 'Last 1d',  value: 24   },
  { label: 'Last 2d',  value: 48   },
  { label: 'Last 3d',  value: 72   },
  { label: 'Last 7d',  value: 168  },
  { label: 'Last 30d', value: 720  },
  { label: 'Last 90d', value: 2160 },
];

// Metrics page: choose a metric type and time range and see the charts.
export default function Metrics() {
  const [selectedType,  setType]  = useState('cpu');
  const [selectedHours, setHours] = useState(24);

  const { data: servers = [] } = useQuery({
    queryKey: ['servers-health-list'],   // separate key from Dashboard to avoid cache shape clash
    queryFn: () =>
      api.get('/dashboard/servers-health').then(r => {
        const d = r.data;
        // Normalise: endpoint returns {servers:[...]} — always extract the array
        return Array.isArray(d) ? d : (d?.servers ?? []);
      }),
    refetchInterval: 30000,
  });

  return (
    <div className="space-y-6">
      <div className="flex items-center gap-3">
        <Activity size={20} className="text-blue-400" />
        <h1 className="text-3xl font-bold text-white">Metrics</h1>
      </div>

      {/* Controls */}
      <div className="flex flex-wrap gap-3">
        <div className="flex rounded-lg overflow-hidden border border-gray-700">
          {METRIC_TYPES.map(t => (
            <button
              key={t}
              onClick={() => setType(t)}
              className={`px-4 py-2 text-sm transition-colors
                ${selectedType === t
                  ? 'bg-blue-600 text-white'
                  : 'bg-gray-800 text-gray-400 hover:text-white'}`}
            >
              {METRIC_LABELS[t] ?? t}
            </button>
          ))}
        </div>

        <div className="flex flex-wrap rounded-lg border border-gray-700">
          {HOURS_OPTIONS.map(({ label, value }) => (
            <button
              key={value}
              onClick={() => setHours(value)}
              className={`px-4 py-2 text-sm transition-colors
                ${selectedHours === value
                  ? 'bg-blue-600 text-white'
                  : 'bg-gray-800 text-gray-400 hover:text-white'}`}
            >
              {label}
            </button>
          ))}
        </div>
      </div>

      {/* Overall chart */}
      <div className="bg-gray-800 rounded-xl border border-gray-700 p-5">
        <h2 className="text-sm font-semibold text-white mb-4">
          {METRIC_LABELS[selectedType] ?? selectedType} Usage — All Servers ({selectedHours}h)
        </h2>
        <MetricsChart metricType={selectedType} hours={selectedHours} />
      </div>

      {/* Per-server charts */}
      {servers.length > 0 && (
        <div>
          <h2 className="text-base font-semibold text-white mb-4">Per Server</h2>
          <div className="grid grid-cols-1 lg:grid-cols-2 gap-4">
            {servers.map(server => (
              <div key={server.id} className="bg-gray-800 rounded-xl border border-gray-700 p-5">
                <p className="text-sm font-medium text-white mb-1">{server.hostname}</p>
                <p className="text-xs text-gray-400 mb-4">{server.ip_address}</p>
                <MetricsChart
                  metricType={selectedType}
                  hours={selectedHours}
                  serverId={server.id}
                />
              </div>
            ))}
          </div>
        </div>
      )}
    </div>
  );
}
