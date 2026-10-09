#!/usr/bin/env python3
"""Offline test suite for the local-file layer.

Runs in under a second, needs no network, and leaves nothing behind: every
workbook is written into a temporary directory that is torn down at the end.
This is the counterpart to backtest.py, which checks the *filter* against the
19 published MOA papers; this checks the *plumbing*.

The .xlsx reader is the riskiest code in the tool, because a workbook the tool
wrote and a human then edited comes back in a different internal encoding than
it went out in. Most of what follows exists to pin that down.

Run:  python3 selftest.py
"""
import os
import shutil
import sys
import tempfile
import xml.sax.saxutils
import zipfile

import config
import store

_fails = []


def check(label, got, want):
    if got == want:
        print(f"  ok    {label}")
    else:
        print(f"  FAIL  {label}\n          got  {got!r}\n          want {want!r}")
        _fails.append(label)


def section(name):
    print(f"\n{name}")


# ------------------------------------------------------------------ fixtures
def _excel_style_workbook(path, header, rows, tab="Dossier"):
    """Build a workbook the way Excel saves one: a sharedStrings table and
    t="s" cells, rather than the inline strings we write.

    This is the format the tool will actually be handed back after someone
    types into the Attending? column, so the reader has to cope with it.
    """
    table, index = [], {}
    for row in [header] + rows:
        for v in row:
            if v not in index:
                index[v] = len(table)
                table.append(v)

    ss = ('<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
          f'<sst xmlns="{store.MAIN_NS}" count="{len(table)}" uniqueCount="{len(table)}">'
          + "".join(f"<si><t>{store._esc(t)}</t></si>" for t in table) + "</sst>")

    body = []
    for r_i, row in enumerate([header] + rows, 1):
        cells = "".join(
            f'<c r="{store.col_letter(c_i)}{r_i}" t="s"><v>{index[v]}</v></c>'
            for c_i, v in enumerate(row) if v != "")
        body.append(f'<row r="{r_i}">{cells}</row>')
    sheet = ('<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
             f'<worksheet xmlns="{store.MAIN_NS}"><sheetData>'
             + "".join(body) + "</sheetData></worksheet>")

    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("[Content_Types].xml",
                   '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
                   '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
                   '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
                   '<Default Extension="xml" ContentType="application/xml"/>'
                   '<Override PartName="/xl/workbook.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"/>'
                   '<Override PartName="/xl/worksheets/sheet1.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/>'
                   '<Override PartName="/xl/sharedStrings.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sharedStrings+xml"/>'
                   '</Types>')
        z.writestr("_rels/.rels", store._ROOT_RELS)
        z.writestr("xl/workbook.xml",
                   '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
                   f'<workbook xmlns="{store.MAIN_NS}" xmlns:r="{store.REL_NS}">'
                   f'<sheets><sheet name="{tab}" sheetId="1" r:id="rId1"/></sheets></workbook>')
        z.writestr("xl/_rels/workbook.xml.rels",
                   '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
                   f'<Relationships xmlns="{store.PKG_REL_NS}">'
                   '<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet" Target="worksheets/sheet1.xml"/>'
                   '<Relationship Id="rId2" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/sharedStrings" Target="sharedStrings.xml"/>'
                   "</Relationships>")
        z.writestr("xl/worksheets/sheet1.xml", sheet)
        z.writestr("xl/sharedStrings.xml", ss)
    return path


# ------------------------------------------------------------------ tests
def test_column_letters():
    section("Column reference maths")
    check("A is 0", store.col_letter(0), "A")
    check("Z is 25", store.col_letter(25), "Z")
    check("AA is 26", store.col_letter(26), "AA")
    check("BM is 64", store.col_letter(64), "BM")
    check("round-trip 0..200",
          all(store.col_index(store.col_letter(i) + "1") == i for i in range(200)),
          True)


def test_round_trip(tmp):
    section("Write then read back, with content that has bitten XML before")
    rows = [
        ["Drug (INN)", "Sponsor", "Novelty", "Comments"],
        ["giredestrant", "Genentech, Inc.", "84", 'quotes " and \' apostrophes'],
        ["obeldesivir", "Gilead & Co <Ltd>", "72", "ampersand & angle < brackets >"],
        ["tirzepatide", "Eli Lilly", "0", "naïve café — em dash, ünïcode"],
        ["insulin icodec", "Novo Nordisk", "84", ""],
    ]
    p = store.xlsx_write(os.path.join(tmp, "roundtrip.xlsx"), {"Queue": rows})
    back = store.xlsx_read(p, "Queue")
    check("row count", len(back), len(rows))
    check("exact round-trip", back, rows)
    check("tab name", store.tab_names(p), ["Queue"])
    check("reading a tab that isn't there returns []",
          store.xlsx_read(p, "Nope"), [])
    check("reading a file that isn't there returns []",
          store.xlsx_read(os.path.join(tmp, "ghost.xlsx"), "Queue"), [])


def test_shape_edges(tmp):
    section("Ragged rows, blank cells, numeric-looking text")
    rows = [
        ["A", "B", "C"],
        ["1", "", "3"],          # hole in the middle
        ["4"],                    # short row
        ["7", "8", ""],           # trailing blank
    ]
    p = store.xlsx_write(os.path.join(tmp, "ragged.xlsx"), {"S": rows})
    back = store.xlsx_read(p, "S")
    check("every row padded to full width",
          [len(r) for r in back], [3, 3, 3, 3])
    check("interior blank preserved", back[1], ["1", "", "3"])
    check("short row padded", back[2], ["4", "", ""])
    check("trailing blank preserved", back[3], ["7", "8", ""])

    p2 = store.xlsx_write(os.path.join(tmp, "trailing.xlsx"),
                          {"S": [["A", "B"], ["x", "y"], ["", ""], ["", ""]]})
    check("trailing all-blank rows dropped", len(store.xlsx_read(p2, "S")), 2)


def test_multi_tab(tmp):
    section("Two tabs in one workbook")
    p = store.xlsx_write(os.path.join(tmp, "two.xlsx"), {
        "Posters": [["Poster Number", "Poster Presentation Title"],
                    ["PI-072", "INDEPENDENT POPULATION EXPOSURE-RESPONSE"]],
        "Sessions": [["Session", "Room"], ["Opening", "Hall A"]],
    })
    check("tab order preserved", store.tab_names(p), ["Posters", "Sessions"])
    check("first tab", store.xlsx_read(p, "Posters")[1][0], "PI-072")
    check("second tab", store.xlsx_read(p, "Sessions")[1][1], "Hall A")
    check("tab=None reads the first", store.xlsx_read(p)[0][0], "Poster Number")


