"""Queue, dossier and invitation-draft output.

Everything is a local .xlsx workbook under ``output/`` (see ``store.py``); the
queue is append-only and keyed, so re-running is always safe: a candidate
already in it is never duplicated, and the editorial columns a human fills in
(AE owner, Status) are never overwritten by the machine.

Dossier re-runs are safe for the same reason but by a different mechanism --
see ``merge_annotations``. The whole workbook is rewritten each time, so a
shorter run cannot leave stale rows behind.
"""
import datetime
import difflib
import html
import os
import re

import config
import store


# ------------------------------------------------------------------ contacts
def _normalise_company(name):
    n = (name or "").lower()
    n = re.sub(r"[^a-z0-9 ]+", " ", n)
    words = [w for w in n.split() if w not in config.COMPANY_NOISE]
    return " ".join(words).strip()


def _prefix_match(a, b):
    """Is one normalised company key a truncation of the other?

    FDA sponsor strings are cut off mid-word ('HAISCO PHARMACEUTICAL GROU'),
    so a plain prefix test earns its keep -- but only just. Bare
    `startswith` is far too generous on short keys: 'VERA THERAPEUTICS'
    normalises to 'vera', which is a prefix of 'verastem oncology', a
    completely different company. That mis-fire sent Vera's invitation to
    Verastem and put three of Verastem's staff under "who to find".

    What separates the two is not length but *whole words*: a truncation drops
    the tail of one word ('grou' / 'group'), whereas a false positive swallows
    entire extra words ('vera' / 'verastem oncology'). So the part that differs
    may not contain a space. The length floor then rules out the remaining
    short-stem collisions such as 'bio' matching 'biogen'.
    """
    if not a or not b:
        return False
    if a == b:
        return True
    short, long_ = (a, b) if len(a) <= len(b) else (b, a)
    if len(short) < 5 or not long_.startswith(short):
        return False
    return " " not in long_[len(short):].lstrip()


def load_contacts(path=None, tab=None):
    """company key -> (display name, head of clin pharm, contact).

    Falls back to the workbook's first tab: the exported grid keeps its
    original long tab name, and asking the user to retype that correctly into
    settings.json is a needless way to lose all the contacts.
    """
    path = path or config.contacts_file()
    if not os.path.exists(path):
        print(f"  WARNING: no contact file at {path} — every candidate will "
              f"come through as NEEDS LOOKUP")
        return {}
    rows = store.xlsx_read(path, tab) or store.xlsx_read(path)
    out = {}
    for r in rows[1:]:
        if not r or not r[0].strip():
            continue
        key = _normalise_company(r[0])
        if key:
            out[key] = (r[0].strip(),
                        r[1].strip() if len(r) > 1 else "",
                        r[2].strip() if len(r) > 2 else "")
    return out


def match_contact(sponsor, contacts):
    """Resolve an FDA sponsor string to a contact row.

    FDA sponsor strings are dirty and sometimes truncated mid-word
    ('HAISCO PHARMACEUTICAL GROU'), so a near miss is common. Anything below
    the threshold returns NEEDS LOOKUP rather than a guess -- a wrong contact
    on an outreach email is worse than a blank one.
    """
    key = _normalise_company(sponsor)
    if not key or not contacts:
        return "NEEDS LOOKUP", ""
    if key in contacts:
        disp, head, contact = contacts[key]
        return (contact or head or f"{disp}: no contact on file"), disp
    best, score = None, 0.0
    for k in contacts:
        s = difflib.SequenceMatcher(None, key, k).ratio()
        # a truncated FDA string is a clean prefix of the real company name
        if _prefix_match(key, k):
            s = max(s, 0.90)
        if s > score:
            best, score = k, s
    if best and score >= config.CONTACT_MATCH_THRESHOLD:
        disp, head, contact = contacts[best]
        suffix = f"  [fuzzy match {score:.2f} -> {disp}]"
        return (contact or head or f"{disp}: no contact on file") + suffix, disp
    return "NEEDS LOOKUP", ""


