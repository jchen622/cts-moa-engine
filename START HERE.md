# Start here

This finds newly approved **novel** drugs that are good candidates for a *Mechanism of
Action* mini-review in **Clinical and Translational Science**, and then works out **which
clinical pharmacologist at the company actually worked on that drug** — by name, from their
published Phase 1 clinical pharmacology studies.

That is the whole point: getting the invitation to the right person. The ASCPT meeting
files are one extra way of reaching them, not the objective.

You do not need to know any programming to use it.

---

## To run it

**Double-click the one file you were sent.**

| You are on | The file |
|---|---|
| a Mac | `CTS MOA Engine.command` |
| a Windows PC | `CTS MOA Engine.bat` |

That single file is the whole program. A window opens in your web browser with
numbered buttons — work down the list. **To stop, close the browser tab**; it shuts
itself down a few seconds later. There is also a **Quit** button on the page.

> **Mac, first time only.** macOS may refuse to open it and say it is from an unidentified
> developer. **Right-click** it → **Open** → **Open**. Once only.
>
> If double-clicking does nothing at all, the file lost its executable flag in transit.
> Open Terminal and run: `chmod +x "CTS MOA Engine.command"`

> **Windows, first time only.** SmartScreen may warn. Click **More info** → **Run anyway**.


## What you need installed

**Python 3** — and the tool will walk you through it.

If Python is missing, double-clicking the file opens the download page in your browser and
tells you what to do. Download it, run the installer, click through, double-click the file
again. About two minutes, and only ever once per computer.

> **On Windows**, tick **"Add Python to PATH"** on the first screen of the installer. It is
> easy to miss and nothing works without it.

Nothing else. No Claude, no AI tool, no account, no API key, no login — the engine is an
ordinary Python program. It talks to two public sources only: the FDA (Drugs@FDA and the
Purple Book) and PubMed.

> **Mac note.** Every Mac already has a `python3` command, but on a machine without working
> developer tools it is a placeholder that prints Xcode errors instead of running. The
> launcher tests each Python it finds and skips that one, so if it says Python is missing,
> it really is.


## The five buttons

| | What it does | Saves anything? |
|---|---|---|
| **1** | Confirms the tool can reach the FDA and PubMed, and can find your files. | No |
| **2** | Shows the drugs it found, ranked, so you can look before committing. | No |
| **3** | Adds any newly approved drugs to your candidate list. | Yes |
| **4** | Builds the outreach list — who to contact at each company, by name. | Yes |
| **5** | Drafts the invitation letters. | Yes |
| **+** | *Optional.* Adds a file that helps you reach people — see below. | Yes |

Buttons **1** and **2** are always safe. Buttons **3**, **4** and **5** ask you to confirm
before they save anything.

**No email is ever sent.** Button 5 writes the letters into a file for you to read, edit and
send yourself.

Everything it writes lands in the **`output`** folder. The **Open my results folder** button
at the bottom takes you straight there.

---

## When to use which

**Every month or so** — button **3**, to keep the list current. Running it twice changes
nothing the second time, so there is no harm in pressing it whenever you wonder.

**About four weeks before the ASCPT Annual Meeting** — button **3**, then button **4**. That
is the one that tells you which people to find at which poster.

Button **4** builds the dossier for the **next** meeting. Where it has nothing for that meeting
yet, it falls back to **who attended the previous one** — shown in its own
`Last year at ASCPT` column, so it is never confused with somewhere you can actually walk to.
The **+** button is where you add a list for the **upcoming** meeting, which replaces that
guesswork with names of people who are actually registered.

**Then, once you have read the dossier** — button **5**. Open the dossier first
(`ASCPT 2027 MOA recruiting dossier.xlsx`), reorder it, fill in who is chasing whom, delete
anything you don't want. Button 5 drafts letters from the list *exactly as you left it*.

---

## The three files it makes

All in the `output` folder. Open them by double-clicking.

1. **`MOA candidate queue.xlsx`** — the running list. Two columns are yours: **AE owner** and
   **Status**. Type in them freely; the tool never overwrites them. Set **Status** to
   `Published`, `Declined` or `Dropped` to retire a drug from future dossiers.
2. **`MOA author outreach list <year>.xlsx`** — the ranked list. The column that matters is
   **Clin pharm contacts**: the people to write to, with the reason each was picked and the
   PubMed IDs behind it. **Attending?** and **Comments** are yours; re-running button 4 keeps
   everything you have typed, matched up by drug name.
3. **`membership check list.xlsx`** — a short list of just those names, with a blank
   **ASCPT member?** column. Fill it in (or ask `members@ascpt.org` to), then add it back
   with the **+** button and every future run remembers.
4. **`MOA invitation drafts <year>.html`** — the letters. Double-click to open in your
   browser, or open it with Word to edit.

You can edit any of these and re-run. The tool matches your notes back up by drug name, so
they survive re-ranking.

---

## How it finds the right person

For each drug it searches PubMed in three passes, because who leads a paper depends on what
kind of study it is:

1. **Clinical pharmacology studies** — food effect, drug–drug interaction, relative
   bioavailability, organ impairment, mass balance/ADME, thorough QT, healthy-volunteer PK.
   These are the ones a clinical pharmacologist designs and **first-authors**.
2. **Pharmacometrics** — population PK, exposure–response, PBPK.
3. **Dose-escalation trials** — only as a fallback. On these the first and last authors are
   the treating clinicians, and the clinical pharmacologist is buried in the middle, so
   here the tool looks for the **company affiliation** instead of the position.

It reads each author's affiliation from PubMed, so it can tell the sponsor's own staff from
the hospital investigators. It also spots when the company that ran the studies is not the
company that now owns the drug — tebipenem is approved to GSK but every paper comes out of
Spero, which GSK acquired — and says so.

Expect **more than one name per drug**. That is deliberate; usually several people worked
on it, and you may want them as co-authors.

## The ASCPT meeting files

Use the **Add an ASCPT attendee list** button for both of these — it works out which kind of
file you picked and saves it under the right name. Both are optional.

**The programme**, once ASCPT publishes it. Export it from the ASCPT website into a
spreadsheet with two tabs, `Posters` and `Sessions`. Make sure it includes the **Presenting
Author** columns — those are what turn a drug into a name you can walk up to.

**The attendee list**, if you can get one. This is the better of the two, because most
sponsor clinical pharmacology leads attend without presenting a poster, so the programme
alone misses them.

### What happens when you have neither

It still works. The tool falls back to **who came to the last meeting** — on the theory that
people who attend one annual meeting tend to attend the next. Those show up in a separate
`Last year at ASCPT` column, clearly marked as history rather than as somewhere to be.

This distinction is the point. Before it existed, a dossier for the 2027 meeting listed
poster times from March 2026 as though you could walk to them.

---

## Two things to expect

**Most contacts will say `NEEDS LOOKUP`.** The tool will not guess who to write to — a wrong
recipient on a journal invitation is worse than a blank one. Filling in
`input/contacts.xlsx` is the main manual work, and the thing that would most improve the
letters.

**It suggests; it does not decide.** Every drug carries a plain-English reason for why it
scored what it did, so you can disagree with it.

---

## If something goes wrong

Press button **1**. It checks each piece in turn and says which one is unhappy.

If the browser window never opens, the launcher still prints an address like
`http://127.0.0.1:61152/?t=…` in its window — paste that into your browser.

`README.md` in this folder has the full detail, including how the tool decides what counts
as "novel".
