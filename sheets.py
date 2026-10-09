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
import gaps
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


def load_roster_rows(rows, require_rows=True):
    """The parsing half of load_roster, for rows already in memory.

    Split out so load_member_records can parse one row at a time and keep its
    records aligned with the sheet. Zipping the two sequences did not work:
    load_roster drops rows with no organisation, so the pairing silently
    shifted every record after the first blank.
    """
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
    if not out and require_rows:
        raise RosterError("no rows had an organisation filled in")
    return out


def load_roster(path, tab=None):
    """Read an attendee export into [{'name', 'org'}].

    Raises RosterError when there is no organisation column: without one
    nothing can be tied back to a drug's sponsor, and silently returning an
    empty roster would look like "nobody is attending".
    """
    return load_roster_rows(store.xlsx_read(path, tab) or store.xlsx_read(path))


def load_member_records(path, tab=None):
    """An ASCPT membership export -> [{'name', 'org', 'discipline'}].

    `load_members()` keys people to their organisation, which answers "is this
    author an ASCPT member?". The outreach list asks the opposite question,
    "which members work at this company?", so the records are kept whole here
    and the discipline comes with them when the export carries one.
    """
    rows = store.xlsx_read(path, tab) or store.xlsx_read(path)
    if len(rows) < 2:
        raise RosterError("the file has no data rows")
    disc_i = _find_column(rows[0], config.MEMBER_DISCIPLINE_COLUMNS)
    mail_i = _find_column(rows[0], config.MEMBER_EMAIL_COLUMNS)

    def cell(row, i):
        return row[i].strip() if i is not None and i < len(row) else ""

    out = []
    # load_roster drops any row with no organisation, so the two sequences
    # would drift apart if they were zipped. It is re-run per row here instead.
    for row in rows[1:]:
        people = load_roster_rows([rows[0], row], require_rows=False)
        if not people:
            continue
        person = dict(people[0])
        person["discipline"] = cell(row, disc_i)
        person["email"] = cell(row, mail_i)
        out.append(person)
    return out


def is_clinpharm(discipline):
    """Does this stated discipline put someone in clinical pharmacology?

    "clinical pharmacy" is deliberately not a match. It is a different
    profession, and an invitation to write a mechanism review that lands on a
    hospital pharmacist is a wasted approach and a slightly insulting one.
    """
    d = (discipline or "").lower()
    if not d:
        return False
    if "clinical pharmacy" in d and "pharmacolog" not in d:
        return False
    return any(t in d for t in config.CLINPHARM_DISCIPLINES)


def members_at_company(sponsor, records):
    """-> (names cell, emails cell, how_matched) for one company's row.

    Every match is listed, numbered, and the two cells are index-aligned so
    line 3 of one is the address of line 3 of the other. A company can have
    several clinical pharmacologists and choosing between them is the editor's
    judgement, not the engine's.

    Company matching is `authors._is_sponsor`, which is already the tested one:
    it knows Verastem is not Vera Therapeutics, and that an affiliation string
    carries a city and a country the sponsor field does not.
    """
    import authors                       # local: authors imports sheets
    if not sponsor or not records:
        return "", "", ""
    at = [r for r in records if authors._is_sponsor(r.get("org", ""), sponsor)]
    if not at:
        return "", "", "no members at this company"

    # Filter to clinical pharmacology where the export says, but do not hide
    # the rest: the team asked to see everyone at the company, with the
    # non-matching disciplines labelled, so the judgement stays theirs.
    # Clinical pharmacologists sort first and unparenthesised, so a regulatory
    # or commercial contact can never be mistaken for one.
    stated = [r for r in at if r.get("discipline")]
    if stated:
        cp = [r for r in at if is_clinpharm(r.get("discipline", ""))]
        other = [r for r in at if r not in cp]
        how = (f"{len(cp)} in clinical pharmacology"
               if cp else "none in clinical pharmacology")
        if other:
            how += f", {len(other)} other discipline(s) listed and labelled"
    else:
        cp, other = at, []
        how = "ASCPT member(s) at this company; export states no discipline"

    # Deduplicated on the name, keeping the first address seen for each. A
    # directory export repeats a person across communities, and listing someone
    # three times reads as three different people at the same company.
    seen, labels = {}, {}
    for r in list(cp) + list(other):
        nm = (r.get("name") or "").strip()
        if not nm or nm in seen:
            continue
        seen[nm] = (r.get("email") or "").strip()
        # A parenthesised discipline marks someone whose stated field is NOT
        # clinical pharmacology. An unparenthesised name is a confirmed match.
        labels[nm] = ("" if r in cp or not r.get("discipline")
                      else f" ({r['discipline']})")
    if not seen:
        return "", "", f"{len(at)} member(s) here, none with a usable name"
    # Sorted within each group, clin pharm group first, so the order is stable
    # between runs and the two cells stay in step.
    people = [(nm, seen[nm]) for nm in
              sorted(seen, key=lambda n: (bool(labels[n]), n.lower()))]

    def listed(values):
        if len(values) == 1:
            return values[0]
        return "\n".join(f"{i}. {v or '-'}" for i, v in enumerate(values, 1))

    how += f"; {len(people)} listed"
    if not any(e for _n, e in people):
        how += "; export carries no email column"
    return (listed([n + labels[n] for n, _e in people]),
            listed([e for _n, e in people]), how)


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


