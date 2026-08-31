# CTS MOA sourcing engine

Finds newly approved **novel** drugs that deserve a *Mechanism of Action* mini-review in
*Clinical and Translational Science*, and identifies **the clinical pharmacologist at the
company who worked on each one** — by name, from their published Phase 1 clinical
pharmacology studies, with the evidence attached.

The objective is reaching that person. The ASCPT programme and attendee list are two further
routes to them, not the point of the tool.

Everything is a local file. Nothing runs unless you run it, nothing is written without
`--go`, and **no email is ever sent**.

---

## 1. Quick start

**Not comfortable in a terminal?** Double-click **`CTS MOA Engine`** for a browser-based
version of everything below, and read `START HERE.md` instead of this file.

```bash
cd "~/Desktop/Desktop/05 Code and Tools/cts-moa-engine"

./moa-engine check                    # verify data sources and files; writes nothing
./moa-engine scan --days 120          # see what it would find; writes nothing
./moa-engine update --go              # add new candidates to the queue
./moa-engine dossier --year 2027 --go # build the pre-ASCPT dossier
#   ... read and edit the dossier ...
./moa-engine invites --year 2027 --go # draft the invitation letters
```

On Windows, double-click **`Start MOA engine.bat`** and use `moa-engine.bat` in place of
`./moa-engine` below. See §12.

Requirements: **Python 3** and nothing else — no Claude or other AI tool, no account,
no API key, no login. Standard library only. **Python 3**. Nothing else — no pip installs, no accounts, no credentials, no
network access beyond the FDA and PubMed feeds the filter needs.

---

## 2. The commands

| Command | What it does | Writes? |
|---|---|---|
| `setup` | Optional configuration. The defaults work, so this can be skipped. | local file |
| `check` | Verifies input files, output folder, FDA sources, PubMed, schedule | no |
| `scan` | Prints ranked candidates for a look-back window | no |
| `update` | Appends **new** candidates to the queue | only with `--go` |
| `dossier` | Builds the outreach list | only with `--go` |
| `roster` | Imports an ASCPT attendee list or programme export | only with `--go` |
| `invites` | Drafts the invitation letters **from the dossier** | only with `--go` |
| `start` / `stop` / `status` | The optional monthly schedule | plist |

Useful flags:

- `--days N` — look-back window for `scan`/`update` (default 120)
- `--since YYYY-MM-DD` — explicit start date instead of `--days`
- `--no-pubmed` — skip PubMed enrichment; roughly 10× faster, but no author suggestions
- `--window N` — for `dossier`, how far back to draw candidates (default 550)
- `--invites N` — for `invites`, how many letters to draft (default 15)
- `--file PATH` — for `roster`, the .xlsx you exported; `--kind` overrides auto-detection
- `--output-dir PATH` — write somewhere else for one run, e.g. when testing

### Why `invites` is separate

`dossier` gives you a ranked list; it is not the final answer. The intended loop is:

```
dossier  →  open the .xlsx, reorder it, fill in AE owner, delete what you don't want
         →  invites
```

`invites` reads the dossier back **in the order you left it**, so your editing is what
drives the letters. Both write to the same file every time — re-running never produces a
second copy.

### Stopping the browser app

Close the tab. The page holds a heartbeat while it is open, and the server exits a few seconds
after the pings stop — so there is nothing to force-quit. A **Quit** button on the page does
the same thing immediately. A job already running is allowed to finish first, so closing the
tab mid-scan does not throw the scan away.

---

## 3. Turning the schedule on and off

The engine is **off by default**. To have it top up the queue monthly:

```bash
./moa-engine start                 # 7th of each month, 09:17
./moa-engine start --day 1 --hour 8
./moa-engine status
./moa-engine stop                  # removes it completely
```

`start` installs a macOS launchd job that runs only `update --go`. The annual dossier stays
manual on purpose — it needs a fresh ASCPT program export that only a person can drop in.

If your Mac is asleep at the scheduled time the job runs at next wake, and if a month is
missed entirely nothing is lost: each run **re-scans a trailing window and de-duplicates by
key**, so it self-heals. Output is appended to `schedule.log`.