def test_excel_saved_format(tmp):
    section("A workbook saved by Excel (sharedStrings, not inline strings)")
    header = ["Drug (INN)", "Novelty", "Attending?", "Comments"]
    rows = [["giredestrant", "84", "yes", "spoke to them at PI-072"],
            ["obeldesivir", "72", "", ""],
            ["tirzepatide", "66", "no", "already has a review"]]
    p = _excel_style_workbook(os.path.join(tmp, "excel-saved.xlsx"), header, rows)

    with zipfile.ZipFile(p) as z:
        check("fixture really does use sharedStrings",
              "xl/sharedStrings.xml" in z.namelist(), True)

    back = store.xlsx_read(p, "Dossier")
    check("header read through the string table", back[0], header)
    check("human-typed Attending? survives", back[1][2], "yes")
    check("human-typed Comments survives", back[1][3], "spoke to them at PI-072")
    check("blank annotations stay blank", back[2], ["obeldesivir", "72", "", ""])
    check("full body", back[1:], rows)


def test_rewrite_shrinks(tmp):
    section("A shorter re-run must not leave orphan rows")
    p = os.path.join(tmp, "shrink.xlsx")
    store.xlsx_write(p, {"Dossier": [["Drug"]] + [[f"drug-{i}"] for i in range(30)]})
    check("30 candidates written", len(store.xlsx_read(p, "Dossier")), 31)
    store.xlsx_write(p, {"Dossier": [["Drug"]] + [[f"drug-{i}"] for i in range(4)]})
    check("re-run with 4 leaves exactly 4", len(store.xlsx_read(p, "Dossier")), 5)


def test_annotation_merge(tmp):
    section("Dossier re-run preserves what a human typed")
    try:
        import sheets
    except Exception as e:                       # pragma: no cover
        print(f"  skip  sheets.py not importable yet ({e})")
        return
    if not hasattr(sheets, "merge_annotations"):
        print("  skip  sheets.merge_annotations not implemented yet")
        return

    cols = ["Rank", "Drug name", "Novelty", "AE owner", "Attending?", "Comments"]
    prior = [cols,
             ["1", "giredestrant", "84", "JC", "yes", "met at PI-072"],
             ["2", "obeldesivir", "72", "", "no", "declined"],
             ["3", "tirzepatide", "66", "AB", "", "chase in Feb"]]
    p = store.xlsx_write(os.path.join(tmp, "prior.xlsx"), {"Prior": prior})

    # A later run: re-ranked, tirzepatide gone, a new drug arrived.
    fresh = [["1", "tirzepatide", "70", "", "", ""],
             ["2", "giredestrant", "84", "", "", ""],
             ["3", "veligrotug", "61", "", "", ""]]
    merged = sheets.merge_annotations(fresh, p, cols, tab="Prior")
    by_drug = {r[1]: r for r in merged}

    check("annotations follow the drug, not the row position",
          by_drug["giredestrant"][4:], ["yes", "met at PI-072"])
    check("AE owner survives re-ranking", by_drug["giredestrant"][3], "JC")
    check("a drug that moved rank keeps its notes",
          by_drug["tirzepatide"][3:], ["AB", "", "chase in Feb"])
    check("a brand-new drug has empty annotations",
          by_drug["veligrotug"][3:], ["", "", ""])
    check("machine columns are NOT taken from the old file",
          by_drug["tirzepatide"][2], "70")
    check("no orphan row for the dropped drug", len(merged), 3)


def test_org_matching():
    section("Company matching — the Vera / Verastem class of error")
    import sheets
    cases = [
        ("vera", "verastem oncology", False,
         "Vera Therapeutics is not Verastem Oncology"),
        ("haisco grou", "haisco group", True,
         "FDA sponsor strings get truncated mid-word"),
        ("bristol myers squibb", "bristol myers squibb compa", True,
         "trailing partial word"),
        ("regeneron", "regeneron pharmaceut", True, "truncated tail"),
        ("gilead", "gilead", True, "exact"),
        ("bio", "biogen", False, "short stem must not match"),
        ("merck", "merck sharp dohme", False, "whole extra words"),
        ("novo", "novo nordisk", False, "one whole extra word, too short"),
        ("lilly", "eli lilly and", False, "not a prefix at all"),
    ]
    for a, b, want, why in cases:
        check(f"{why}: {a!r} vs {b!r}", sheets._prefix_match(a, b), want)
        check(f"  symmetric", sheets._prefix_match(b, a), want)

    # The bug as it actually presented: a wrong invitation recipient.
    contacts = {"verastem oncology": ("Verastem Oncology", "Dr Someone",
                                      "someone@verastem.com")}
    check("a near-miss sponsor reports NEEDS LOOKUP, not the wrong address",
          sheets.match_contact("VERA THERAPEUTICS INC.", contacts)[0],
          "NEEDS LOOKUP")


def _program(rows):
    """A minimal in-memory programme, shaped like load_program() returns."""
    return {"posters": rows, "sessions": [], "has_authors": True}


def test_roster_from_program():
    section("Deriving last year's attendees from a programme")
    import sheets
    prog = _program([
        {"Presenting Author First Name": "Brian", "Presenting Author Last Name": "Moser",
         "Presenting Author Organization": "Eli Lilly and Company"},
        {"Presenting Author First Name": "Tsai-Wei", "Presenting Author Last Name": "Lin",
         "Presenting Author Organization": "Eli Lilly and Co"},
        {"Presenting Author First Name": "Ann", "Presenting Author Last Name": "Ng",
         "Presenting Author Organization": "University of California, San Francisco"},
        {"Presenting Author First Name": "Bo", "Presenting Author Last Name": "Li",
         "Presenting Author Organization": "University of California San Francisco"},
        {"Presenting Author First Name": "", "Presenting Author Last Name": "",
         "Presenting Author Organization": "Certara"},
    ])
    idx = sheets.roster_from_program(prog)
    lilly = sheets._lookup_org("ELI LILLY AND CO", idx)
    check("both Lilly spellings collapse to one org",
          lilly and sorted(lilly["people"]), ["Brian Moser", "Tsai-Wei Lin"])
    ucsf = sheets._lookup_org("University of California San Francisco", idx)
    check("comma'd and un-comma'd UCSF collapse too",
          ucsf and sorted(ucsf["people"]), ["Ann Ng", "Bo Li"])
    check("an org with no named presenter is still known",
          bool(sheets._lookup_org("Certara", idx)), True)
    check("an unrelated sponsor does not match",
          sheets._lookup_org("Zealand Pharma", idx), None)