def merge_annotations(rows, path, columns=None, key_column="Drug name",
                      tab=None):
    """Carry a previous run's hand-typed columns onto freshly computed rows.

    Matched on drug name, not row position: between runs the ranking changes,
    drugs drop out when their Status is set, and new approvals push in. Merging
    positionally would silently attach one drug's meeting notes to another.

    Machine columns are always taken from ``rows`` -- a re-run is supposed to
    refresh scores and program matches.
    """
    columns = columns or config.HOTLIST_COLUMNS
    prior = store.xlsx_read(path, tab or HOTLIST_TAB)
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


# write_dossier() removed 2026-10-08: the hot list is the single outreach output.

# read_dossier() removed 2026-10-08: the hot list is now the single outreach
# output. See build_hotlist().

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
INVITE_TEMPLATE = """<h2>{drug}</h2>
<p><b>Company:</b> {sponsor} &nbsp;|&nbsp; <b>Approved:</b> {approved}
&nbsp;|&nbsp; <b>Application:</b> {appl}</p>
<p><b>Contact:</b> {contact} &nbsp;|&nbsp; <b>AE owner:</b> {owner}</p>
<p><b>Mechanism of action (from the label):</b> {moa}</p>
<p><b>Approved indication:</b> {indication}</p>
<hr>
<p>Dear Dr. ____,</p>
<p>I am writing on behalf of <i>Clinical and Translational Science</i> (CTS), the ASCPT
journal, where I serve as an Associate Editor. We publish an invited series of
<b>Mechanism of Action mini-reviews</b>: concise, single-drug pieces that explain a new
agent's mechanism through a clinical pharmacology and translational lens.</p>
<p>Following the approval of <b>{drug}</b>, we would be glad to invite you and your
clinical pharmacology colleagues to contribute a mini-review. The format is short, the
audience is the translational and clinical pharmacology community, and previous entries in
the series have been written by the sponsor teams closest to the molecule.</p>
<p>If this is of interest, I would be happy to share the format guidance and agree a
timeline. {ascpt_line}</p>
<p>With best regards,<br>____</p>
<p style="color:#888"><i>Draft generated by the CTS MOA sourcing engine. Review and edit
before sending. Nothing is sent automatically.</i></p>
"""