# ------------------------------------------------------------------ queue
def ensure_queue(dry_run=True):
    """Return (path, created). The workbook is only created for a real run."""
    path = config.queue_path()
    if os.path.exists(path):
        return path, False
    if dry_run:
        return path, True
    store.xlsx_write(path, {config.QUEUE_TAB: [config.QUEUE_COLUMNS]})
    return path, True


def existing_keys(path):
    rows = store.xlsx_read(path, config.QUEUE_TAB)
    return {r[0].strip() for r in rows[1:] if r and r[0].strip()}


def queue_row(rec, contact):
    return [
        rec["key"],
        rec.get("approval_date", ""),
        rec.get("ingredient", ""),
        rec.get("brand", ""),
        rec.get("sponsor_raw", ""),
        rec.get("center", ""),
        rec.get("modality", ""),
        rec.get("gap", ""),
        str(rec.get("score", "")),
        (rec.get("prior_review_detail") or "yes") if rec.get("prior_review") else "no",
        rec.get("clinpharm_contacts", ""),
        rec.get("clinpharm_evidence", ""),
        ", ".join(rec.get("candidate_authors", [])[:6]),
        contact,
        "",                                   # AE owner - human fills
        "New",                                # Status - human maintains
        datetime.date.today().isoformat(),
    ]


def append_candidates(path, rows, dry_run=True):
    """Add rows to the queue, preserving everything already in it.

    There is no server-side append on a local file, so this is
    read-modify-write. The existing rows go back verbatim, which is what keeps
    hand-typed AE owner and Status values safe.
    """
    if not rows:
        return 0
    if dry_run:
        return len(rows)
    existing = store.xlsx_read(path, config.QUEUE_TAB)
    if not existing:
        existing = [config.QUEUE_COLUMNS]
    store.xlsx_write(path, {config.QUEUE_TAB: existing + [list(r) for r in rows]})
    return len(rows)


def refresh_queue_contacts(path, enrich_fn, members=None, only_missing=True,
                           log=print):
    """Backfill the clinical-pharmacologist columns on an existing queue.

    The queue predates these columns, and `update` only ever appends, so
    without this the rows already in it would stay blank for ever. Human
    columns are read and written back untouched; only machine columns change.
    """
    rows = store.xlsx_read(path, config.QUEUE_TAB)
    if not rows:
        return 0
    hdr = list(rows[0])
    # Widen an older workbook to the current layout, keeping values with their
    # own headers rather than by position.
    body = [{hdr[i]: (r[i] if i < len(r) else "") for i in range(len(hdr))}
            for r in rows[1:] if r and r[0].strip()]
    out, done = [], 0
    for rec in body:
        have = rec.get("Clin pharm contacts", "")
        if not (only_missing and have):
            drug = rec.get("Drug (INN)", "")
            if drug:
                got = enrich_fn({"ingredient": drug,
                                 "sponsor_raw": rec.get("Sponsor", "")},
                                members=members)
                rec["Clin pharm contacts"] = got.get("clinpharm_contacts", "")
                rec["Contact evidence (PMIDs)"] = got.get("clinpharm_evidence", "")
                if got.get("candidate_authors"):
                    rec["Candidate authors"] = ", ".join(
                        got["candidate_authors"][:6])
                done += 1
                log(f"    {drug[:26]:26s} {rec['Clin pharm contacts'][:56]}")
        out.append([rec.get(c, "") for c in config.QUEUE_COLUMNS])
    store.xlsx_write(path, {config.QUEUE_TAB: [config.QUEUE_COLUMNS] + out})
    return done


def read_queue(path):
    """Full queue as dicts, so the dossier can rank what has accumulated."""
    rows = store.xlsx_read(path, config.QUEUE_TAB)
    if not rows:
        return []
    hdr = rows[0]
    out = []
    for r in rows[1:]:
        if not r or not r[0].strip():
            continue
        out.append({hdr[i]: (r[i] if i < len(r) else "") for i in range(len(hdr))})
    return out


