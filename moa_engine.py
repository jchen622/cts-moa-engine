#!/usr/bin/env python3
"""CTS MOA sourcing engine.

Finds newly approved novel agents that deserve a Mechanism of Action
mini-review, keeps them in a queue, and once a year builds a recruiting
dossier timed to the ASCPT Annual Meeting.

Everything is a local file. Nothing runs on a schedule unless you explicitly
install one, and nothing is ever emailed. See README.md.
"""
import argparse
import datetime
import os
import shutil
import sys
import traceback

import classify
import config
import enrich
import scheduler
import sheets
import sources
import store


def _log(msg=""):
    print(msg, flush=True)


def _iso_days_ago(days):
    return (datetime.date.today() - datetime.timedelta(days=days)).isoformat()


def _meeting_year(explicit=None):
    return explicit or config.meeting_year()


# ------------------------------------------------------------------ setup
def cmd_setup(args):
    """Optional configuration. The defaults work, so this can be skipped."""
    s = config.load_settings(required=False)
    _log("CTS MOA sourcing engine — setup")
    _log("-" * 60)
    _log("Every question is optional: the defaults in [brackets] already work.")
    _log("Press Enter to keep them.\n")

    def ask(key, prompt, default=""):
        cur = s.get(key) or default
        val = input(f"{prompt}\n  [{cur or 'not set'}]: ").strip()
        return val or cur

    s["output_dir"] = ask(
        "output_dir",
        "Folder for the candidate queue, dossier and invitation drafts",
        config.output_dir())
    s["contacts_file"] = ask(
        "contacts_file",
        "Clinical pharmacology contact grid (.xlsx: company, head, contact)",
        config.contacts_file())
    s["input_dir"] = ask(
        "input_dir",
        "Folder holding the contact grid and the ASCPT meeting exports",
        config.input_dir())
    s["owner_initials"] = ask("owner_initials",
                              "Your initials, used in generated drafts", "")
    s["ncbi_email"] = ask(
        "ncbi_email",
        "Your email, sent to NCBI so they can contact you about automated\n"
        "  queries (optional; leave blank and only a tool name is sent)")

    # No question for the programme file any more: meeting exports are found by
    # name ("ascpt program 2027.xlsx", "ascpt attendees 2027.xlsx"), so there is
    # nothing to configure and no year to keep in sync by hand.

    config.save_settings(s)
    _log(f"\nSaved to {config.SETTINGS_PATH}")
    _log("Next:  ./moa-engine check      (verifies everything, writes nothing)")
    return 0


def cmd_check(args):
    """Verify every dependency without writing anything."""
    ok = True
    _log("Checking the engine. Nothing will be written.\n")

    _log("1. Folders and input files")
    # 'check' promises to write nothing, so probe the nearest folder that
    # already exists rather than creating the output folder as a side effect.
    out = config.output_dir()
    probe_at = out
    while probe_at and not os.path.isdir(probe_at):
        parent = os.path.dirname(probe_at)
        if parent == probe_at:
            break
        probe_at = parent
    if os.access(probe_at, os.W_OK):
        exists = "" if os.path.isdir(out) else " (will be created on first run)"
        _log(f"   OK  output folder is writable: {out}{exists}")
    else:
        _log(f"   FAIL  cannot write to {probe_at}")
        ok = False

    contacts = config.contacts_file()
    if os.path.exists(contacts):
        n = len(sheets.load_contacts())
        _log(f"   OK  contact grid: {n} company row(s) — {contacts}")
        if n == 0:
            _log("       (readable but empty — candidates will say NEEDS LOOKUP)")
    else:
        _log(f"   --  no contact grid at {contacts}")
        _log("       not fatal; every candidate will come through as NEEDS LOOKUP")

    _log("\n2. ASCPT meeting files")
    upcoming = _meeting_year()
    files = config.meeting_files()
    if not files:
        _log(f"   --  none found in {config.input_dir()}")
    for y in sorted(files, reverse=True):
        bits = []
        if files[y]["program"]:
            p = sheets.load_program(files[y]["program"])
            bits.append(f"programme ({len(p['posters'])} posters, "
                        f"presenter names "
                        f"{'present' if p['has_authors'] else 'ABSENT'})")
        if files[y]["attendees"]:
            try:
                bits.append(f"attendee list ({len(sheets.load_roster(files[y]['attendees']))} people)")
            except sheets.RosterError as e:
                bits.append(f"attendee list UNREADABLE — {e}")
        tag = "  <- upcoming meeting" if y == upcoming else ""
        _log(f"   OK  {y}: {', '.join(bits)}{tag}")

    if upcoming not in files or not files[upcoming]["program"]:
        _log(f"   --  no programme for {upcoming} yet, so there are no poster or")
        _log(f"       session leads for the upcoming meeting. Earlier years are")
        _log(f"       still used as attendance history.")
    if upcoming not in files or not files[upcoming]["attendees"]:
        _log(f"   --  no attendee list for {upcoming} (optional). Add one with:")
        _log(f"         ./moa-engine roster --file <path> --go")

    _log("\n3. FDA sources")
    try:
        recs = sources.drugs_at_fda_nmes(since=_iso_days_ago(120), verbose=False)
        _log(f"   OK  Drugs@FDA reachable; {len(recs)} NME approvals in 120 days")
    except Exception as e:
        _log(f"   FAIL  Drugs@FDA: {e}")
        ok = False
    try:
        urls = sources.purple_book_urls()
        _log(f"   OK  Purple Book reachable; {len(urls)} monthly reports listed "
             f"(latest {urls[-1][0]}-{urls[-1][1]})")
    except Exception as e:
        _log(f"   FAIL  Purple Book: {e}")
        ok = False

    _log("\n4. PubMed")
    try:
        prior, detail = enrich.has_moa_review("upadacitinib")
        _log(f"   OK  E-utilities reachable (upadacitinib prior review: {prior})")
    except Exception as e:
        _log(f"   FAIL  PubMed: {e}")
        ok = False

    _log("\n5. Schedule")
    _log("   " + scheduler.status_line())

    _log("\n" + ("All checks passed." if ok else "Some checks FAILED — see above."))
    return 0 if ok else 1


