"""Question -> retrieval -> answer, streamed as a sequence of event dicts.

Events: meta (sources and mode), delta (answer text), done (citation checks),
error. A language model (Claude, or a free provider) writes the answer when one
is configured. Otherwise, or if the call fails before any text was sent, an
extractive answer is built directly from the clauses, so the app always works
and always says which mode ran.
"""
from __future__ import annotations

import logging
import os
import re
from collections.abc import Iterator
from dataclasses import dataclass, field
from datetime import date

import anthropic
import openai

from . import config
from .corpus import CURRENT, EXPIRED, SUPERSEDED, UPCOMING, Corpus
from .prompts import SYSTEM_PROMPT, build_user_message
from .retrieval import Retrieved, Retriever

log = logging.getLogger(__name__)

CITATION = re.compile(r"\[([A-Z]{2}-[\w.\-]*\w)\]")
FOLLOW_UP = re.compile(r"^(and|but|also|then|what about|how about|what if|same|ok|okay)\b|\b(it|that|this|those|there|they)\b", re.I)
NOT_COVERED = (
    "The hostel manual and notices I have do not cover this. "
    "Please ask your block warden for hostel matters or the Transport Office for bus matters."
)


@dataclass
class Turn:
    role: str
    content: str


@dataclass
class Checks:
    cited: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)


def nice_date(value: date) -> str:
    return f"{value.day} {value.strftime('%b %Y')}"


def check_citations(text: str, retrieved_ids: list[str], corpus: Corpus, as_of: date) -> Checks:
    """Verify the answer against what was retrieved. This runs on every answer, in code."""
    checks = Checks()
    seen = list(dict.fromkeys(CITATION.findall(text)))
    known = set(retrieved_ids)
    checks.cited = [c for c in seen if c in known]
    for bad in (c for c in seen if c not in known):
        checks.warnings.append(f"The answer cites {bad}, which is not one of the retrieved clauses. Treat that statement as unverified.")
    if not seen and retrieved_ids and text.strip() and not is_decline(text):
        checks.warnings.append("This answer cites no clause. Check it against the notice board before relying on it.")

    cited_current = {c for c in checks.cited if corpus.status(c, as_of) == CURRENT}
    for cid in checks.cited:
        if corpus.status(cid, as_of) in (SUPERSEDED, EXPIRED):
            group = corpus.group_of(cid)
            if not any(corpus.group_of(c) == group for c in cited_current):
                in_force = [c.id for c in corpus.history(cid, as_of) if corpus.status(c.id, as_of) == CURRENT]
                if in_force:
                    checks.warnings.append(f"The answer cites outdated clause {cid} without the rule now in force ({', '.join(in_force)}).")
    return checks


def is_decline(text: str) -> bool:
    return bool(re.search(r"(do|does) not cover|don't cover|not covered", text, re.I))


def offline_answer(result: Retrieved, corpus: Corpus, as_of: date) -> str:
    """Quote the clauses in force for the best-matching rules. No generation involved."""
    if not result.confident:
        return NOT_COVERED
    keep = [g for g, s in zip(result.groups, result.group_scores) if s >= result.group_scores[0] * 0.75][:2]
    parts = []
    for group in keep:
        statuses = {cid: corpus.status(cid, as_of) for cid in group}
        in_force = [cid for cid in group if statuses[cid] == CURRENT]
        for cid in in_force:
            clause = corpus.clauses[cid]
            line = f"**{clause.heading}:** {clause.text} [{cid}]"
            if clause.effective_until:
                line += f"\n\nThis is temporary and runs until {nice_date(clause.effective_until)}."
            parts.append(line)
        if not in_force:
            parts.append("No version of this rule is in force on this date.")
        for cid in group:
            clause = corpus.clauses[cid]
            if statuses[cid] == UPCOMING:
                parts.append(f"**Changing on {nice_date(clause.effective_from)}:** {clause.text} [{cid}]")
        older = [cid for cid in group if statuses[cid] in (SUPERSEDED, EXPIRED)]
        if older:
            cites = " ".join(f"[{cid}]" for cid in older)
            parts.append(f"Older versions of this rule no longer apply: {cites}")
    return "\n\n".join(parts)


def build_messages(question: str, history: list[Turn], result: Retrieved, corpus: Corpus, as_of: date) -> list[dict]:
    messages = [{"role": t.role, "content": t.content} for t in history[-config.HISTORY_MESSAGES:]]
    messages.append({"role": "user", "content": build_user_message(question, result.clause_ids, corpus, as_of, result.confident)})
    return messages