# ------------------------------------------------------------------ dossier
def load_program(path=None, posters_tab="Posters", sessions_tab="Sessions"):
    """Read one year's ASCPT program export.

    This is a manual drop-in: the annual-meeting program is rendered
    client-side by EventScribe and cannot be fetched by script. Whatever
    columns the export has, we use; a presenter/author column switches on
    person-level matching and is otherwise skipped.
    """
    program = {"posters": [], "sessions": [], "has_authors": False}
    if path is None:
        return program
    if not path or not os.path.exists(path):
        return program
    for tab, dest in ((posters_tab, "posters"), (sessions_tab, "sessions")):
        rows = store.xlsx_read(path, tab)
        if not rows:
            continue
        hdr = [h.strip() for h in rows[0]]
        for r in rows[1:]:
            if not r or not any(c.strip() for c in r):
                continue
            program[dest].append({hdr[i]: (r[i] if i < len(r) else "")
                                  for i in range(len(hdr))})
        if any(re.search(r"author|presenter|speaker", h, re.I) for h in hdr):
            program["has_authors"] = True
    return program


def _tokens(s):
    return {w for w in re.split(r"[^a-z0-9]+", (s or "").lower()) if len(w) > 3}


def _col(row, *names):
    for n in names:
        if row.get(n):
            return str(row[n]).strip()
    return ""


def _presenter(row):
    first = _col(row, "Presenting Author First Name")
    last = _col(row, "Presenting Author Last Name")
    org = _col(row, "Presenting Author Organization")
    name = " ".join(x for x in (first, last) if x)
    return name, org


# ------------------------------------------------------------------ attendance
# Who from a sponsor was in the room, this year and in previous years.
#
# The premise, and it is only a premise: people who came to the annual meeting
# once tend to come again. So a sponsor whose clin pharm team was visibly at
# AM2026 is a better bet for an in-person conversation at AM2027 than one that
# has never appeared -- even before the AM2027 programme exists.

# Column spellings seen in attendee exports. Matched case-insensitively against
# whatever the file actually has, the same tolerance load_program() applies.
_ROSTER_ORG_COLUMNS = ("organization", "organisation", "company", "institution",
                       "affiliation", "employer", "org")
_ROSTER_NAME_COLUMNS = ("name", "full name", "attendee name", "attendee",
                        "display name")
_ROSTER_FIRST_COLUMNS = ("first name", "firstname", "given name", "first")
_ROSTER_LAST_COLUMNS = ("last name", "lastname", "surname", "family name", "last")


class RosterError(RuntimeError):
    pass


def _find_column(hdr, candidates):
    low = [h.strip().lower() for h in hdr]
    for want in candidates:
        if want in low:
            return low.index(want)
    for i, h in enumerate(low):           # substring fallback: "Company Name"
        if any(want in h for want in candidates):
            return i
    return None


def looks_like_members(path):
    """Does this workbook carry a membership-only column?"""
    try:
        rows = store.xlsx_read(path)
    except Exception:
        return False
    if not rows:
        return False
    hdr = [str(h).strip().lower() for h in rows[0]]
    return any(any(m == h or m in h for m in config.MEMBER_ONLY_COLUMNS)
               for h in hdr)


def looks_like_check_list(path):
    """Is this the engine's own membership check list, filled in?"""
    try:
        rows = store.xlsx_read(path)
    except Exception:
        return False
    if not rows:
        return False
    hdr = [str(h).strip().lower() for h in rows[0]]
    return "ascpt member?" in hdr and "name" in hdr


def load_check_list(path):
    """Read a filled-in membership check list into {person key -> organisation}.

    Only rows answered affirmatively count. A blank answer means "not checked
    yet", which is different from "not a member", so both are skipped rather
    than being recorded as a negative.
    """
    import authors
    rows = store.xlsx_read(path)
    if not rows:
        return {}
    hdr = [str(h).strip().lower() for h in rows[0]]
    try:
        i_name = hdr.index("name")
        i_ans = hdr.index("ascpt member?")
    except ValueError:
        return {}
    i_org = hdr.index("company") if "company" in hdr else None
    out = {}
    for r in rows[1:]:
        def cell(i):
            return r[i].strip() if i is not None and i < len(r) else ""
        ans = cell(i_ans).lower()
        if ans[:1] in ("y", "t", "1") or ans == "member":
            name = cell(i_name)
            if name:
                out[authors.person_key(name)] = cell(i_org)
    return out


