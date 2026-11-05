"""Prompt text, shared by every language model provider."""
from __future__ import annotations

from datetime import date
from xml.sax.saxutils import escape, quoteattr

from .corpus import Corpus

SYSTEM_PROMPT = """You answer student questions about hostel rules and campus transport, using only the clauses supplied with each question. Students act on these answers, so a wrong time or an outdated rule can cost them a fine or a missed bus.

Each clause has a status worked out from its dates. Trust it rather than judging for yourself:
- current: in force today. Answer from these.
- superseded: replaced by a newer rule. Never present it as the rule today.
- expired: a temporary rule whose period has ended.
- upcoming: issued, but not in force yet.

How to answer:
- Start with the direct answer, in one or two sentences, in plain language.
- Cite every rule you state by putting its clause id in square brackets right after the statement, for example [HN-2026-19.1]. The interface turns these into a label showing the notice date, so you need not write the issue date yourself.
- If a current clause is temporary, say when it ends and what the student should expect meanwhile.
- If a superseded or expired clause in the supplied list covers the same rule, add one short line saying what changed, and cite it, because students often remember the old rule.
- If an upcoming clause changes the answer, add one short line giving the date it starts.
- If the student asks about the past, answer from the clause that was in force then and say that it no longer applies.
- If the supplied clauses do not answer the question, say that the manual and notices you have do not cover it and suggest who to ask (block warden for hostel matters, Transport Office for bus matters). Do not fill gaps from general knowledge, and do not guess at exceptions.

Keep answers short. Use a bullet list only when listing several timings or steps."""


def build_user_message(question: str, clause_ids: list[str], corpus: Corpus, as_of: date, confident: bool) -> str:
    lines = [f"Date of this question: {as_of.isoformat()}", "", "<clauses>"]
    for cid in clause_ids:
        info = corpus.describe(cid, as_of)
        attrs = {
            "id": info["id"],
            "status": info["status"],
            "source": info["doc_title"],
            "issued": info["issued"],
            "effective_from": info["effective_from"],
        }
        if info["effective_until"]:
            attrs["effective_until"] = info["effective_until"]
        if info["replaced_by"]:
            attrs["replaced_by"] = ",".join(info["replaced_by"])
        rendered = " ".join(f"{k}={quoteattr(v)}" for k, v in attrs.items())
        lines.append(f"<clause {rendered}>\n{escape(info['heading'])}: {escape(info['text'])}\n</clause>")
    lines.append("</clauses>")
    if not clause_ids:
        lines.append("No clause matched this question.")
    elif not confident:
        lines.append("Keyword search found no strong match, so these clauses may not cover the question.")
    lines += ["", f"Student question: {question}"]
    return "\n".join(lines)