# ------------------------------------------------------------------ scan
def _members():
    """The ASCPT member directory, if one has been imported. Optional."""
    path = config.members_file()
    if not os.path.exists(path):
        return {}
    try:
        return sheets.load_members(path)
    except Exception as e:
        _log(f"  WARNING: could not read {os.path.basename(path)} ({e})")
        return {}


def _gather(since, skip_pubmed=False, limit=None):
    _log(f"Scanning FDA approvals since {since} …")
    recs = sources.collect(since=since, novel_only=True)
    if limit:
        recs = recs[-limit:]
    members = {} if skip_pubmed else _members()
    if members:
        _log(f"ASCPT member directory: {len(members)} people — matches will be flagged")
    _log(f"\n{len(recs)} novel agent(s) found. Enriching …")
    out = []
    for r in recs:
        if skip_pubmed:
            r = dict(r, prior_review=False, prior_review_detail="",
                     candidate_authors=[], clinpharm_people=[],
                     clinpharm_contacts="", clinpharm_evidence="",
                     clinpharm_paper_count=0)
        else:
            r = enrich.enrich(r, members=members)
        out.append(classify.enrich_record(r, r.get("prior_review", False)))
    return out


def cmd_scan(args):
    """Show what the engine would add, without touching Drive."""
    recs = _gather(args.since or _iso_days_ago(args.days),
                   skip_pubmed=args.no_pubmed, limit=args.limit)
    recs.sort(key=lambda r: -r["score"])
    _log("")
    _log(f"{'score':>5}  {'approved':10}  {'drug':28}  {'modality':26}  gap")
    _log("-" * 100)
    for r in recs:
        _log(f"{r['score']:>5}  {r['approval_date']:10}  {r['ingredient'][:28]:28}  "
             f"{r['modality'][:26]:26}  {r['gap']}")
    _log("")
    _log(f"{len(recs)} candidate(s). Nothing was written.")
    if recs:
        top = recs[0]
        _log(f"\nTop candidate — {top['ingredient']}:")
        _log(f"  novelty : {top['novelty_reason']}")
        _log(f"  score   : {top['score_why']}")
        if top.get("candidate_authors"):
            _log(f"  authors : {', '.join(top['candidate_authors'][:5])}")
    return 0


def _refresh_existing(args, qpath, dry):
    """Fill in contacts for rows already in the queue.

    `update` only ever appends, so rows added before the contact columns
    existed would stay blank for ever without this.
    """
    if not getattr(args, "refresh", False) or not os.path.exists(qpath):
        return
    _log("\nRefreshing contacts for candidates already in the queue …")
    if dry:
        _log("[dry run] would look up contacts for existing rows")
        return
    n = sheets.refresh_queue_contacts(qpath, enrich.enrich, members=_members(),
                                      only_missing=True, log=_log)
    _log(f"  {n} row(s) refreshed")