MEMBERS_COLUMNS = ["Name", "Company"]


def save_members(path, members):
    """Write {person key -> org} as a plain two-column directory file."""
    rows = [[name, org] for name, org in sorted(members.items())]
    store.xlsx_write(path, {"Members": [MEMBERS_COLUMNS] + rows})
    return len(rows)


def merge_members(existing_path, new):
    """Existing confirmed members plus newly confirmed ones.

    A check list only carries the names that had no answer yet, so importing
    one must add to what is already known rather than replace it -- otherwise
    every round of checking would discard the previous round's answers.
    """
    current = {}
    if existing_path and os.path.exists(existing_path):
        try:
            current = load_members(existing_path)
        except Exception:
            current = {}
    merged = dict(current)
    merged.update(new)
    return merged, len(merged) - len(current)


def load_members(path, tab=None):
    """Read an ASCPT member directory into {person key -> organisation}.

    Same {name, org} shape as an attendee list, so it reuses load_roster; the
    difference is what it is used for. Keyed on first-initial + surname to
    survive middle initials, matching authors.person_key.
    """
    import authors                       # local: authors imports sheets
    out = {}
    for p in load_roster(path, tab):
        if not p.get("name"):
            continue
        out[authors.person_key(p["name"])] = p.get("org", "")
    return out


def load_roster(path, tab=None):
    """Read an attendee export into [{'name', 'org'}].

    Raises RosterError when there is no organisation column: without one
    nothing can be tied back to a drug's sponsor, and silently returning an
    empty roster would look like "nobody is attending".
    """
    rows = store.xlsx_read(path, tab) or store.xlsx_read(path)
    if len(rows) < 2:
        raise RosterError("the file has no data rows")
    hdr = rows[0]

    org_i = _find_column(hdr, _ROSTER_ORG_COLUMNS)
    if org_i is None:
        raise RosterError(
            "no organisation column found (looked for "
            + ", ".join(_ROSTER_ORG_COLUMNS) + "). "
            "Without one, attendees cannot be matched to a drug's sponsor.")

    name_i = _find_column(hdr, _ROSTER_NAME_COLUMNS)
    first_i = _find_column(hdr, _ROSTER_FIRST_COLUMNS)
    last_i = _find_column(hdr, _ROSTER_LAST_COLUMNS)

    def cell(r, i):
        return r[i].strip() if i is not None and i < len(r) else ""

    # First+Last wins over a single "name" column. The substring fallback in
    # _find_column happily matches "First Name" for the generic "name", which
    # would silently truncate every attendee to their first name.
    split_name = first_i is not None and last_i is not None

    out = []
    for r in rows[1:]:
        org = cell(r, org_i)
        if not org:
            continue
        if split_name:
            name = " ".join(x for x in (cell(r, first_i), cell(r, last_i)) if x)
        else:
            name = cell(r, name_i)
        out.append({"name": name, "org": org})
    if not out:
        raise RosterError("no rows had an organisation filled in")
    return out


def _index_people(pairs):
    """[(org, name)] -> {normalised org key: {'display': str, 'people': set}}."""
    idx = {}
    for org, name in pairs:
        key = _normalise_company(org)
        if not key:
            continue
        e = idx.setdefault(key, {"display": org.strip(), "people": set()})
        if name:
            e["people"].add(name)
    return idx


def roster_from_program(program):
    """Derive a proxy roster from a programme's presenting authors.

    A fallback, and a thin one: it only sees people who submitted an abstract,
    so sponsor leads who attend without presenting are invisible. Against the
    2026 export it covers 5 of 25 candidate sponsors. A real attendee list
    beats it comfortably -- this exists so the feature works before you have
    one.
    """
    pairs = []
    for p in program.get("posters", []):
        name, org = _presenter(p)
        if org:
            pairs.append((org, name))
    return _index_people(pairs)