def test_load_roster(tmp):
    section("Reading an attendee list, whatever its columns are called")
    import sheets

    p1 = store.xlsx_write(os.path.join(tmp, "r1.xlsx"), {"Attendees": [
        ["Name", "Organization"], ["Dana Reyes", "Gilead Sciences Inc"]]})
    check("Name + Organization", sheets.load_roster(p1),
          [{"name": "Dana Reyes", "org": "Gilead Sciences Inc"}])

    p2 = store.xlsx_write(os.path.join(tmp, "r2.xlsx"), {"Sheet1": [
        ["First Name", "Last Name", "Company"], ["Sam", "Oyelaran", "AbbVie Inc"]]})
    check("First + Last + Company", sheets.load_roster(p2),
          [{"name": "Sam Oyelaran", "org": "AbbVie Inc"}])

    p3 = store.xlsx_write(os.path.join(tmp, "r3.xlsx"), {"Sheet1": [
        ["Attendee Name", "Institution", "Country"],
        ["Mei Tan", "Novo Nordisk", "DK"]]})
    check("Attendee Name + Institution", sheets.load_roster(p3)[0]["org"],
          "Novo Nordisk")

    p4 = store.xlsx_write(os.path.join(tmp, "r4.xlsx"), {"Sheet1": [
        ["Name", "Email"], ["Nobody", "n@example.com"]]})
    try:
        sheets.load_roster(p4)
        check("a file with no organisation column is refused", "accepted", "refused")
    except sheets.RosterError:
        check("a file with no organisation column is refused", "refused", "refused")

    p5 = store.xlsx_write(os.path.join(tmp, "r5.xlsx"), {"Sheet1": [
        ["Name", "Organization"], ["Someone", ""]]})
    try:
        sheets.load_roster(p5)
        check("a file with no filled organisations is refused", "accepted", "refused")
    except sheets.RosterError:
        check("a file with no filled organisations is refused", "refused", "refused")


def test_build_attendance(tmp):
    section("Which year is 'now' and which is history")
    import sheets

    prog26 = store.xlsx_write(os.path.join(tmp, "ascpt program 2026.xlsx"), {
        "Posters": [["Presenting Author First Name", "Presenting Author Last Name",
                     "Presenting Author Organization"],
                    ["Jin", "Zhou", "Gilead Sciences, Inc."]]})
    att27 = store.xlsx_write(os.path.join(tmp, "ascpt attendees 2027.xlsx"), {
        "Attendees": [["Name", "Organization"], ["Dana Reyes", "Gilead Sciences Inc"]]})
    prog27 = store.xlsx_write(os.path.join(tmp, "ascpt program 2027.xlsx"), {
        "Posters": [["Presenting Author First Name", "Presenting Author Last Name",
                     "Presenting Author Organization"],
                    ["Ignore", "Me", "Gilead Sciences, Inc."]]})

    files = {2026: {"program": prog26, "attendees": None},
             2027: {"program": prog27, "attendees": att27}}
    a = sheets.build_attendance(2027, files)
    check("2027 is current", sheets._lookup_org("GILEAD SCIENCES INC", a["current"])
          ["people"], {"Dana Reyes"})
    check("an explicit roster beats derived presenters", a["sources"][2027],
          "attendee roster")
    check("2026 is history", sorted(a["history"]), [2026])
    check("history falls back to presenters", a["sources"][2026],
          "programme presenters")

    b = sheets.build_attendance(2026, files)
    check("a later meeting is not treated as history", sorted(b["history"]), [])
    check("2026 becomes current when it is the target",
          sheets._lookup_org("GILEAD SCIENCES INC", b["current"])["people"],
          {"Jin Zhou"})


def test_attendance_columns(tmp):
    section("Attendance reaches the dossier without moving the ranking")
    import sheets
    prog26 = store.xlsx_write(os.path.join(tmp, "hist.xlsx"), {
        "Posters": [["Presenting Author First Name", "Presenting Author Last Name",
                     "Presenting Author Organization"],
                    ["Brian", "Moser", "Eli Lilly and Company"]]})
    files = {2026: {"program": prog26, "attendees": None}}
    attendance = sheets.build_attendance(2027, files)

    cands = [{"Drug (INN)": "orforglipron", "Sponsor": "ELI LILLY AND CO", "Novelty": "84"},
             {"Drug (INN)": "insulin icodec", "Sponsor": "NOVO NORDISK INC", "Novelty": "84"},
             {"Drug (INN)": "atacicept", "Sponsor": "VERA THERAPEUTICS INC.", "Novelty": "74"}]
    empty = {"posters": [], "sessions": [], "has_authors": False}
    plain = sheets.dossier_rows(cands, empty)
    withatt = sheets.dossier_rows(cands, empty, attendance)

    col = {c: i for i, c in enumerate(config.DOSSIER_COLUMNS)}
    check("ranking is unchanged by attendance",
          [r[col["Drug (INN)"]] for r in plain],
          [r[col["Drug (INN)"]] for r in withatt])
    check("no upcoming-meeting leads are invented",
          {r[col["ASCPT presence"]] for r in withatt}, {"none found"})
    check("last year is recorded for the sponsor that was there",
          withatt[0][col["Last year at ASCPT"]],
          "AM2026: Eli Lilly and Company (1 present)")
    check("and names a person to find",
          withatt[0][col["Who to find"]],
          "Brian Moser — Eli Lilly and Company (AM2026)")
    check("a sponsor that was not there says so",
          withatt[1][col["Last year at ASCPT"]], "not seen")
    check("Vera Therapeutics is not matched to anything",
          withatt[2][col["Who to find"]], "")


def test_year_rollover():
    section("The meeting year rolls over on its own")
    import datetime
    for y, m, want, why in [
            (2026, 8, 2027, "today: working towards the next March meeting"),
            (2026, 12, 2027, "still the same meeting over new year"),
            (2027, 1, 2027, "January does not jump ahead"),
            (2027, 3, 2027, "the month of the meeting"),
            (2027, 5, 2027, "just after it, still 2027"),
            (2027, 6, 2028, "June switches to the following meeting"),
            (2028, 3, 2028, "and again, with no code change"),
            (2035, 9, 2036, "years from now"),
    ]:
        check(f"{y}-{m:02d} -> ASCPT {want} ({why})",
              config.meeting_year(datetime.date(y, m, 15)), want)

    # The GUI labels its buttons with this. Computing it in two places once let
    # the page say one year while the dossier built another.
    import moa_engine
    check("the CLI reads the same clock as config",
          moa_engine._meeting_year(), config.meeting_year())
    check("--year still overrides", moa_engine._meeting_year(2031), 2031)


