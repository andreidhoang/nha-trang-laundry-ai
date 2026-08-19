# TASK-assistant-brain-002 — the assistant answers money questions with an order count

**Goal:** correct two intent-matching defects in `DeterministicAssistantBrain` that make the
assistant answer a different question than the one asked, and make one of its own suggestion chips
resolve to the wrong intent.

**Domains:** `agent_tools`, `platform`

**Stable work item:** `ASSISTANT-BRAIN-002`

**Stage:** PRODUCTION_HARDENING
**Risk:** MEDIUM. Nothing crashes; the assistant is confidently wrong in Vietnamese, on the screen
whose stated promise is that it says when it does not know.

## Why this exists

Corrective item against `ASSISTANT-001`, which is `COMPLETE` and therefore immutable planning history
under `context/CONTINUATION_PROTOCOL.md`. Both defects were reproduced by a concurrent session
against the real stack rather than stubs.

**A1 — a money question is answered with an order count and never refused.**

```
"Doanh thu hôm nay bao nhiêu?"      -> TODAY_OVERVIEW
"Hôm nay thu được bao nhiêu tiền?"  -> TODAY_OVERVIEW
"Tình hình doanh thu thế nào?"      -> TODAY_OVERVIEW
```

`DeterministicAssistantBrain.answer` is first-match-wins and tests `hom nay` / `tinh hinh` before
`doanh thu` / `tien`. Any money question that names a timeframe — which is how an owner asks one —
matches the earlier rule. `assistant.js` promises operators *"điều gì nó không biết, nó nói là không
biết"*. Here it neither computes money nor refuses; it answers a different question, which is the one
behaviour the screen's own disclosure rules out.

**A2 — diacritic-stripped bare cues match inside ordinary words.**

`_normalize` strips diacritics, so `lo` and `lai` match within unrelated words:

```
"Bạn trả lời được gì?"             -> REVENUE_UNAVAILABLE   ("lo" inside "loi")
"Đơn này giặt lại được không?"     -> REVENUE_UNAVAILABLE   ("lai": lại ≠ lãi)
"Máy giặt bị lỗi thì ghi vào đâu?" -> REVENUE_UNAVAILABLE
"Làm sao lọc đơn theo trạng thái?" -> REVENUE_UNAVAILABLE
```

The first is **one of the four `SUGGESTIONS` chips `assistant.js` ships**, and `assistant.js:52`
documents the chips as each mapping to a documented intent. It maps to the wrong one, on the
empty-transcript screen every new operator sees first. That comment is itself a disclosure-class
claim about the system.

## Required design

- Move the revenue check ahead of the topic checks. **A refusal outranks a partial topical match** —
  state that as a precedence rule in the module docstring, because the next person adding an intent
  needs it, and the defect is what happens when it is left implicit.
- Replace bare `tien` / `lai` / `lo` with unambiguous multi-word cues.
- A phrasing that loses every cue falls through to `UNSUPPORTED`, whose own text already delivers the
  money refusal — so the fallback is correct rather than merely safe.

## Constraints

- The brain stays deterministic. No model, no scoring, no fuzzy match: the intent table is
  first-match-wins and total, and both properties are pinned by existing unit tests.
- **Matching never rewrites the question.** The stored question stays the verbatim text typed.
- No disclosure string in `assistant.js` changes. The fix makes the two registered disclosures in
  that file *more* true, not less.

## Required tests

- each phrasing above resolves to the intent a Vietnamese speaker would expect, as regression cases;
- a money question naming a timeframe refuses rather than reporting a count;
- every `SUGGESTIONS` chip resolves to the intent `assistant.js:52` documents for it;
- the first-match-wins and totality properties still hold.

## Done when

- all eight reproductions above resolve correctly;
- the precedence rule is documented where the next intent will be added;
- the full gate battery passes with no required skips.

## Ownership

A concurrent session reproduced both defects and holds the fix in flight, along with
`apps/api/src/nha_trang_laundry_api/assistant.py` and `apps/api/tests/test_assistant_brain.py`. This
packet exists so that work lands under an item with an evidence record rather than as an unowned
change to a `COMPLETE` item. It is deliberately not being implemented here.
