# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this is

A single-purpose tool for the *Clinical and Translational Science* (CTS) editorial team: it
watches FDA approvals, keeps only **genuinely novel agents**, ranks them as candidates for the
journal's Mechanism of Action mini-review series, and once a year builds a recruiting dossier
timed to the ASCPT Annual Meeting.

Python 3, **standard library only** — no `pandas`, no `openpyxl`, no pip installs. Output is
local `.xlsx` and `.html` in `output/`. There is no Google dependency, no account and no
credential: the tool is meant to be copied to another editor's laptop and double-clicked.

`README.md` is the user-facing guide; `START HERE.md` is the non-technical one. This file
covers what you need to change the code safely. **Read `OPEN ITEMS.md` too.**

## Commands

```bash
python3 moa_engine.py check                      # verify files + FDA + PubMed. Writes nothing.
python3 moa_engine.py scan --days 120            # preview ranked candidates. Writes nothing.
python3 moa_engine.py update --go                # append new candidates to the queue workbook
python3 moa_engine.py dossier --year 2027 --go   # build the dossier workbook
python3 moa_engine.py roster  --file X.xlsx --go # import a programme or attendee list (optional)
python3 moa_engine.py invites --year 2027 --go   # draft letters FROM the dossier

python3 backtest.py                     # the filter's test suite — must stay 18/19
python3 selftest.py                     # the file layer's test suite — offline, <1s
python3 gui.py                          # the browser app, against the live source
python3 build_single_file.py            # rebuild the two files in SEND THIS/
```

`--go` is required for any write; everything is dry-run otherwise. `--no-pubmed` skips
enrichment and makes `scan`/`update` roughly 10x faster for iterating on filter logic.
`--output-dir /tmp/moa-test` redirects every write for one run — use it rather than polluting
`output/` while testing.

### Two test suites, two triggers

- **`backtest.py`** re-scans every FDA approval since 2015 and asks whether the 19
  already-published MOA drugs survive the filter. Currently **18/19**; the expected miss is
  molnupiravir (EUA-only, never in Drugs@FDA). Run after any change to `sources.py` or
  `config.py`. A filter that rejects drugs the series published is not ready. Takes minutes.
- **`selftest.py`** covers the `.xlsx` reader/writer and the annotation merge. Offline,
  tempdir-only, under a second. Run after any change to `store.py` or `sheets.py`.

## The trap that will bite you first

`sources._MOIETY_INDEX`, `_ROUTE_INDEX` and `_BRAND_INDEX` are module-level globals populated as a
**side effect** of `drugs_at_fda_nmes()` and `purple_book_new_cber()`. `is_novel_agent()` reads
them and **silently returns a wrong, plausible-looking answer if called before a loader has run**:

```python
sources.is_novel_agent(rec)          # -> (True, 'first approval of this moiety')   WRONG
sources.drugs_at_fda_nmes(...)       # populates the indexes
sources.is_novel_agent(rec)          # -> (True, 'new brand ... moiety previously approved 2004')
```

Always go through `sources.collect()`, which loads both feeds and then filters. If you add a code
path that calls `is_novel_agent()` directly, load the sources first.

## Architecture

`sources` (collect + novelty gate) → `classify` (modality, gap flag, score) → `enrich` (PubMed) →
`sheets` (queue, dossier, program match, invite drafts) → `store` (.xlsx read/write).
`config` holds every tunable and resolves every path; `gui` is a local web front-end that shells
out to `moa_engine.py`; `scheduler` owns the optional launchd job.

**Records change shape mid-pipeline.** A freshly scanned candidate is a dict with lowercase keys
(`ingredient`, `score`, `sponsor_raw`). A candidate re-read from the queue workbook is a dict keyed
by the sheet's *column headings* (`Drug (INN)`, `Novelty`, `Sponsor`). `dossier` consumes the second
shape. Helpers that must accept both — `sheets._score_of()`, `sheets.match_program()` — check both
key spellings. This already caused one silent bug: sorting on `"score"` alone made every queue row
sort as zero and produced an unranked dossier.