def build_invites_html(rows, year, columns=None):
    """One editable draft per candidate, as a single HTML file.

    ``columns`` comes from the outreach workbook's own header rather than from
    config, so a column inserted by hand does not shift every field in the
    drafts by one.

    The fields are the outreach list's own nine columns. They used to be the
    retired dossier's ("Drug (INN)", "Sponsor", "Contact", "Candidate
    authors"), none of which exist on this sheet, so every draft rendered with
    an empty drug name, an empty company and a contact reading NEEDS LOOKUP
    even where an AE had filled one in.
    """
    parts = [f"<h1>MOA invitation drafts, {year}</h1>",
             "<p>Generated from the outreach list. Every draft below is a starting "
             "point: check the contact, confirm the recipient, and edit the wording "
             "before sending. <b>Nothing here has been sent.</b></p>"]
    cols = columns or config.HOTLIST_COLUMNS
    for r in rows:
        d = dict(zip(cols, r))
        # Either contact column counts: both are a human naming a real person.
        contact, needs_choice = letter_contact(d)
        if needs_choice:
            contact += "   [several members listed; choose one before sending]"
        owner = next((v for h, v in d.items()
                      if _human_col(h) == OWNER_COL and (v or "").strip()), "")
        # The meeting line is only offered when a human has named someone to
        # approach. Without the dossier there is no ASCPT attendance signal, and
        # promising to find them in person on no evidence is worse than silence.
        ascpt_line = ("I expect to be at the ASCPT Annual Meeting and would be glad "
                      "to discuss in person." if contact else "")
        parts.append(INVITE_TEMPLATE.format(
            drug=html.escape(d.get("Drug name", "")),
            sponsor=html.escape(d.get("Company name", "")),
            approved=html.escape(d.get("Approval date", "")),
            appl=html.escape(d.get("NDA/BLA number", "") or "not listed"),
            contact=html.escape(contact or "NEEDS LOOKUP"),
            owner=html.escape(owner or "unassigned"),
            moa=html.escape(d.get("MOA", "") or "not stated in the label"),
            indication=html.escape(d.get("Indication", "") or "not listed"),
            ascpt_line=ascpt_line))
    # The charset declaration is not optional: without it the accented author
    # names and en dashes in these drafts render as mojibake in Word.
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


