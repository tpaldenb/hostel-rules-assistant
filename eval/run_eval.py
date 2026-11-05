"""Scores the assistant on an eval file, with the date fixed at 2026-11-06.

  python eval/run_eval.py                 offline extractive mode, free
  python eval/run_eval.py --mode claude   real API calls (costs money, needs a key)
  python eval/run_eval.py --mode gemini --delay 6   free tier, paced to stay under rate limits
  python eval/run_eval.py --file heldout.json   questions not used for tuning
"""
import argparse
import json
import sys
import time
from collections import Counter
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import os  # noqa: E402

parser = argparse.ArgumentParser()
parser.add_argument("--mode", choices=["offline", "claude", "gemini", "groq", "ollama"], default="offline")
parser.add_argument("--delay", type=float, default=0, help="seconds to wait between questions")
parser.add_argument("--file", default="questions.json", help="questions.json (used while tuning) or heldout.json")
args = parser.parse_args()
os.environ["ASSISTANT_MODE"] = args.mode

from app import config  # noqa: E402
from app.answer import Assistant, is_decline  # noqa: E402
from app.corpus import Corpus  # noqa: E402

AS_OF = date(2026, 11, 6)
assistant = Assistant(Corpus.load(config.DOCUMENTS_DIR))
cases = json.loads((ROOT / "eval" / args.file).read_text())

rows, failures, wide_hits, wide_total, wide_sizes = [], [], 0, 0, []
answered_by = Counter()
for number, case in enumerate(cases):
    if number and args.delay:
        time.sleep(args.delay)
    if not case.get("abstain"):
        wide = assistant.retriever.search(case["q"], AS_OF, wide=True).clause_ids
        wide_hits += all(c in wide for c in case["current"] + case.get("upcoming", []))
        wide_total += 1
        wide_sizes.append(len(wide))
    text, cited, sources, warnings = "", [], [], []
    for event in assistant.answer(case["q"], [], AS_OF):
        if event["type"] == "meta":
            sources = [s["id"] for s in event["sources"]]
        elif event["type"] == "delta":
            text += event["text"]
        elif event["type"] == "done":
            cited, warnings = event["cited"], event["warnings"]
            answered_by[event["mode"]] += 1
        elif event["type"] == "error":
            warnings.append(event["message"])

    if case.get("abstain"):
        result = {"abstain_ok": is_decline(text) and not cited}
    else:
        wanted = case["current"] + case.get("upcoming", [])
        result = {
            "retrieved": all(c in sources for c in wanted),
            "cited_current": all(c in cited for c in case["current"]),
            "facts": all(m.lower() in text.lower() for m in case["must"]),
            "no_warnings": not warnings,
        }
    rows.append(result)
    if not all(result.values()):
        failures.append((case["q"], result, text[:160]))


def rate(key):
    values = [r[key] for r in rows if key in r]
    return sum(values), len(values)


print(f"mode={args.mode} model={config.model_for(args.mode)} as_of={AS_OF} cases={len(cases)}")
print(f"  Answered by: {dict(answered_by)}")
if args.mode != "offline" and answered_by["offline"]:
    print(f"  WARNING: {answered_by['offline']} answers fell back to offline, so this is not a clean {args.mode} score. Raise --delay.")
for key, label in [
    ("retrieved", "Right clauses retrieved"),
    ("cited_current", "Rule in force cited"),
    ("facts", "Expected facts in answer"),
    ("no_warnings", "No citation warnings"),
    ("abstain_ok", "Declined out-of-scope"),
]:
    hit, total = rate(key)
    print(f"  {label:<26}{hit}/{total}")
print(f"  {'Model context recall':<26}{wide_hits}/{wide_total}  (clauses a model would receive, avg {sum(wide_sizes) / len(wide_sizes):.1f} clauses)")
for question, result, text in failures:
    print(f"\nFAIL: {question}\n  {result}\n  {text!r}")
sys.exit(1 if failures else 0)
