# Copyright (c) 2026 Axara Pty Ltd.
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Similar-incident memory (RAG): what is stored for a resolved incident and what a new,
similar incident gets back. ChromaDB is replaced by an in-memory collection with the same
cosine-distance and `where` semantics; embeddings are a deterministic bag of words."""
import hashlib
import math
import re
import sys
import types
from pathlib import Path
from types import SimpleNamespace

import pytest

SERVER = Path(__file__).resolve().parents[2]
if str(SERVER) not in sys.path:
    sys.path.insert(0, str(SERVER))
sys.modules.setdefault("chromadb", types.ModuleType("chromadb"))       # not needed: faked below

from axara.community.incident_management import rag_engine as rag  # noqa: E402


# ----------------------------------------------------------------- fakes

class FakeEmbedder:
    """Deterministic bag-of-words embedder for tests."""
    DIM = 512

    def generate(self, text):
        """Normalised hashed word-count vector of a text."""
        v = [0.0] * self.DIM
        for w in re.findall(r"[a-z0-9]+", text.lower()):
            v[int(hashlib.md5(w.encode()).hexdigest(), 16) % self.DIM] += 1.0
        n = math.sqrt(sum(x * x for x in v)) or 1.0
        return [x / n for x in v]


def _match(md, where):
    """Whether metadata matches a ChromaDB where filter ($eq, $ne, $and)."""
    if not where:
        return True
    if "$and" in where:
        return all(_match(md, w) for w in where["$and"])
    (k, cond), = where.items()
    op, val = next(iter(cond.items()))
    return md.get(k) == val if op == "$eq" else md.get(k) != val


class FakeCollection:
    """In-memory stand-in for a ChromaDB collection."""
    def __init__(self):
        """Start empty."""
        self.rows = {}

    def count(self):
        """Number of stored rows."""
        return len(self.rows)

    def upsert(self, ids, embeddings, metadatas, documents):
        """Insert or replace rows (metadata must be scalar, as in ChromaDB)."""
        for i, e, m, d in zip(ids, embeddings, metadatas, documents):
            assert all(isinstance(v, (str, int, float, bool)) for v in m.values()), m
            self.rows[i] = (e, m, d)

    def add(self, **kw):                     # ChromaDB ignores an existing id on add()
        """Insert new rows; fails on an existing id, which ChromaDB would silently ignore."""
        for i in kw["ids"]:
            assert i not in self.rows, "add() of an existing id is ignored by ChromaDB"
        self.upsert(**kw)

    def delete(self, ids):
        """Remove rows by id."""
        for i in ids:
            self.rows.pop(i, None)

    def query(self, query_embeddings, n_results, where=None, include=None):
        """Nearest rows by cosine distance, filtered by `where`."""
        q = query_embeddings[0]
        hits = []
        for i, (e, m, _) in self.rows.items():
            if _match(m, where):
                hits.append((1.0 - sum(a * b for a, b in zip(q, e)), i, m))   # cosine distance
        hits.sort()
        hits = hits[:n_results]
        return {"ids": [[h[1] for h in hits]], "distances": [[h[0] for h in hits]],
                "metadatas": [[h[2] for h in hits]]}


@pytest.fixture
def engine():
    """A RAG engine wired to the fake embedder and collection."""
    e = rag.RAGEngine.__new__(rag.RAGEngine)
    e.top_k, e.similarity_threshold = 5, 0.7
    e.embedding_generator = FakeEmbedder()
    e._collections = {"incidents": FakeCollection()}
    return e


def inc(title, desc, app="payments", host="app01"):
    """An incident dict for tests."""
    return {"title": title, "description": desc, "severity": "high",
            "application": {"name": app}, "server": {"hostname": host}, "resolved_at": "2026-11-15T10:00:00"}


DISK = inc("Disk usage critical on /var", "Partition /var at 97% (threshold 90%) on app01")
RESOLUTION = {"root_cause": "Log rotation stopped after logrotate config was overwritten",
              "recommendations": ["Restore /etc/logrotate.d/app", "Add a disk usage trend alert"],
              "resolution": "Restored logrotate config and purged old logs",
              "resolution_source": "operator", "confidence": 0.82}


# ----------------------------------------------------------------- round trip

def test_resolved_incident_round_trip(engine):
    """A stored resolution is found again for a similar new incident, with all its fields."""
    assert engine.store_incident("inc-1", DISK, RESOLUTION)
    new = inc("Disk usage critical on /var", "Partition /var at 95% (threshold 90%) on app01")
    hits = engine.find_similar_incidents(new, {"application": "payments"}, exclude_id="inc-2")
    assert len(hits) == 1
    h = hits[0]
    assert h["incident_id"] == "inc-1"
    assert h["root_cause"] == RESOLUTION["root_cause"]
    assert h["resolution"] == RESOLUTION["resolution"]
    assert h["recommendations"] == RESOLUTION["recommendations"]
    assert h["resolution_source"] == "operator"
    assert h["similarity_score"] > 0.8


def test_signature_is_symmetric(engine):
    """Same failure, long resolution text: still an (almost) exact match."""
    long = dict(RESOLUTION, root_cause="x " * 400, resolution="y " * 400)
    engine.store_incident("inc-1", DISK, long)
    hits = engine.find_similar_incidents(DISK, None)
    assert hits and hits[0]["similarity_score"] > 0.99


def test_unrelated_incident_not_returned(engine):
    """An unrelated incident finds nothing."""
    engine.store_incident("inc-1", DISK, RESOLUTION)
    other = inc("SSL certificate expires in 5 days", "Certificate for api.example.com expires 2026-11-20")
    assert engine.find_similar_incidents(other, {"application": "payments"}) == []


def test_application_filter_and_exclude_self(engine):
    """Matches are limited to the application and never include the incident itself."""
    engine.store_incident("inc-1", DISK, RESOLUTION)
    engine.store_incident("inc-9", inc(DISK["title"], DISK["description"], app="billing"), RESOLUTION)
    hits = engine.find_similar_incidents(DISK, {"application": "payments"})
    assert [h["incident_id"] for h in hits] == ["inc-1"]
    assert engine.find_similar_incidents(DISK, {"application": "payments"}, exclude_id="inc-1") == []


def test_re_resolution_replaces_record(engine):
    """Resolving again replaces the stored record."""
    engine.store_incident("inc-1", DISK, RESOLUTION)
    engine.store_incident("inc-1", DISK, dict(RESOLUTION, resolution="Extended the volume"))
    coll = engine._collections["incidents"]
    assert coll.count() == 1
    assert engine.find_similar_incidents(DISK, None)[0]["resolution"] == "Extended the volume"


def test_similarity_is_cosine(engine):
    """Similarity is 1 - cosine distance and matches below the threshold are dropped."""
    res = {"ids": [["a", "b"]], "distances": [[0.2, 0.35]], "metadatas": [[{}, {}]]}
    scores = [h["similarity_score"] for h in engine._parse_results(res)]
    assert scores == [0.8]                       # 1 - 0.35 = 0.65 < 0.7 is dropped


# ----------------------------------------------------------------- analysis fields

def test_analysis_fields_structured_and_failed():
    """Structured analysis fields are used; a failed analysis gives nothing usable."""
    ok = rag.analysis_fields({"root_cause": "RC", "recommendations": ["a"], "confidence": 0.7}, None)
    assert ok == {"root_cause": "RC", "recommendations": ["a"], "confidence": 0.7, "usable": True}
    failed = rag.analysis_fields({"root_cause": "Analysis failed - unable to determine root cause (x)",
                                  "recommendations": ["Manual investigation required"],
                                  "confidence": 0.0, "failed": True}, None)
    assert failed["usable"] is False and failed["root_cause"] == "" and failed["recommendations"] == []
    assert rag.analysis_fields(None, None)["usable"] is False


def test_analysis_fields_parses_rendered_report():
    """Fields are parsed from a rendered analysis report."""
    text = ("Disk filled by logs\n\nComponent: logrotate\nROOT CAUSE:\nLog rotation stopped\n\n"
            "RECOMMENDATIONS:\n1. Restore config\n2. Add trend alert\n\nCONFIDENCE: 82%")
    f = rag.analysis_fields(None, text)
    assert f == {"root_cause": "Log rotation stopped", "recommendations": ["Restore config", "Add trend alert"],
                 "confidence": 0.82, "usable": True}


# ----------------------------------------------------------------- orchestrator storage rules

def _store(engine, incident, **kw):
    """Call the orchestrator's _store_in_rag with a fake orchestrator."""
    orch_mod = pytest.importorskip("axara.community.incident_management.orchestrator")
    fake = SimpleNamespace(rag_engine=engine, _incident_to_dict=lambda i: DISK)
    return orch_mod.IncidentOrchestrator._store_in_rag(fake, incident, check_licence=False, **kw)