# ---------------------------------------------------------------- hot list
def _hotlist_methodology(report, months, top, considered, shown, listed=None,
                         member_note=""):
    """Sheet 2. Written so a weight can be argued with, not just trusted."""
    L = lambda *c: list(c)
    rows = [L("How this list was built", "")]
    rows += [
        L("", ""),
        L("WHAT THE LIST IS", ""),
        L("Purpose", "Find clinical pharmacology contacts at the companies behind "
                     "novel mechanisms the MOA mini-review series has not covered."),
        L("Who fills it in", "The AE team owns the last four columns: 'ClinPharm "
                             "ASCPT Member', 'ClinPharm ASCPT Member email', "
                             "'Clin pharm contact' and 'AE owner'. All four ship "
                             "empty and all four are carried forward on every "
                             "later run."),
        L("Why they ship empty", "Pre-filling a contact with a paper's author would "
                                 "be worse than blank: that person is often not the "
                                 "right one to approach."),
        L("Why the ASCPT pair is manual", "The engine does not query the ASCPT "
                                          "Membership Directory and stores no "
                                          "credential for it. An AE looks a person "
                                          "up and types the name in. The only "
                                          "automated route is an export someone "
                                          "entitled to the data supplies, imported "
                                          "with 'roster --kind members'."),
        L("The ASCPT member pair", "'ClinPharm ASCPT Member' and 'ClinPharm ASCPT "
                                   "Member email' are filled in by the team. They "
                                   "are index-aligned: line 3 of one is the address "
                                   "of line 3 of the other, and a member with no "
                                   "address shows as '-' so a gap cannot shift the "
                                   "lines below it onto the wrong person."),
        L("Typed values always win", "If an ASCPT membership export has been "
                                     "imported, the engine fills that pair too, but "
                                     "ONLY where BOTH cells came back empty. It "
                                     "never overwrites a name someone typed, and it "
                                     "fills the two cells together or not at all, "
                                     "since filling one would break the alignment "
                                     "between them."),
        L("Where that list comes from", "An ASCPT membership export the team "
                                        "imports with 'roster --kind members', "
                                        "stored at input/" + config.MEMBERS_FILE +
                                        ". The engine does not query the ASCPT "
                                        "directory itself."),
        L("Membership list status", member_note or "not checked"),
        L("Company matching", "authors._is_sponsor, the same test used for author "
                              "affiliations. It knows Verastem Oncology is not "
                              "Vera Therapeutics, which a substring match does not."),
        L("Clinical pharmacology filter", "Matched on the stated discipline against "
                                          + str(len(config.CLINPHARM_DISCIPLINES)) +
                                          " terms in config.CLINPHARM_DISCIPLINES. "
                                          "'Clinical pharmacy' is deliberately NOT "
                                          "a match: different profession. Where the "
                                          "export states no discipline, every "
                                          "member at the company is listed and the "
                                          "status row above says so."),
        L("Re-runs are safe", "Anything typed into those four columns is carried "
                              "forward onto the next run, keyed on the drug rather "
                              "than the row, because the ranking changes between "
                              "runs. The headers are matched loosely, so rewording "
                              "one in Excel does not silently lose a column."),
        L("", ""),
        L("1. GAP ANALYSIS  (decides what is on the list)", ""),
        L("Run", "Live from PubMed on every invocation, never from a stored list."),
        L("Query", gaps.SERIES_QUERY),
        L("Series test", "Drug before the colon, the convention after it. Excludes "
                         "research articles that merely mention mechanism of action."),
        L("Published test", "The paper must have a PMC release date. An accepted "
                            "paper assigned to a future issue is not published; this "
                            "is the rule that caught fruquintinib being counted early."),
        L("Published papers found", str(report["published"])),
        L("Two axes", "Molecular format (small molecule, antibody, "
                      "oligonucleotide) and target class (kinase inhibitor, "
                      "GLP-1 receptor agonist) are separate questions and are "
                      "counted separately. They used to be one flat set, which "
                      "made the counts incomparable: of the published papers, "
                      "18 were filed by format and 4 by target class."),
        L("Format counts roll up", "An antibody-drug conjugate is an antibody; a "
                                   "kinase inhibitor is a small molecule. Each "
                                   "paper increments every level of its branch. "
                                   "Without the roll-up small molecule read 10 "
                                   "when it was 14 and antibody read 3 when it "
                                   "was 7, so a modality could be called "
                                   "uncovered that the series had published on "
                                   "four times."),
        L("Status", report["note"]),
        L("Formats covered (rolled up)",
          ", ".join(f"{k} ({v})" for k, v in
                    sorted(report["coverage"].items(),
                           key=lambda kv: -kv[1])) or "-"),
        L("Target classes covered",
          ", ".join(f"{k} ({v})" for k, v in
                    sorted((report.get("targets") or {}).items(),
                           key=lambda kv: -kv[1])) or "-"),
        L("Open gaps", ", ".join(sorted(report["gap_categories"])) or "none"),
        L("Important", "A gap is purely a mechanism the series has not covered. "
                       "Whether a contact is already known is NOT part of the test, "
                       "because finding those contacts is the point of the sheet."),
        L("", ""),
        L("2. HOT SCORE  (decides the order only)", ""),
        L("Interesting mechanism", "named coverage gap still open +40; "
                                   "unpublished molecular format +12; "
                                   "unpublished target class +8; first approval "
                                   "of this moiety +20; known moiety by a new "
                                   "route +8. The three gap tiers are "
                                   "exclusive, strongest first: a flat test let "
                                   "'MRI contrast agent' outrank a real CAR-T "
                                   "gap the team had named."),
        L("News-worthy", "FDA priority review +12; orphan designation +8. Both come "
                         "from the Drugs@FDA bulk files (Submissions.ReviewPriority "
                         "and SubmissionPropertyType)."),
        L("Attention", "PubMed papers in the last 24 months: >=100 +20, >=40 +14, "
                       ">=15 +8, any +3. ClinicalTrials.gov studies: >=20 +10, "
                       ">=5 +6, any +2."),
        L("Deliberately excluded", "FAERS adverse-event report counts. They measure "
                                   "accumulated patient exposure, so they rank older "
                                   "drugs highest and say almost nothing about a drug "
                                   "approved in the last three years."),
        L("", ""),
        L("3. TIME WINDOW", ""),
        L("Window used", f"{months} months before today"),
        L("Why 36 and not 12", "Measured from the series itself: joining the published "
                               "reviews to their Drugs@FDA approval dates gives a "
                               "median approval-to-review lag of 2.8 years "
                               "(quartiles 2.0 / 2.8 / 4.3). 25% are reviewed within "
                               "two years, 55% within three. A 12-month window would "
                               "catch only the three fastest the series has managed."),
        L("", ""),
        L("4. SOURCES", ""),
        L("Approvals (CDER)", "Drugs@FDA bulk relational files. Primary source."),
        L("Approvals (CBER)", "Purple Book monthly change report. Required, not "
                              "optional: Drugs@FDA omits CBER entirely. Of nine CBER "
                              "products checked (Aucatzyl, Kymriah, Yescarta, "
                              "Carvykti, Zolgensma, Luxturna, Hemgenix, Comirnaty, "
                              "StrataGraft) all nine are absent from Drugs@FDA, and "
                              "only 73 of the 125xxx BLA range appear at all. Those "
                              "are exactly the cell & gene, CAR-T and vaccine gaps."),
        L("MOA and indications", "DailyMed SPL, from the approved label. "
                                 "LOINC 43679-0 and 34067-9."),
        L("Prior review check", "PubMed, per drug."),
        L("Not used", "openFDA drug/drugsfda: it matches conditions across the whole "
                      "application document, so a date-plus-class query returns "
                      "approvals from unrelated years."),
        L("", ""),
        L("5. THIS RUN", ""),
        L("Gap candidates found", str(considered)),
        L("Rows shown", f"{shown} (capped; the analysis considered all "
                        f"{considered})"),
        L("Priority block", f"top {top} by hot score, highlighted on sheet 1"),
        L("Generated", datetime.date.today().isoformat()),
    ]
    if listed:
        rows += [
            L("", ""),
            L("6. HOW EACH LISTED DRUG WAS CLASSIFIED", ""),
            L("Class source", "FDA's Established Pharmacologic Class via NLM "
                              "RxClass where the label carries one, the "
                              "engine's INN-stem table otherwise. The column "
                              "says which, because a stem-based guess must not "
                              "be read as an FDA assignment."),
            L("Drug", "Format | Target class | Class source | Gap axis"),
        ]
        for r in sorted(listed, key=lambda x: -int(x.get("hot_score") or 0)):
            rows.append(L(
                r.get("ingredient_raw") or r.get("ingredient", ""),
                " | ".join([r.get("modality_format", "") or "-",
                            r.get("target_class", "") or "-",
                            r.get("class_source", "") or "not classified",
                            r.get("gap_tier", "") or "added by hand"])))
    return rows


