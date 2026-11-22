// Copyright (c) 2026 Axara Pty Ltd.
// SPDX-License-Identifier: AGPL-3.0-or-later
import { useQuery } from '@tanstack/react-query';
import { LineChart, Line, AreaChart, Area, XAxis, YAxis, CartesianGrid, Tooltip, Legend, ResponsiveContainer } from 'recharts';
import { format } from 'date-fns';
import { api } from '../services/api';

/**
 * Pick a single display unit for the whole chart based on the largest value
 * being plotted. Keeps Y-axis ticks in one unit (e.g. all in MB/s) rather
 * than mixing KB and MB on a single axis. SI thresholds (1000) — the
 * convention for network throughput.
 *
 *   pickAxisUnit(5922152) -> { unit: 'MB/s', divisor: 1_000_000, decimals: 2 }
 *   pickAxisUnit(132980)  -> { unit: 'KB/s', divisor: 1_000,     decimals: 1 }
 *   pickAxisUnit(450)     -> { unit: 'B/s',  divisor: 1,         decimals: 0 }
 */
function pickAxisUnit(maxBytesPerSec) {
  if (maxBytesPerSec >= 1_000_000) return { unit: 'MB/s', divisor: 1_000_000, decimals: 2 };
  if (maxBytesPerSec >= 1_000)     return { unit: 'KB/s', divisor: 1_000,     decimals: 1 };
  return { unit: 'B/s', divisor: 1, decimals: 0 };
}

