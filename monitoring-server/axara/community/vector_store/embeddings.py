# Copyright (c) 2026 Axara Pty Ltd.
# SPDX-License-Identifier: AGPL-3.0-or-later
"""
Embedding generation for RAG. The sentence-transformers model is loaded once per worker
process and shared, rather than once per incident orchestrator (one per background task).
"""
import threading

_MODEL_NAME = 'all-MiniLM-L6-v2'
_model = None
_lock = threading.Lock()


def _shared_model():
    """The sentence-transformer model, loaded once per process."""
    global _model
    if _model is None:
        with _lock:
            if _model is None:
                from sentence_transformers import SentenceTransformer
                _model = SentenceTransformer(_MODEL_NAME)
    return _model


class EmbeddingGenerator:
    """Turns text into embedding vectors."""
    def __init__(self):
        """Use the shared model."""
        self.model = _shared_model()

    def generate(self, text: str):
        """Embedding of a text.

        Args:
            text: Text to embed.

        Returns:
            The vector.
        """
        return self.model.encode(text)