def test_history_accumulates(tmp):
    section("Past meetings become history without being told")
    import sheets

    def prog(y, first, last):
        return store.xlsx_write(os.path.join(tmp, f"ascpt program {y}.xlsx"), {
            "Posters": [["Presenting Author First Name",
                         "Presenting Author Last Name",
                         "Presenting Author Organization"],
                        [first, last, "Gilead Sciences, Inc."]]})

    files = {y: {"program": prog(y, f, l), "attendees": None}
             for y, f, l in ((2026, "Jin", "Zhou"), (2027, "Ana", "Mendes"),
                             (2028, "Kofi", "Mensah"))}

    a = sheets.build_attendance(2027, files)
    check("AM2027 upcoming is 2027's own file",
          sheets._lookup_org("GILEAD SCIENCES INC", a["current"])["people"],
          {"Ana Mendes"})
    check("AM2027 history is 2026 only", sorted(a["history"]), [2026])

    a = sheets.build_attendance(2028, files)
    check("a year later, 2027 has joined the history",
          sorted(a["history"], reverse=True), [2027, 2026])

    a = sheets.build_attendance(2029, files)
    check("with no file for the target year, upcoming is empty", a["current"], {})
    check("but all three earlier years are history",
          sorted(a["history"], reverse=True), [2028, 2027, 2026])


def test_bundle_arg_dispatch(tmp):
    section("The bundle routes --gui to the browser app, not to argparse")
    import re
    import build_single_file as bsf

    # The Windows launcher passes --gui under pythonw. The bootstrap treated any
    # argument as a CLI subcommand, so argparse exited 2 -- and with no console
    # that error was invisible: double-click, nothing happens. Exercise the real
    # dispatch expression rather than trusting the source to look right.
    m = re.search(r"args = (\[a for a in sys\.argv\[1:\][^\]]*\])", bsf.BOOTSTRAP)
    check("the bootstrap filters argv rather than passing it through",
          bool(m), True)
    if not m:
        return
    expr = m.group(1)
    for argv, want in ((["--gui"], []), ([], []),
                       (["hotlist", "--go"], ["hotlist", "--go"]),
                       (["--gui", "hotlist"], ["hotlist"])):
        got = eval(expr, {"sys": type("S", (), {"argv": ["x"] + argv})})
        check(f"argv {argv} -> {want}", got, want)

    check("a crash is surfaced rather than swallowed",
          "MessageBoxW" in bsf.BOOTSTRAP and "_die" in bsf.BOOTSTRAP, True)
    check("the batch header is exactly one line",
          bsf.WIN_HEADER.count("\n"), 1)


def test_gui_endpoints(tmp):
    section("Every GUI endpoint actually answers")
    import http.server
    import json as _json
    import threading
    import urllib.parse
    import urllib.request

    # Two endpoints shipped referring to a `qs` that was never defined in
    # do_GET. Each raised NameError, the connection closed with no response,
    # and the page said "the engine is not responding" while everything else
    # worked. Nothing exercised them, so nothing caught it. This does.
    os.environ["MOA_SETTINGS"] = os.path.join(tmp, "s.json")
    with open(os.environ["MOA_SETTINGS"], "w") as fh:
        _json.dump({"input_dir": os.path.join(tmp, "in"),
                    "output_dir": os.path.join(tmp, "out")}, fh)
    import importlib
    import config as _c
    importlib.reload(_c)
    import gui
    importlib.reload(gui)

    srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), gui.Handler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    port = srv.server_address[1]

    def call(path, **q):
        q["t"] = gui.TOKEN
        url = f"http://127.0.0.1:{port}{path}?" + urllib.parse.urlencode(q)
        try:
            with urllib.request.urlopen(url, timeout=10) as r:
                return r.status, r.read().decode()
        except Exception as e:
            return 0, f"{type(e).__name__}: {e}"

    cases = [("/alive", {}), ("/log", {"since": "0"}), ("/where", {}),
             ("/setdrugs", {"text": "vericiguat"}),
             ("/setoutdir", {"dir": os.path.join(tmp, "picked")})]
    bad = []
    for path, q in cases:
        code, body = call(path, **q)
        if code != 200:
            bad.append(f"{path} -> {body[:60]}")
            continue
        try:
            _json.loads(body)
        except ValueError:
            bad.append(f"{path} -> not JSON")
    srv.shutdown()
    check("all GET endpoints return 200 with JSON", bad, [])
    check("the drug list reached disk",
          os.path.exists(os.path.join(tmp, "in", _c.MANUAL_DRUGS_FILE)), True)


def test_python_floor(tmp):
    section("Every module parses on the oldest Python a recipient may have")
    import glob
    import subprocess as sp
    here = os.path.dirname(os.path.abspath(__file__))
    # /usr/bin/python3 ships with macOS and is 3.9. A colleague who never
    # installed Python will be running something like it, so syntax newer than
    # that is a SyntaxError on exactly the machines this tool exists to serve.
    # A nested f-string (PEP 701, 3.12) shipped once and broke the bundle.
    old = next((p for p in ("/usr/bin/python3", "/usr/bin/python3.9")
                if os.path.exists(p)), None)
    if not old:
        print("  skip  no older interpreter on this machine to test against")
        return
    ver = sp.run([old, "-c", "import sys;print('%d.%d' % sys.version_info[:2])"],
                 capture_output=True, text=True).stdout.strip()
    bad = []
    for f in sorted(glob.glob(os.path.join(here, "*.py"))):
        r = sp.run([old, "-c",
                    "import ast,sys;ast.parse(open(sys.argv[1]).read())", f],
                   capture_output=True, text=True)
        if r.returncode:
            bad.append(os.path.basename(f) + ": "
                       + (r.stderr.strip().splitlines() or [""])[-1])
    check(f"all modules parse on Python {ver}", bad, [])