def cmd_update(args):
    """Append new candidates to the queue. Idempotent."""
    dry = not args.go

    qpath, created = sheets.ensure_queue(dry_run=dry)
    if created and dry:
        _log(f"[dry run] would create {qpath}")
    elif created:
        _log(f"created {qpath}")

    known = sheets.existing_keys(qpath)
    _log(f"queue currently holds {len(known)} candidate(s)")

    recs = _gather(args.since or _iso_days_ago(args.days),
                   skip_pubmed=args.no_pubmed, limit=args.limit)
    fresh = [r for r in recs if r["key"] not in known]
    _log(f"{len(recs)} scanned, {len(recs) - len(fresh)} already in the queue, "
         f"{len(fresh)} new")

    if not fresh:
        _log("\nNothing to add.")
        _refresh_existing(args, qpath, dry)
        return 0

    contacts = sheets.load_contacts()
    _log(f"contact grid: {len(contacts)} company row(s) on file")

    rows, needs_lookup = [], 0
    for r in sorted(fresh, key=lambda x: x["approval_date"]):
        contact, matched = sheets.match_contact(r["sponsor_raw"], contacts)
        if contact == "NEEDS LOOKUP":
            needs_lookup += 1
        rows.append(sheets.queue_row(r, contact))

    _log("\nWould add:" if dry else "\nAdding:")
    for r, row in zip(sorted(fresh, key=lambda x: x["approval_date"]), rows):
        _log(f"  {r['approval_date']}  {r['ingredient'][:30]:30}  "
             f"score {r['score']:>3}  {row[11][:40]}")

    n = sheets.append_candidates(qpath, rows, dry_run=dry)
    _log(f"\n{n} row(s) {'would be ' if dry else ''}appended.")
    if needs_lookup:
        _log(f"{needs_lookup} candidate(s) need a contact looked up by hand "
             f"(marked NEEDS LOOKUP).")
    if dry:
        _log("\nThis was a dry run. Nothing was written. "
             "Re-run with --go to update the queue.")
    else:
        _log(f"\nQueue: {qpath}")
    return 0


