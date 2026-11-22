// Copyright (c) 2026 Axara Pty Ltd.
// SPDX-License-Identifier: AGPL-3.0-or-later
import React from 'react';
import { useParams, useNavigate } from 'react-router-dom';
import { useQuery } from '@tanstack/react-query';
import axios from 'axios';
import { endpoints } from '../services/api';
import {
  ArrowLeft,
  AlertTriangle,
  Clock,
  Server,
  User,
  ExternalLink,
  CheckCircle,
  XCircle,
  MessageSquare,
  Brain,
  GitBranch,
  Link2
} from 'lucide-react';
import { Link as RouterLink } from 'react-router-dom';
import { ticketToolName } from '../constants';

/**
 * Load one incident with its analysis, comments, ticket and plugin fields.
 * @param {string} id - Incident id.
 * @returns {Promise<object>} The incident.
 */
const fetchIncident = async (id) => {
  const { data } = await endpoints.incident(id);
  return data;
};

/**
 * Download the incident's attached screenshot through the API and save it in the browser.
 * @param {object} incident - Incident with id and attachment path.
 * @returns {Promise<void>}
 */
const handleDownloadAttachment = async (incident) => {
  try {
    const response = await endpoints.incidentAttachmentDownload(incident.id);

    // Derive filename from incident.attachment (the path), fallback to a default
    const filename = incident.attachment
      ? incident.attachment.split(/[\\/]/).pop()
      : `NA`;

    // Create a blob URL and trigger a browser download
    const blobUrl = window.URL.createObjectURL(response.data);
    const link = document.createElement('a');
    link.href = blobUrl;
    link.download = filename;
    document.body.appendChild(link);
    link.click();

    document.body.removeChild(link);
    window.URL.revokeObjectURL(blobUrl);
  } catch (err) {
    console.error('Attachment download failed:', err);
    // Optional: show a toast/banner. Don't expose the raw error message
    // — it may contain backend details that aren't operator-friendly.
    alert('Could not download attachment. Please try again or contact support.');
  }
};
  
/**
 * Incident status pill.
 * @param {{status: string}} props
 * @returns {JSX.Element}
 */
function StatusBadge({ status }) {
  const styles = {
    open:        'bg-red-500/20 text-red-400 border border-red-500/30',
    investigating:'bg-yellow-500/20 text-yellow-400 border border-yellow-500/30',
    resolved:    'bg-green-500/20 text-green-400 border border-green-500/30',
    closed:      'bg-gray-500/20 text-gray-400 border border-gray-500/30',
  };
  return (
    <span className={`px-2.5 py-1 rounded-full text-xs font-medium ${styles[status] ?? styles.open}`}>
      {status?.toUpperCase()}
    </span>
  );
}

/**
 * Severity pill.
 * @param {{severity: string}} props
 * @returns {JSX.Element}
 */
function SeverityBadge({ severity }) {
  const styles = {
    critical: 'bg-red-600 text-white',
    high:     'bg-orange-500 text-white',
    medium:   'bg-yellow-500 text-black',
    low:      'bg-blue-500 text-white',
  };
  return (
    <span className={`px-2.5 py-1 rounded-sm text-xs font-bold ${styles[severity] ?? styles.low}`}>
      {severity?.toUpperCase()}
    </span>
  );
}

// Correlation badges (design ref §6.9.7) — same visual language as
// Incidents.jsx's CorrelationParentBadge/CorrelationLinkedBadge, kept as a
// separate local copy rather than a shared import since these two files
// don't currently share a components directory for badges (see the inline
// severityDot/StatusBadge/SeverityBadge pattern already established here).

function CorrelationParentBadge() {
  return (
    <span
      title="Other incidents have been correlated to this one"
      className="inline-flex items-center gap-1 px-2.5 py-1 rounded-full text-xs
                 font-medium bg-purple-500/20 text-purple-300 border border-purple-500/30"
    >
      <GitBranch size={12} />
      Parent
    </span>
  );
}

/**
 * Badge for an incident linked to a correlation parent (Enterprise correlation fields).
 * @returns {JSX.Element}
 */
function CorrelationLinkedBadge() {
  return (
    <span
      title="This incident has been correlated to a parent"
      className="inline-flex items-center gap-1 px-2.5 py-1 rounded-full text-xs
                 font-medium bg-blue-500/20 text-blue-300 border border-blue-500/30"
    >
      <Link2 size={12} />
      Linked
    </span>
  );
}