---

## 4. What counts as a "novel agent"

This is the heart of the tool, and the part most worth arguing about.

**Included**
- First approval of a molecule anywhere in the FDA record (FDA class Type 1 / Type 1-4)
- A combination that introduces at least one new moiety (e.g. cefepime + **zidebactam**)
- A **new active ingredient** — enantiomer, salt or ester of a known moiety (Type 2).
  Scored lower than a true first-in-class, but included: esketamine is a Type 2 and the
  series published it.
- A **known molecule arriving by a new route**, especially under a new brand — e.g.
  intrathecal onasemnogene abeparvovec (ITVISMA), ophthalmic bevacizumab (LYTENAVA)

**Excluded**
- Generics (ANDAs) and biosimilars / interchangeables (351(k))
- Reformulations and new combinations of already-approved drugs (Type 3/4/5)
- Repeat applications for a molecule already approved by the same route
- Plasma-derived products (immune globulin, prothrombin complex, albumin)

Novelty is decided against a **known-moiety index** built from every application in the FDA
record — roughly 29,000 applications — not from a keyword list. Every candidate carries a
plain-text reason you can disagree with.

### Ranking

Each candidate gets a 0–100 score, and the reasons are recorded in full:

```
first-in-human moiety (+40); fills coverage gap: Incretins & cardiometabolic (+22);
no existing MOA review found (+8); clin pharm literature exists, 13 papers (+6);
approved this year (+8)
```

Weights live in one place (`classify.py`, `novelty_score`). Change them if the group
disagrees — that is the intended way to tune the tool.

---

## 5. What it produces

Three files, all in `output/` (configurable, and overridable per run with `--output-dir`):

1. **`MOA candidate queue.xlsx`** — append-only. A candidate already in it is never added
   twice, and the **`AE owner`** and **`Status`** columns are yours and are never
   overwritten. Set `Status` to `Published`, `Declined` or `Dropped` to retire a row from
   future dossiers.
2. **`ASCPT <year> MOA recruiting dossier.xlsx`** — ranked candidates joined to the meeting
   program, with `Attending?` and `Comments` columns matching the ones already in use in
   *CTS coverage AM 2026*.
3. **`MOA invitation drafts <year>.html`** — one editable draft per candidate. Opens in a
   browser or in Word. **Review and edit every one before sending. Nothing is sent for you.**

### Re-running is safe

The whole workbook is rewritten each time, so a shorter run cannot leave stale rows behind.
Before writing the dossier, the tool reads back your `AE owner` / `Attending?` / `Comments`
and re-attaches them **matched on drug name, not row position** — so they follow the drug
through a re-ranking. Machine columns (score, program matches) are always refreshed.

The one thing to know: the dossier is *derived* from the queue. Deleting a row from the
dossier removes it from that run's letters, but it will come back next time the dossier is
rebuilt. To retire a candidate for good, set its `Status` in the **queue**.

---

## 6. The input files

All live in `input/`. None is required — the tool degrades rather than failing.

**`contacts.xlsx`** — three columns: company, head of clinical pharmacology, contact.
Without it every candidate reads `NEEDS LOOKUP`.

**`ascpt program <year>.xlsx`** and **`ascpt attendees <year>.xlsx`** — one pair per meeting
year, discovered by filename. Import them with `./moa-engine roster --file <path> --go`, which
works out which kind it is and files it under the right name, or drop them in by hand.

### How the meeting year is used

This matters, because it used to be wrong. The tool distinguishes three things:

| Signal | Source | Shown in |
|---|---|---|
| Presenting at the **upcoming** meeting | `ascpt program <year>.xlsx` | `ASCPT presence`, `Poster / session detail` |
| **Registered** for the upcoming meeting | `ascpt attendees <year>.xlsx` *(optional)* | `ASCPT presence` |
| Was at a **previous** meeting | any earlier year's files | `Last year at ASCPT`, `Who to find` |

