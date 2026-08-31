# Open items

State as of 2026-08-31. Read alongside `CLAUDE.md`. Delete items as they are done.

## Resolved on 2026-08-31 — finding the clinical pharmacologist, and one-file handoff

The objective is now stated as **reaching the company clinical pharmacologist who worked on
the drug**; the ASCPT meeting is one channel to that person, not the point. Output renamed
`MOA author outreach list <year>.xlsx` (the old dossier filename is still read, so existing
annotations migrate), and contacts moved ahead of the ASCPT columns.

- `authors.py` (new) — three-tier PubMed search. Tier 1 clin pharm studies (food effect,
  DDI, rBA, organ impairment, mass balance/ADME, thorough QT, healthy volunteers), tier 2
  pharmacometrics, tier 3 dose escalation. Author position is read differently per tier
  because on a dose-escalation paper first and last are the treating clinicians.
- Affiliations come from `efetch` XML; `esummary` carries none.
- Handles acquisitions (`programme_org`) — tebipenem is GSK on paper but Spero in the
  literature; baxdrostat is AstraZeneca but CinCor. Handles people moving employer.
- **Live result: 24 of 27 candidates get a named clinical pharmacologist**, 20 at the
  sponsor or the company that ran the programme.
- `update --refresh` backfills contacts onto rows already in the queue.
- ASCPT member directory is an optional upload, plus a `membership check list.xlsx` the
  tool writes so only specific names need verifying. **The directory is login-gated and is
  never scraped.**
- `build_single_file.py` → `dist/CTS MOA Engine.command` and `.bat`, ~92 KB each,
  sh/Python and batch/Python polyglots. Modules load from memory; a working folder is
  created beside the file on first run. Mac path smoke-tested from a clean directory;
  **Windows still untested on real hardware.**

**Still open:** whether ASCPT permits the member directory being used this way — ask rather
than assume. `members@ascpt.org` is the contact.

## Resolved on 2026-08-21 — the move off Google Drive

The tool used to write to Google Drive through `gapi.py` and needed a `gcloud` login. It now
writes local `.xlsx` and `.html` files and has no Google dependency at all. That closed the
three defects this file used to list:

1. **Dossier re-runs wiping human annotations** — fixed by `sheets.merge_annotations()`, which
   reads `AE owner` / `Attending?` / `Comments` back and re-attaches them keyed on drug INN.
   Verified against a workbook re-encoded the way Excel saves one.
2. **Orphan rows after a shorter re-run** — gone by construction: `store.xlsx_write()` rewrites
   the whole workbook, so there is no previous content to leave behind.
3. **Invitation drafts duplicating every run** — `invites` is now its own command and writes to
   one fixed path, overwritten in place. The two stale copies of `MOA invitation drafts 2027`
   in Drive were moved to Trash.

## Resolved on 2026-08-21 — attendee rosters and the meeting year

The old "pending decision" on attendee rosters is settled. `sheets.load_roster()` reads one and
adapts to whatever columns it has; `./moa-engine roster --file X.xlsx --go` (and the GUI's
sixth button) imports it. It is **optional** — with no roster the tool derives a proxy from
that year's presenting authors.

This also fixed a real defect found while investigating: `config.program_file()` was a fixed
path with **no year**, so `dossier --year 2027` matched against the 2026 programme and printed
poster slots dated 3/5/2026 as somewhere to walk to. Meeting files are now year-keyed and the
upcoming meeting is reported separately from history.

**Still worth asking:** whether ASCPT places any restriction on how an attendee list may be
used, since it is personal data feeding an outreach-drafting tool. It stays on the user's own
machine and nothing is transmitted, but the question should be asked rather than assumed.

**Still true:** a real attendee list beats the derived one comfortably. The derived roster only
sees people who submitted an abstract — 5 of 25 candidate sponsors against the 2026 export.
Most sponsor clin pharm leads attend without presenting.

## Data gap, not a code defect

`input/contacts.xlsx` has 44 companies and almost no names, so most candidates come through as
`NEEDS LOOKUP` — 12 of the top 15 on the current dossier. The tool deliberately refuses to guess
below `config.CONTACT_MATCH_THRESHOLD`. Filling that sheet in is the single highest-leverage
manual task and would make the invitation drafts genuinely sendable.

