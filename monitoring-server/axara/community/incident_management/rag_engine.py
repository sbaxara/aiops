# Copyright (c) 2026 Axara Pty Ltd.
# SPDX-License-Identifier: AGPL-3.0-or-later
"""
RAG (Retrieval Augmented Generation) engine: remembers resolved incidents and finds the
most similar ones for a new incident, so the LLM can reuse what was learned.

Storage (one ChromaDB record per resolved incident, id = incident id, upserted):
  embedding  of the incident SIGNATURE only (title, description, application). The same
             signature is built for the query, so identical failures score as identical.
  metadata   structured scalar fields read back directly (ChromaDB metadata values must be
             str/int/float/bool): title, severity, application, server, resolved_at,
             root_cause, recommendations, resolution, resolution_source, confidence.

Similarity: the collection uses cosine space, where ChromaDB returns distance = 1 - cosine
similarity. similarity_threshold is therefore a true cosine similarity (0..1).

Record version 2 (2026-11-15). Records written before that embedded the root cause and
resolution text and stored the whole rendered LLM report; rebuild them with
`python manage.py rag-reindex`.
"""
import logging
import re
from typing import Dict, Any, List, Optional

from axara.community.vector_store.chroma_client import ChromaDBClient
from axara.community.vector_store.embeddings import EmbeddingGenerator

logger = logging.getLogger(__name__)

DEFAULT_COLLECTION = "incidents"
RECORD_VERSION = 2
AUTO_RESOLUTION = "Recovered automatically: the monitored condition returned to normal."
_FAILED_PREFIX = "analysis failed"


# ----------------------------------------------------------------- analysis fields

def analysis_fields(structured: Optional[Dict[str, Any]], rendered: Optional[str]) -> Dict[str, Any]:
    """Root cause, recommendations and confidence of an incident's LLM analysis.

    Prefers the structured copy saved at analysis time (incidents.llm_analysis_data); falls
    back to parsing the text rendered by LLMAnalyzer._render_raw_analysis (incidents analysed
    before llm_analysis_data existed). `usable` is False when the analysis failed or never ran.
    """
    root, recs, conf = "", [], None
    if isinstance(structured, dict) and structured:
        root = str(structured.get("root_cause") or "").strip()
        recs = [str(r).strip() for r in (structured.get("recommendations") or []) if str(r).strip()]
        conf = structured.get("confidence")
        failed = bool(structured.get("failed"))
    elif rendered:
        root, recs, conf = _parse_rendered(rendered)
        failed = False
    else:
        failed = True
    try:
        conf = None if conf is None else float(conf)
    except (TypeError, ValueError):
        conf = None
    if not root or root.lower().startswith(_FAILED_PREFIX) or conf == 0.0:
        failed = True
    return {"root_cause": "" if failed else root,
            "recommendations": [] if failed else recs,
            "confidence": conf if conf is not None else 0.0,
            "usable": not failed}


def _parse_rendered(text: str):
    """Inverse of LLMAnalyzer._render_raw_analysis (sections ROOT CAUSE / RECOMMENDATIONS /
    CONFIDENCE). Returns ('', [], None) for text in any other format."""
    m = re.search(r"(?ms)^ROOT CAUSE:\s*\n(.*?)(?:\n\s*\n|\nRECOMMENDATIONS:|\nCONFIDENCE:|\Z)", text)
    root = m.group(1).strip() if m else ""
    recs = []
    r = re.search(r"(?ms)^RECOMMENDATIONS:\s*\n(.*?)(?:\n\s*\n|\nCONFIDENCE:|\Z)", text)
    if r:
        recs = [re.sub(r"^\s*\d+\.\s*", "", ln).strip() for ln in r.group(1).splitlines() if ln.strip()]
    c = re.search(r"(?m)^CONFIDENCE:\s*([\d.]+)\s*%", text)
    conf = float(c.group(1)) / 100.0 if c else None
    return root, recs, conf