def build_attendance(year, files=None):
    """What we know about who attends, for one meeting year.

    Returns {'current': index, 'history': {year: index}, 'sources': {...}}.
    An explicit attendee roster beats a roster derived from that year's
    programme; history is every earlier year we have anything for, so it
    deepens on its own as exports accumulate.
    """
    files = config.meeting_files() if files is None else files
    out = {"current": {}, "history": {}, "sources": {}}

    for y in sorted(files):
        entry = files[y]
        index, source = {}, None
        if entry.get("attendees"):
            try:
                index = _index_people([(a["org"], a["name"])
                                       for a in load_roster(entry["attendees"])])
                source = "attendee roster"
            except RosterError as e:
                print(f"  WARNING: ignoring the {y} attendee list — {e}")
        if not index and entry.get("program"):
            index = roster_from_program(load_program(entry["program"]))
            source = "programme presenters"
        if not index:
            continue
        if y == year:
            out["current"] = index
        elif y < year:
            out["history"][y] = index
        else:
            continue                       # a later meeting tells us nothing
        out["sources"][y] = source
    return out


def _lookup_org(sponsor, index):
    """Sponsor string -> entry in an org index, or None.

    Exact normalised key first, then the prefix rule match_contact() uses for
    FDA strings truncated mid-word. Deliberately no fuzzy threshold: a wrong
    name under "who to find" sends someone to introduce themselves to a
    stranger.
    """
    key = _normalise_company(sponsor)
    if not key or not index:
        return None
    if key in index:
        return index[key]
    for k, e in index.items():
        if _prefix_match(key, k):
            return e
    return None


def match_attendance(rec, attendance):
    """(on_roster_count, last_year_summary, who_to_find) for one candidate."""
    sponsor = rec.get("Sponsor") or rec.get("sponsor_raw", "")

    current = _lookup_org(sponsor, attendance.get("current") or {})
    on_roster = len(current["people"]) if current else 0

    notes, people = [], []
    for y in sorted(attendance.get("history", {}), reverse=True):
        e = _lookup_org(sponsor, attendance["history"][y])
        if not e:
            continue
        n = len(e["people"])
        notes.append(f"AM{y}: {e['display']}" + (f" ({n} present)" if n else ""))
        for name in sorted(e["people"]):
            people.append(f"{name} — {e['display']} (AM{y})")

    if current:
        for name in sorted(current["people"]):
            people.insert(0, f"{name} — {current['display']} (registered)")

    return on_roster, ("; ".join(notes) or "not seen"), people


def match_program(rec, program):
    """Find annual-meeting content plausibly connected to this candidate.

    Three independent signals, strongest first:
      drug named in the title or abstract  -> the work is about this drug
      sponsor is the presenting organization -> that company is in the room
    Reported as leads to check, not as facts: an abstract naming a drug does
    not prove the sponsor's clin pharm lead is standing at the poster.
    """
    drug_tokens = _tokens(rec.get("Drug (INN)") or rec.get("ingredient", ""))
    drug_tokens |= _tokens(rec.get("Brand") or rec.get("brand", ""))
    sponsor = rec.get("Sponsor") or rec.get("sponsor_raw", "")
    comp_tokens = _tokens(_normalise_company(sponsor))

    hits = []
    for p in program.get("posters", []):
        title = _col(p, "Poster Presentation Title")
        abstract = _col(p, "Abstract Text")
        _, org = _presenter(p)
        title_tokens = _tokens(title)
        abstract_tokens = _tokens(abstract)
        org_tokens = _tokens(_normalise_company(org))

        if drug_tokens & title_tokens:
            hits.append(("poster", p, "drug in title"))
        elif drug_tokens & abstract_tokens:
            hits.append(("poster", p, "drug in abstract"))
        elif comp_tokens and comp_tokens & org_tokens:
            hits.append(("poster", p, "sponsor presenting"))

    for s in program.get("sessions", []):
        blob = _tokens(" ".join(str(v) for v in s.values()))
        if drug_tokens & blob:
            hits.append(("session", s, "drug named"))

    # strongest signal first
    order = {"drug in title": 0, "drug in abstract": 1, "drug named": 1,
             "sponsor presenting": 2}
    hits.sort(key=lambda h: order.get(h[2], 9))
    return hits