## Worth knowing about the .xlsx layer

`store.py` is hand-rolled on `zipfile` + `xml.etree` because there is no `openpyxl` and adding a
dependency would break the zero-install handoff. It is well covered by `selftest.py`, including
the sharedStrings re-encoding Excel applies on save, but it is still the newest and least
battle-tested code here.

If a workbook a human edited ever fails to read, the fallback is to write a `.csv` sidecar and
treat that as the authoritative read-back. Not needed so far.

Users must save edits as **`.xlsx`**. Saving as `.csv` or `.xls` loses the tab structure the tool
looks for.

## Where the dials are, for tuning

| Want to change | Edit |
|---|---|
| How candidates are scored / ranked | `classify.novelty_score()` — every term is one line |
| What modality a drug is tagged as | `config.MODALITY_STEMS`, `config.MODALITY_KEYWORDS` |
| Which coverage gaps earn a bonus | `config.GAP_CATEGORIES` |
| Which FDA classes are in scope at all | `config.CANDIDATE_CLASSES` |
| How eagerly contacts are matched | `config.CONTACT_MATCH_THRESHOLD` (0.82) |
| How old an approval can be and still count as "new" | `is_novel_agent(grace_days=400)` |
| Dossier size / recency | `dossier --window` (550 days) |
| How many letters get drafted | `invites --invites` (15) |
| Where files are written | `settings.json` `output_dir`, or `--output-dir` for one run |
| How strictly sponsors match orgs | `sheets._prefix_match()` — length floor and the no-space rule |
| Which meeting files are found | `config.meeting_files()` glob, `config.PROGRAM_PATTERN` |
| What the GUI buttons do | `gui.ACTIONS` (an allowlist — the page cannot name a command) |
| How people are found and ranked | `authors.py` — `TIERS`, then `rank()` |
| Which study types count as clin pharm | `authors.CLINPHARM_STUDY` |
| What lands in the single-file build | `build_single_file.py` — `MODULES`, `DATA` |

**After any change to `sources.py` or `config.py`, run `python3 backtest.py`** — it must stay at
18/19. **After any change to `store.py`, `sheets.py` or `authors.py`, run
`python3 selftest.py`** — it pins the pivekimab case (three AbbVie authors at positions
20–22, and neither MD Anderson clinician), the Spero acquisition, and the Vera/Verastem
false positive. **After changing any module, re-run `python3 build_single_file.py`** or the
distributables go stale.

## Verified facts worth not re-deriving

- Recall 18/19; esketamine only passes because `TYPE 2` was added to `CANDIDATE_CLASSES`. The
  single expected miss is molnupiravir (EUA-only, never in Drugs@FDA).
- 586 novel agents on file since 2015.
- Migrated out of Drive on 2026-08-21: 25 queue candidates, 44 contact rows, 297 posters and
  36 sessions from *CTS coverage AM 2026*.
- *CTS coverage AM 2026* is owned by a colleague outside this account, not by us — `input/ascpt
  program 2026.xlsx` is a snapshot, and each future meeting needs a fresh export from them.
  (Their address is in the sheet's Drive sharing panel; deliberately not recorded here.)
- Homebrew Python 3.14.6 on this machine has **no tkinter**, which is why the GUI is a browser
  app. `/usr/bin/python3` 3.9.6 has it but with Tk 8.5.9, which renders badly.
- `xlsxwriter` happens to be installed here but is deliberately unused — a recipient won't have
  it.
- The 2026 programme yields 257 named presenters across 165 normalised organisations, matching
  5 of the 25 queued candidates by sponsor.
- `VERA THERAPEUTICS` normalises to `vera`, which is a prefix of `verastem oncology`. This was
  a live false positive in both attendance and, worse, `match_contact`. Fixed in
  `_prefix_match`; `selftest.py` pins it. Watch for it recurring if that helper is touched.
- The Drive team folder still holds two Slides decks that are **not** engine output and were
  deliberately kept: `CTS MOA Mini-Reviews — Small Team Brainstorm (DRAFT)` and
  `How the MOA sourcing engine works`.
