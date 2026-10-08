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
python3 moa_engine.py hotlist --go              # THE outreach list (dossier is retired)
python3 moa_engine.py roster  --file X.xlsx --go # import a programme or attendee list (optional)
python3 moa_engine.py invites --year 2027 --go   # draft letters FROM the dossier

python3 backtest.py                     # the filter's test suite — must stay 21/22
python3 selftest.py                     # the file layer's test suite — offline, <1s
python3 gui.py                          # the browser app, against the live source
python3 build_single_file.py            # rebuild the two files in SEND THIS/
```

`--go` is required for any write; everything is dry-run otherwise. `--no-pubmed` skips
enrichment and makes `scan`/`update` roughly 10x faster for iterating on filter logic.
`--output-dir /tmp/moa-test` redirects every write for one run — use it rather than polluting
`output/` while testing.

### Two test suites, two triggers

- **`backtest.py`** re-scans every FDA approval since 2015 and asks whether the 22
  already-published MOA drugs survive the filter. Currently **21/22**; the expected miss is
  molnupiravir (EUA-only, never in Drugs@FDA). The labelled set in
  `config.PUBLISHED_MOA_DRUGS` is pinned so this gate is reproducible offline; it is NOT
  what the live gap analysis reads. Run after any change to `sources.py` or
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

**A new module must be added to `build_single_file.MODULES`.** That list is hand-maintained
and a module missing from it does not fail the build: it fails when a user clicks the button
that needs it, as an ImportError that only ever appears inside the bundle. `rxclass.py` was
added and left off, so the hot list worked from source and would have raised in the `.app`.
`selftest.test_bundle_module_list` now parses the imports and keeps the list closed.

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


## The hot list, and why the gap analysis is live

`hotlist` answers a different question from `dossier`. The dossier is for **approaching**
people; the hot list is for **finding** them, so its `Clin pharm contact` column ships empty
and the engine never writes to it. Pre-filling it with a paper's author would be worse than
blank, because that person is frequently not the right one to approach.

**Inclusion is the gap test alone.** Whether a contact is already known plays no part in it.
That was an explicit correction from the editorial team, and `is_gap()` must not grow a
contact check.

**`gaps.py` queries PubMed on every run.** `config.PUBLISHED_MOA_DRUGS` sat at 19 while the
series had published 22, and `GAP_CATEGORIES` was copied by hand off a slide, so neither can
drive a current answer. The pinned list survives only as `backtest.py`'s fixture, and
`GAP_CATEGORIES` only as the offline fallback, which announces itself when used.

Two rules in `gaps.py` carry the weight:

- **A paper with no PMC release date is not published.** Fruquintinib was indexed against a
  future issue while still unreleased, and counting it put a wrong total on a slide that was
  about to be presented.
- **The gap CATEGORY decides, not the fine modality label.** Olezarsen tags as "Antisense
  oligonucleotide" and the published imetelstat as "Oligonucleotide"; comparing fine labels
  called siRNA / ASO an open gap when the series had covered it. Gaps are **tiered**, and
  the tiers are exclusive, strongest first: a named category gap scores +40, an unpublished
  molecular format +12, an unpublished target class +8. A flat test let "MRI contrast agent"
  outrank an actual CAR-T gap.


## Modality is two axes, and format counts roll up

Rewritten 2026-10-08. The 58 labels used to be one flat set mixing molecular format with
target class, which made the coverage counts incomparable: of the 22 published papers, 18
were filed by format and 4 by target class.

- **Axis 1, molecular format**, hierarchical. `config.MODALITY_PARENT` is the child to parent
  map and `config.modality_chain()` walks it. An ADC is an antibody; a kinase inhibitor is a
  small molecule, so each paper increments **every level of its branch**.
- **Axis 2, target class**, flat and independent. FDA's Established Pharmacologic Class via
  `rxclass.py` (NLM RxClass, free, keyless, ~45% coverage) where the label carries one, the
  INN-stem table otherwise. **The row records which source answered**, because a stem guess
  must never be read as an FDA assignment on a sheet people act on.

`classify.classify_modality()` returns `(format, target_class, gap_category)`. The old
`modality()` two-tuple wrapper is kept for existing callers.

Four things that were measurably wrong before this, each now asserted in `selftest.py`:

- **Roll-up.** Small molecule read **10** when it is **14**; antibody read **3** when it is
  **7**. A gap test on those numbers calls a modality uncovered that the series has published
  on four times. Roll-up is directional: an ADC counts toward Antibody, a bare antibody does
  not count toward ADC.
- **The stem search only saw `rec["ingredient"]`**, which a Purple Book record truncates to
  the first word ("afamitresgene"), so a stem in the second word was invisible and **every
  `-leucel` cell therapy fell through to the fallback**, the worst place to lose the table,
  since CBER products are the cell, gene and vaccine gaps. It now scans `ingredient_raw` too.
- **`-leucel` is the WHO stem for an autologous T-cell product, not for a CAR.** CAR
  specificity comes from `-cabtagene`. It mapped to "CAR-T cell therapy", which called
  afamitresgene autoleucel, a TCR-T, a CAR-T.
- **The fallback defaulted everything to "Small molecule"**, filing a fibrinogen concentrate
  and an acellular nerve allograft as small molecules and inflating the very count the gap
  test reads. Worse, the CBER branch asserted the named category "Cell & gene therapy" on
  nothing but the reviewing centre, so fibrinogen scored +40 as an untouched cell and gene
  therapy. The regulatory route decides now (NDA to small molecule, BLA to "Biologic
  (unspecified)", a declared root that rolls up to nothing), and the name's shape only when
  there is no application number at all. Ten of the 22 published drugs reach "Small molecule"
  through this fallback, so it is load-bearing: do not remove it, extend the stem table.

The per-drug classification, with its source and which axis flagged the gap, is written to
the **Methodology sheet** of the outreach list. It is not on sheet 1: that sheet's columns are
fixed by editorial instruction.

`labels.py` reads MOA and indications from the DailyMed SPL. Three traps are pinned there:
indications can live in nested child sections (Pluvicto), the SPL states them twice (once in
Highlights), and a child section may be "Limitations of Use" rather than an indication
(orforglipron has exactly one child and that is what it is).


## The outreach list is the only outreach output

`dossier` was retired on 2026-10-08. `hotlist` writes
`output/MOA outreach list <date>.xlsx`, one file per date, with nine columns
(`config.HOTLIST_COLUMNS`): Drug name, MOA, Indication, NDA/BLA number, Approval date,
Company name, **AE owner**, **Clin pharm contact**, **ClinPharm contact from ASCPT Membership
Directory**.

**The last three are the team's and the engine must never write to them.** They are the
question being put to the AE team, and they ship blank on every run.

**The ASCPT column is manual by design, not by omission.** The Membership Directory is behind
a member login and its terms do not permit compiling it into a list, so the engine does not
query it: an AE looks a person up and types the name in. Do not add a scraper, and do not use
the credentials that appear earlier in this project's transcripts. `roster` remains the
supported path for ASCPT data: the user uploads an export they are entitled to.

**`invites` accepts a contact from EITHER contact column**, since both are a human naming a
real person. Blank in both means no letter, and it reports how many were skipped.

**Carrying the three columns forward is load-bearing.** One file per date means a run
overwrites, so `sheets._prior_contacts()` reads today's file if it exists and otherwise the most
recent earlier one, keyed on the INN inside `BRAND (ingredient)` (which also survives a brand
re-capitalisation: `AUCATZYL` to `Aucatzyl`). Without it, every run would hand the team a blank
sheet and silently discard a directory lookup done one row at a time.
`config.latest_hotlist_path()` picks by the date in the FILENAME, not mtime, because Drive sync
rewrites mtimes. Headers are matched **loosely** (`sheets._human_col()`): anything mentioning
ASCPT is the directory column, anything mentioning an owner is the owner column. An exact-string
match would break the moment someone reworded a header in Excel, and it would break silently.

**Hand-added drugs add to the priority block, they do not take its places.** `input/my drugs.txt`
(one per line, brand or generic) goes through `sources.find_by_name()`, which deliberately
bypasses the date window, the NME class filter and the novelty rule: the point of typing a drug
is that a human judged it interesting, and it may well be older than the window. Verified
against metformin (1995), Ozempic by brand, and vericiguat.

What retiring the dossier cost: ASCPT presence, poster times, "who to find", automatic contact
matching. `authors.py` now has no caller and is dormant, though still covered by `selftest`.

**The invitation letters were broken for a day and are now fixed.** `build_invites_html()` was
still reading the retired dossier's column names ("Drug (INN)", "Sponsor", "Contact",
"Candidate authors"), none of which exist on the outreach sheet, so every draft rendered with no
drug name, no company, and a contact of NEEDS LOOKUP even where an AE had filled one in. It
reads the outreach list's own columns now and `selftest.test_invitation_drafts` pins it. The
banner row was also being counted as a candidate: the filter matched the prefix "PRIORITY" after
the banner text had changed to "TOP 20 BY HOT SCORE", so the count read 51 for 50 rows. Banners
are detected structurally now, by every other cell being blank.

## Label parsing: five traps, all pinned

`labels.py` reads MOA and indications from the DailyMed SPL. Every one of these produced a
confidently wrong cell before it was fixed, and `selftest` holds nine drugs against expected
indication counts:

1. **Nested child sections.** Pluvicto keeps its two indications in `1.1`/`1.2` children;
   reading the section's own text returns empty.
2. **A child that is not an indication.** Orforglipron has exactly one child and it is
   "Limitations of Use", so taking children blindly yields a restriction, and stripping the
   restriction then leaves nothing.
3. **"are indicated", not "is indicated".** A combined label reads "RYBELSUS and OZEMPIC
   tablets are indicated", so matching only "is" left the heading in the text.
4. **Brand truncation must skip the heading.** Cutting at the first brand mention reduced the
   Ozempic indication to the fragment "RYBELSUS and".
5. **The Highlights copy restates indications in different words.** A prefix comparison missed
   it; duplicates are now detected on overlap of significant words.

## The GUI

Two fixes on 2026-10-08, both user-reported:

- **The server used to kill itself while still in use.** `setInterval` at 6s against a 25s
  timeout, but browsers throttle background-tab timers to roughly once a minute, so switching
  tabs for half a minute looked exactly like closing the page. Timeout is now 600s, the page
  also beats on `visibilitychange`, and if the engine really has exited the page says so rather
  than letting the reader think a refresh will help. A browser cannot restart a local server.
- **No terminal window.** `SEND THIS/CTS MOA Engine.app` is an `osacompile`d AppleScript that
  launches the same bundled script with `nohup`. A `.command` is opened BY Terminal.app, so the
  extension is what caused the window. Two things the .app must set: `MOA_APPDIR_BASE`, so the
  working folder lands beside the .app rather than inside its bundle, and `PYTHONUNBUFFERED=1`,
  the equivalent of `python3 -u`, without which `gui.log` stays empty and a user whose browser
  did not open cannot find the URL.

The page can now also set the output folder (validated in `gui._validate_outdir`: inside the
home folder, writable, created if needed) and edit `input/my drugs.txt`.


## Python floor: 3.9, and it is now gated

**Never use syntax newer than Python 3.9 in any module.** `/usr/bin/python3` ships with macOS
and is 3.9, and the launcher takes the first working interpreter it finds, so a colleague who
never installed Python runs something like it.

This shipped once and broke the whole bundle: a nested f-string
(`f"{f'...{x}...' if y else ''}"`, PEP 701, needs 3.12) parsed fine on the dev machine's 3.14
and was a SyntaxError on 3.9. The engine died before reaching the browser, which presented
to the user as "the browser tab did not open".

`selftest.test_python_floor` now parses every module under `/usr/bin/python3` and fails if any
of them needs something newer.

Two things made that bug far harder to find than it should have been, both fixed:

- **The bundle compiled every module under the LAST module's filename**, so a SyntaxError in
  `sources.py` was reported as `selftest.py:603`.
- **A failed launch was silent.** No terminal, log discarded, nothing on screen. The .app now
  waits five seconds, checks the process is alive, and shows a dialog with the log tail if it
  is not.

## What ships, and how it is packaged

`SEND THIS/` holds exactly one sendable file per platform:

- **`CTS MOA Engine (Mac).zip`** — the .app, plus the `.command` tucked in an
  "If the app is blocked" folder as a Gatekeeper fallback. A .app is a directory, so zipping is
  the only way to hand over one item while keeping the no-terminal launch.
- **`CTS MOA Engine (Windows).bat`** — already a single file.

The runtime log goes to `/tmp/cts-moa-engine.log`, not beside the app: with the browser opening
reliably it is only ever a diagnostic, and it was cluttering the folder the user actually opens.
The working folder beside the app holds just `input/`, `output/` and `settings.json`.

### Signing the Mac app: two traps, both now handled

**Re-sign after adding the payload.** `osacompile` signs the bundle; copying `engine.command`
into `Contents/Resources` afterwards breaks the seal. `codesign --verify` then reports "a sealed
resource is missing or invalid" and macOS refuses to open the app **with no dialog to click
past** -- which is not the ordinary unsigned-developer warning and cannot be worked around by
the user. `_build_mac_app()` re-signs ad-hoc (`--sign -`) and verifies, printing a warning if
verification still fails.

**Zip with `ditto`, never `zipfile`.** A signed bundle depends on extended attributes that
Python's zip writer does not carry. Use `ditto -c -k --sequesterRsrc` and no `--keepParent`
(it wraps everything in the staging folder's name).

**Quarantine still blocks it, and that is unavoidable.** Ad-hoc signing is not notarization, so
`spctl` rejects the app, and anything arriving by mail or Drive carries
`com.apple.quarantine`. Tested: a quarantined copy **silently fails to launch**, and launches
fine once the flag is stripped. Notarizing needs a paid Developer ID.

So the zip ships `If the app will not open/Open the engine.command`, which strips the flag,
launches the app, **waits up to 20 seconds and confirms the process is alive**, and prints the
log tail if it is not. An earlier version exited after two seconds and reported success into a
race with the detached launch, which is the same silent-failure class as the rest of this
section.


## Progress reporting

The engine prints `::progress <done> <total> <label>` on stdout; `gui.Job._pump()` picks those
lines out, stores the latest, and **keeps them out of `self.lines`** so the visible log stays
readable. `snapshot()` carries `progress` to the page, which already polls `/log`, so no new
polling was needed.

`total=0` means indeterminate. Two phases of `hotlist` use it (downloading the FDA feeds, and
the published-series query) because a bar sitting at zero through a two-minute download reads as
broken. The per-candidate loop is determinate: about four network calls per candidate across
180, which is where the wait actually is.

Only `hotlist` emits markers. Every other command shows the spinner as before, and the page
hides the bar when `progress` is null.

## Two bug classes this codebase keeps producing

Both have now bitten more than once, and both are gated:

**An endpoint or path that is never exercised.** `/setdrugs` and `/setoutdir` both shipped
referring to a `qs` that `do_GET` never defined. Each raised `NameError`, the connection closed
with no response, and the page said "the engine is not responding" while the engine answered
everything else fine. `selftest.test_gui_endpoints` now starts the real handler and calls every
GET route; it was confirmed to FAIL with the bug reintroduced, so it is a gate rather than
decoration.

**A failure with nowhere to print.** Under `pythonw` on Windows and `nohup` on macOS a traceback
vanishes, which is how a SyntaxError in one module presented to the user as "the browser tab did
not open". The bundle's `main()` is wrapped: the traceback goes to
`<temp>/cts-moa-engine-error.log` and, on Windows, into a `MessageBoxW` dialog. The `pythonw`
branches of `WIN_HEADER` also redirect to `%TEMP%\cts-moa-engine.log`.

`--gui` is stripped from argv rather than special-cased, so a bare double-click and the Windows
no-console path converge. Passing it through made argparse exit 2 invisibly.