def _hit_detail(kind, row, why):
    if kind == "poster":
        num = _col(row, "Poster Number")
        date = _col(row, "Session Date")
        start = _col(row, "Session Start Time")
        sess = _col(row, "Session Title")
        name, org = _presenter(row)
        who = f"{name} ({org})" if name else org
        title = _col(row, "Poster Presentation Title")[:55]
        return (f"Poster {num} [{why}] — {who} — {sess} {date} {start} — {title}")
    date = _col(row, "Session Date")
    start = _col(row, "Session Start Time")
    room = _col(row, "Room")
    sess = _col(row, "Session")[:55]
    return f"Session [{why}] — {sess} — {date} {start} {room}"


def _score_of(rec):
    """Score, whether the record came from a live scan or a re-read queue row.

    Queue rows are keyed by the sheet's column headings, so 'score' is absent
    and the value lives under 'Novelty'. Reading only 'score' silently sorted
    everything as zero and produced an unranked dossier.
    """
    for k in ("score", "Novelty"):
        v = rec.get(k)
        if v not in (None, ""):
            try:
                return int(float(v))
            except (TypeError, ValueError):
                pass
    return 0


def dossier_rows(candidates, program, attendance=None):
    """Build the dossier body.

    ``program`` is the UPCOMING meeting's export and drives the two presence
    columns; ``attendance`` carries previous years and any registration list.
    Attendance is reported, never scored -- ranking stays editorial, so a drug
    does not outrank a better one because its sponsor travels to meetings.
    """
    attendance = attendance or {"current": {}, "history": {}}
    rows = []
    for i, rec in enumerate(sorted(candidates, key=lambda r: -_score_of(r)), 1):
        hits = match_program(rec, program)
        on_roster, last_year, who = match_attendance(rec, attendance)

        bits = []
        if hits:
            bits.append(f"{len(hits)} lead(s)")
        if on_roster:
            bits.append(f"{on_roster} on roster")
        presence = ", ".join(bits) or "none found"
        detail = " | ".join(_hit_detail(k, r, w) for k, r, w in hits[:3])
        # Cell order must track config.DOSSIER_COLUMNS exactly: who to contact
        # first, then the meeting as one further way of reaching them.
        rows.append([
            str(i),
            rec.get("Drug (INN)") or rec.get("ingredient", ""),
            rec.get("Brand") or rec.get("brand", ""),
            rec.get("Sponsor") or rec.get("sponsor_raw", ""),
            rec.get("Approval date") or rec.get("approval_date", ""),
            rec.get("Modality") or rec.get("modality", ""),
            rec.get("Gap flag") or rec.get("gap", ""),
            str(_score_of(rec)),
            rec.get("Prior review?") or ("yes" if rec.get("prior_review") else "no"),
            rec.get("Clin pharm contacts") or rec.get("clinpharm_contacts", ""),
            rec.get("Contact evidence (PMIDs)") or rec.get("clinpharm_evidence", ""),
            rec.get("Contact", ""),
            presence,
            detail,
            last_year,
            " | ".join(who[:4]),
            rec.get("Candidate authors") or ", ".join(rec.get("candidate_authors", [])),
            rec.get("AE owner", ""),
            "",                                # Attending?  - human fills at the meeting
            "",                                # Comments    - human fills
        ])
    return rows


DOSSIER_TAB = "Dossier"

# Columns a human owns. The machine writes them blank on a first run and must
# never clobber them afterwards -- notes typed at the meeting are the whole
# point of carrying the dossier around.
HUMAN_COLUMNS = ("AE owner", "Attending?", "Comments")