**Two FDA sources, and both are needed.** Drugs@FDA bulk files cover NDAs and CDER biologics
(761xxx); the Purple Book monthly report covers CBER products, which Drugs@FDA omits entirely —
verified: BLA 125730 (StrataGraft) is absent from it, and only 73 of the classic 125xxx
applications appear at all. CBER products are exactly the cell/gene/vaccine coverage gaps the
series cares about, so dropping the Purple Book feed would quietly remove a whole category.

**Do not switch to the openFDA API.** `api.fda.gov/drug/drugsfda.json` matches conditions across
the whole application document rather than within one submission, so a date-range + class query
returns approvals from unrelated years — a "2026 + TYPE 1" search returns 2008/2014/2016/2019/2020
records and claims 279 results. The bulk relational files are used precisely so the join can be
done correctly per submission.

## The .xlsx layer

`store.py` hand-rolls both halves on `zipfile` + `xml.etree`. The asymmetry is the thing to know:

**We write inline strings (`t="inlineStr"`); Excel and Numbers save back sharedStrings
(`t="s"`).** So the reader must handle `t="s"`, `t="inlineStr"`, `t="str"` and `t="b"`. Reading
only the format we write would work perfectly until the first time a human edited a file — which
is exactly when it matters. `selftest.py` builds a sharedStrings fixture specifically to pin this.

Other things `store.py` defends against, each because it would otherwise corrupt a workbook or
lose a column silently: control characters illegal in XML 1.0 (FDA sponsor strings are dirty),
ragged rows (pad to the widest, or `dict(zip(header, row))` drops a blank trailing `Comments`),
and trailing styled-but-empty rows Excel leaves behind. Writes go to a `.tmp` and `os.replace`, so
an interrupted run cannot leave half a workbook.

**Rewriting the whole file each time is deliberate** — it is what makes a shorter re-run
incapable of leaving orphan rows.

## Domain rules encoded in the filter

These are editorial decisions, not implementation details. Changing them changes what the journal
gets invited to write about.

- `config.NME_CLASSES` is `{"TYPE 1", "TYPE 1/4"}`. The lookup table spells the combined code
  **`TYPE 1/4`**, not `TYPE 1/TYPE 4`; getting this wrong silently drops applications.
- `config.NEW_ACTIVE_INGREDIENT_CLASSES` (Type 2) is included but scored lower. Type 2 is an
  enantiomer/salt/ester of a known moiety. It is in scope **because esketamine is Type 2 and the
  series published it** — the backtest scored 17/19 until this tier was added.
- A known moiety is kept if it arrives by a **new route**, especially under a new brand
  (intrathecal Zolgensma / ITVISMA, ophthalmic bevacizumab / LYTENAVA). Where route data is
  unreadable the fallback is new-brand + FDA NME classification.
- Excluded: ANDAs, 351(k) biosimilars, Type 3/4/5 reformulations, repeat applications by the same
  route (reformulated COVID-19 vaccines), and plasma-derived products.

Scoring lives entirely in `classify.novelty_score()` and every term is written onto the row as
plain text. If the editorial group disagrees with a weight, that is a one-line change there — the
intended tuning point.

## Source-format landmines

**Purple Book CSV** — a 3-line preamble so the header is row index 3; a BOM (read `utf-8-sig`);
repeated header rows *inside* the data; two date formats (`25-Mar-25` **and** `April 28, 2025`);
and a literal `Invalid date` sentinel. The two-format issue is not cosmetic: unparsed dates sort
as strings *after* any ISO date, so date filters silently stop excluding anything. Discover the
monthly URLs by scraping the downloads page — never construct them, and expect publishing lag.