class RAGEngine:
    """RAG engine for similar-incident retrieval (ChromaDB + sentence-transformers)."""

    def __init__(self, top_k: int = 5, similarity_threshold: float = 0.7):
        """Create the engine.

        Args:
            top_k: Most similar incidents returned.
            similarity_threshold: Minimum cosine similarity of a match.
        """
        self.top_k = top_k
        self.similarity_threshold = similarity_threshold
        self.chroma_client = ChromaDBClient()
        self.embedding_generator = EmbeddingGenerator()
        self._collections: Dict[str, Any] = {}

    def initialize(self, collection: str = DEFAULT_COLLECTION):
        """Initialize (and cache) a named ChromaDB collection."""
        try:
            self._collections[collection] = self.chroma_client.get_or_create_collection(collection)
            logger.info(f"RAG engine initialized collection '{collection}'")
        except Exception as e:
            logger.error(f"Failed to initialize RAG collection '{collection}': {e}")
            raise

    def _collection(self, name: str):
        """The named collection, initialised on first use."""
        if name not in self._collections:
            self.initialize(name)
        return self._collections[name]

    def reset_collection(self, collection: str = DEFAULT_COLLECTION):
        """Drop and recreate a collection (used by `manage.py rag-reindex`)."""
        try:
            self.chroma_client.client.delete_collection(collection)
        except Exception:
            pass                                      # did not exist
        self._collections.pop(collection, None)
        self.initialize(collection)

    # ------------------------------------------------------------- signature

    @staticmethod
    def signature_text(incident: Dict[str, Any]) -> str:
        """What identifies a failure. Used for BOTH stored records and queries."""
        app = (incident.get('application') or {}).get('name') or ''
        parts = [
            str(incident.get('title') or '').strip(),
            str(incident.get('description') or '').strip(),
            f"Application: {app}" if app and app != 'Unknown' else '',
        ]
        return '\n'.join(p for p in parts if p)

    # ------------------------------------------------------------- retrieval

    def find_similar_incidents(
        self,
        incident: Dict[str, Any],
        filter_criteria: Dict[str, Any] = None,
        collection: str = DEFAULT_COLLECTION,
        exclude_id: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        """Most similar resolved incidents at or above similarity_threshold, best first.
        `exclude_id` keeps an incident from matching its own earlier record (reopened)."""
        try:
            coll = self._collection(collection)
            count = coll.count()
            if count == 0:
                logger.info("RAG collection is empty — no similar incidents to find")
                return []
            query_embedding = self.embedding_generator.generate(self.signature_text(incident))
            where_filter = self._build_where_filter(filter_criteria, exclude_id)
            results = coll.query(
                query_embeddings=[_as_list(query_embedding)],
                n_results=min(self.top_k, count),
                where=where_filter,
                include=["metadatas", "distances"],
            )
            similar = self._parse_results(results)
            logger.info(f"Found {len(similar)} similar incidents")
            return similar
        except Exception as e:
            logger.error(f"Similar incident search failed: {e}", exc_info=True)
            return []

    # ------------------------------------------------------------- storage

    def store_incident(
        self,
        incident_id: str,
        incident: Dict[str, Any],
        resolution_info: Dict[str, Any] = None,
        collection: str = DEFAULT_COLLECTION,
    ) -> bool:
        """Store (or replace) a resolved incident. resolution_info keys: root_cause,
        recommendations (list), resolution, resolution_source, confidence."""
        try:
            coll = self._collection(collection)
            info = resolution_info or {}
            doc_text = self.signature_text(incident)
            embedding = self.embedding_generator.generate(doc_text)

            def _s(v):
                """A metadata value ChromaDB accepts (None becomes '', other types become text)."""
                return '' if v is None else (v if isinstance(v, (str, int, float, bool)) else str(v))

            recs = info.get('recommendations') or []
            metadata = {
                'document_type':     'resolved_incident',
                'record_version':    RECORD_VERSION,
                'incident_id':       _s(incident_id),
                'title':             _s(incident.get('title', '')),
                'severity':          _s(incident.get('severity', '')),
                'application':       _s((incident.get('application') or {}).get('name', '')),
                'server':            _s((incident.get('server') or {}).get('hostname', '')),
                'resolved_at':       _s(str(incident.get('resolved_at') or '')),
                'status':            'resolved',
                'root_cause':        _s(info.get('root_cause', '')),
                'recommendations':   '\n'.join(str(r) for r in recs) if isinstance(recs, list) else _s(recs),
                'resolution':        _s(info.get('resolution', '')),
                'resolution_source': _s(info.get('resolution_source', '')),
                'confidence':        float(info.get('confidence') or 0.0),
            }
            coll.upsert(
                ids=[str(incident_id)],
                embeddings=[_as_list(embedding)],
                metadatas=[metadata],
                documents=[doc_text],
            )
            logger.info(f"Stored incident {incident_id} in RAG collection '{collection}'")
            return True
        except Exception as e:
            logger.error(f"Failed to store incident in RAG: {e}", exc_info=True)
            return False

    def update_incident(self, incident_id: str, resolution_info: Dict[str, Any] = None,
                        collection: str = DEFAULT_COLLECTION):
        """Remove an incident's record (store_incident upserts, so this is only for deletes)."""
        try:
            self._collection(collection).delete(ids=[str(incident_id)])
        except Exception as e:
            logger.warning(f"Could not delete incident record: {e}")

    # ------------------------------------------------------------- helpers

    @staticmethod
    def _build_where_filter(filter_criteria: Dict[str, Any], exclude_id: Optional[str] = None) -> Dict:
        """ChromaDB where filter — multiple conditions must use $and."""
        conditions = [{"status": {"$eq": "resolved"}}]
        if filter_criteria:
            if filter_criteria.get("application"):
                conditions.append({"application": {"$eq": filter_criteria["application"]}})
            if filter_criteria.get("severity"):
                conditions.append({"severity": {"$eq": filter_criteria["severity"]}})
        if exclude_id:
            conditions.append({"incident_id": {"$ne": str(exclude_id)}})
        return conditions[0] if len(conditions) == 1 else {"$and": conditions}

    def _parse_results(self, results: Dict[str, Any]) -> List[Dict[str, Any]]:
        """Result objects straight from structured metadata (no text parsing)."""
        similar: List[Dict[str, Any]] = []
        if not results or not results.get('ids') or not results['ids'][0]:
            return similar
        ids = results['ids'][0]
        distances = results['distances'][0]
        metadatas = (results.get('metadatas') or [[]])[0] or []
        for idx, incident_id in enumerate(ids):
            similarity = max(0.0, min(1.0, 1.0 - float(distances[idx])))   # cosine space
            if similarity < self.similarity_threshold:
                continue
            md = metadatas[idx] if idx < len(metadatas) and metadatas[idx] else {}
            recs = md.get('recommendations', '')
            similar.append({
                'incident_id':       incident_id,
                'title':             md.get('title', ''),
                'severity':          md.get('severity', ''),
                'application':       md.get('application', ''),
                'similarity_score':  round(similarity, 4),
                'root_cause':        md.get('root_cause', ''),
                'recommendations':   [r for r in recs.split('\n') if r] if isinstance(recs, str) else [],
                'resolution':        md.get('resolution', ''),
                'resolution_source': md.get('resolution_source', ''),
                'resolved_at':       md.get('resolved_at', ''),
            })
        similar.sort(key=lambda x: x['similarity_score'], reverse=True)
        return similar


def _as_list(vec):
    """An embedding as a plain list (numpy arrays converted)."""
    return vec.tolist() if hasattr(vec, "tolist") else list(vec)