def merge_annotations(rows, path, columns=None, key_column="Drug (INN)"):
    """Carry a previous run's hand-typed columns onto freshly computed rows.

    Matched on drug name, not row position: between runs the ranking changes,
    drugs drop out when their Status is set, and new approvals push in. Merging
    positionally would silently attach one drug's meeting notes to another.

    Machine columns are always taken from ``rows`` -- a re-run is supposed to
    refresh scores and program matches.
    """
    columns = columns or config.DOSSIER_COLUMNS
    prior = store.xlsx_read(path, DOSSIER_TAB)
    if len(prior) < 2:
        return rows

    old_hdr = prior[0]
    if key_column not in old_hdr:
        return rows
    old_key = old_hdr.index(key_column)

    # Only merge columns that exist in BOTH layouts, so an older dossier
    # written before a column was added cannot shift values sideways.
    shared = [c for c in HUMAN_COLUMNS if c in old_hdr and c in columns]
    if not shared:
        return rows

    saved = {}
    for r in prior[1:]:
        if old_key >= len(r):
            continue
        k = r[old_key].strip().lower()
        if not k:
            continue
        saved[k] = {c: (r[old_hdr.index(c)].strip()
                        if old_hdr.index(c) < len(r) else "")
                    for c in shared}

    new_key = columns.index(key_column)
    merged = []
    for r in rows:
        r = list(r)
        note = saved.get(r[new_key].strip().lower()) if new_key < len(r) else None
        if note:
            for c, v in note.items():
                if v:
                    r[columns.index(c)] = v
        merged.append(r)
    return merged


def write_dossier(year, rows, dry_run=True):
    """Write the dossier workbook, keeping any annotations already in it."""
    path = config.dossier_path(year)
    name = config.dossier_name(year)
    if dry_run:
        return path, name
    rows = merge_annotations(rows, path)
    store.xlsx_write(path, {DOSSIER_TAB: [config.DOSSIER_COLUMNS] + rows})
    return path, name


def read_dossier(year):
    """The dossier as (columns, rows), for the invitation step to draft from.

    Read back rather than recomputed, so edits made in the workbook -- a
    reordering, a deleted row, an AE owner filled in -- carry into the drafts.
    """
    rows = store.xlsx_read(config.dossier_path(year, existing=True), DOSSIER_TAB)
    if len(rows) < 2:
        return [], []
    return rows[0], [r for r in rows[1:] if any(c.strip() for c in r)]


def membership_check_rows(candidates, members=None):
    """One row per distinct person the engine surfaced, for manual verification.

    Only people we would actually contact, de-duplicated across drugs, with
    anyone already known to be a member left out -- so the list shrinks each
    time an answer comes back rather than asking the same question twice.
    """
    import authors
    members = members or {}
    seen, rows = {}, []
    for rec in candidates:
        drug = rec.get("Drug (INN)") or rec.get("ingredient", "")
        for p in rec.get("clinpharm_people", []) or []:
            key = authors.person_key(p["name"])
            if key in members:
                continue
            if key in seen:
                if drug and drug not in seen[key]:
                    seen[key].append(drug)
                continue
            seen[key] = [drug] if drug else []
            rows.append([p["name"], p.get("org", ""), "", key])
    by_key = {r[3]: r for r in rows}
    for key, drugs in seen.items():
        if key in by_key:
            by_key[key][3] = ", ".join(drugs[:4])
    return rows


MEMBERSHIP_CHECK_COLUMNS = ["Name", "Company", "ASCPT member?", "Found for (drug)"]


def write_membership_check(path, rows, dry_run=True):
    if dry_run or not rows:
        return len(rows)
    store.xlsx_write(path, {"Check": [MEMBERSHIP_CHECK_COLUMNS] + rows})
    return len(rows)