def cmd_dossier(args):
    """Build the outreach list: who to contact at each drug's company.

    The objective is reaching the clinical pharmacologist who worked on the
    drug. The ASCPT programme and attendee list are two further ways of getting
    to that person, not the purpose of the exercise.
    """
    dry = not args.go
    year = _meeting_year(args.year)

    qpath = config.queue_path()
    if not os.path.exists(qpath):
        _log(f"No queue yet at {qpath}\nRun:  ./moa-engine update --go")
        return 1
    queue = sheets.read_queue(qpath)
    _log(f"queue holds {len(queue)} candidate(s)")

    cutoff = _iso_days_ago(args.window)
    live = [r for r in queue
            if (r.get("Approval date") or "") >= cutoff
            and (r.get("Status") or "New").lower() not in ("published", "declined", "dropped")]
    _log(f"{len(live)} within the trailing {args.window} days and still open")

    # The UPCOMING meeting's programme, and only that one. Earlier years are
    # attendance history, reported separately -- previously they were the same
    # thing, so a 2027 dossier listed poster slots from March 2026.
    files = config.meeting_files()
    prog_file = (files.get(year) or {}).get("program")
    program = sheets.load_program(prog_file)
    if prog_file:
        _log(f"AM{year} programme: {len(program['posters'])} poster(s), "
             f"{len(program['sessions'])} session(s), "
             f"presenter names {'present' if program['has_authors'] else 'ABSENT'}")
        if not program["has_authors"]:
            _log("  note: without a presenter/author column, matching is by "
                 "company and title keyword only")
    else:
        _log(f"no AM{year} programme yet — no poster or session leads for the "
             f"upcoming meeting")

    attendance = sheets.build_attendance(year, files)
    if attendance["current"]:
        _log(f"AM{year} attendee list: {len(attendance['current'])} organisation(s)")
    for y in sorted(attendance["history"], reverse=True):
        _log(f"AM{y} history: {len(attendance['history'][y])} organisation(s) "
             f"(from the {attendance['sources'][y]})")
    if not attendance["current"] and not attendance["history"]:
        _log("no attendance history on file")

    rows = sheets.dossier_rows(live, program, attendance)

    col = {c: i for i, c in enumerate(config.DOSSIER_COLUMNS)}
    i_rank, i_drug = col["Rank"], col["Drug (INN)"]
    i_score, i_pres = col["Novelty"], col["ASCPT presence"]
    i_hist = col["Last year at ASCPT"]

    i_contacts = col["Clin pharm contacts"]
    with_leads = sum(1 for r in rows if r[i_pres] != "none found")
    with_hist = sum(1 for r in rows if r[i_hist] != "not seen")
    with_people = sum(1 for r in rows if r[i_contacts].strip())
    _log(f"\n{len(rows)} candidate(s); {with_people} with a named clinical "
         f"pharmacologist at the company")
    _log(f"  also: {with_leads} present at AM{year}, "
         f"{with_hist} whose sponsor has been to a previous meeting")
    _log(f"\n{'rank':>4}  {'drug':24}  {'who to contact':60}")
    _log("-" * 96)
    for r in rows[:20]:
        who = r[i_contacts].split(" | ")[0] if r[i_contacts].strip() else "—"
        _log(f"{r[i_rank]:>4}  {r[i_drug][:24]:24}  {who[:60]}")
    if len(rows) > 20:
        _log(f"  … {len(rows) - 20} more")

    path, name = sheets.write_dossier(year, rows, dry_run=dry)

    # A short list of specific people to verify, not a copy of the directory.
    known = _members()
    check = sheets.membership_check_rows(live, known)
    cpath = config.membership_check_path()
    sheets.write_membership_check(cpath, check, dry_run=dry)

    if dry:
        kept = sheets.merge_annotations(rows, path)
        annotated = sum(1 for r in kept
                        if any(r[config.DOSSIER_COLUMNS.index(c)]
                               for c in sheets.HUMAN_COLUMNS))
        _log(f"\n[dry run] would write {path}")
        if annotated:
            _log(f"          {annotated} row(s) already carry your notes; "
                 f"they would be preserved.")
        _log("\nNothing was written. Re-run with --go.")
    else:
        _log(f"\nDossier: {path}")
        if check:
            _log(f"Membership check list: {cpath}")
            _log(f"  {len(check)} name(s) with no membership answer yet. Fill in the "
                 f"'ASCPT member?' column\n  (or ask members@ascpt.org to), then import "
                 f"it back with:  ./moa-engine roster --file \"{cpath}\" --go")
        _log("\nReview and edit it — reorder rows, fill in AE owner, drop what you "
             "don't want.\nThen write the letters with:  "
             f"./moa-engine invites --year {year} --go")
    return 0