def test_contact_carry_forward(tmp):
    """All three of the team's columns survive the next run.

    None of them can be regenerated. The ASCPT directory column in particular
    is a lookup someone did one row at a time, so losing it silently is the
    worst thing this file can do.
    """
    section("What the AE team typed survives the next run")
    import sheets

    cols = list(config.HOTLIST_COLUMNS)
    ci = cols.index(sheets.CONTACT_COL)
    ai = cols.index(sheets.ASCPT_COL)
    oi = cols.index(sheets.OWNER_COL)
    prior = [cols]
    for drug, owner, contact, ascpt in (
            ("AUCATZYL (obecabtagene autoleucel)", "JC", "Pierre L-S", ""),
            ("TRYNGOLZA (olezarsen)", "", "", ""),
            ("PLUVICTO (vipivotide tetraxetan)", "Erica", "", "Sanne de Jong"),
    ):
        row = [""] * len(cols)
        row[0], row[oi], row[ci], row[ai] = drug, owner, contact, ascpt
        prior.append(row)
    p = store.xlsx_write(os.path.join(tmp, "MOA outreach list 2026-01-01.xlsx"),
                         {sheets.HOTLIST_TAB: prior})

    got = sheets._prior_contacts(p)
    check("a filled-in contact is carried",
          got.get("obecabtagene autoleucel", {}).get(sheets.CONTACT_COL),
          "Pierre L-S")
    check("the AE owner is carried",
          got.get("obecabtagene autoleucel", {}).get(sheets.OWNER_COL), "JC")
    check("an ASCPT directory name is carried",
          got.get("vipivotide tetraxetan", {}).get(sheets.ASCPT_COL),
          "Sanne de Jong")
    check("the two contact columns do not bleed into each other",
          got.get("vipivotide tetraxetan", {}).get(sheets.CONTACT_COL), None)
    check("a wholly blank row is not carried", "olezarsen" in got, False)

    # These headers get reworded in Excel. An exact-string match would fail
    # silently and the next run would write a blank sheet.
    reworded = [["Drug name", "ASCPT member (clin pharm)", "Owner (AE)"],
                ["AUCATZYL (obecabtagene autoleucel)", "Sanne de Jong", "JC"]]
    p2 = store.xlsx_write(os.path.join(tmp, "MOA outreach list 2026-01-02.xlsx"),
                          {sheets.HOTLIST_TAB: reworded})
    got2 = sheets._prior_contacts(p2)
    check("a reworded ASCPT header still carries",
          got2.get("obecabtagene autoleucel", {}).get(sheets.ASCPT_COL),
          "Sanne de Jong")
    check("a reworded owner header still carries",
          got2.get("obecabtagene autoleucel", {}).get(sheets.OWNER_COL), "JC")

    # The brand can change between runs; the INN in parentheses is the anchor.
    check("a renamed brand still matches its drug",
          sheets._contact_key("SOMETHINGELSE (obecabtagene autoleucel)"),
          sheets._contact_key("AUCATZYL (obecabtagene autoleucel)"))
    check("different drugs do not collide",
          sheets._contact_key("A (olezarsen)") ==
          sheets._contact_key("B (plozasiran)"), False)
    check("a missing file is not an error", sheets._prior_contacts(
          os.path.join(tmp, "nope.xlsx")), {})


def test_invitation_drafts():
    """A draft names the drug and the contact, from either contact column.

    The letter builder was still reading the retired dossier's column names,
    so every draft rendered with no drug, no company and a contact of
    NEEDS LOOKUP even where an AE had filled one in.
    """
    section("Invitation drafts read the outreach list's own columns")
    import sheets

    cols = list(config.HOTLIST_COLUMNS)
    row = [""] * len(cols)
    row[cols.index("Drug name")] = "AUCATZYL (obecabtagene autoleucel)"
    row[cols.index("Company name")] = "Autolus, Inc."
    row[cols.index("MOA")] = "A CD19-directed autologous CAR-T cell therapy."
    row[cols.index("NDA/BLA number")] = "BLA 125813"
    row[cols.index(sheets.OWNER_COL)] = "JC"
    row[cols.index(sheets.ASCPT_COL)] = "Sanne de Jong"
    html_out = sheets.build_invites_html([row], 2027, cols)

    check("the drug name appears", "obecabtagene autoleucel" in html_out, True)
    check("the company appears", "Autolus, Inc." in html_out, True)
    check("the ASCPT-sourced contact is used",
          "Sanne de Jong" in html_out, True)
    check("and it is not reported as needing lookup",
          "NEEDS LOOKUP" in html_out, False)
    check("the label MOA appears", "CD19-directed" in html_out, True)
    check("the AE owner appears", ">JC<" in html_out or "JC" in html_out, True)
    # Standing instruction: no em dashes in anything a person reads. These
    # drafts are sent to people outside Genentech.
    check("no em dash in a letter that gets sent", "\u2014" in html_out, False)

    blank = [""] * len(cols)
    blank[cols.index("Drug name")] = "X (ydrug)"
    out2 = sheets.build_invites_html([blank], 2027, cols)
    check("with no contact it says so plainly",
          "NEEDS LOOKUP" in out2, True)
    check("and makes no promise to meet at ASCPT",
          "in person" in out2, False)


def _article(pmid, journal, authors_):
    """Minimal efetch-shaped XML. authors_ is [(fore, last, affiliation)].

    Everything is escaped: real journal titles contain ampersands
    ("Diabetes, obesity & metabolism"), and an unescaped one makes the whole
    document unparseable.
    """
    esc = xml.sax.saxutils.escape
    people = "".join(
        f"<Author><LastName>{esc(l)}</LastName><ForeName>{esc(f)}</ForeName>"
        f"<AffiliationInfo><Affiliation>{esc(a)}</Affiliation></AffiliationInfo>"
        f"</Author>" for f, l, a in authors_)
    return (f"<PubmedArticle><MedlineCitation><PMID>{pmid}</PMID><Article>"
            f"<Journal><Title>{esc(journal)}</Title></Journal>"
            f"<AuthorList>{people}</AuthorList></Article>"
            f"</MedlineCitation></PubmedArticle>")


def _wrap(*arts):
    return ("<PubmedArticleSet>" + "".join(arts) + "</PubmedArticleSet>").encode()


LILLY = "Eli Lilly and Company, Indianapolis, Indiana, USA."
SPERO = "Spero Therapeutics, Inc., Cambridge, Massachusetts, USA."
MDA = "Department of Leukemia, The University of Texas MD Anderson Cancer Center."
ABBVIE = "AbbVie, Inc, North Chicago, IL."


def test_clinpharm_tier1():
    """Sponsor-affiliated first author on a clin pharm study ranks top."""
    import authors
    section("Tier 1 — a clinical pharmacology study, sponsor at the front")
    xml = _wrap(_article("41994902", "Diabetes, obesity & metabolism", [
        ("Xiaosu", "Ma", LILLY), ("Ying Grace", "Li", LILLY),
        ("Sohini", "Raha", LILLY), ("Shobha", "Bhattachar", LILLY)]))
    arts = authors.parse_articles(xml)
    for a in arts:
        a["tier"] = 1
    ranked = authors.rank(arts, "ELI LILLY AND CO")
    check("six-author Lilly paper parses", len(arts[0]["authors"]), 4)
    check("Xiaosu Ma ranks first", ranked[0]["name"], "Xiaosu Ma")
    check("recognised as at the sponsor", ranked[0]["sponsor"], True)
    check("org is the company, not the city", ranked[0]["org"],
          "Eli Lilly and Company")


