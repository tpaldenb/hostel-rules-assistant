"""Keyword retrieval (BM25) that returns whole rule histories, not lone clauses.

An old manual clause and the notice that replaced it often match a question
equally well. Scoring happens per clause, but results are grouped by rule, so
finding any version brings in every version. A replacing notice therefore
cannot be lost to a better-worded outdated clause.
"""
from __future__ import annotations

import math
import re
from collections import Counter
from dataclasses import dataclass
from datetime import date

from .corpus import Clause, Corpus

K1, B = 1.5, 0.75
HEADING_WEIGHT = 3
SYNONYM_WEIGHT = 0.5
SYNONYM_CREDIT = 0.6  # a synonym match is weaker evidence than the exact word
MAX_GROUPS = 4  # rules returned by a narrow search
RELATIVE_CUTOFF = 0.35  # drop rules scoring under this share of the best rule
# Keyword search misses paraphrases ("my mother" vs "parents"), and a clause that
# never reaches the model cannot be recovered. So model answers are topped up with
# the remaining rules, best first, up to this many clauses. On a corpus this size
# that is all of them, for about 3k tokens. On a larger one the ranking decides.
MAX_CONTEXT_CLAUSES = 60
MIN_COVERAGE = 0.5  # share of the question's weight the best clause must match

STOPWORDS = set("""a about all also am an and any are as at be been but by can could did do does for from get
got had has have how i if in into is it its just me my need of on or our should so that the their them then there
these they this to us was we were what when where which who why will with would you your please tell know want
much many long still go going goes come coming comes back through way there's
s t d ll re ve""".split())

# Student wording -> document wording. Keys and values are stems.
SYNONYMS = {
    "curfew": ["gate", "clos", "in-time"], "deadline": ["clos"], "lock": ["gate", "clos"],
    "shut": ["clos"], "close": ["clos"], "closing": ["clos"], "intime": ["gate", "clos"],
    "night": ["overnight"], "overnight": ["leave"], "week": ["night", "long"], "days": ["night", "long"],
    "monday": ["weekday"], "tuesday": ["weekday"], "wednesday": ["weekday"], "thursday": ["weekday"], "friday": ["weekday"],
    "permission": ["leave", "approv"], "apply": ["application", "leave"], "home": ["leave"],
    "outpass": ["leave", "outing"], "vacation": ["leave"], "sick": ["medical", "unwell"], "ill": ["medical", "unwell"],
    "doctor": ["medical"], "hospital": ["medical", "emergency"], "urgent": ["emergency"],
    "food": ["mess"], "meal": ["mess"], "canteen": ["mess"], "dining": ["mess"],
    "breakfast": ["mess"], "lunch": ["mess"], "dinner": ["mess"], "snack": ["mess"],
    "parent": ["visitor", "guardian"], "guest": ["visitor"], "friend": ["visitor", "guest"], "meet": ["visitor"],
    "visit": ["visitor"], "bus": ["route"], "transport": ["route", "bus"], "shuttle": ["bus"],
    "train": ["railway", "station"], "railway": ["station"], "station": ["railway"], "airport": ["route"],
    "cost": ["fee", "fine"], "price": ["cost"], "fee": ["cost", "fine"], "charge": ["fine", "cost"],
    "penalty": ["fine"], "fine": ["fee"], "lost": ["replacement"], "miss": ["lost"],
    "noise": ["quiet"], "music": ["quiet"], "timing": ["time"], "schedule": ["timetable", "leave"],
    "timetable": ["time"], "library": ["pass"],
}


def stem(token: str) -> str:
    for suffix in ("ing", "ies", "es", "ed", "s"):
        if token.endswith(suffix) and len(token) - len(suffix) >= 3 and not token.endswith("ss"):
            token = token[: -len(suffix)] + ("y" if suffix == "ies" else "")
            break
    return token[:-1] if token.endswith("e") and len(token) > 3 else token


def tokenize(text: str) -> list[str]:
    tokens = re.findall(r"[a-z0-9]+", text.lower().replace("in-time", "intime"))
    return [stem(t) for t in tokens if t not in STOPWORDS]


