# Copyright (c) 2026 Axara Pty Ltd.
# SPDX-License-Identifier: AGPL-3.0-or-later
"""
Embedding generation for RAG. The sentence-transformers model is loaded once per worker
process and shared, rather than once per incident orchestrator (one per background task).

The model is downloaded into the image at build time (Dockerfile) and always loaded from that
local copy: local_files_only stops the library from asking the Hugging Face Hub for a newer
version, so the server never attempts a network call for it (air-gapped installs included).
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
                _model = SentenceTransformer(_MODEL_NAME, local_files_only=True)
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
