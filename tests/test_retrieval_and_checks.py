from datetime import date

from app.answer import Assistant, Turn, check_citations, offline_answer
from app.retrieval import Retriever, tokenize
from conftest import TODAY


def test_finding_old_clause_brings_in_replacement(corpus):
    result = Retriever(corpus).search("Old Bus Stand stop", TODAY)
    assert "TM-1.2" in result.clause_ids and "TN-2026-03.1" in result.clause_ids


def test_time_travel_excludes_future_notices(corpus):
    result = Retriever(corpus).search("weekday gate closing time", date(2026, 2, 1))
    assert "HN-2026-19.1" not in result.clause_ids
    assert "HN-2026-04.1" in result.clause_ids


def test_low_coverage_is_not_confident(corpus):
    assert not Retriever(corpus).search("laundry charges", TODAY).confident
    assert Retriever(corpus).search("", TODAY).clause_ids == []


def test_tokenize_stems_and_drops_stopwords():
    assert tokenize("The gates are closing") == tokenize("gate closes")


def test_offline_answer_never_presents_outdated_rule_as_current(corpus):
    result = Retriever(corpus).search("weekday gate closing time", TODAY)
    text = offline_answer(result, corpus, TODAY)
    assert "9:15 PM" in text and "9:30" not in text and "[HM-2.1]" in text


def test_follow_up_uses_previous_question(corpus):
    assistant = Assistant(corpus, provider="offline")
    history = [Turn("user", "What time does the Route 3 bus leave?"), Turn("assistant", "7:25 AM")]
    assert "TN-2026-09.1" in assistant.retrieve("and what about it in the evening?", history, TODAY).clause_ids


def test_new_topic_does_not_inherit_previous_question(corpus):
    assistant = Assistant(corpus, provider="offline")
    history = [Turn("user", "What time does the Route 3 bus leave?")]
    assert "TN-2026-09.1" not in assistant.retrieve("mess timings", history, TODAY).clause_ids


def test_check_flags_invented_citation(corpus):
    checks = check_citations("Gate shuts at 8 [HN-2099-1.1]", ["HN-2026-19.1"], corpus, TODAY)
    assert checks.cited == [] and "HN-2099-1.1" in checks.warnings[0]


def test_check_flags_outdated_citation_without_current_rule(corpus):
    ids = ["HM-2.1", "HN-2026-04.1", "HN-2026-19.1"]
    assert check_citations("Closes 9:30 [HM-2.1]", ids, corpus, TODAY).warnings
    assert not check_citations("Closes 9:15 [HN-2026-19.1], was 9:30 [HM-2.1]", ids, corpus, TODAY).warnings


def test_check_flags_uncited_answer_but_not_a_decline(corpus):
    assert check_citations("It closes at nine.", ["HM-2.2"], corpus, TODAY).warnings
    assert not check_citations("The notices do not cover this.", ["HM-2.2"], corpus, TODAY).warnings


def test_wide_search_reaches_paraphrases_without_inflating_confidence(corpus):
    retriever = Retriever(corpus)
    narrow = retriever.search("Do buses operate on Republic Day?", TODAY)
    wide = retriever.search("Do buses operate on Republic Day?", TODAY, wide=True)
    assert "TM-3.4" not in narrow.clause_ids and "TM-3.4" in wide.clause_ids
    assert wide.confident == narrow.confident
    assert len(wide.clause_ids) == len(set(wide.clause_ids))


def test_wide_search_respects_as_of_and_cap(corpus):
    wide = Retriever(corpus).search("zzz", date(2026, 2, 1), wide=True)
    assert "HN-2026-19.1" not in wide.clause_ids
    assert wide.clause_ids and not wide.confident and len(wide.clause_ids) <= 60
