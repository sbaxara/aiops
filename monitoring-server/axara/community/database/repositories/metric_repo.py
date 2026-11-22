# Copyright (c) 2026 Axara Pty Ltd.
# SPDX-License-Identifier: AGPL-3.0-or-later
"""
Metric Repository
"""
from datetime import datetime
from typing import Optional
from sqlalchemy.orm import Session
from axara.community.database.models import Metric
from .base_repo import BaseRepository


class MetricRepository(BaseRepository):
    """Queries for stored metrics, including plugin stand-in types."""
    def __init__(self, db: Session):
        """Bind the repository to a session.

        Args:
            db: SQLAlchemy session.
        """
        super().__init__(db)
        self.model = Metric

    def has_metrics_of_type(self, base_type: str, cutoff: datetime,
                            server_id: Optional[str] = None) -> bool:
        """True if any rows of `base_type` exist since `cutoff` (optionally scoped
        to one server). Cheap EXISTS query — uses the (server_id, timestamp) index."""
        q = self.db.query(Metric.id).filter(
            Metric.metric_type == base_type,
            Metric.timestamp >= cutoff,
        )
        if server_id:
            q = q.filter(Metric.server_id == server_id)
        return self.db.query(q.exists()).scalar()

    def resolve_effective_metric_type(self, base_type: str, cutoff: datetime,
                                      server_id: Optional[str] = None) -> str:
        """The metric type to read for `base_type` in the window.

        If `base_type` has data in the window, use it. Otherwise use the plugin type that
        stands in for it (Registry.add_metric_type(stands_in_for=...)), if there is one, so
        charts and tiles show data before a monitor of the base type reports.

        Args:
            base_type: Metric type asked for.
            cutoff: Start of the window.
            server_id: Limit to one server.

        Returns:
            base_type or its stand-in type.
        """
        from axara.community.plugins.loader import get_registry
        fallback = get_registry().metric_fallback(base_type)
        if not fallback:
            return base_type
        if self.has_metrics_of_type(base_type, cutoff, server_id):
            return base_type
        return fallback