/**
 * One labelled value in the details card.
 * @param {{icon: Function, label: string, value: any}} props
 * @returns {JSX.Element}
 */
function InfoRow({ icon: Icon, label, value }) {
  return (
    <div className="flex items-start gap-3 py-3 border-b border-gray-700 last:border-0">
      <Icon size={16} className="text-gray-400 mt-0.5 shrink-0" />
      <div className="flex-1 min-w-0">
        <p className="text-xs text-gray-400 mb-0.5">{label}</p>
        <p className="text-sm text-white wrap-break-word">{value ?? '—'}</p>
      </div>
    </div>
  );
}

/**
 * Titled card container.
 * @param {{title: string, icon?: Function, children: any}} props
 * @returns {JSX.Element}
 */
function Card({ title, icon: Icon, children }) {
  return (
    <div className="bg-gray-800 rounded-xl border border-gray-700">
      <div className="flex items-center gap-2 px-5 py-4 border-b border-gray-700">
        {Icon && <Icon size={16} className="text-blue-400" />}
        <h2 className="text-sm font-semibold text-white">{title}</h2>
      </div>
      <div className="px-5 py-4">{children}</div>
    </div>
  );
}

/**
 * Incident detail page: status, analysis, correlation links, ticket, comments and actions.
 * @returns {JSX.Element}
 */