def cmd_roster(args):
    """Import an ASCPT attendee list or programme export for a meeting year.

    Optional: everything works without one. An attendee list simply turns
    "their team came last year" into "these named people are registered".
    """
    dry = not args.go
    year = _meeting_year(args.year)
    src = os.path.expanduser(args.file)

    if not os.path.exists(src):
        _log(f"No such file: {src}")
        return 1
    if not src.lower().endswith(".xlsx"):
        _log(f"Expected a .xlsx file, got {os.path.basename(src)}.\n"
             f"Open it in Excel and use File > Save As > Excel Workbook (.xlsx).")
        return 1

    # Auto-detect. A programme has posters. A member directory and an attendee
    # list are both just name + organisation, so the only thing separating them
    # is a membership-specific column -- absent that, assume attendees.
    kind = args.kind
    if not kind:
        tabs = store.tab_names(src)
        head = store.xlsx_read(src, "Posters" if "Posters" in tabs else None)
        hdr = [h.strip().lower() for h in (head[0] if head else [])]
        if any("poster" in h or "abstract" in h for h in hdr):
            kind = "program"
        elif sheets.looks_like_check_list(src) or sheets.looks_like_members(src):
            kind = "members"
        else:
            kind = "attendees"
    label = {"program": "ASCPT programme", "attendees": "attendee list",
             "members": "ASCPT member directory"}[kind]
    _log(f"Detected: {label}  (override with --kind)")

    if kind == "program":
        prog = sheets.load_program(src)
        if not prog["posters"] and not prog["sessions"]:
            _log("Could not find a 'Posters' or 'Sessions' tab with any rows.")
            return 1
        _log(f"  {len(prog['posters'])} poster(s), {len(prog['sessions'])} session(s)")
        _log(f"  presenter names {'present' if prog['has_authors'] else 'ABSENT'}")
        if not prog["has_authors"]:
            _log("  without a Presenting Author column you get companies, not people")
        dest = config.program_file(year)
    elif kind == "members":
        from_check = sheets.looks_like_check_list(src)
        try:
            members = (sheets.load_check_list(src) if from_check
                       else sheets.load_members(src))
        except sheets.RosterError as e:
            _log(f"Cannot use this file: {e}")
            return 1
        if not members:
            _log("No confirmed members found." if from_check else
                 "No named people found — a directory needs a name column.")
            return 1
        dest = config.members_file()
        if from_check:
            _log(f"  filled-in check list: {len(members)} confirmed member(s)")
            merged, added = sheets.merge_members(dest, members)
            _log(f"  {added} new; {len(merged)} known in total after merging")
            members = merged
        else:
            _log(f"  {len(members)} member(s) with a name")
    else:
        try:
            people = sheets.load_roster(src)
        except sheets.RosterError as e:
            _log(f"Cannot use this file: {e}")
            return 1
        orgs = {sheets._normalise_company(p["org"]) for p in people}
        named = sum(1 for p in people if p["name"])
        _log(f"  {len(people)} attendee(s), {len(orgs)} distinct organisation(s)")
        _log(f"  {named} with a name; {len(people) - named} organisation-only")
        dest = config.attendees_file(year)

    # How much difference it will actually make, before committing to it.
    qpath = config.queue_path()
    if kind == "attendees" and os.path.exists(qpath):
        index = sheets._index_people([(p["org"], p["name"]) for p in people])
        hits = sum(1 for r in sheets.read_queue(qpath)
                   if sheets._lookup_org(r.get("Sponsor", ""), index))
        _log(f"  {hits} of your queued candidates have a sponsor on this list")

    if dry:
        _log(f"\n[dry run] would save it as {dest}")
        _log("Nothing was written. Re-run with --go.")
        return 0

    os.makedirs(os.path.dirname(dest), exist_ok=True)
    if kind == "members":
        # Written, not copied: a check-list import is a merge with what is
        # already on file, so the source file is not what should land here.
        sheets.save_members(dest, members)
    elif os.path.abspath(src) != os.path.abspath(dest):
        shutil.copyfile(src, dest)
    _log(f"\nSaved as {dest}")
    if kind == "members":
        _log("Clinical pharmacologists found on PubMed will now be flagged when "
             "they appear in this directory. Not year-specific — it applies to "
             "every run until you replace it.")
    else:
        _log(f"It will be used from the next dossier run for AM{year}.")
    return 0


def cmd_invites(args):
    """Draft the invitation letters, from the dossier as it now stands."""
    dry = not args.go
    year = _meeting_year(args.year)
    dpath = config.dossier_path(year, existing=True)

    if not os.path.exists(dpath):
        _log(f"No dossier for {year} at {dpath}\n"
             f"Run:  ./moa-engine dossier --year {year} --go")
        return 1

    columns, rows = sheets.read_dossier(year)
    if not rows:
        _log(f"The {year} dossier has no candidate rows. Nothing to draft.")
        return 1
    _log(f"dossier holds {len(rows)} candidate(s), in the order you left them")

    rows = rows[:args.invites]
    try:
        drug_i = columns.index("Drug (INN)")
        contact_i = columns.index("Contact")
    except ValueError:
        _log("The outreach list is missing a 'Drug (INN)' or 'Contact' column — "
             "was its header edited?")
        return 1

    needs = sum(1 for r in rows
                if contact_i >= len(r) or not r[contact_i].strip()
                or r[contact_i].strip() == "NEEDS LOOKUP")
    _log(f"\ndrafting {len(rows)} letter(s):")
    for i, r in enumerate(rows, 1):
        contact = r[contact_i] if contact_i < len(r) else ""
        _log(f"  {i:>3}  {r[drug_i][:32]:32}  {(contact or 'NEEDS LOOKUP')[:38]}")

    path = sheets.write_invites(year, rows, columns, dry_run=dry)
    if needs:
        _log(f"\n{needs} of {len(rows)} still say NEEDS LOOKUP — fill the contact "
             f"grid in\nand re-run to improve them. The tool will not guess a "
             f"recipient.")
    if dry:
        _log(f"\n[dry run] would write {path}")
        _log("Nothing was written. Re-run with --go.")
    else:
        _log(f"\nDrafts: {path}")
        _log("Open it in a browser or Word. Every letter is a starting point.")
        _log("\nNOTHING HAS BEEN EMAILED. Review and send each one yourself.")
    return 0


# ------------------------------------------------------------------ schedule
def cmd_start(args):
    return scheduler.start(args.day, args.hour)


def cmd_stop(args):
    return scheduler.stop()


