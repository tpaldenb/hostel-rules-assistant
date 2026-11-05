"""Loads the rule documents and works out which clause is in force on a date.

Status is computed here, in plain code, and never left to the language model.
A clause is replaced only while a successor is actually in effect, so when a
temporary notice lapses the rule it overrode comes back on its own.
"""
from __future__ import annotations

import re
from collections import defaultdict
from dataclasses import dataclass
from datetime import date
from pathlib import Path

CURRENT, SUPERSEDED, EXPIRED, UPCOMING = "current", "superseded", "expired", "upcoming"
CLAUSE_HEADING = re.compile(r"^##\s+(?P<id>[A-Z]{2}-[\w.\-]+)\s*\|\s*(?P<heading>.+)$")


class CorpusError(ValueError):
    """A rule document is malformed or contradicts another one."""


@dataclass(frozen=True)
class Clause:
    id: str
    heading: str
    text: str
    doc_id: str
    doc_title: str
    doc_type: str  # manual | notice
    category: str  # hostel | transport
    issued: date
    effective_from: date
    effective_until: date | None
    issued_by: str
    supersedes: tuple[str, ...] = ()


def _parse_date(value: str, field: str, path: Path) -> date:
    try:
        return date.fromisoformat(value)
    except ValueError as exc:
        raise CorpusError(f"{path.name}: bad date in {field}: {value!r}") from exc


def parse_document(path: Path) -> list[Clause]:
    raw = path.read_text(encoding="utf-8")
    match = re.match(r"^---\n(.*?)\n---\n(.*)$", raw, re.DOTALL)
    if not match:
        raise CorpusError(f"{path.name}: missing front matter")
    meta = {}
    for line in match.group(1).splitlines():
        key, _, value = line.partition(":")
        meta[key.strip()] = value.strip()
    for field in ("id", "title", "type", "category", "issued", "effective_from", "issued_by"):
        if not meta.get(field):
            raise CorpusError(f"{path.name}: missing {field}")

    issued = _parse_date(meta["issued"], "issued", path)
    start = _parse_date(meta["effective_from"], "effective_from", path)
    until = _parse_date(meta["effective_until"], "effective_until", path) if meta.get("effective_until") else None
    if start < issued:
        raise CorpusError(f"{path.name}: takes effect before it was issued")
    if until and until < start:
        raise CorpusError(f"{path.name}: ends before it starts")

    clauses, current = [], None

    def flush():
        if current:
            text = " ".join(current["lines"]).strip()
            if not text:
                raise CorpusError(f"{path.name}: clause {current['id']} is empty")
            clauses.append(Clause(
                id=current["id"], heading=current["heading"], text=text,
                doc_id=meta["id"], doc_title=meta["title"], doc_type=meta["type"],
                category=meta["category"], issued=issued, effective_from=start,
                effective_until=until, issued_by=meta["issued_by"],
                supersedes=tuple(current["supersedes"]),
            ))

    for line in match.group(2).splitlines():
        heading = CLAUSE_HEADING.match(line)
        if heading:
            flush()
            current = {"id": heading["id"], "heading": heading["heading"].strip(), "lines": [], "supersedes": []}
        elif current is None:
            continue
        elif line.lower().startswith("supersedes:"):
            current["supersedes"] += [s.strip() for s in line.split(":", 1)[1].split(",") if s.strip()]
        elif line.strip():
            current["lines"].append(line.strip())
    flush()
    return clauses


class Corpus:
    def __init__(self, clauses: list[Clause]):
        self.clauses: dict[str, Clause] = {}
        for clause in clauses:
            if clause.id in self.clauses:
                raise CorpusError(f"duplicate clause id {clause.id}")
            self.clauses[clause.id] = clause

        self.successors: dict[str, list[str]] = defaultdict(list)
        for clause in clauses:
            for old in clause.supersedes:
                if old not in self.clauses:
                    raise CorpusError(f"{clause.id} supersedes unknown clause {old}")
                if self.clauses[old].effective_from > clause.effective_from:
                    raise CorpusError(f"{clause.id} takes effect before {old}, which it supersedes")
                self.successors[old].append(clause.id)
        self._groups = self._build_groups()

    @classmethod
    def load(cls, directory: Path) -> "Corpus":
        clauses = []
        for path in sorted(directory.glob("*.md")):
            clauses += parse_document(path)
        if not clauses:
            raise CorpusError(f"no documents found in {directory}")
        return cls(clauses)

    def _build_groups(self) -> dict[str, str]:
        """Union-find: clauses linked by supersession share a group, i.e. one rule's history."""
        parent = {cid: cid for cid in self.clauses}

        def find(x):
            while parent[x] != x:
                parent[x] = parent[parent[x]]
                x = parent[x]
            return x

        for clause in self.clauses.values():
            for old in clause.supersedes:
                parent[find(old)] = find(clause.id)
        return {cid: find(cid) for cid in self.clauses}

    def group_of(self, clause_id: str) -> str:
        return self._groups[clause_id]

    def is_visible(self, clause_id: str, as_of: date) -> bool:
        """A clause cannot be known before its document was issued."""
        return self.clauses[clause_id].issued <= as_of

    def _in_effect(self, clause: Clause, as_of: date) -> bool:
        return clause.effective_from <= as_of and (clause.effective_until is None or as_of <= clause.effective_until)

    def status(self, clause_id: str, as_of: date) -> str:
        clause = self.clauses[clause_id]
        if clause.effective_from > as_of:
            return UPCOMING
        if clause.effective_until and clause.effective_until < as_of:
            return EXPIRED
        for new_id in self.successors[clause_id]:
            if self._in_effect(self.clauses[new_id], as_of):
                return SUPERSEDED
        return CURRENT

    def history(self, clause_id: str, as_of: date) -> list[Clause]:
        """Every visible version of this rule, oldest first."""
        return self.group_history(self._groups[clause_id], as_of)

    def group_history(self, group: str, as_of: date) -> list[Clause]:
        members = [c for c in self.clauses.values() if self._groups[c.id] == group and c.issued <= as_of]
        return sorted(members, key=lambda c: (c.effective_from, c.issued, c.id))

    def describe(self, clause_id: str, as_of: date) -> dict:
        """JSON-ready clause, with its status on the given date."""
        clause = self.clauses[clause_id]
        status = self.status(clause_id, as_of)
        replaced_by = [
            s for s in self.successors[clause_id]
            if self.is_visible(s, as_of) and self._in_effect(self.clauses[s], as_of)
        ]
        return {
            "id": clause.id,
            "heading": clause.heading,
            "text": clause.text,
            "doc_id": clause.doc_id,
            "doc_title": clause.doc_title,
            "doc_type": clause.doc_type,
            "category": clause.category,
            "issued": clause.issued.isoformat(),
            "effective_from": clause.effective_from.isoformat(),
            "effective_until": clause.effective_until.isoformat() if clause.effective_until else None,
            "issued_by": clause.issued_by,
            "status": status,
            "temporary": clause.effective_until is not None,
            "replaced_by": replaced_by,
        }