export default function IncidentDetail() {
  const { id } = useParams();
  const navigate = useNavigate();

  const { data: incident, isLoading, isError } = useQuery({
    queryKey: ['incident', id],
    queryFn: () => fetchIncident(id),
    refetchInterval: 30000,
  });

  if (isLoading) {
    return (
      <div className="flex items-center justify-center h-64 text-gray-400">
        <div className="animate-spin rounded-full h-8 w-8 border-b-2 border-blue-500" />
      </div>
    );
  }

  if (isError || !incident) {
    return (
      <div className="flex flex-col items-center justify-center h-64 gap-3 text-gray-400">
        <XCircle size={40} className="text-red-400" />
        <p>Incident not found or failed to load.</p>
        <button
          onClick={() => navigate('/incidents')}
          className="text-blue-400 hover:underline text-sm"
        >
          Back to Incidents
        </button>
      </div>
    );
  }

  return (
    <div className="max-w-5xl mx-auto space-y-6">

      {/* Header */}
      <div className="flex items-start gap-4">
        <button
          onClick={() => navigate('/incidents')}
          className="mt-1 text-gray-400 hover:text-white transition-colors"
        >
          <ArrowLeft size={20} />
        </button>
        <div className="flex-1 min-w-0">
          <div className="flex flex-wrap items-center gap-2 mb-1">
            <StatusBadge status={incident.status} />
            <SeverityBadge severity={incident.severity} />
            {incident.is_correlation_parent && <CorrelationParentBadge />}
            {incident.parent_incident_id && <CorrelationLinkedBadge />}
          </div>
          <h1 className="text-xl font-bold text-white leading-snug">
            {incident.title}
          </h1>
          <p className="text-sm text-gray-400 mt-1">ID: {incident.id}</p>
        </div>
      </div>

      <div className="grid grid-cols-1 lg:grid-cols-3 gap-6">

        {/* Left column */}
        <div className="lg:col-span-2 space-y-6">

          {/* Description */}
          <Card title="Description" icon={AlertTriangle}>
            <p className="text-sm text-gray-300 whitespace-pre-wrap leading-relaxed">
              {incident.description ?? 'No description provided.'}
            </p>
          </Card>

          {/* LLM Analysis */}
          {incident.llm_analysis && (
            <Card title="AI Analysis" icon={Brain}>
              <p className="text-sm text-gray-300 whitespace-pre-wrap leading-relaxed">
                {incident.llm_analysis}
              </p>
            </Card>
          )}

          {/* Similar Incidents */}
          {incident.similar_incidents?.length > 0 && (
            <Card title="Similar Past Incidents" icon={MessageSquare}>
              <div className="space-y-3">
                {incident.similar_incidents.map((sim, idx) => (
                  <div
                    key={idx}
                    className="p-3 bg-gray-700/50 rounded-lg border border-gray-600"
                  >
                    <p className="text-sm text-white font-medium">{sim.title}</p>
                    {sim.resolution && (
                      <p className="text-xs text-gray-400 mt-1">
                        Resolution: {sim.resolution}
                      </p>
                    )}
                  </div>
                ))}
              </div>
            </Card>
          )}

          {/* Correlated Incidents (design ref §6.9.7) */}
          {(incident.parent_incident_id || incident.correlated_children?.length > 0) && (
            <Card title="Correlated Incidents" icon={GitBranch}>
              {incident.parent_incident_id && (
                <div className="mb-3 pb-3 border-b border-gray-700">
                  <p className="text-xs text-gray-400 mb-1">Correlated to parent</p>
                  <RouterLink
                    to={`/incidents/${incident.parent_incident_id}`}
                    className="flex items-center gap-1.5 text-sm text-blue-400 hover:underline"
                  >
                    <Link2 size={13} />
                    {incident.parent_title || incident.parent_incident_id}
                  </RouterLink>
                </div>
              )}
              {incident.correlated_children?.length > 0 && (
                <div className="space-y-2">
                  <p className="text-xs text-gray-400 mb-1">
                    {incident.correlated_children.length} correlated incident
                    {incident.correlated_children.length !== 1 ? 's' : ''}
                  </p>
                  {incident.correlated_children.map(child => (
                    <RouterLink
                      key={child.id}
                      to={`/incidents/${child.id}`}
                      className="flex items-center justify-between p-2 bg-gray-700/50
                                 rounded-lg border border-gray-600 hover:bg-gray-700
                                 transition-colors"
                    >
                      <span className="text-sm text-white truncate">{child.title}</span>
                      <span className="text-xs text-gray-400 shrink-0 ml-2">
                        {child.status}
                      </span>
                    </RouterLink>
                  ))}
                </div>
              )}
            </Card>
          )}

        </div>

        {/* Right column */}
        <div className="space-y-6">

          {/* Details */}
          <Card title="Details">
            <InfoRow icon={Clock}  label="Created"  value={incident.created_at
              ? new Date(incident.created_at).toLocaleString() : null} />
            <InfoRow icon={Clock}  label="Resolved" value={incident.resolved_at
              ? new Date(incident.resolved_at).toLocaleString() : 'Ongoing'} />
            <InfoRow icon={Server} label="Server"   value={incident.server_hostname} />
            <InfoRow icon={User}   label="Assignee" value={incident.assignee} />
          </Card>

          {/* External Tickets */}
          <Card title="External Ticket">
            {incident.ticket_ref ? (
              <div className="flex items-center justify-between py-2">
                <span className="text-sm text-gray-300">{ticketToolName(incident.ticket_provider)}</span>
                {incident.ticket_url ? (
                  <a href={incident.ticket_url}
                    target="_blank"
                    rel="noreferrer"
                    className="flex items-center gap-1 text-blue-400 hover:underline text-sm"
                  >
                    {incident.ticket_ref}
                    <ExternalLink size={12} />
                  </a>
                ) : (
                  <span className="text-sm text-gray-300">{incident.ticket_ref}</span>
                )}
              </div>
            ) : (
              <p className="text-sm text-gray-500 py-2">No ticket</p>
            )}
          </Card>

          {/* Attachment */}
          {incident.attachment && (
            <Card title="Attachment">
              <div className="space-y-2">
                <p className="text-xs text-gray-400 break-all font-mono">
                  {incident.attachment.split(/[\\/]/).pop()}
                </p>
                <button
                  type="button"
                  onClick={() => handleDownloadAttachment(incident)}
                  className="flex items-center gap-1.5 px-3 py-1.5 bg-blue-600 hover:bg-blue-700
                     text-white rounded-lg text-sm transition-colors"
                >
                  <ExternalLink size={12} />
                  Download screenshot
                </button>
              </div>
            </Card>
          )}

          {/* Application */}
          {incident.application && (
            <Card title="Application">
              <InfoRow
                icon={Server}
                label="Name"
                value={incident.application_name}
              />
              <InfoRow
                icon={CheckCircle}
                label="Environment"
                value={incident.application.environment}
              />
            </Card>
          )}

        </div>
      </div>
    </div>
  );
}