_SYNONYM_STEMS = {stem(k): [stem(v) for v in vals] for k, vals in SYNONYMS.items()}


@dataclass
class Retrieved:
    clause_ids: list[str]  # ordered, best rule first, each rule's versions together
    scores: dict[str, float]  # search score per clause id
    groups: list[list[str]]  # clause ids per rule, best rule first
    group_scores: list[float]
    coverage: float

    @property
    def confident(self) -> bool:
        return bool(self.groups) and self.coverage >= MIN_COVERAGE


class Retriever:
    def __init__(self, corpus: Corpus):
        self.corpus = corpus
        self.docs: dict[str, Counter] = {}
        for clause in corpus.clauses.values():
            self.docs[clause.id] = self._terms(clause)
        self.n = len(self.docs)
        self.avg_len = sum(sum(d.values()) for d in self.docs.values()) / self.n
        self.df = Counter(term for doc in self.docs.values() for term in doc)

    @staticmethod
    def _terms(clause: Clause) -> Counter:
        counts = Counter(tokenize(clause.text))
        for term in tokenize(clause.heading):
            counts[term] += HEADING_WEIGHT
        return counts

    def _idf(self, term: str) -> float:
        df = self.df.get(term, 0)
        return math.log(1 + (self.n - df + 0.5) / (df + 0.5))

    def _bm25(self, doc: Counter, weights: dict[str, float]) -> float:
        length = sum(doc.values())
        score = 0.0
        for term, weight in weights.items():
            tf = doc.get(term, 0)
            if tf:
                score += weight * self._idf(term) * tf * (K1 + 1) / (tf + K1 * (1 - B + B * length / self.avg_len))
        return score

    def _coverage(self, terms: list[str], doc: Counter) -> float:
        """Share of the question's information the clause matches. Unknown words count heavily."""
        total = matched = 0.0
        for term in set(terms):
            idf = self._idf(term)
            total += idf
            if term in doc:
                matched += idf
            elif any(s in doc for s in _SYNONYM_STEMS.get(term, [])):
                matched += idf * SYNONYM_CREDIT
        return matched / total if total else 0.0

    def search(self, question: str, as_of: date, context: str = "", wide: bool = False) -> Retrieved:
        """`context` is the previous question, used only for follow-ups like "what about Sundays?"."""
        terms = tokenize(question)
        weights: dict[str, float] = {}
        for term in terms:
            weights[term] = 1.0
        for term in terms:
            for synonym in _SYNONYM_STEMS.get(term, []):
                weights.setdefault(synonym, SYNONYM_WEIGHT)
        for term in tokenize(context):
            weights.setdefault(term, SYNONYM_WEIGHT)

        scores = {}
        for cid, doc in self.docs.items():
            if self.corpus.is_visible(cid, as_of):
                score = self._bm25(doc, weights)
                if score > 0:
                    scores[cid] = score
        best_per_group: dict[str, float] = {}
        for cid, score in scores.items():
            group = self.corpus.group_of(cid)
            best_per_group[group] = max(best_per_group.get(group, 0.0), score)
        ranked = sorted(best_per_group.items(), key=lambda kv: -kv[1])
        top = ranked[0][1] if ranked else 0.0
        chosen = [(g, s) for g, s in ranked if s >= top * RELATIVE_CUTOFF][:MAX_GROUPS]

        groups, group_scores = [], []
        for group, score in chosen:
            groups.append(self._members(group, as_of))
            group_scores.append(score)
        ordered = [cid for members in groups for cid in members]
        coverage = max((self._coverage(terms, self.docs[cid]) for cid in ordered), default=0.0)

        if wide:
            remaining = [g for g, _ in ranked if g not in dict(chosen)]
            remaining += [g for g in dict.fromkeys(self.corpus.group_of(c) for c in self.docs) if g not in best_per_group]
            for group in remaining:
                members = self._members(group, as_of)
                if members and len(ordered) + len(members) <= MAX_CONTEXT_CLAUSES:
                    ordered += members
        return Retrieved(ordered, scores, groups, group_scores, coverage)

    def _members(self, group: str, as_of: date) -> list[str]:
        return [c.id for c in self.corpus.group_history(group, as_of)]