def _incident(analysis=None, notes=None):
    """A fake incident with analysis data and resolution notes."""
    return SimpleNamespace(id="inc-5", llm_analysis=None, llm_analysis_data=analysis, resolution_notes=notes)


def test_store_rules(engine):
    """What is stored: nothing for a failed analysis; auto or operator resolutions otherwise."""
    good = {"root_cause": "RC", "recommendations": ["fix"], "confidence": 0.8}
    failed = {"root_cause": "Analysis failed - x", "confidence": 0.0, "failed": True}
    coll = engine._collections["incidents"]

    assert _store(engine, _incident(failed)) is False            # nothing learned: not stored
    assert coll.count() == 0

    assert _store(engine, _incident(good)) is True                # auto-recovered, good analysis
    md = coll.rows["inc-5"][1]
    assert md["root_cause"] == "RC" and md["resolution"] == rag.AUTO_RESOLUTION and md["resolution_source"] == "auto"

    assert _store(engine, _incident(failed), resolution_notes="Restarted the DB pool") is True
    md = coll.rows["inc-5"][1]
    assert md["root_cause"] == "" and md["resolution"] == "Restarted the DB pool" and md["resolution_source"] == "operator"

    assert _store(engine, _incident(good, notes="Closed in Jira: rebooted"), source="jira") is True
    md = coll.rows["inc-5"][1]
    assert md["resolution"] == "Closed in Jira: rebooted" and md["resolution_source"] == "jira"