Before this split there was a single fixed programme file with no year attached, so a 2027
dossier was matched against the 2026 programme and offered poster slots that had already
happened. Now, if there is no programme for the upcoming year yet, `ASCPT presence` honestly
reads `none found` and the earlier years appear only as history.

The history is the useful part before the next programme is published: **people who came to
one annual meeting tend to come to the next**, so a sponsor whose clin pharm team was visibly
at AM2026 is a better bet for a conversation than one that has never appeared. It is
reported, never scored — ranking stays editorial.

When there is no attendee list for a year, the tool derives a proxy roster from that year's
**presenting authors**. That is thinner than a real roster: it only sees people who submitted
an abstract, so it matches 5 of the current 25 candidate sponsors. A real attendee list is a
straight upgrade and needs no code change.

### It keeps itself current

You never set the year. `config.meeting_year()` works it out — the meeting is in March, so from
June onwards the tool targets next year's — and the GUI labels its buttons with the result, so
they read "ASCPT 2027" today and "ASCPT 2028" from June 2027. Past years slide into history on
their own as you add each year's export, and the history deepens rather than being replaced.
`--year` overrides whenever you need a specific meeting.

### The programme export

Two tabs, `Posters` and `Sessions`. The engine uses whatever columns exist, but these unlock
the good part:

| Column | Enables |
|---|---|
| `Poster Presentation Title` | drug-name matching |
| `Abstract Text` | drug-name matching when the title doesn't say it |
| `Presenting Author First/Last Name` | **the person to walk up to** |
| `Presenting Author Organization` | sponsor-in-the-room matching |
| `Poster Number`, `Session Date`, `Session Start Time` | where and when |

With those, a dossier row reads:

```
Poster PI-072 [sponsor presenting] — Brian Moser (Eli Lilly and Company)
— Poster Session I 3/4/2026 5:00 PM — INDEPENDENT POPULATION EXPOSURE-RESPONSE…
```

---

## 7. Timing it to the meeting

ASCPT 2026 ran 4–7 March. Run the dossier about **four weeks before** the meeting — early
February — so there is time to write ahead and set up conversations:

```bash
./moa-engine update --days 400 --go
./moa-engine roster  --file ~/Downloads/ascpt-2027-programme.xlsx --go   # when published
./moa-engine roster  --file ~/Downloads/ascpt-2027-attendees.xlsx --go   # if you can get it
./moa-engine dossier --year 2027 --go
#   read it, edit it
./moa-engine invites --year 2027 --go
```

The two `roster` calls are optional. Without them the dossier still ranks candidates and still
tells you which sponsors came to the last meeting; it just cannot say where they will be
standing.

---

## 8. Handing this to someone else

Nothing is tied to one machine or one person, and there is no credential to share.

1. Copy this folder. Do **not** copy `settings.json`, `cache/` or `output/`.
2. Tell them to double-click **`CTS MOA Engine`** and read `START HERE.md`.
3. They need Python 3 and nothing else.

If you send the folder as a zip, the launcher may lose its executable bit. If double-clicking
does nothing, they run once in Terminal: `chmod +x "Start MOA engine.command"`.

---

## 9. Files

| File | Role |
|---|---|
| `CTS MOA Engine.app` | macOS double-clickable launcher — opens the browser app, no Terminal |
| `build_icon.py` | draws the app icon and installs it into the bundle |
| `Start MOA engine.command` | macOS, same but shows a Terminal window; useful when debugging |
| `Start MOA engine.bat` | **Windows** double-clickable launcher |
| `START HERE.md` | plain-language guide to send with the folder |
| `gui.py` | the local browser app |
| `moa-engine` / `moa-engine.bat` | command-line wrapper (macOS+Linux / Windows) |
| `moa_engine.py` | command-line interface |
| `sources.py` | the two FDA feeds and the novelty gate |
| `classify.py` | modality tagging, coverage-gap flags, scoring |
| `enrich.py` | PubMed: prior-review check, candidate authors |
| `sheets.py` | queue, dossier, program matching, invitation drafts |
| `store.py` | reads and writes .xlsx workbooks, standard library only |
| `scheduler.py` | start/stop the optional monthly job |
| `backtest.py` | ground-truth check against the 19 published MOA papers |
| `selftest.py` | offline check of the file layer |
| `config.py` | all tunable data: stems, gaps, weights, column names, paths |
| `settings.json` | your folder paths — optional, not shared, not committed |

