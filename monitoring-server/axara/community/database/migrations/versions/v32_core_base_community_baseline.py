# Copyright (c) 2026 Axara Pty Ltd.
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Community baseline schema (v3.2).

Revision ID: v32_core_base
Revises: (none - first revision of the core branch)
Create Date: 2026-11-22

Fresh baseline for the open-core split. It replaces the twelve v3.0/v3.1 migrations;
there is no upgrade path from a v3.1 database (database.migrate refuses one).

Creates every Community table: applications, servers, monitors, metrics and their state,
incidents and comments, maintenance windows, users, platform config, API keys, agent
enrolment tokens, and the append-only audit log with the
triggers that block UPDATE, DELETE and TRUNCATE on it.

Incidents carry one generic ticket reference (ticket_provider + ticket_ref) used by
whichever ITSM provider the application is set to. Plugins keep their own tables in
their own migration branches.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy import Text
from sqlalchemy.dialects import postgresql

revision: str = 'v32_core_base'
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = ('core',)
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Create the Community schema and the audit-log triggers."""
    op.create_table('agent_enrolment_tokens',
    sa.Column('id', sa.UUID(), nullable=False),
    sa.Column('label', sa.VARCHAR(length=100), nullable=False),
    sa.Column('secret_hash', sa.VARCHAR(length=64), nullable=False),
    sa.Column('hint', sa.VARCHAR(length=12), nullable=False),
    sa.Column('max_uses', sa.INTEGER(), nullable=False),
    sa.Column('use_count', sa.INTEGER(), server_default=sa.text('0'), nullable=False),
    sa.Column('created_by', sa.VARCHAR(length=255), nullable=False),
    sa.Column('created_at', postgresql.TIMESTAMP(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('expires_at', postgresql.TIMESTAMP(timezone=True), nullable=False),
    sa.Column('last_used_at', postgresql.TIMESTAMP(timezone=True), nullable=True),
    sa.Column('revoked_at', postgresql.TIMESTAMP(timezone=True), nullable=True),
    sa.Column('revoked_by', sa.VARCHAR(length=255), nullable=True),
    sa.CheckConstraint('max_uses >= 1', name='ck_agent_enrolment_max_uses'),
    sa.CheckConstraint('use_count >= 0 AND use_count <= max_uses', name='ck_agent_enrolment_use_count'),
    sa.PrimaryKeyConstraint('id', name='agent_enrolment_tokens_pkey'),
    sa.UniqueConstraint('secret_hash', name='uq_agent_enrolment_secret_hash')
    )
    op.create_table('api_keys',
    sa.Column('id', sa.UUID(), nullable=False),
    sa.Column('service_name', sa.VARCHAR(length=100), nullable=True),
    sa.Column('key_name', sa.VARCHAR(length=255), nullable=True),
    sa.Column('encrypted_key', sa.TEXT(), nullable=True),
    sa.Column('created_at', postgresql.TIMESTAMP(timezone=True), nullable=True),
    sa.Column('expires_at', postgresql.TIMESTAMP(timezone=True), nullable=True),
    sa.PrimaryKeyConstraint('id', name='api_keys_pkey')
    )
    op.create_table('applications',
    sa.Column('id', sa.UUID(), nullable=False),
    sa.Column('name', sa.VARCHAR(length=255), nullable=False),
    sa.Column('type', sa.VARCHAR(length=50), nullable=True),
    sa.Column('description', sa.TEXT(), nullable=True),
    sa.Column('environment', sa.VARCHAR(length=50), nullable=True),
    sa.Column('primary_support_team', sa.VARCHAR(length=255), nullable=True),
    sa.Column('primary_assignee', sa.VARCHAR(length=255), nullable=True),
    sa.Column('tags', postgresql.JSON(astext_type=Text()), nullable=True),
    sa.Column('itsm_provider', sa.VARCHAR(length=50), nullable=True),
    sa.Column('created_at', postgresql.TIMESTAMP(timezone=True), nullable=True),
    sa.Column('updated_at', postgresql.TIMESTAMP(timezone=True), nullable=True),
    sa.PrimaryKeyConstraint('id', name='applications_pkey'),
    sa.UniqueConstraint('name', name='applications_name_key')
    )
    op.create_table('audit_log',
    sa.Column('id', sa.BIGINT(), autoincrement=True, nullable=False),
    sa.Column('ts', postgresql.TIMESTAMP(timezone=True), nullable=False),
    sa.Column('actor_type', sa.VARCHAR(length=20), nullable=False),
    sa.Column('actor', sa.VARCHAR(length=255), nullable=True),
    sa.Column('actor_id', sa.VARCHAR(length=64), nullable=True),
    sa.Column('action', sa.VARCHAR(length=150), nullable=False),
    sa.Column('resource_type', sa.VARCHAR(length=100), nullable=True),
    sa.Column('resource_id', sa.VARCHAR(length=255), nullable=True),
    sa.Column('outcome', sa.VARCHAR(length=20), nullable=False),
    sa.Column('source_ip', sa.VARCHAR(length=64), nullable=True),
    sa.Column('user_agent', sa.VARCHAR(length=300), nullable=True),
    sa.Column('details', postgresql.JSONB(astext_type=Text()), nullable=True),
    sa.Column('prev_hash', sa.VARCHAR(length=64), nullable=False),
    sa.Column('hash', sa.VARCHAR(length=64), nullable=False),
    sa.PrimaryKeyConstraint('id', name='audit_log_pkey'),
    sa.UniqueConstraint('hash', name='uq_audit_log_hash')
    )
    op.create_index('idx_audit_log_action', 'audit_log', ['action'], unique=False)
    op.create_index('idx_audit_log_actor', 'audit_log', ['actor'], unique=False)
    op.create_index('idx_audit_log_outcome', 'audit_log', ['outcome'], unique=False)
    op.create_index('idx_audit_log_ts', 'audit_log', ['ts'], unique=False)
    op.create_table('platform_config',
    sa.Column('id', sa.UUID(), nullable=False),
    sa.Column('key', sa.VARCHAR(length=100), nullable=False),
    sa.Column('value', postgresql.JSONB(astext_type=Text()), nullable=False),
    sa.Column('updated_at', postgresql.TIMESTAMP(timezone=True), nullable=True),
    sa.PrimaryKeyConstraint('id', name='platform_config_pkey'),
    sa.UniqueConstraint('key', name='platform_config_key_key')
    )
    op.create_table('servers',
    sa.Column('id', sa.UUID(), nullable=False),
    sa.Column('hostname', sa.VARCHAR(length=255), nullable=False),
    sa.Column('ip_address', sa.VARCHAR(length=45), nullable=True),
    sa.Column('os_type', sa.VARCHAR(length=50), nullable=True),
    sa.Column('os_version', sa.VARCHAR(length=100), nullable=True),
    sa.Column('agent_version', sa.VARCHAR(length=50), nullable=True),
    sa.Column('last_heartbeat', postgresql.TIMESTAMP(timezone=True), nullable=True),
    sa.Column('status', sa.VARCHAR(length=20), nullable=True),
    sa.Column('api_key_hash', sa.VARCHAR(length=64), nullable=True),
    sa.Column('created_at', postgresql.TIMESTAMP(timezone=True), nullable=True),
    sa.Column('decommissioned_at', postgresql.TIMESTAMP(timezone=True), nullable=True),
    sa.Column('decommissioned_by', sa.VARCHAR(length=255), nullable=True),
    sa.PrimaryKeyConstraint('id', name='servers_pkey')
    )
    op.create_index('idx_servers_status', 'servers', ['status'], unique=False)
    op.create_table('users',
    sa.Column('id', sa.UUID(), nullable=False),
    sa.Column('username', sa.VARCHAR(length=255), nullable=False),
    sa.Column('email', sa.VARCHAR(length=255), nullable=True),
    sa.Column('password_hash', sa.VARCHAR(length=255), nullable=True),
    sa.Column('role', sa.VARCHAR(length=50), nullable=True),
    sa.Column('is_approved', sa.BOOLEAN(), nullable=True),
    sa.Column('status', sa.VARCHAR(length=20), nullable=True),
    sa.Column('token_invalidated_at', postgresql.TIMESTAMP(timezone=True), nullable=True),
    sa.Column('created_at', postgresql.TIMESTAMP(timezone=True), nullable=True),
    sa.Column('last_login', postgresql.TIMESTAMP(timezone=True), nullable=True),
    sa.Column('auth_source', sa.VARCHAR(length=20), server_default=sa.text("'local'::character varying"), nullable=False),
    sa.Column('external_id', sa.VARCHAR(length=255), nullable=True),
    sa.PrimaryKeyConstraint('id', name='users_pkey'),
    sa.UniqueConstraint('auth_source', 'external_id', name='uq_users_auth_source_external_id'),
    sa.UniqueConstraint('email', name='users_email_key'),
    sa.UniqueConstraint('username', name='users_username_key')
    )
    op.create_table('application_servers',
    sa.Column('application_id', sa.UUID(), nullable=False),
    sa.Column('server_id', sa.UUID(), nullable=False),
    sa.ForeignKeyConstraint(['application_id'], ['applications.id'], name='application_servers_application_id_fkey'),
    sa.ForeignKeyConstraint(['server_id'], ['servers.id'], name='application_servers_server_id_fkey'),
    sa.PrimaryKeyConstraint('application_id', 'server_id', name='application_servers_pkey')
    )
    op.create_table('maintenance_windows',
    sa.Column('id', sa.UUID(), nullable=False),
    sa.Column('name', sa.VARCHAR(length=255), nullable=True),
    sa.Column('application_id', sa.UUID(), nullable=True),
    sa.Column('server_id', sa.UUID(), nullable=True),
    sa.Column('start_time', postgresql.TIMESTAMP(timezone=True), nullable=False),
    sa.Column('end_time', postgresql.TIMESTAMP(timezone=True), nullable=False),
    sa.Column('description', sa.TEXT(), nullable=True),
    sa.Column('suppress_type', sa.VARCHAR(length=20), nullable=False),
    sa.Column('created_by', sa.VARCHAR(length=255), nullable=True),
    sa.Column('created_at', postgresql.TIMESTAMP(timezone=True), nullable=True),
    sa.ForeignKeyConstraint(['application_id'], ['applications.id'], name='maintenance_windows_application_id_fkey'),
    sa.ForeignKeyConstraint(['server_id'], ['servers.id'], name='maintenance_windows_server_id_fkey'),
    sa.PrimaryKeyConstraint('id', name='maintenance_windows_pkey')
    )
    op.create_table('monitor_configs',
    sa.Column('id', sa.UUID(), nullable=False),
    sa.Column('name', sa.VARCHAR(length=255), nullable=False),
    sa.Column('type', sa.VARCHAR(length=50), nullable=False),
    sa.Column('subtype', sa.VARCHAR(length=50), nullable=True),
    sa.Column('application_id', sa.UUID(), nullable=True),
    sa.Column('server_id', sa.UUID(), nullable=True),
    sa.Column('enabled', sa.BOOLEAN(), nullable=True),
    sa.Column('check_interval_seconds', sa.INTEGER(), nullable=True),
    sa.Column('threshold_config', postgresql.JSONB(astext_type=Text()), nullable=True),
    sa.Column('consecutive_failures', sa.INTEGER(), nullable=True),
    sa.Column('severity', sa.VARCHAR(length=20), nullable=True),
    sa.Column('assignee_override', sa.VARCHAR(length=255), nullable=True),
    sa.Column('notification_override', postgresql.JSONB(astext_type=Text()), nullable=True),
    sa.Column('config', postgresql.JSONB(astext_type=Text()), nullable=True),
    sa.Column('created_at', postgresql.TIMESTAMP(timezone=True), nullable=True),
    sa.Column('updated_at', postgresql.TIMESTAMP(timezone=True), nullable=True),
    sa.Column('archived_at', postgresql.TIMESTAMP(timezone=True), nullable=True),
    sa.ForeignKeyConstraint(['application_id'], ['applications.id'], name='monitor_configs_application_id_fkey'),
    sa.ForeignKeyConstraint(['server_id'], ['servers.id'], name='monitor_configs_server_id_fkey'),
    sa.PrimaryKeyConstraint('id', name='monitor_configs_pkey')
    )
    op.create_table('incidents',
    sa.Column('id', sa.UUID(), nullable=False),
    sa.Column('incident_key', sa.VARCHAR(length=255), nullable=True),
    sa.Column('monitor_config_id', sa.UUID(), nullable=True),
    sa.Column('server_id', sa.UUID(), nullable=True),
    sa.Column('application_id', sa.UUID(), nullable=True),
    sa.Column('title', sa.VARCHAR(length=500), nullable=True),
    sa.Column('description', sa.TEXT(), nullable=True),
    sa.Column('severity', sa.VARCHAR(length=20), nullable=True),
    sa.Column('status', sa.VARCHAR(length=50), nullable=True),
    sa.Column('assignee', sa.VARCHAR(length=255), nullable=True),
    sa.Column('llm_analysis', sa.TEXT(), nullable=True),
    sa.Column('similar_incidents', postgresql.JSONB(astext_type=Text()), nullable=True),
    sa.Column('attachment', sa.VARCHAR(length=500), nullable=True),
    sa.Column('created_at', postgresql.TIMESTAMP(timezone=True), nullable=True),
    sa.Column('updated_at', postgresql.TIMESTAMP(timezone=True), nullable=True),
    sa.Column('resolved_at', postgresql.TIMESTAMP(timezone=True), nullable=True),
    sa.Column('resolution_notes', sa.TEXT(), nullable=True),
    sa.Column('llm_analysis_data', postgresql.JSONB(astext_type=Text()), nullable=True),
    sa.Column('ticket_provider', sa.VARCHAR(length=30), nullable=True),
    sa.Column('ticket_ref', sa.VARCHAR(length=100), nullable=True),
    sa.ForeignKeyConstraint(['application_id'], ['applications.id'], name='incidents_application_id_fkey'),
    sa.ForeignKeyConstraint(['monitor_config_id'], ['monitor_configs.id'], name='fk_incidents_monitor_config', ondelete='SET NULL'),
    sa.ForeignKeyConstraint(['server_id'], ['servers.id'], name='incidents_server_id_fkey'),
    sa.PrimaryKeyConstraint('id', name='incidents_pkey')
    )
    op.create_index('idx_incidents_severity', 'incidents', ['severity'], unique=False)
    op.create_index('idx_incidents_status', 'incidents', ['status'], unique=False)
    op.create_index('idx_incidents_ticket', 'incidents', ['ticket_provider', 'ticket_ref'], unique=False)
    op.create_table('metrics',
    sa.Column('id', sa.INTEGER(), autoincrement=True, nullable=False),
    sa.Column('server_id', sa.UUID(), nullable=False),
    sa.Column('monitor_config_id', sa.UUID(), nullable=True),
    sa.Column('metric_type', sa.VARCHAR(length=50), nullable=True),
    sa.Column('metric_name', sa.VARCHAR(length=100), nullable=True),
    sa.Column('metric_value', sa.DOUBLE_PRECISION(precision=53), nullable=True),
    sa.Column('metric_data', postgresql.JSONB(astext_type=Text()), nullable=True),
    sa.Column('envelope', postgresql.JSONB(astext_type=Text()), nullable=True),
    sa.Column('status', sa.VARCHAR(length=20), nullable=True),
    sa.Column('timestamp', postgresql.TIMESTAMP(timezone=True), nullable=True),
    sa.ForeignKeyConstraint(['monitor_config_id'], ['monitor_configs.id'], name='fk_metrics_monitor_config', ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['server_id'], ['servers.id'], name='metrics_server_id_fkey'),
    sa.PrimaryKeyConstraint('id', name='metrics_pkey')
    )
    op.create_index('idx_metrics_monitor_time', 'metrics', ['monitor_config_id', 'timestamp'], unique=False)
    op.create_index('idx_metrics_server_time', 'metrics', ['server_id', 'timestamp'], unique=False)
    op.create_table('incident_comments',
    sa.Column('id', sa.UUID(), nullable=False),
    sa.Column('incident_id', sa.UUID(), nullable=False),
    sa.Column('comment_type', sa.VARCHAR(length=50), nullable=True),
    sa.Column('content', sa.TEXT(), nullable=True),
    sa.Column('created_by', sa.VARCHAR(length=255), nullable=True),
    sa.Column('created_at', postgresql.TIMESTAMP(timezone=True), nullable=True),
    sa.ForeignKeyConstraint(['incident_id'], ['incidents.id'], name='incident_comments_incident_id_fkey'),
    sa.PrimaryKeyConstraint('id', name='incident_comments_pkey')
    )
    op.create_table('log_monitor_states',
    sa.Column('id', sa.UUID(), nullable=False),
    sa.Column('monitor_config_id', sa.UUID(), nullable=True),
    sa.Column('log_file_path', sa.VARCHAR(length=500), nullable=True),
    sa.Column('pattern_name', sa.VARCHAR(length=100), nullable=True),
    sa.Column('consecutive_failures', sa.INTEGER(), nullable=True),
    sa.Column('last_failure_time', postgresql.TIMESTAMP(timezone=True), nullable=True),
    sa.Column('last_success_time', postgresql.TIMESTAMP(timezone=True), nullable=True),
    sa.Column('last_error_count', sa.INTEGER(), nullable=True),
    sa.Column('incident_id', sa.UUID(), nullable=True),
    sa.Column('updated_at', postgresql.TIMESTAMP(timezone=True), nullable=True),
    sa.ForeignKeyConstraint(['incident_id'], ['incidents.id'], name='log_monitor_states_incident_id_fkey'),
    sa.ForeignKeyConstraint(['monitor_config_id'], ['monitor_configs.id'], name='fk_log_monitor_states_monitor_config', ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id', name='log_monitor_states_pkey')
    )
    op.create_index('idx_log_state_unique', 'log_monitor_states', ['monitor_config_id', 'log_file_path', 'pattern_name'], unique=True)
    op.create_table('monitor_states',
    sa.Column('id', sa.UUID(), nullable=False),
    sa.Column('monitor_config_id', sa.UUID(), nullable=True),
    sa.Column('current_status', sa.VARCHAR(length=20), nullable=True),
    sa.Column('consecutive_failures', sa.INTEGER(), nullable=True),
    sa.Column('last_failure_time', postgresql.TIMESTAMP(timezone=True), nullable=True),
    sa.Column('last_success_time', postgresql.TIMESTAMP(timezone=True), nullable=True),
    sa.Column('incident_id', sa.UUID(), nullable=True),
    sa.Column('updated_at', postgresql.TIMESTAMP(timezone=True), nullable=True),
    sa.ForeignKeyConstraint(['incident_id'], ['incidents.id'], name='monitor_states_incident_id_fkey'),
    sa.ForeignKeyConstraint(['monitor_config_id'], ['monitor_configs.id'], name='fk_monitor_states_monitor_config', ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id', name='monitor_states_pkey'),
    sa.UniqueConstraint('monitor_config_id', name='monitor_states_monitor_config_id_key')
    )

    op.execute("""
        CREATE OR REPLACE FUNCTION audit_log_block_modify() RETURNS trigger AS $$
        BEGIN
            RAISE EXCEPTION 'audit_log is append-only (% blocked)', TG_OP;
        END;
        $$ LANGUAGE plpgsql;
    """)
    op.execute("""
        CREATE TRIGGER trg_audit_log_no_update_delete
            BEFORE UPDATE OR DELETE ON audit_log
            FOR EACH ROW EXECUTE FUNCTION audit_log_block_modify();
    """)
    op.execute("""
        CREATE TRIGGER trg_audit_log_no_truncate
            BEFORE TRUNCATE ON audit_log
            FOR EACH STATEMENT EXECUTE FUNCTION audit_log_block_modify();
    """)


def downgrade() -> None:
    """Drop the whole Community schema (the enterprise branch must be downgraded first)."""
    op.execute("DROP TRIGGER IF EXISTS trg_audit_log_no_truncate ON audit_log")
    op.execute("DROP TRIGGER IF EXISTS trg_audit_log_no_update_delete ON audit_log")
    op.execute("DROP FUNCTION IF EXISTS audit_log_block_modify()")
    op.drop_table('monitor_states')
    op.drop_table('log_monitor_states')
    op.drop_table('incident_comments')
    op.drop_table('metrics')
    op.drop_table('incidents')
    op.drop_table('monitor_configs')
    op.drop_table('maintenance_windows')
    op.drop_table('application_servers')
    op.drop_table('users')
    op.drop_table('servers')
    op.drop_table('platform_config')
    op.drop_table('audit_log')
    op.drop_table('applications')
    op.drop_table('api_keys')
    op.drop_table('agent_enrolment_tokens')