# ------------------------------------------------------------------ invites
INVITE_TEMPLATE = """<h2>{drug} — {brand}</h2>
<p><b>Sponsor:</b> {sponsor} &nbsp;|&nbsp; <b>Approved:</b> {approved}
&nbsp;|&nbsp; <b>Modality:</b> {modality} &nbsp;|&nbsp; <b>Priority:</b> {score}/100</p>
<p><b>Contact:</b> {contact}<br>
<b>Suggested authors (from PubMed):</b> {authors}<br>
<b>At ASCPT:</b> {ascpt}<br>
<b>Previously at ASCPT:</b> {history}<br>
<b>Who to look for:</b> {who}</p>
<p><i>Why this candidate: {why}</i></p>
<hr>
<p>Dear Dr. ____,</p>
<p>I am writing on behalf of <i>Clinical and Translational Science</i> (CTS), the ASCPT
journal, where I serve as an Associate Editor. We publish an invited series of
<b>Mechanism of Action mini-reviews</b> — concise, single-drug pieces that explain a new
agent's mechanism through a clinical pharmacology and translational lens.</p>
<p>Following the approval of <b>{brand} ({drug})</b>, we would be glad to invite you and
your clinical pharmacology colleagues to contribute a mini-review on {drug}. The format is
short, the audience is the translational and clinical pharmacology community, and previous
entries in the series have been written by the sponsor teams closest to the molecule.</p>
<p>If this is of interest, I would be happy to share the format guidance and agree a
timeline. {ascpt_line}</p>
<p>With best regards,<br>____</p>
<p style="color:#888"><i>Draft generated by the CTS MOA sourcing engine — review and edit
before sending. Nothing is sent automatically.</i></p>
"""


def build_invites_html(rows, year, columns=None):
    """One editable draft per candidate, as a single HTML file.

    ``columns`` comes from the dossier workbook's own header rather than from
    config, so a column the user inserted by hand does not shift every field
    in the drafts by one.
    """
    parts = [f"<h1>MOA invitation drafts — {year}</h1>",
             "<p>Generated from the ASCPT recruiting dossier. Every draft below is a "
             "starting point: check the contact, confirm the author list, and edit the "
             "wording before sending. <b>Nothing here has been sent.</b></p>"]
    cols = columns or config.DOSSIER_COLUMNS
    for r in rows:
        d = dict(zip(cols, r))
        ascpt = d.get("ASCPT presence", "none found")
        detail = d.get("Poster / session detail", "")
        history = d.get("Last year at ASCPT", "") or "not seen"
        who = d.get("Who to find", "")
        # Offer to meet if they are coming, or if their team came before --
        # a returning group is a reasonable bet even before the programme
        # for the next meeting exists.
        expected = bool(detail) or bool(who) or history != "not seen"
        ascpt_line = ("I will be at the ASCPT Annual Meeting and would be glad to discuss "
                      "in person." if expected else "")
        parts.append(INVITE_TEMPLATE.format(
            drug=html.escape(d.get("Drug (INN)", "")),
            brand=html.escape(d.get("Brand", "") or "—"),
            sponsor=html.escape(d.get("Sponsor", "")),
            approved=html.escape(d.get("Approval date", "")),
            modality=html.escape(d.get("Modality", "")),
            score=html.escape(d.get("Novelty", "")),
            contact=html.escape(d.get("Contact", "") or "NEEDS LOOKUP"),
            authors=html.escape(d.get("Candidate authors", "") or "none found"),
            ascpt=html.escape(f"{ascpt}. {detail}" if detail else ascpt),
            history=html.escape(history),
            who=html.escape(who or "no one identified yet"),
            why=html.escape(d.get("Gap flag", "") or "new agent"),
            ascpt_line=ascpt_line))
    # The charset declaration is not optional: without it the em dashes and
    # accented author names in these drafts render as mojibake when the file
    # is opened in Word.
    return ("<!DOCTYPE html><html><head><meta charset=\"utf-8\">"
            f"<title>MOA invitation drafts {year}</title>"
            "<style>body{font-family:Calibri,Helvetica,sans-serif;font-size:11pt;"
            "max-width:44em;margin:2em auto;line-height:1.45}"
            "h1{font-size:18pt}h2{font-size:13pt;margin-top:2em}"
            "hr{border:0;border-top:1px solid #ccc;margin:1.2em 0}</style></head><body>"
            + "\n".join(parts) + "</body></html>")


def write_invites(year, rows, columns=None, dry_run=True):
    """Write the drafts to one file, overwritten in place on every run."""
    path = config.invites_path(year)
    if dry_run:
        return path
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(build_invites_html(rows, year, columns))
    return path