HOTLIST_TAB = "Outreach list"
CONTACT_COL = "Clin pharm contact"
OWNER_COL = "AE owner"
MEMBER_COL = "ClinPharm ASCPT Member"
MEMBER_EMAIL_COL = "ClinPharm ASCPT Member email"

# All four belong to the team, in sheet order, and all four carry forward. The
# member pair was engine-only until the team decided to fill it in by hand, at
# which point carrying it forward stopped being optional: a run that
# regenerated those cells would hand back a blank sheet and the typing would be
# gone. The engine still fills the pair where a cell comes back EMPTY and a
# membership export exists, but a typed value always wins.
HUMAN_COLS = [MEMBER_COL, MEMBER_EMAIL_COL, CONTACT_COL, OWNER_COL]

# The columns that can name a recipient, in preference order. "Clin pharm
# contact" comes first because it is an AE's deliberate single choice, where the
# member column may legitimately hold several candidates.
CONTACT_SOURCES = [CONTACT_COL, MEMBER_COL]


def letter_contact(d):
    """-> (name, needs_narrowing) for one row's header->value mapping.

    `needs_narrowing` is True when the only value available is a numbered list
    of several members. A draft addressed to "1. Amita Joshi 2. Rong Shi" is
    worse than one addressed to a blank, so the letter says a choice is still
    needed rather than silently taking the first name.
    """
    by_col = {}
    for h, v in d.items():
        col = _human_col(h)
        if col in CONTACT_SOURCES and (v or "").strip():
            by_col.setdefault(col, v.strip())
    for col in CONTACT_SOURCES:
        val = by_col.get(col)
        if val:
            return val, "\n" in val
    return "", False


def _appl_label(rec):
    """'NDA 219627' / 'BLA 761530', or blank.

    A Purple Book CBER record has no Drugs@FDA application number, so it must
    render empty rather than inventing one. Showing a wrong number on an
    outreach sheet is worse than showing none.
    """
    no = (rec.get("appl_no") or "").strip()
    kind = (rec.get("appl_type") or "").strip().upper()
    if not no:
        return ""
    if not kind:
        kind = "BLA" if no.startswith(("125", "761")) else "NDA"
    return f"{kind} {no}"


def _norm_header(h):
    return re.sub(r"[^a-z]+", " ", (h or "").lower()).strip()


