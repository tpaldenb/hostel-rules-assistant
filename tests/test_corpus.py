from datetime import date

import pytest

from app.corpus import Clause, Corpus, CorpusError
from conftest import TODAY


def test_two_step_chain_only_latest_is_current(corpus):
    assert corpus.status("HN-2026-19.1", TODAY) == "current"
    assert corpus.status("HN-2026-04.1", TODAY) == "superseded"
    assert corpus.status("HM-2.1", TODAY) == "superseded"


def test_each_version_was_current_in_its_own_time(corpus):
    assert corpus.status("HM-2.1", date(2026, 1, 18)) == "current"
    assert corpus.status("HN-2026-04.1", date(2026, 1, 19)) == "current"
    assert corpus.status("HN-2026-04.1", date(2026, 8, 31)) == "current"
    assert corpus.status("HN-2026-19.1", date(2026, 9, 1)) == "current"


def test_issued_notice_does_not_replace_rule_before_it_takes_effect(corpus):
    day = date(2026, 8, 25)  # 19/2026 issued, effective 1 Sep
    assert corpus.status("HN-2026-19.1", day) == "upcoming"
    assert corpus.status("HN-2026-04.1", day) == "current"


def test_base_rule_returns_when_temporary_notice_lapses(corpus):
    assert corpus.status("HM-4.1", date(2026, 10, 25)) == "superseded"
    assert corpus.status("HN-2026-23.1", date(2026, 10, 25)) == "current"
    assert corpus.status("HM-4.1", date(2026, 10, 26)) == "current"
    assert corpus.status("HN-2026-23.1", date(2026, 10, 26)) == "expired"


def test_active_temporary_and_upcoming_notices(corpus):
    assert corpus.status("HN-2026-24.1", TODAY) == "current"
    assert corpus.status("HM-5.1", TODAY) == "superseded"
    assert corpus.status("TN-2026-13.1", TODAY) == "upcoming"
    assert corpus.status("TM-3.1", TODAY) == "current"


def test_notices_are_hidden_before_issue(corpus):
    assert not corpus.is_visible("HN-2026-19.1", date(2026, 2, 1))
    assert [c.id for c in corpus.history("HM-2.1", date(2026, 2, 1))] == ["HM-2.1", "HN-2026-04.1"]


def make(cid, start, supersedes=()):
    return Clause(cid, "h", "t", "D", "T", "notice", "hostel", start, start, None, "x", tuple(supersedes))


def test_rejects_unknown_supersedes_and_duplicates():
    with pytest.raises(CorpusError):
        Corpus([make("HN-1", date(2026, 1, 1), ["HN-9"])])
    with pytest.raises(CorpusError):
        Corpus([make("HN-1", date(2026, 1, 1)), make("HN-1", date(2026, 1, 2))])


def test_rejects_replacement_that_predates_the_rule():
    with pytest.raises(CorpusError):
        Corpus([make("HN-1", date(2026, 5, 1)), make("HN-2", date(2026, 1, 1), ["HN-1"])])