// Time-series chart of one metric type for a monitor or server over the last `hours`.
function MetricsChart({ metricType, monitorConfigId, serverId, hours = 24, chartType = 'area', title }) {
  const { data, isLoading, error } = useQuery({
    queryKey: ['metrics-timeseries', metricType, monitorConfigId, serverId, hours],
    queryFn: async () => {
      if (monitorConfigId) {
        const response = await api.get(`/metrics/timeseries/${monitorConfigId}?hours=${hours}`);
        return response.data;
      }

      // Network with a known server: the agent has already computed per-second
      // rates from psutil counters and shipped them under details.rates. Use those
      // values directly rather than re-deriving from cumulative GB counters.
      // Both this branch and the all-servers branch now produce datapoints in
      // canonical B/s; the chart's formatter chooses display units at render time.
      if (metricType === 'network' && serverId) {
        const response = await api.get(
          `/metrics/recent/${serverId}?hours=${hours}&metric_type=network`
        );
        const rawMetrics = response.data?.metrics ?? [];
        // rawMetrics is sorted newest-first; build datapoints then reverse so the
        // chart renders oldest -> newest (left -> right), matching the all-servers
        // branch which the API delivers in ascending time order.
        const datapoints = [];
        for (const m of rawMetrics) {
          const rates = m.details?.rates ?? {};
          datapoints.push({
            timestamp: m.timestamp,
            sent:      Number(rates.bytes_sent_per_sec) || 0,
            recv:      Number(rates.bytes_recv_per_sec) || 0,
            status:    m.status,
          });
        }
        datapoints.reverse();
        return { datapoints };
      }

      // All other metric types: dashboard aggregate endpoint. For network this
      // endpoint now also returns 'sent' / 'recv' in B/s (was MB/s before the
      // canonical-unit fix); for cpu/memory/disk it returns 'value' as before.
      const params = new URLSearchParams({ type: metricType, hours });
      if (serverId) params.append('server_id', serverId);
      const response = await api.get(`/dashboard/metrics-timeseries?${params}`);
      return response.data;
    },
    refetchInterval: 60000,
  });

  if (isLoading) {
    return (
      <div className="flex items-center justify-center h-64">
        <div className="animate-spin rounded-full h-8 w-8 border-b-2 border-blue-600"></div>
      </div>
    );
  }

  if (error) {
    return (
      <div className="flex items-center justify-center h-64 text-red-600">
        <p>Failed to load metrics: {error.message}</p>
      </div>
    );
  }

  const datapoints = data?.datapoints || [];

  if (datapoints.length === 0) {
    return (
      <div className="flex items-center justify-center h-64 text-gray-500">
        <p>No data available</p>
      </div>
    );
  }

  const isNetwork = metricType === 'network';

  const chartData = datapoints.map(point => ({
    timestamp: new Date(point.timestamp).getTime(),
    value:     point.value,
    sent:      point.sent,
    recv:      point.recv,
    status:    point.status,
  }));

  const colorMap = {
    cpu:     '#3b82f6',
    memory:  '#10b981',
    disk:    '#f59e0b',
    network: '#8b5cf6',
  };
  const color     = colorMap[metricType] ?? '#6366f1';
  const colorRecv = '#06b6d4';

  const formatXAxis = (ts) => format(new Date(ts), 'HH:mm');

  // For network: pick ONE axis unit from the largest value being plotted, so
  // the Y-axis ticks are all in the same unit. Tooltip uses the same unit so
  // on-hover values and axis ticks always agree.
  let axisUnit = null;
  if (isNetwork) {
    const maxVal = chartData.reduce((m, p) => {
      const s = Number(p.sent) || 0;
      const r = Number(p.recv) || 0;
      return Math.max(m, s, r);
    }, 0);
    axisUnit = pickAxisUnit(maxVal);
  }

  // Tooltip renderer (a render function, not a component, so it is not recreated as one).
  const renderTooltip = ({ active, payload, label }) => {
    if (!active || !payload?.length) return null;
    return (
      <div className="bg-white p-3 border border-gray-200 rounded-sm shadow-lg min-w-[140px]">
        <p className="text-sm text-gray-600 mb-1">
          {format(new Date(label), 'MMM dd, HH:mm')}
        </p>
        {isNetwork ? (
          <>
            {(() => {
              const sentBytes = payload.find(p => p.dataKey === 'sent')?.value ?? 0;
              const recvBytes = payload.find(p => p.dataKey === 'recv')?.value ?? 0;
              const sentScaled = (Number(sentBytes) || 0) / axisUnit.divisor;
              const recvScaled = (Number(recvBytes) || 0) / axisUnit.divisor;
              return (
                <>
                  <p className="text-sm font-semibold" style={{ color }}>
                    ↑ TX: {sentScaled.toFixed(axisUnit.decimals)} {axisUnit.unit}
                  </p>
                  <p className="text-sm font-semibold" style={{ color: colorRecv }}>
                    ↓ RX: {recvScaled.toFixed(axisUnit.decimals)} {axisUnit.unit}
                  </p>
                </>
              );
            })()}
          </>
        ) : (
          <>
            <p className="text-sm font-semibold" style={{ color }}>
              {(payload[0]?.value ?? 0).toFixed(2)}%
            </p>
            <p className="text-xs text-gray-500 capitalize">
              Status: {payload[0]?.payload?.status}
            </p>
          </>
        )}
      </div>
    );
  };

  if (isNetwork) {
    const yTick = (v) => {
      const scaled = (Number(v) || 0) / axisUnit.divisor;
      return `${scaled.toFixed(axisUnit.decimals)} ${axisUnit.unit}`;
    };
    return (
      <div>
        {title && <h3 className="text-sm font-semibold text-gray-700 mb-3">{title}</h3>}
        <ResponsiveContainer width="100%" height={300}>
          <LineChart data={chartData} margin={{ top: 10, right: 30, left: 10, bottom: 0 }}>
            <CartesianGrid strokeDasharray="3 3" stroke="#f0f0f0" />
            <XAxis dataKey="timestamp" tickFormatter={formatXAxis}
              stroke="#9ca3af" style={{ fontSize: '12px' }} />
            <YAxis stroke="#9ca3af" style={{ fontSize: '12px' }}
              tickFormatter={yTick} width={75} />
            <Tooltip content={renderTooltip} />
            <Legend formatter={v => v === 'sent' ? '↑ Transmit' : '↓ Receive'} />
            <Line type="monotone" dataKey="sent" stroke={color}
              strokeWidth={2} dot={false} name="sent" />
            <Line type="monotone" dataKey="recv" stroke={colorRecv}
              strokeWidth={2} dot={false} name="recv" />
          </LineChart>
        </ResponsiveContainer>
      </div>
    );
  }

  return (
    <div>
      {title && (
        <h3 className="text-sm font-semibold text-gray-700 mb-3">{title}</h3>
      )}
      <ResponsiveContainer width="100%" height={300}>
        {chartType === 'area' ? (
          <AreaChart data={chartData} margin={{ top: 10, right: 30, left: 0, bottom: 0 }}>
            <defs>
              <linearGradient id={`color-${metricType}`} x1="0" y1="0" x2="0" y2="1">
                <stop offset="5%"  stopColor={color} stopOpacity={0.8}/>
                <stop offset="95%" stopColor={color} stopOpacity={0.1}/>
              </linearGradient>
            </defs>
            <CartesianGrid strokeDasharray="3 3" stroke="#f0f0f0" />
            <XAxis dataKey="timestamp" tickFormatter={formatXAxis}
              stroke="#9ca3af" style={{ fontSize: '12px' }} />
            <YAxis stroke="#9ca3af" style={{ fontSize: '12px' }} domain={[0, 100]} />
            <Tooltip content={renderTooltip} />
            <Area type="monotone" dataKey="value" stroke={color}
              fillOpacity={1} fill={`url(#color-${metricType})`} />
          </AreaChart>
        ) : (
          <LineChart data={chartData} margin={{ top: 10, right: 30, left: 0, bottom: 0 }}>
            <CartesianGrid strokeDasharray="3 3" stroke="#f0f0f0" />
            <XAxis dataKey="timestamp" tickFormatter={formatXAxis}
              stroke="#9ca3af" style={{ fontSize: '12px' }} />
            <YAxis stroke="#9ca3af" style={{ fontSize: '12px' }} domain={[0, 100]} />
            <Tooltip content={renderTooltip} />
            <Line type="monotone" dataKey="value" stroke={color}
              strokeWidth={2} dot={false} />
          </LineChart>
        )}
      </ResponsiveContainer>
    </div>
  );
}

export default MetricsChart;