---

## 10. Verifying it still works

```bash
python3 backtest.py     # the filter, against the 19 published MOA papers
python3 selftest.py     # the file layer: .xlsx round-trip, annotation merge
```

`backtest.py` re-scans every FDA approval since 2015 and asks whether the 19 already-published
MOA mini-review drugs survive the filter. Current result: **18/19**. The one miss is
**molnupiravir**, which only ever held an Emergency Use Authorization and so never entered
Drugs@FDA — a limitation of the source, not a bug.

`selftest.py` runs offline in under a second and leaves nothing on disk. Run it after
touching `store.py` or `sheets.py`; run `backtest.py` after touching `sources.py` or
`config.py`. A filter that rejects drugs the series actually published is not ready to trust.

To exercise the write path without touching your real files:

```bash
./moa-engine update --go --output-dir /tmp/moa-test --no-pubmed
```

---

## 11. Known limitations

- **EUA-only products are invisible** (molnupiravir).
- **Route data is imperfect.** Drugs@FDA has no route column; route is parsed from `Form`,
  which sometimes holds a presentation (`VIAL;SINGLE-DOSE`). Where route is unreadable the
  tool falls back to "new brand + FDA NME classification", which is what correctly caught
  ophthalmic bevacizumab.
- **Contacts are mostly missing.** The contact grid has ~44 companies with almost no names
  filled in, so most candidates come through as `NEEDS LOOKUP`. The tool will never guess:
  a fuzzy match below 0.82 is reported as unknown rather than risking a wrong recipient.
- **Program matches are leads, not facts.** "Sponsor presenting" means someone from that
  company has a poster — not that they own the molecule.
- **Prior-year attendance is a prediction, not a booking.** It says a sponsor's team came
  before, which is a reason to expect them, not a guarantee they will be there.
- **The derived roster only sees presenters.** Without a real attendee list, sponsor leads
  who attend without submitting an abstract are invisible — 5 of 25 candidates match today.
- **CBER coverage depends on Purple Book publishing lag** (only January was posted for 2026
  at the time of writing).
- **Edit the workbooks in a real spreadsheet app and save them as `.xlsx`.** The reader
  handles what Excel and Numbers produce, but saving as `.csv` or `.xls` will lose the tab
  structure the tool looks for.
- **The Windows support has not been run on a Windows machine** (see §12).
- **It runs on your laptop.** If the group adopts it, decide who owns it long-term.

---

## 12. Windows and Linux

The engine itself is plain standard-library Python and platform-independent: the filter, the
scoring, the `.xlsx` layer and the browser GUI all behave the same everywhere. Only the
launchers and two OS calls differ.

**Windows.** Double-click **`Start MOA engine.bat`**. Use `moa-engine.bat` for the
command line:

```bat
moa-engine.bat check
moa-engine.bat update --go
```

Install Python 3 from python.org and tick **"Add Python to PATH"** during setup. The
launcher looks for the `py` launcher first, then `python`.

Scheduling uses **Task Scheduler** via `schtasks`, under the task name
`CTS MOA engine monthly update`. `start` creates it, `stop` deletes it, `status` reports it.

> **Untested.** The Windows code paths were written from documented behaviour and exercised
> only by simulation on a Mac — the `schtasks` argv, the `os.startfile` branch and the
> console-suppression flag are all unverified on real hardware. Someone on a PC should run
> `moa-engine.bat check` and press each GUI button once before you rely on it. Nothing there
> can damage data — the worst case is a button that does nothing.

**Linux.** Everything works except the launchers and the schedule. Run `./moa-engine`
directly, or `python3 gui.py` for the browser app. `moa-engine start` prints the `crontab -e`
line to add rather than editing your crontab for you. The folder-open button uses `xdg-open`.