def _human_col(header):
    """Which of the team's four columns is this header, if any?

    Matched loosely on purpose. These headers get reworded by hand in Excel,
    and a carry-forward keyed on an exact string fails silently: the next run
    writes a blank sheet and the typed-in names are gone.

    **The test order is load-bearing**, because the headers overlap on
    substrings. "ClinPharm ASCPT Member email" contains "ascpt", and the
    retired "ClinPharm contact from ASCPT Membership Directory" contains both
    "ascpt" and "contact". So email is tested before ASCPT, and ASCPT before
    contact. Putting contact first sends the retired header to the wrong
    column and silently moves one person's name onto another row's meaning.

    That retired header maps onto MEMBER_COL deliberately: it is semantically
    the same thing, a name someone looked up in the ASCPT directory, so a value
    already typed under the old heading reappears in the new column instead of
    being dropped when the column went away.
    """
    h = re.sub(r"[^a-z]+", " ", (header or "").lower()).strip()
    if not h:
        return None
    if "ascpt" in h and ("email" in h or "mail" in h):
        return MEMBER_EMAIL_COL
    if "ascpt" in h:
        return MEMBER_COL
    if "owner" in h:
        return OWNER_COL
    if "contact" in h:
        return CONTACT_COL
    return None


def _team_cells(prior, sponsor, members):
    """The team's four columns for one row, in HUMAN_COLS order.

    Precedence, which is the whole point of this function: a value the team
    typed wins over anything the engine can derive. The membership export only
    reaches a cell that carried forward EMPTY.

    The member pair is filled or left alone TOGETHER. Filling a name from the
    export while keeping a typed address, or the reverse, would break the index
    alignment between the two cells and put one person's name against
    another's address.
    """
    out = {c: (prior.get(c) or "") for c in HUMAN_COLS}
    if members and not (out[MEMBER_COL] or out[MEMBER_EMAIL_COL]):
        names, emails, _how = members_at_company(sponsor, members)
        out[MEMBER_COL], out[MEMBER_EMAIL_COL] = names, emails
    return [out[c] for c in HUMAN_COLS]


def _member_records():
    """The imported ASCPT membership export, or none. -> (records, note)

    Optional in every direction. No file, an unreadable file or a file with no
    organisation column all return an empty list and a note saying why, because
    a blank member column for a bad reason must be visible rather than look
    like "no members at any of these companies".
    """
    path = config.members_file()
    if not os.path.exists(path):
        return [], ("no ASCPT membership list imported, so '" + MEMBER_COL
                    + "' is whatever the team has typed "
                      "(import one with: roster --kind members)")
    try:
        recs = load_member_records(path)
    except Exception as e:
        return [], f"could not read {os.path.basename(path)} ({e})"
    if not recs:
        return [], f"{os.path.basename(path)} has no usable rows"
    n_disc = sum(1 for r in recs if r.get("discipline"))
    note = f"{len(recs)} ASCPT member(s) imported"
    note += (f", {n_disc} with a stated discipline" if n_disc
             else ", none with a stated discipline so no clin pharm filter applies")
    return recs, note


def _prior_contacts(path):
    """{drug INN: {column: value}} from an earlier outreach list.

    Keyed on the drug, never on row position: the ranking changes between runs,
    so a positional carry-forward would move one drug's contact onto another.

    All three of the team's columns are carried, because all three are typed in
    by hand and none of them can be regenerated. The ASCPT directory column in
    particular represents a lookup someone did one row at a time.
    """
    out = {}
    if not path or not os.path.exists(path):
        return out
    try:
        rows = store.xlsx_read(path, HOTLIST_TAB)
    except Exception:
        return out
    if not rows:
        return out
    hdr = rows[0]
    try:
        di = hdr.index("Drug name")
    except ValueError:
        return out
    cols = {i: _human_col(h) for i, h in enumerate(hdr) if _human_col(h)}
    if not cols:
        return out
    for r in rows[1:]:
        if len(r) <= di:
            continue
        drug = (r[di] or "").strip()
        if not drug:
            continue
        vals = {name: (r[i] or "").strip()
                for i, name in cols.items()
                if i < len(r) and (r[i] or "").strip()}
        if vals:
            out.setdefault(_contact_key(drug), {}).update(vals)
    return out


def _contact_key(drug_cell):
    """Match on the INN inside 'BRAND (ingredient)'.

    The brand can change between runs and the display string carries both, so
    the stable part is the ingredient in parentheses.
    """
    m = re.search(r"\(([^)]*)\)\s*$", drug_cell or "")
    base = (m.group(1) if m else drug_cell or "")
    return re.sub(r"[^a-z0-9]+", " ", base.lower()).strip()