def stream_claude(client: anthropic.Anthropic, messages: list[dict]) -> Iterator[dict]:
    with client.beta.messages.stream(
        model=config.MODEL,
        max_tokens=config.MAX_TOKENS,
        system=SYSTEM_PROMPT,
        thinking={"type": "adaptive"},
        output_config={"effort": config.EFFORT},
        betas=["server-side-fallback-2026-07-01"],
        fallbacks="default",
        messages=messages,
    ) as stream:
        for text in stream.text_stream:
            yield {"type": "delta", "text": text}
        final = stream.get_final_message()
    yield {"type": "_stop", "stop_reason": final.stop_reason}


def stream_openai_compatible(client: openai.OpenAI, model: str, messages: list[dict]) -> Iterator[dict]:
    stream = client.chat.completions.create(
        model=model,
        messages=[{"role": "system", "content": SYSTEM_PROMPT}, *messages],
        stream=True,
    )
    finish_reason = None
    for chunk in stream:
        if not chunk.choices:
            continue
        choice = chunk.choices[0]
        if choice.delta and choice.delta.content:
            yield {"type": "delta", "text": choice.delta.content}
        finish_reason = choice.finish_reason or finish_reason
    stop_reason = {"length": "max_tokens", "content_filter": "refusal"}.get(finish_reason, "end_turn")
    yield {"type": "_stop", "stop_reason": stop_reason}


def describe_failure(exc: Exception, label: str) -> str:
    """Both SDKs use the same error names, so one mapping covers every provider."""
    if isinstance(exc, (anthropic.AuthenticationError, openai.AuthenticationError)):
        return f"{label} rejected the API key."
    if isinstance(exc, (anthropic.RateLimitError, openai.RateLimitError)):
        return f"The {label} rate limit was reached."
    if isinstance(exc, (anthropic.APIStatusError, openai.APIStatusError)):
        log.warning("%s API error %s: %s", label, exc.status_code, exc.message)
        return f"{label} returned an error ({exc.status_code})."
    if isinstance(exc, (anthropic.APIConnectionError, openai.APIConnectionError)):
        return f"{label} could not be reached."
    log.warning("%s client error: %s", label, exc)  # e.g. no credentials found
    return f"{label} is not configured."


class Assistant:
    def __init__(self, corpus: Corpus, client=None, provider: str | None = None):
        self.corpus = corpus
        self.retriever = Retriever(corpus)
        self._client = client
        self._provider = provider or ("claude" if client is not None else None)

    @property
    def mode(self) -> str:
        return self._provider or config.active_provider()

    @property
    def client(self):
        if self._client is None:
            if self.mode == "claude":
                self._client = anthropic.Anthropic()
            else:
                preset = config.OPENAI_COMPATIBLE[self.mode]
                key = os.getenv(preset["key_env"], "") if preset["key_env"] else "not-needed"
                self._client = openai.OpenAI(base_url=preset["base_url"], api_key=key or "missing", timeout=45, max_retries=1)
        return self._client

    def retrieve(self, question: str, history: list[Turn], as_of: date) -> Retrieved:
        context = ""
        if FOLLOW_UP.search(question):
            context = next((t.content for t in reversed(history) if t.role == "user"), "")
        return self.retriever.search(question, as_of, context, wide=self.mode != "offline")

    def _stream(self, mode: str, messages: list[dict]) -> Iterator[dict]:
        if mode == "claude":
            return stream_claude(self.client, messages)
        return stream_openai_compatible(self.client, config.model_for(mode), messages)

    def answer(self, question: str, history: list[Turn], as_of: date) -> Iterator[dict]:
        result = self.retrieve(question, history, as_of)
        mode = self.mode
        yield {
            "type": "meta",
            "mode": mode,
            "model": config.model_for(mode),
            "as_of": as_of.isoformat(),
            "sources": [self.corpus.describe(cid, as_of) for cid in result.clause_ids],
        }

        text, notes = "", []
        if mode != "offline":
            label = config.provider_label(mode)
            failure = None
            try:
                messages = build_messages(question, history, result, self.corpus, as_of)
                for event in self._stream(mode, messages):
                    if event["type"] == "delta":
                        text += event["text"]
                        yield event
                    elif event["stop_reason"] == "refusal":
                        failure = f"{label} declined this request."
                    elif event["stop_reason"] == "max_tokens":
                        notes.append("The answer was cut off at the length limit.")
            except (anthropic.AnthropicError, openai.OpenAIError) as exc:
                failure = describe_failure(exc, label)
            if failure and text:
                yield {"type": "error", "message": f"{failure} The answer above is incomplete."}
                return
            if failure:
                notes.append(f"{failure} Showing the matching clauses directly instead.")
                mode = "offline"

        if mode == "offline":
            text = offline_answer(result, self.corpus, as_of)
            yield {"type": "delta", "text": text}

        checks = check_citations(text, result.clause_ids, self.corpus, as_of)
        yield {
            "type": "done",
            "mode": mode,
            "label": config.provider_label(mode),
            "cited": checks.cited,
            "warnings": checks.warnings,
            "notes": notes,
        }
