# Hostel and Transport Rules Assistant: project report

## 1. Problem

Hostel and transport rules live in a yearly manual plus a stream of notices. A notice can quietly replace a manual clause, a later notice can replace that one, and a temporary notice can lapse and bring the old rule back. A student who searches the manual, or asks a plain chatbot, can get a rule that was correct last semester and be fined for it.

The assistant must answer from these documents only, cite the notice date, and make outdated rules visible.

## 2. Key design idea

**The language model never decides which rule is current.** Dates are facts, so ordinary code works out each clause's status. The model receives clauses already labelled and only writes the explanation.

| Status | Meaning |
|---|---|
| current | In force on the question date |
| superseded | A replacement is in effect right now |
| expired | A temporary rule whose period has ended |
| upcoming | Issued, but not yet in effect |

A clause counts as superseded only while a successor is in effect. This one rule handles three cases without special code: replacement chains, notices that are announced in advance, and temporary notices that lapse.

## 3. Pipeline

```
Question + "as of" date
   |
   v
Retrieval: BM25 per clause, grouped into rule histories
   |
   v
Status: computed in code for every clause on that date
   |
   v
Answer: the model writes from the labelled clauses, citing clause ids
        (offline mode quotes the clauses instead)
   |
   v
Citation check: code verifies every cited id
   |
   v
UI: ids become dated chips, replaced rules struck through
```

## 4. Design decisions

**Rule histories as the retrieval unit.** An old clause and its replacement often match a question equally well. Clauses linked by `supersedes` form one group, and finding any version returns all of them, so the replacing notice cannot be lost to a better-worded old clause.

**Topping up Claude's context.** Held-out testing showed keyword search misses paraphrases ("my mother" for "parents"). A clause that never reaches Claude cannot be recovered, so the Claude path adds the remaining rules, best first, up to 60 clauses. For this 40-clause sample that means all of them, at about 3,300 tokens. A real corpus above the cap would depend on ranking again.

**Clause ids as citations.** Claude writes `[HN-2026-19.1]` and the interface turns that into a chip such as "Notice, 20 Aug 2026". The date comes from the document metadata, so the model cannot misquote it. The Claude Citations API is an alternative that would also verify quoted text, at the cost of more complex stream handling.

**Citation checks after every answer.** Code flags ids that were not retrieved, answers with no citation, and answers that cite an outdated clause without the rule in force. These appear as visible warnings.

**"As of" date.** The date is an input, not a hidden clock. Notices issued after the chosen date are hidden entirely, which makes the behaviour testable and lets a student check what applied on a past date.

**Offline mode.** If no key is set, or the API fails before any text is sent, the app quotes clauses directly and says so on screen. It declines when the match is weak, because a wrong rule is worse than no answer.

## 5. Model settings

The app supports four providers behind one prompt: Gemini, Groq and Ollama have free options and use their OpenAI-compatible endpoints, and Claude uses the Anthropic SDK. Because status and citation checks are in code, a smaller free model can write the answer without being trusted to choose the current rule. The settings below apply to the Claude path.

| Setting | Value | Reason |
|---|---|---|
| Model | `claude-opus-5-5` | Current default; change with `CLAUDE_MODEL` |
| Thinking | adaptive | Lets the model reason over dates when needed |
| Effort | medium | Short question answering |
| Streaming | on | Text appears as it is written |
| Refusal fallback | `fallbacks: "default"` | A declined request is retried on another model by the API |

## 6. Evaluation

All results use the date 6 Nov 2026. Each set was run once per mode, except that the tuning set was rerun on Gemini after one check was corrected (see below). The model was `gemini-flash-lite-latest` on the free tier, and all 42 answers came from Gemini with none falling back to offline.

The tuning set shaped the stopwords and synonyms, so its offline score is optimistic. The held-out set was written after tuning and was not used to tune wording. It did prompt the context top-up in section 4.

| Measure | Offline, tuning | Offline, held-out | Gemini, tuning | Gemini, held-out |
|---|---|---|---|---|
| Rule in force cited | 22/23 | 5/11 | 23/23 | 11/11 |
| Expected facts in answer | 22/23 | 5/11 | 22/23 | 11/11 |
| No citation warnings | 23/23 | 11/11 | 23/23 | 11/11 |
| Out-of-scope questions declined | 5/5 | 3/3 | 5/5 | 3/3 |

**Main finding.** Keyword matching tuned on known questions did not carry over to new wording (offline fell from 22/23 to 5/11), while the language model handled new wording fully on this set. Offline's six held-out misses were all declines, not wrong rules.

**Scoring notes.**
- "Expected facts" is a text match. The one Gemini miss (Route 4) was a correct answer that said "on Saturdays" rather than the expected "Saturdays only".
- The first Gemini run on the tuning set scored 22/23 cited and 20/23 facts. One check was too strict ("will not stop" for a correct "does not stop") and was changed to "Town Hall" before the rerun. The other two misses were not reproduced, so they are run-to-run variation, described next.

**Run-to-run variation.** In the first run, one answer stopped after two words ("On weekdays") and had no citation, and the citation check flagged it. Repeating that question four times gave complete answers. In another run, an answer about the late pass may have left out the upcoming 11:30 PM change. These small sets cannot show how often this happens, so the scores above should not be read as a guarantee.

**Other findings.** One repeat of the curfew question also added unrequested late pass information. `gemini-flash-latest` timed out on this key, even on a tiny request, while the flash-lite models answered in under 2 seconds.

Claude has not been evaluated, because no Anthropic key was available. That path was tested only against a local fake server.

There are 31 automated tests covering status logic, retrieval, citation checks, the API and the model request paths. The interface was checked by hand in a browser in dark mode, in light mode and at phone width.

## 7. Limitations and next steps

- All documents are invented. Real manuals will have messier wording, and `supersedes` links must be entered by whoever publishes a notice.
- A partial change is modelled by restating the whole clause. A notice that amends one sentence needs the same treatment.
- Answers from the free model occasionally vary or stop early. The citation check flags uncited answers, but it cannot tell whether a cited answer left something out.
- Follow-up questions are detected with a simple pattern, not by understanding the conversation.
- Embedding search would address the paraphrase failures for larger collections.
- Answers are guidance, not an official ruling. A production version should link to the original signed notice.
