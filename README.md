# Rules Desk: hostel and transport rules assistant

Answers student questions about hostel timings, leave procedures and bus routes from the hostel manual and transport notices. Every rule in an answer carries its notice date, and rules that have been replaced are visibly marked.

The institute and all documents in `data/documents/` are made-up samples.

## Run it

1. Open `.env` and paste your free Gemini key after `GEMINI_API_KEY=`. If `.env` is missing, `./run.sh` creates it.
2. From this folder, start the app:

```bash
./run.sh
```

3. Open http://localhost:8123. The badge at the top right shows "Gemini" when the key is loaded.

The first run sets up Python packages. Without a key, the app still runs in offline mode, which quotes clauses directly instead of writing answers.

## Choose a language model

| Provider | Cost | Setup |
|---|---|---|
| Gemini | Free tier | Get a key at https://aistudio.google.com/apikey and set `GEMINI_API_KEY` |
| Groq | Free tier | Get a key at https://console.groq.com/keys and set `GROQ_API_KEY` |
| Ollama | Free, runs on your computer | Install from https://ollama.com, run `ollama pull llama3.1:8b`, set `ASSISTANT_MODE=ollama` |
| Claude | Paid | Set `ANTHROPIC_API_KEY` |

Free tiers have rate limits and their model lists change. If a model name stops working, set `LLM_MODEL` in `.env` to one your account lists. When a limit is hit, the app shows a note and quotes the clauses instead. Free tiers may also have different data terms, so read them before sending real student questions.

## Try these

| Question | What it shows |
|---|---|
| What time does the hostel gate close on weekdays? | A rule replaced twice: 9:30, then 9:00, now 9:15 |
| Does Route 1 stop at Market Square? | A temporary notice currently in force |
| What are the mess timings? | A temporary notice that ended, so the manual rule is back |
| How much is the semester bus pass? | A fee change that has been announced but has not started |
| Can I keep a pet in my room? | A question the documents do not cover |

Change **Rules as of** to 1 Feb 2026 and ask about the gate again: the answer becomes 9:00 PM, and later notices are hidden.

## Use your own documents

Add one Markdown file per notice to `data/documents/` and restart. The app refuses to start if a file has bad dates or points to a clause that does not exist.

```markdown
---
id: HN-2027-01
title: Hostel Notice 01/2027 - Revised gate time
type: notice            # manual or notice
category: hostel        # hostel or transport
issued: 2027-01-05
effective_from: 2027-01-10
effective_until:        # optional; fill in for temporary notices
issued_by: Chief Warden
---

## HN-2027-01.1 | Weekday gate closing time
The main gate closes at 9:30 PM on weekdays.
supersedes: HN-2026-19.1
```

## Test it

```bash
.venv/bin/python -m pytest -q
```

```bash
.venv/bin/python eval/run_eval.py --file heldout.json
```

To score a language model (real API calls, about 3k input tokens per question). `--delay` paces requests to stay under free rate limits:

```bash
.venv/bin/python eval/run_eval.py --file heldout.json --mode gemini --delay 6
```

## Layout

| Path | Purpose |
|---|---|
| `app/corpus.py` | Parses documents and works out each clause's status on a date |
| `app/retrieval.py` | BM25 search that returns whole rule histories |
| `app/prompts.py` | System prompt and clause formatting |
| `app/answer.py` | Model streaming (Claude and free providers), offline answers, citation checks |
| `app/main.py` | FastAPI endpoints |
| `static/` | Chat UI, plain HTML, CSS and JavaScript |
| `eval/` | Tuning and held-out question sets, scoring script |
| `docs/REPORT.md` | Assignment write-up |