**Drugs@FDA `Products.txt` has no route column.** Route is the tail of `Form` (`VIAL; INTRAVENOUS`)
but sometimes holds a presentation instead (`VIAL;SINGLE-DOSE`), so `_route_from_form()` validates
against `config.ROUTE_VOCAB` and returns `""` rather than inventing a route. A bogus route reads as
a *new* route and would wrongly promote a repeat application.

**INN stems follow the 2021 WHO revision** — `-tug`, `-bart`, `-mig`, `-ment` replaced `-mab` for
new antibodies. Without those stems current approvals (e.g. veligrotug) get filed as small
molecules.

## The attendance model

Three distinct signals, and keeping them distinct is the whole point of the design:

| Signal | Source | Dossier column |
|---|---|---|
| presenting at the upcoming meeting | `input/ascpt program <year>.xlsx` | `ASCPT presence`, `Poster / session detail` |
| registered for the upcoming meeting | `input/ascpt attendees <year>.xlsx` (optional) | `ASCPT presence` |
| attended a previous meeting | any earlier year's file | `Last year at ASCPT`, `Who to find` |

`config.program_file()` used to be a **fixed path with no year**, so `--year 2027` changed only
the output filenames and the dossier was matched against the 2026 programme -- shipping poster
slots dated 3/5/2026 as places to walk to. Files are now discovered by
`config.meeting_files()`, which globs `ascpt (program|attendees) <yyyy>.xlsx`. Never reintroduce
a default that ignores the year.

`config.meeting_year()` is the **single source of truth** for which meeting is next: March
meeting, so June onwards targets next calendar year. `moa_engine._meeting_year()` and the GUI's
`/where` both delegate to it — they each had their own copy briefly, which would have let the
page label a button "ASCPT 2027" while the dossier built 2028. `selftest.test_year_rollover`
pins the boundaries and asserts the two agree. Between the March meeting and 1 June it still
returns the year just gone, which is deliberate: you are still following up from it, and
`--year` overrides.

`sheets.build_attendance(year)` assembles the index: an explicit attendee roster beats
`roster_from_program()` for the same year, and every earlier year becomes history. The derived
roster only sees people who submitted an abstract -- 5 of 25 candidate sponsors against the 2026
export -- so a real attendee list is a strict upgrade and needs no code change.

**Attendance is reported, never scored.** `classify.novelty_score()` is untouched by it, so
ranking stays editorial and a drug cannot outrank a better one because its sponsor travels.
`selftest.test_attendance_columns` asserts exactly that.

### `_prefix_match` — why it is fussy

Org matching is exact-on-normalised-key plus a prefix rule, because FDA sponsor strings are
truncated mid-word (`HAISCO PHARMACEUTICAL GROU`). A bare `startswith` is far too generous:
`VERA THERAPEUTICS` normalises to `vera`, which is a prefix of `verastem oncology`. That
mis-fire put three Verastem staff under "who to find" for atacicept **and** -- in the
pre-existing `match_contact`, which had the same bug -- would have addressed Vera's invitation
to `someone@verastem.com`, breaking the tool's one hard promise.

The discriminator is whole words, not length: a truncation drops the tail of one word, a false
positive swallows entire extra words. So the differing part may contain no space, and there is a
five-character floor to kill `bio`/`biogen`. Both call sites share the helper; do not
reintroduce a raw `startswith`.

## Output contract

The queue is **append-only and keyed** by `appl_no:ingredient_stem`, so re-running is always safe
and a missed month self-heals — each run re-scans a trailing window and de-duplicates rather than
tracking a "last run" timestamp. The **`AE owner` and `Status` columns belong to humans and must
never be overwritten**; `Status` values `Published`/`Declined`/`Dropped` retire a row from future
dossiers.

The dossier is rewritten wholesale each run, but `sheets.merge_annotations()` first reads back
`AE owner` / `Attending?` / `Comments` and re-attaches them **keyed on drug INN, not row
position** — between runs the ranking changes and rows come and go, so a positional merge would
attach one drug's meeting notes to another. Machine columns are always taken from the fresh run.
Only columns present in *both* the old and new headers are merged, so an older dossier written
before a column was added cannot shift values sideways.