def test_clinpharm_acquisition_and_drift():
    """FDA sponsor differs from who ran the programme; people move."""
    import authors
    section("Tier 1 — an acquisition, and an author who has since moved")
    xml = _wrap(
        _article("38432233", "Antimicrobial agents and chemotherapy", [
            ("Vipul K", "Gupta", SPERO),
            ("Amanda", "Ek", "Takeda Pharmaceuticals, Cambridge, MA, USA."),
            ("Angela", "Talley", SPERO)]),
        _article("35762796", "Clinical pharmacology in drug development", [
            ("Vipul K", "Gupta", SPERO), ("Angela K", "Talley", SPERO)]))
    arts = authors.parse_articles(xml)
    for a in arts:
        a["tier"] = 1
    # tebipenem is approved to GSK; every paper is authored out of Spero
    ranked = authors.rank(arts, "GLAXOSMITHKLINE")
    names = [p["name"] for p in ranked]
    check("programme org inferred as Spero", authors.programme_org(arts),
          "Spero Therapeutics")
    check("Gupta found despite the sponsor mismatch", names[0], "Vipul K Gupta")
    check("credited to the programme, not the FDA sponsor",
          ranked[0]["programme"], True)
    check("middle initial does not split Angela Talley",
          sum(1 for n in names if "Talley" in n), 1)


def test_clinpharm_dose_escalation():
    """The hard case: 24 authors, the clin pharm group buried at 20-22."""
    import authors
    section("Tier 3 — dose escalation, where author position is noise")
    people = ([("Naveen", "Pemmaraju", MDA)]
              + [("A", f"Investigator{i}",
                  f"University Hospital {i}, City, Country.") for i in range(18)]
              + [("Yining", "Du", ABBVIE),
                 ("Sribalaji", "Lakshmikanthan", ABBVIE),
                 ("Jalaja", "Potluri", ABBVIE),
                 ("Naval G", "Daver", MDA)])
    xml = _wrap(_article("38776914", "Journal of clinical oncology", people))
    arts = authors.parse_articles(xml)
    for a in arts:
        a["tier"] = 3
    ranked = authors.rank(arts, "ABBVIE INC")
    names = {p["name"] for p in ranked}
    check("all three AbbVie authors surface",
          {"Yining Du", "Sribalaji Lakshmikanthan", "Jalaja Potluri"} <= names, True)
    check("the MD Anderson first author is not returned",
          "Naveen Pemmaraju" in names, False)
    check("the MD Anderson last author is not returned",
          "Naval G Daver" in names, False)
    check("no site investigators returned",
          any("Investigator" in n for n in names), False)


def test_sponsor_affiliation_matching():
    import authors
    section("Sponsor matching against real affiliation strings")
    check("Lilly matches its own affiliation",
          authors._is_sponsor(LILLY, "ELI LILLY AND CO"), True)
    check("AbbVie matches", authors._is_sponsor(ABBVIE, "ABBVIE INC"), True)
    check("Vera does not match Verastem",
          authors._is_sponsor("Verastem Oncology, Needham, MA, USA.",
                              "VERA THERAPEUTICS INC."), False)
    check("MD Anderson is not a sponsor match",
          authors._is_sponsor(MDA, "ABBVIE INC"), False)


def test_modality_axes():
    """The two-axis taxonomy, offline.

    These are the two numbers that were wrong. The flat label set counted each
    paper once under whatever label matched, so the four kinase inhibitors did
    not count as small molecules and the ADC and three T-cell engagers did not
    count as antibodies. A gap test built on 10 and 3 can call a modality
    uncovered that the series has published on four times.
    """
    import classify
    import config
    import gaps
    section("Modality taxonomy: two axes with roll-up")

    cov, tcov = {}, {}
    for d in config.PUBLISHED_MOA_DRUGS:
        rec = {"ingredient": d.lower(), "ingredient_raw": d}
        fmt, target, _g = classify.classify_modality(rec)
        for label in config.modality_chain(fmt):
            cov[label] = cov.get(label, 0) + 1
        if target:
            tcov[target] = tcov.get(target, 0) + 1

    check("published fixture still has 22 drugs",
          len(config.PUBLISHED_MOA_DRUGS), 22)
    check("small molecule rolls up to 14 (was 10)", cov.get("Small molecule"), 14)
    check("antibody rolls up to 7 (was 3)", cov.get("Antibody"), 7)
    check("the ADC is still counted once as an ADC",
          cov.get("Antibody-drug conjugate"), 1)

    # Roll-up has to be directional, or "has the series covered ADCs?" would
    # answer yes on the strength of three unrelated bispecifics.
    chain = config.modality_chain("Antibody-drug conjugate")
    check("ADC rolls up to Antibody", "Antibody" in chain, True)
    check("Antibody does not roll down to ADC",
          "Antibody-drug conjugate" in config.modality_chain("Antibody"), False)
    check("roll-up terminates", len(chain) < 10, True)

    # Mixing the axes is the original bug and the easiest thing to put back.
    formats, targets = set(), set()
    for stem, fmt, target, _gap in list(config.MODALITY_STEMS) + list(
            config.MODALITY_KEYWORDS):
        formats.add(fmt)
        if target:
            targets.add(target)
    check("no label is used on both axes", sorted(formats & targets), [])
    check("every format is declared in MODALITY_FORMATS",
          sorted(formats - set(config.MODALITY_FORMATS)), [])
    check("every parent is itself a declared format",
          sorted(set(config.MODALITY_PARENT.values())
                 - set(config.MODALITY_FORMATS)), [])

    # Same target class, different format: the pair that proves the axes are
    # genuinely independent rather than two names for one thing.
    sema = classify.classify_modality({"ingredient": "semaglutide",
                                       "ingredient_raw": "SEMAGLUTIDE"})
    orfo = classify.classify_modality({"ingredient": "orforglipron",
                                       "ingredient_raw": "ORFORGLIPRON"})
    check("semaglutide is a peptide", sema[0], "Peptide")
    check("orforglipron is a small molecule", orfo[0], "Small molecule")
    check("both are GLP-1 receptor agonists", sema[1] == orfo[1], True)

    # A kinase inhibitor must not read as a format gap: the series has 14 small
    # molecules, four of them kinase inhibitors.
    report = {"live": True, "coverage": cov, "targets": tcov,
              "gap_categories": set(config.GAP_CATEGORIES)}
    hit, tier, why = gaps.is_gap({"ingredient": "zanubrutinib",
                                  "ingredient_raw": "ZANUBRUTINIB"}, report)
    check("a kinase inhibitor is not a modality gap", tier != "modality", True)
    check("and the reason quotes the rolled-up count",
          "Small molecule: 14" in why or tier == "target", True)

    check("modality() still returns a 2-tuple for old callers",
          len(classify.modality({"ingredient": "zanubrutinib"})), 2)

    # A Purple Book record keeps only the first word in `ingredient`, so a stem
    # carried by the second word used to be invisible and every -leucel cell
    # therapy fell through to the fallback. CBER products are the cell, gene and
    # vaccine gaps, so that was the worst place to lose the stem table.
    tecelra = {"ingredient": "afamitresgene",
               "ingredient_raw": "afamitresgene autoleucel",
               "brand": "tecelra", "center": "CBER",
               "appl_type": "BLA", "appl_no": "125789"}
    check("a second-word stem is seen", classify.classify_modality(tecelra)[0],
          "Engineered cell therapy")
    check("and it keeps its cell & gene gap category",
          classify.classify_modality(tecelra)[2], "Cell & gene therapy")
    # -leucel is the WHO stem for an autologous T-cell product. CAR specificity
    # comes from -cabtagene, so calling every -leucel a CAR-T mislabelled this
    # TCR-T therapy.
    check("a TCR-T is not called a CAR-T",
          classify.classify_modality(tecelra)[0] != "CAR-T cell therapy", True)
    check("a -cabtagene product is still a CAR-T",
          classify.classify_modality(
              {"ingredient": "obecabtagene",
               "ingredient_raw": "Obecabtagene autoleucel"})[0],
          "CAR-T cell therapy")

    # The fallback. Defaulting everything to small molecule filed a fibrinogen
    # concentrate as one, and the old CBER branch asserted the named gap
    # category "Cell & gene therapy" on nothing but the reviewing centre, so
    # fibrinogen scored +40 as an untouched cell and gene therapy.
    fib = {"ingredient": "fibrinogen human chmt",
           "ingredient_raw": "fibrinogen, human-chmt", "center": "CBER",
           "appl_type": "BLA", "appl_no": "125833"}
    check("a plasma protein is not a small molecule",
          classify.classify_modality(fib)[0], "Biologic (unspecified)")
    check("and CBER alone no longer asserts a cell & gene gap",
          classify.classify_modality(fib)[2], None)
    check("an NDA with no stem match is still a small molecule",
          classify.classify_modality(
              {"ingredient": "maribavir", "ingredient_raw": "MARIBAVIR",
               "appl_type": "NDA", "appl_no": "215596"})[0], "Small molecule")
    check("the unmatched fallback rolls up to nothing",
          config.modality_chain("Biologic (unspecified)"),
          ["Biologic (unspecified)"])


