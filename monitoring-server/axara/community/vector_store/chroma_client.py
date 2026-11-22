# Copyright (c) 2026 Axara Pty Ltd.
# SPDX-License-Identifier: AGPL-3.0-or-later
"""
ChromaDB client for vector storage.

JSON Standardization (D2 6.3): supports NAMED collections so the contract is
collection-aware and ready for future collections without rework. Default collection
remains "incidents",
so existing callers are unchanged.
"""
import chromadb
import os


class ChromaDBClient:
    """Connection to ChromaDB and its collections (cosine space for incident text)."""
    # Known collections and their HNSW space; cosine suits sentence-transformer text.
    _COLLECTION_SPACE = {
        "incidents": "cosine",
    }
    DEFAULT_COLLECTION = "incidents"

    def __init__(self):
        """Connect to CHROMA_URL (default http://chromadb:8000)."""
        chroma_url = os.getenv("CHROMA_URL") or "http://chromadb:8000"

        # chromadb.HttpClient expects host and port separately. Parse them out of
        # CHROMA_URL so the docker-compose form ("http://chromadb:8000") works.
        parsed = chroma_url.rstrip('/')
        if '://' in parsed:
            parsed = parsed.split('://', 1)[1]   # strip scheme
        if ':' in parsed:
            host, port_str = parsed.rsplit(':', 1)
            port = int(port_str)
        else:
            host = parsed
            port = 8000

        self.client = chromadb.HttpClient(host=host, port=port)
        # Backward-compat attribute; callers that referenced it still work.
        self.collection_name = self.DEFAULT_COLLECTION

    def get_or_create_collection(self, name: str = None):
        """
        Get or create a named collection. Defaults to "incidents" so existing
        callers (rag_engine.initialize()) keep working with no argument.
        """
        coll_name = name or self.DEFAULT_COLLECTION
        space = self._COLLECTION_SPACE.get(coll_name, "cosine")
        return self.client.get_or_create_collection(
            name=coll_name,
            metadata={"hnsw:space": space},
        )