`invites` is a **separate, explicitly triggered step** that reads the dossier back in whatever
order the human left it. It is not part of `dossier`, because the point is to draft from a
reviewed list rather than a raw ranking.

Two hard rules: contact matching below `config.CONTACT_MATCH_THRESHOLD` (0.82) reports
`NEEDS LOOKUP` rather than guessing — a wrong recipient on journal-branded outreach is worse than a
blank — and **nothing is ever emailed**. Invitations are written to a local HTML file for a human
to review and send.

## The GUI

`gui.py` is a stdlib `http.server` bound to `127.0.0.1` on an ephemeral port, opened in the
default browser. Three things about it are load-bearing:

- **The page cannot name a command.** `gui.ACTIONS` is an allowlist; the browser sends a key, and
  the server builds the argv. Never let a request supply arguments directly.
- **Every request carries a token** minted at startup and compared with `secrets.compare_digest`,
  so another page open in the same browser cannot drive it.
- **The output folder is resolved in the parent** and passed down as `--output-dir`, so the folder
  the page displays and the folder the job writes to cannot disagree.
- **`roster` is the one action taking a payload.** The page sends base64 bytes, never a path;
  `_stash_upload()` validates the size and the `PK` zip magic, writes to `tempfile.mkstemp`, and
  the server supplies that path. A browser-supplied filename would be a path-traversal
  invitation, so it is discarded.

**The server must not outlive its window.** The `.app` starts it detached, so before the
heartbeat existed the only way to stop it was to force-quit Python. The page pings `/alive`
every 6s; `watchdog()` shuts the server down after `HEARTBEAT_TIMEOUT` (25s) of silence, or
`HEARTBEAT_GRACE` (90s) if no browser ever connected. A `pagehide` beacon covers the tidy case,
but beacons get dropped on crash or sleep, so the heartbeat is the guarantee. A running job
suppresses the watchdog -- closing the tab mid-scan should not discard the scan.

One job runs at a time; its stdout is pumped into a line buffer the page polls. The `.app` is an
`osacompile`d AppleScript that runs `python3 -u gui.py` with `nohup` — the `-u` matters, because
without it nothing reaches `gui.log` and a user whose browser failed to open has no way to find
the URL.

## Platform support

The engine is platform-independent stdlib. Only three things branch:

- `gui.reveal()` — `os.startfile` / `open` / `xdg-open`
- `gui._NO_WINDOW` — `creationflags=CREATE_NO_WINDOW` on Windows, so pressing a button under
  `pythonw` does not flash a console
- `scheduler` — launchd on macOS, `schtasks` on Windows, a printed `crontab -e` line on Linux

There are exactly two launchers, both generated: `SEND THIS/CTS MOA Engine.command` (macOS)
and `SEND THIS/CTS MOA Engine.bat` (Windows). Each is the whole program in one file and is
both the GUI (no arguments) and the CLI (with arguments). Rebuild them with
`python3 build_single_file.py` after ANY source change, or they ship stale.

For development, run the live source directly — `python3 moa_engine.py <cmd>` or
`python3 gui.py`. The old `.app` and `Start MOA engine.*` wrappers were removed because
seven launchers in one folder made it unclear which single file to hand over.

**The Windows paths have never run on Windows.** They were written from documented behaviour and
exercised only by reloading `scheduler`/`gui` with `sys.platform` patched to `win32` — which
confirms dispatch and `schtasks` argv but not that Windows accepts them. If you touch them, say
plainly that they remain unverified rather than implying otherwise.

## Configuration and portability

`config.py` resolves every path and works with **no settings file at all** — `output_dir` defaults
to `output/` beside the code. `settings.json` only exists to move folders elsewhere; it is
gitignored, and `settings.example.json` is the template. `MOA_SETTINGS` overrides its location,
which is the clean way to run against a scratch config.

Nothing is machine-specific and there is no server, no shared credential and no account.
