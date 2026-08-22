# Start here

This folder finds newly approved **novel** drugs that are good candidates for a
*Mechanism of Action* mini-review in **Clinical and Translational Science**, keeps them in a
list for you, and once a year works out which of those drug sponsors will be presenting at
the ASCPT Annual Meeting — so you know who to look for in person.

You do not need to know any programming to use it.

---

## To run it

### On a Mac

**Double-click `CTS MOA Engine`** — the blue molecule icon.

A window opens in your web browser with five numbered buttons. Work down the list.

**To stop it, just close the browser tab.** The app shuts itself down a few seconds later.
There is also a **Quit** button at the bottom of the page. You should never need to force-quit
anything.

> **The first time only**, macOS may refuse to open it and say it is from an unidentified
> developer. If that happens: **right-click** it → **Open** → **Open**. You only have to do
> this once.

If nothing happens, double-click **`Start MOA engine.command`** instead. That does the same
thing but leaves a Terminal window open showing what it is doing, which is useful if
something has gone wrong.

### On a PC

**Double-click `Start MOA engine.bat`.** Same browser window, same five buttons, and the same
rule for stopping it: close the browser tab.

> Windows support is new and has not yet been tried on an actual PC. If a button does
> nothing, say so — nothing can be damaged, it just may not have been wired up correctly.

---

## What you need installed

Just one thing: **Python 3**, from https://www.python.org/downloads/

On Windows, tick **"Add Python to PATH"** on the first screen of the installer. It is easy to
miss and nothing works without it.

That's it. There is no account to create, no password, no sign-in, and nothing that talks to
Google. Everything stays on this computer.

---

## The five buttons

| | What it does | Saves anything? |
|---|---|---|
| **1** | Confirms the tool can reach the FDA and PubMed, and can find your files. | No |
| **2** | Shows the drugs it found, ranked, so you can look before committing. | No |
| **3** | Adds any newly approved drugs to your candidate list. | Yes |
| **4** | Builds the ASCPT meeting dossier — who to find at which poster. | Yes |
| **5** | Drafts the invitation letters. | Yes |
| **+** | *Optional.* Adds an ASCPT attendee list, if you can get one. | Yes |

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
2. **`ASCPT <year> MOA recruiting dossier.xlsx`** — the ranked list joined to the meeting
   programme, with **Attending?** and **Comments** columns for your notes. Re-running button 4
   keeps everything you have typed, matched up by drug name.
3. **`MOA invitation drafts <year>.html`** — the letters. Double-click to open in your
   browser, or open it with Word to edit.

You can edit any of these and re-run. The tool matches your notes back up by drug name, so
they survive re-ranking.

---

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

If the browser window never opens, double-click `Start MOA engine.command` (Mac) or
`Start MOA engine.bat` (PC) instead — it prints an address like
`http://127.0.0.1:61152/?t=…` that you can paste into your browser.

`README.md` in this folder has the full detail, including how the tool decides what counts
as "novel".