def test_class_provenance():
    """Every target class says where it came from, and offline must not fail."""
    import rxclass
    section("Target-class provenance")
    rec = {"ingredient": "teclistamab", "ingredient_raw": "TECLISTAMAB-CQYV"}

    real = rxclass.epc
    try:
        rxclass.epc = lambda *a, **k: "Janus Kinase Inhibitor"
        cls, src = rxclass.target_class(rec, local="Kinase inhibitor")
        check("FDA class wins over the local table", cls, "Janus Kinase Inhibitor")
        check("and is labelled as FDA's", src,
              "FDA established pharmacologic class")

        # No FDA answer: fall back, and say so. A stem guess presented as an FDA
        # assignment on a sheet people act on is the failure to avoid.
        rxclass.epc = lambda *a, **k: None
        cls, src = rxclass.target_class(rec, local="Kinase inhibitor")
        check("falls back to the stem table", cls, "Kinase inhibitor")
        check("and says the stem table gave it", src, "INN stem")
        check("no class at all reports no source",
              rxclass.target_class(rec, local=None), ("", ""))

        # Network down. RxClass is a nice-to-have; a failed run is not.
        def boom(*a, **k):
            raise OSError("no route to host")
        rxclass.epc = boom
        check("a failed lookup falls back instead of raising",
              rxclass.target_class(rec, local="Kinase inhibitor"),
              ("Kinase inhibitor", "INN stem"))
        rxclass.epc = real
        check("a nonsense drug name returns None, not an exception",
              rxclass.epc("zzzznotadrug", use_cache=False), None)
    finally:
        rxclass.epc = real


def test_bundle_module_list(tmp):
    """Every module the engine imports is in the bundle's MODULES list.

    The list is hand-maintained, and a module missing from it does not fail the
    build. It fails at the moment a user clicks the button that needs it, as an
    ImportError that only ever appears inside the bundle: rxclass was added and
    left off, so the hot list worked from source and raised in the .app.
    """
    import ast
    import glob
    section("Bundle module list is closed under import")
    here = os.path.dirname(os.path.abspath(__file__))
    try:
        import build_single_file
    except Exception:
        check("build_single_file not present (running from a bundle), skipped",
              True, True)
        return
    declared = set(build_single_file.MODULES)
    # The builder itself is a developer tool and is imported only by this test.
    # Bundling it would ship the build system inside its own output.
    local = {os.path.splitext(os.path.basename(f))[0]
             for f in glob.glob(os.path.join(here, "*.py"))} - {"build_single_file"}
    missing = {}
    for name in sorted(declared):
        path = os.path.join(here, name + ".py")
        if not os.path.exists(path):
            missing[name] = "declared but no such file"
            continue
        tree = ast.parse(open(path).read())
        for node in ast.walk(tree):
            mods = []
            if isinstance(node, ast.Import):
                mods = [a.name.split(".")[0] for a in node.names]
            elif isinstance(node, ast.ImportFrom) and node.level == 0:
                mods = [(node.module or "").split(".")[0]]
            for m in mods:
                if m in local and m not in declared and m != name:
                    missing.setdefault(m, f"imported by {name}")
    check("no runtime import is left out of the bundle",
          sorted(f"{k} ({v})" for k, v in missing.items()), [])
    check("rxclass specifically is bundled", "rxclass" in declared, True)


