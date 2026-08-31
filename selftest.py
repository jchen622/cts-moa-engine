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

    cols = ["Rank", "Drug (INN)", "Novelty", "AE owner", "Attending?", "Comments"]
    prior = [cols,
             ["1", "giredestrant", "84", "JC", "yes", "met at PI-072"],
             ["2", "obeldesivir", "72", "", "no", "declined"],
             ["3", "tirzepatide", "66", "AB", "", "chase in Feb"]]
    p = store.xlsx_write(os.path.join(tmp, "dossier.xlsx"), {"Dossier": prior})

    # A later run: re-ranked, tirzepatide gone, a new drug arrived.
    fresh = [["1", "tirzepatide", "70", "", "", ""],
             ["2", "giredestrant", "84", "", "", ""],
             ["3", "veligrotug", "61", "", "", ""]]
    merged = sheets.merge_annotations(fresh, p, cols)
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


def test_wider_dossier_merge(tmp):
    section("An old 16-column dossier merges into the 18-column layout")
    import sheets
    old_cols = ["Rank", "Drug (INN)", "Brand", "Sponsor", "Approval date", "Modality",
                "Gap flag", "Novelty", "Prior review?", "ASCPT presence",
                "Poster / session detail", "Contact", "Candidate authors",
                "AE owner", "Attending?", "Comments"]
    prior = [old_cols,
             ["1", "bulevirtide", "", "GILEAD", "", "", "", "56", "no", "", "",
              "", "", "JC", "yes", "met at PI-072"]]
    p = store.xlsx_write(os.path.join(tmp, "old-dossier.xlsx"), {"Dossier": prior})

    fresh = [["1", "bulevirtide"] + [""] * (len(config.DOSSIER_COLUMNS) - 2)]
    merged = sheets.merge_annotations(fresh, p)
    col = {c: i for i, c in enumerate(config.DOSSIER_COLUMNS)}
    check("notes survive the column insertion",
          [merged[0][col["AE owner"]], merged[0][col["Attending?"]],
           merged[0][col["Comments"]]],
          ["JC", "yes", "met at PI-072"])
    check("the new columns stay empty rather than picking up shifted values",
          [merged[0][col["Last year at ASCPT"]], merged[0][col["Who to find"]]],
          ["", ""])


# ------------------------------------------------- clinical-pharmacologist ID
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
        test_wider_dossier_merge(tmp)
        test_clinpharm_tier1()
        test_clinpharm_acquisition_and_drift()
        test_clinpharm_dose_escalation()
        test_sponsor_affiliation_matching()
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