def cmd_status(args):
    _log(scheduler.status_line())
    return 0


# ------------------------------------------------------------------ main
def main(argv=None):
    # Double-clicked with no arguments? Open the app rather than printing an
    # argparse error. `moa-engine` looks launchable in Finder, and "the
    # following arguments are required: cmd" is a useless thing to show someone
    # who just wanted the tool to start.
    if argv is None and len(sys.argv) == 1:
        try:
            import gui
        except Exception:
            pass
        else:
            return gui.main() or 0

    p = argparse.ArgumentParser(
        prog="moa-engine",
        description="Find novel drug approvals that need a CTS MOA mini-review.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="Nothing is written without --go, and nothing is ever emailed.")
    sub = p.add_subparsers(dest="cmd", required=True)

    def out_flag(sp):
        sp.add_argument("--output-dir",
                        help="write somewhere else for this run (useful for testing)")
        return sp

    sp = sub.add_parser("setup", help="optional configuration; the defaults work")
    sp.set_defaults(fn=cmd_setup)

    sp = out_flag(sub.add_parser("check", help="verify files and data sources; writes nothing"))
    sp.set_defaults(fn=cmd_check)

    sp = sub.add_parser("scan", help="preview candidates; writes nothing")
    sp.add_argument("--days", type=int, default=120, help="look-back window (default 120)")
    sp.add_argument("--since", help="explicit ISO start date, overrides --days")
    sp.add_argument("--limit", type=int, help="cap candidates (useful for a quick look)")
    sp.add_argument("--no-pubmed", action="store_true", help="skip PubMed enrichment")
    sp.set_defaults(fn=cmd_scan)

    sp = out_flag(sub.add_parser("update", help="append new candidates to the queue"))
    sp.add_argument("--days", type=int, default=120)
    sp.add_argument("--since")
    sp.add_argument("--limit", type=int)
    sp.add_argument("--no-pubmed", action="store_true")
    sp.add_argument("--refresh", action="store_true",
                    help="also fill in contacts for candidates already in the queue")
    sp.add_argument("--go", action="store_true", help="actually write the file")
    sp.set_defaults(fn=cmd_update)

    sp = out_flag(sub.add_parser("dossier",
                       help="build the outreach list — who to contact at each company"))
    sp.add_argument("--year", type=int, help="meeting year (default: next meeting)")
    sp.add_argument("--window", type=int, default=550,
                    help="include approvals from the trailing N days (default 550)")
    sp.add_argument("--go", action="store_true", help="actually write the file")
    sp.set_defaults(fn=cmd_dossier)

    sp = sub.add_parser(
        "roster",
        help="import an ASCPT programme, attendee list or member directory (optional)")
    sp.add_argument("--file", required=True, help="the .xlsx you exported")
    sp.add_argument("--year", type=int, help="meeting year (default: next meeting)")
    sp.add_argument("--kind", choices=("program", "attendees", "members"),
                    help="override the auto-detected file type")
    sp.add_argument("--go", action="store_true", help="actually save it")
    sp.set_defaults(fn=cmd_roster)

    sp = out_flag(sub.add_parser(
        "invites", help="draft the invitation letters from the outreach list"))
    sp.add_argument("--year", type=int, help="meeting year (default: next meeting)")
    sp.add_argument("--invites", type=int, default=15,
                    help="how many letters to draft (default 15)")
    sp.add_argument("--go", action="store_true", help="actually write the file")
    sp.set_defaults(fn=cmd_invites)

    sp = sub.add_parser("start", help="turn the monthly schedule ON")
    sp.add_argument("--day", type=int, default=7, help="day of month (default 7)")
    sp.add_argument("--hour", type=int, default=9, help="hour, 24h clock (default 9)")
    sp.set_defaults(fn=cmd_start)

    sp = sub.add_parser("stop", help="turn the monthly schedule OFF")
    sp.set_defaults(fn=cmd_stop)

    sp = sub.add_parser("status", help="is the schedule on or off?")
    sp.set_defaults(fn=cmd_status)

    args = p.parse_args(argv)
    if getattr(args, "output_dir", None):
        config.set_output_dir(args.output_dir)
    try:
        return args.fn(args)
    except config.SettingsError as e:
        _log(f"\n{e}")
        return 1
    except KeyboardInterrupt:
        _log("\nInterrupted.")
        return 130
    except Exception:
        _log("\nUnexpected error:\n" + traceback.format_exc())
        return 1


if __name__ == "__main__":
    sys.exit(main())