def test_member_column(tmp):
    """The two engine-filled columns, from an imported ASCPT export.

    Every edge here came from a real export's shape: a person repeated across
    communities, a lookalike company, a member with no address, and a
    "Clinical Pharmacy" member who is a different profession.
    """
    section("ClinPharm ASCPT Member, from the imported membership list")
    import sheets

    rows = [["First Name", "Last Name", "Company", "Primary Discipline",
             "Email", "Member Since"],
            ["Amita", "Joshi", "Genentech, Inc.", "Clinical Pharmacology",
             "ajoshi@gene.com", "2015"],
            ["Amita", "Joshi", "Genentech, Inc.", "Pharmacometrics",
             "ajoshi@gene.com", "2015"],
            ["Rong", "Shi", "Genentech Inc", "Clinical Pharmacology", 
             "shi.rong@gene.com", "2018"],
            ["Pat", "Noname", "Genentech, Inc.", "Clinical Pharmacology", "", "2020"],
            ["Chris", "Reg", "Genentech, Inc.", "Regulatory Affairs",
             "cr@gene.com", "2019"],
            ["Sam", "Pharm", "Genentech, Inc.", "Clinical Pharmacy",
             "sp@gene.com", "2021"],
            ["Lee", "Else", "Verastem Oncology", "Clinical Pharmacology",
             "lee@verastem.com", "2022"],
            ["", "NoOrg", "", "Clinical Pharmacology", "x@y.com", "2022"]]
    p = store.xlsx_write(os.path.join(tmp, "ascpt members.xlsx"),
                         {"Members": rows})

    check("recognised as a member directory", sheets.looks_like_members(p), True)
    recs = sheets.load_member_records(p)
    check("the row with no organisation is dropped", len(recs), 7)
    check("the email column is read", recs[0].get("email"), "ajoshi@gene.com")
    check("the discipline column is read", recs[0].get("discipline"),
          "Clinical Pharmacology")

    names, emails, how = sheets.members_at_company("GENENTECH INC", recs)
    check("every match is listed, not a sample", names.count("\n") + 1, 3)
    check("a person repeated across communities appears once",
          names.count("Amita Joshi"), 1)
    check("a comma in the company name does not block the match",
          "Rong Shi" in names, True)
    check("Regulatory Affairs is excluded", "Chris Reg" in names, False)
    # Different profession. An invitation to write a mechanism review landing on
    # a hospital pharmacist is a wasted approach and a slightly insulting one.
    check("Clinical Pharmacy is excluded", "Sam Pharm" in names, False)
    check("the match count is reported", "3 match(es)" in how, True)

    # The two cells are read across, so a missing address must hold its place
    # rather than shift every line below it onto the wrong person.
    nl, el = names.split("\n"), emails.split("\n")
    gap = next(i for i, v in enumerate(nl) if "Pat Noname" in v)
    check("a member with no address holds its place",
          el[gap].endswith("-"), True)
    check("and the addresses line up with the names",
          [e.split(". ", 1)[1] for e in el],
          ["ajoshi@gene.com" if "Joshi" in n else
           "-" if "Noname" in n else "shi.rong@gene.com" for n in nl])
    check("and every line is numbered the same way",
          [l.split(".")[0] for l in names.split("\n")],
          [l.split(".")[0] for l in emails.split("\n")])

    # The company test is authors._is_sponsor, which knows these two apart.
    n2, _e2, how2 = sheets.members_at_company("VERA THERAPEUTICS INC.", recs)
    check("Verastem is not matched to Vera Therapeutics", n2, "")
    check("and the row says why", how2, "no members at this company")

    # An export with no discipline column must not silently claim everyone is a
    # clinical pharmacologist; it lists them and says the filter did not apply.
    plain = [["Name", "Organization"], ["A Person", "Autolus, Inc."]]
    p2 = store.xlsx_write(os.path.join(tmp, "plain.xlsx"), {"S": plain})
    n3, _e3, how3 = sheets.members_at_company(
        "Autolus, Inc.", sheets.load_member_records(p2))
    check("with no discipline column the member is still listed", n3, "A Person")
    check("and the cell says the filter did not apply",
          "no discipline" in how3, True)
    check("and that no address was available",
          "no email column" in how3, True)

    check("clinical pharmacy alone is not clin pharm",
          sheets.is_clinpharm("Clinical Pharmacy"), False)
    check("translational medicine is", sheets.is_clinpharm("Translational"), True)
    check("a blank discipline is not", sheets.is_clinpharm(""), False)

    # The engine's columns must stay out of the carry-forward. Their headers
    # contain both "ascpt" and "member", so the loose matcher would otherwise
    # land them on the manual directory column and overwrite a hand lookup.
    check("the member column is not carried forward",
          sheets._human_col(sheets.MEMBER_COL), None)
    check("nor is the email column",
          sheets._human_col(sheets.MEMBER_EMAIL_COL), None)
    check("a reworded manual column still is, not mistaken for the engine's",
          sheets._human_col("ASCPT member (clin pharm)"), sheets.ASCPT_COL)
    check("no engine column is in HUMAN_COLS",
          [c for c in sheets.ENGINE_COLS if c in sheets.HUMAN_COLS], [])
    check("the sheet has both engine columns",
          [c for c in sheets.ENGINE_COLS if c not in config.HOTLIST_COLUMNS], [])

    # No file at all is the state the team is in until ASCPT sends the export.
    real = config.members_file
    try:
        config.members_file = lambda: os.path.join(tmp, "nope.xlsx")
        got, note = sheets._member_records()
        check("no membership file is not an error", got, [])
        check("and the blank column is explained", "no ASCPT membership list" in note,
              True)
    finally:
        config.members_file = real


def main():
    tmp = tempfile.mkdtemp(prefix="moa-selftest-")
    print(f"Self-test — local file layer. Scratch dir: {tmp}")
    try:
        test_column_letters()
        test_round_trip(tmp)
        test_shape_edges(tmp)
        test_multi_tab(tmp)
        test_excel_saved_format(tmp)
        test_rewrite_shrinks(tmp)
        test_annotation_merge(tmp)
        test_org_matching()
        test_roster_from_program()
        test_load_roster(tmp)
        test_build_attendance(tmp)
        test_attendance_columns(tmp)
        test_year_rollover()
        test_history_accumulates(tmp)
        test_bundle_arg_dispatch(tmp)
        test_gui_endpoints(tmp)
        test_python_floor(tmp)
        test_contact_carry_forward(tmp)
        test_invitation_drafts()
        test_member_column(tmp)
        test_clinpharm_tier1()
        test_clinpharm_acquisition_and_drift()
        test_clinpharm_dose_escalation()
        test_sponsor_affiliation_matching()
        test_modality_axes()
        test_class_provenance()
        test_bundle_module_list(tmp)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)

    print()
    if _fails:
        print(f"FAILED — {len(_fails)} check(s): {', '.join(_fails)}")
        return 1
    print("All checks passed. Nothing was left on disk.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