def build_hotlist(records, report, months, top, total=None, path=None, verbose=True):
    """Write the two-sheet hot-list workbook. -> path

    Rows are companies rather than drugs, because the question being asked is
    "does anyone know someone at Autolus?". The priority block is the top `top`
    by hot score; everything else follows below a second banner, still present.
    """
    path = path or config.hotlist_path()
    # Carry forward whatever the team has already filled in: from today's file
    # if this is a re-run, otherwise from the most recent earlier one.
    carried = _prior_contacts(path) or _prior_contacts(config.latest_hotlist_path())
    # The membership export, if the user has imported one. Entirely optional:
    # with no file the tenth column is blank and everything else is unchanged.
    members, member_note = _member_records()
    # Hand-added drugs ADD to the priority block; they do not take one of its
    # places. A typed drug carries an editorial judgement the score cannot, so
    # letting it displace a high-scoring candidate would lose information.
    manual = [r for r in records if r.get("manual")]
    auto = [r for r in records if not r.get("manual")]
    ranked = sorted(auto, key=lambda r: -int(r.get("hot_score") or 0))
    total = total or len(ranked)
    ranked = ranked[:total]                 # the sheet is capped, not the analysis
    pri = ranked[:top] if top else ranked
    rest = ranked[top:] if top else []

    header = list(config.HOTLIST_COLUMNS)
    rows, highlight, banner = [header], set(), set()

    def block(label, items, priority=False):
        nonlocal rows
        if not items:
            return
        rows.append([label] + [""] * (len(header) - 1))
        banner.add(len(rows))
        # Drug leads the sheet now, so rows are in plain hot-score order rather
        # than grouped under a company heading.
        for r in sorted(items, key=lambda x: -int(x.get("hot_score") or 0)):
            inds = r.get("indications") or []
            drug = f"{r.get('brand','')} ({r.get('ingredient_raw','')})".strip()
            rows.append([
                drug,
                r.get("moa", ""),
                "\n".join(f"{n}. {x}" for n, x in enumerate(inds, 1)) if len(inds) > 1
                else (inds[0] if inds else ""),
                _appl_label(r),
                r.get("approval_date", ""),
                r.get("sponsor_raw", ""),
                # The team's four columns, in sheet order. A value typed on an
                # earlier run always wins; the membership export only fills a
                # cell that came back empty, and with no export imported these
                # are simply whatever the team last typed.
                *_team_cells(carried.get(_contact_key(drug), {}),
                             r.get("sponsor_raw", ""), members),
            ])
            if priority:
                highlight.add(len(rows))

    if manual:
        block(f"ADDED BY HAND  -  {len(manual)} drug(s) you asked for", manual,
              priority=True)
    block(f"TOP {len(pri)} BY HOT SCORE", pri, priority=True)
    if rest:
        rows.append([""] * len(header))
        block(f"ALSO UNCOVERED  -  {len(rest)} further candidates, not prioritised",
              rest)

    tabs = {HOTLIST_TAB: rows,
            "Methodology": _hotlist_methodology(report, months, top,
                                                len(records), len(ranked),
                                                listed=manual + ranked,
                                                member_note=member_note)}
    store.xlsx_write(path, tabs, highlight={HOTLIST_TAB: highlight},
                     banner={HOTLIST_TAB: banner})
    if verbose:
        # Report every team column that came back with something in it. The
        # count is the team's own work surviving the run, so it is worth
        # printing rather than leaving them to scroll and check.
        idx = [header.index(c) for c in HUMAN_COLS]
        kept = sum(1 for row in rows[1:]
                   if any(i < len(row) and (row[i] or "").strip() for i in idx))
        hand = f"{len(manual)} by hand + " if manual else ""
        print(f"  outreach list: {hand}{len(pri)} priority + {len(rest)} further "
              f"= {len(manual) + len(ranked)} rows")
        if kept:
            print(f"    carried forward {kept} row(s) the team had filled in")
        mi = header.index(MEMBER_COL)
        hit = sum(1 for row in rows[1:]
                  if mi < len(row) and (row[mi] or "").strip())
        print(f"    {member_note}"
              + (f"; {hit} row(s) have a member named" if members else ""))
        print(f"    -> {path}")
    return path
