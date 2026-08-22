"""The two FDA approval feeds.

Both return normalised candidate records:
    {key, appl_no, center, sponsor_raw, brand, ingredient, approval_date,
     class_code, source}

Why two sources: Drugs@FDA covers NDAs and CDER-regulated BLAs (761xxx) but
omits CBER products entirely -- BLA 125730 (StrataGraft, a cell therapy) is
absent from it. The Purple Book change report is the complement, and is exactly
where the cell/gene/vaccine coverage gaps would show up.
"""
import csv
import io
import os
import re
import sys
import time
import urllib.request
import zipfile

import config

UA = {"User-Agent": "CTS-MOA-sourcing/1.0 (ASCPT editorial use)"}

# moiety stem -> earliest US approval date seen anywhere in the FDA record.
# Populated as a side effect of reading the source files; consulted by
# is_novel_agent() to reject new applications for already-approved molecules.
_MOIETY_INDEX = {}
# moiety stem -> {normalised route: earliest date}. A previously-approved
# molecule arriving by a route it has never used before is still interesting.
_ROUTE_INDEX = {}
# moiety stem -> {normalised brand: earliest date}
_BRAND_INDEX = {}


# Real routes of administration. The tail of Drugs@FDA's Form column is USUALLY
# the route ('VIAL; INTRAVENOUS') but sometimes a presentation ('VIAL;SINGLE-DOSE'),
# so it must be validated against a vocabulary rather than trusted.
ROUTE_VOCAB = {
    "oral", "intravenous", "subcutaneous", "intramuscular", "topical",
    "ophthalmic", "inhalation", "transdermal", "nasal", "sublingual",
    "vaginal", "rectal", "buccal", "otic", "irrigation", "intraperitoneal",
    "intrathecal", "intravitreal", "intraocular", "intradermal", "epidural",
    "intra-articular", "intracavernous", "intrauterine", "intravesical",
    "percutaneous", "dental", "urethral", "infiltration", "perfusion",
    "intracardiac", "intrapleural", "intralesional", "intracavitary",
    "implantation", "iontophoresis", "enteral", "intraduodenal",
}
# 'INJECTION' is a real tail but says nothing about the route, so it is not
# treated as informative.


def _route_from_form(form):
    """Extract a validated route from Drugs@FDA's Form column.

    Returns '' when the tail is a presentation ('SINGLE-DOSE'), a bare
    'INJECTION', or otherwise not a recognised route -- better to report no
    route than to invent one, since a bogus route reads as a NEW route and
    would wrongly promote a repeat application.
    """
    f = (form or "")
    if ";" not in f:
        return ""
    tail = f.rsplit(";", 1)[1].strip().lower()
    tail = re.sub(r"-\d+$", "", tail)                    # ORAL-28 -> oral
    parts = [p.strip() for p in re.split(r"[,/]", tail)]
    good = [p for p in parts if p in ROUTE_VOCAB]
    return ", ".join(good)


def _norm_route(route):
    """Collapse FDA route spellings so 'INTRAVENOUS; SUBCUTANEOUS' compares sanely."""
    r = (route or "").lower()
    r = re.sub(r"[^a-z; ]+", " ", r)
    parts = set()
    for p in re.split(r"[;,]", r):
        p = re.sub(r"\s+", " ", p).strip()
        if not p:
            continue
        # treat injection-site variants as distinct, but fold obvious synonyms
        p = {"iv": "intravenous", "im": "intramuscular", "sc": "subcutaneous",
             "subcut": "subcutaneous", "oral solution": "oral",
             "ophthalmic solution": "ophthalmic"}.get(p, p)
        parts.add(p)
    return parts


def _norm_brand(brand):
    b = re.sub(r"\s*\(.*$", "", (brand or "").lower())
    b = re.sub(r"[^a-z0-9 ]+", " ", b)
    return re.sub(r"\s+", " ", b).strip()


def _index_history(components, route, brand, date):
    """Record what was already known about a moiety, as of ``date``."""
    for comp in components:
        prev = _MOIETY_INDEX.get(comp)
        if prev is None or date < prev:
            _MOIETY_INDEX[comp] = date
        rr = _ROUTE_INDEX.setdefault(comp, {})
        for r in _norm_route(route):
            if r not in rr or date < rr[r]:
                rr[r] = date
        bb = _BRAND_INDEX.setdefault(comp, {})
        nb = _norm_brand(brand)
        if nb and (nb not in bb or date < bb[nb]):
            bb[nb] = date


def _fetch(url, dest=None, max_age_h=12):
    """GET with a simple on-disk cache so repeated runs don't hammer FDA."""
    if dest:
        path = os.path.join(config.CACHE, dest)
        if os.path.exists(path) and (time.time() - os.path.getmtime(path)) < max_age_h * 3600:
            return open(path, "rb").read()
    req = urllib.request.Request(url, headers=UA)
    with urllib.request.urlopen(req, timeout=120) as r:
        data = r.read()
    if dest:
        os.makedirs(config.CACHE, exist_ok=True)
        open(os.path.join(config.CACHE, dest), "wb").write(data)
    return data


SALT_WORDS = (r"hydrochloride|hcl|sodium|calcium|potassium|sulfate|tartrate|"
              r"phosphate|maleate|mesylate|besylate|citrate|acetate|fumarate|"
              r"succinate|decanoate|dihydrate|monohydrate|disodium|bromide|"
              r"hydrobromide|pivoxil|alfa|beta|recombinant|injection|autoleucel")


def _stem(name):
    """Normalise an ingredient string to a comparable INN stem."""
    n = (name or "").lower().strip()
    n = re.sub(r"\s*\([^)]*\)", "", n)              # drop parentheticals
    n = re.sub(r"-[a-z]{4}\b", " ", n)              # drop biologic suffix: -nbln, -eknm
    n = re.sub(rf"\b({SALT_WORDS})\b", " ", n)
    n = re.sub(r"[^a-z0-9 ]+", " ", n)
    return re.sub(r"\s+", " ", n).strip()


def _components(ingredient):
    """Split a combination product into individually-checkable moieties.

    Drugs@FDA separates combination actives with ';'. A fixed-dose combination
    of two already-approved drugs is not a novel agent; a combination that
    introduces one new moiety is.
    """
    parts = re.split(r"[;]", ingredient or "")
    out = []
    for p in parts:
        s = _stem(p)
        if s:
            out.append(s)
    return out or ([_stem(ingredient)] if _stem(ingredient) else [])


def _rec(appl_no, center, sponsor, brand, ingredient, date, class_code, source,
         appl_type="", route=""):
    st = _stem(ingredient)
    return {
        "key": f"{appl_no}:{st}",
        "appl_no": appl_no,
        "appl_type": appl_type,
        "center": center,
        "sponsor_raw": (sponsor or "").strip(),
        "brand": (brand or "").strip(),
        "ingredient": st,
        "ingredient_raw": (ingredient or "").strip(),
        "components": _components(ingredient),
        "route": (route or "").strip(),
        "approval_date": date,
        "class_code": class_code,
        "source": source,
    }


# ------------------------------------------------------------------ Drugs@FDA
def drugs_at_fda_nmes(since=None, until=None, verbose=True):
    """New molecular/biological entities from the Drugs@FDA bulk relational files.

    openFDA cannot express this query: it matches conditions across the whole
    application document rather than within one submission, so a
    date-range + class search returns approvals from unrelated years. The bulk
    files let us join properly, per submission.
    """
    raw = _fetch(config.DRUGS_AT_FDA_ZIP, "drugsatfda.zip", max_age_h=12)
    zf = zipfile.ZipFile(io.BytesIO(raw))

    def tsv(name):
        with zf.open(name) as fh:
            text = io.TextIOWrapper(fh, encoding="utf-8", errors="replace")
            return list(csv.DictReader(text, delimiter="\t"))

    cls = {r["SubmissionClassCodeID"]: r["SubmissionClassCode"].strip()
           for r in tsv("SubmissionClass_Lookup.txt")}
    apps = {r["ApplNo"]: r for r in tsv("Applications.txt")}
    products = {}
    for p in tsv("Products.txt"):
        products.setdefault(p["ApplNo"], []).append(p)

    # Earliest approval date per application, used to build the moiety index.
    first_ap = {}
    subs = tsv("Submissions.txt")
    for s in subs:
        if s["SubmissionType"] != "ORIG" or s["SubmissionStatus"] != "AP":
            continue
        d = (s["SubmissionStatusDate"] or "")[:10]
        if not d:
            continue
        prev = first_ap.get(s["ApplNo"])
        if prev is None or d < prev:
            first_ap[s["ApplNo"]] = d

    # Index every application on file (including ANDAs) by moiety, route and
    # brand. This is what distinguishes a new molecule -- or a known molecule
    # by a new route under a new brand -- from a plain repeat application.
    for appl_no, pl in products.items():
        d = first_ap.get(appl_no)
        if not d:
            continue
        for p in pl:
            _index_history(_components(p.get("ActiveIngredient", "")),
                           _route_from_form(p.get("Form", "")),
                           p.get("DrugName", ""), d)

    out = []
    for s in subs:
        if s["SubmissionType"] != "ORIG" or s["SubmissionStatus"] != "AP":
            continue
        date = (s["SubmissionStatusDate"] or "")[:10]
        if not date:
            continue
        if since and date < since:
            continue
        if until and date > until:
            continue
        code = cls.get(s["SubmissionClassCodeID"], "")
        if code not in config.CANDIDATE_CLASSES:
            continue
        a = apps.get(s["ApplNo"], {})
        # Belt and braces: generics are ANDAs and never carry an NME class,
        # but never let one through on a lookup miss.
        if a.get("ApplType", "").upper() not in ("NDA", "BLA"):
            continue
        pl = products.get(s["ApplNo"], [])
        p0 = pl[0] if pl else {}
        out.append(_rec(
            s["ApplNo"], "CDER", a.get("SponsorName", ""),
            p0.get("DrugName", ""), p0.get("ActiveIngredient", ""),
            date, code, "Drugs@FDA", a.get("ApplType", ""),
            _route_from_form(p0.get("Form", "")),
        ))
    out.sort(key=lambda r: r["approval_date"])
    if verbose:
        print(f"  Drugs@FDA: {len(out)} NME original approvals"
              f"{f' from {since}' if since else ''}{f' to {until}' if until else ''}",
              file=sys.stderr)
    return out


# ----------------------------------------------------------------- Purple Book
MONTHS = ["january", "february", "march", "april", "may", "june", "july",
          "august", "september", "october", "november", "december"]


def purple_book_urls():
    """Discover published monthly change-report CSVs.

    URLs must be discovered, not constructed: FDA publishes with lag, and a
    guessed .../2026/...august...csv 404s.
    """
    html = _fetch(config.PURPLE_BOOK_DOWNLOADS, "pb_downloads.html", max_age_h=24)
    text = html.decode("utf-8", errors="replace")
    found = re.findall(
        r'https://www\.accessdata\.fda\.gov/drugsatfda_docs/PurpleBook/'
        r'(\d{4})/purplebook-search-([a-z]+)-data-download\.csv', text)
    urls = []
    for year, month in sorted(set(found), key=lambda t: (t[0], MONTHS.index(t[1])
                                                         if t[1] in MONTHS else 99)):
        urls.append((year, month,
                     f"https://www.accessdata.fda.gov/drugsatfda_docs/PurpleBook/"
                     f"{year}/purplebook-search-{month}-data-download.csv"))
    return urls


def _parse_purple_book(blob):
    """Parse one monthly change report.

    Format traps: a 3-line title preamble (header lands on row index 3), a BOM,
    and stray repeated header rows inside the data.
    """
    text = blob.decode("utf-8-sig", errors="replace")
    rows = list(csv.reader(io.StringIO(text)))
    hi = None
    for i, r in enumerate(rows[:12]):
        if "Applicant" in r and "BLA Type" in r:
            hi = i
            break
    if hi is None:
        raise ValueError("Purple Book: header row not found in first 12 lines")
    hdr = rows[hi]
    idx = {c: i for i, c in enumerate(hdr)}
    data = []
    for r in rows[hi + 1:]:
        if len(r) != len(hdr):
            continue
        if r[idx["Applicant"]] == "Applicant":      # repeated header inside data
            continue
        data.append(r)
    return hdr, idx, data


def purple_book_new_cber(since=None, until=None, verbose=True, max_months=18):
    """New CBER-regulated biologic licences from the Purple Book change reports.

    The file is already a delta: the N/R/U column marks New / added in Release /
    Updated, so no month-over-month diffing is needed. 351(k) biosimilars and
    interchangeables are excluded outright.
    """
    out, seen = [], set()
    urls = purple_book_urls()[-max_months:]
    for year, month, url in urls:
        try:
            blob = _fetch(url, f"pb_{year}_{month}.csv", max_age_h=24 * 14)
            hdr, idx, data = _parse_purple_book(blob)
        except Exception as e:                       # a single bad month must not kill the run
            print(f"  Purple Book {year}-{month}: SKIPPED ({e})", file=sys.stderr)
            continue
        # Each monthly file carries the FULL licence history, not just the
        # changed rows -- so use it to extend the known-moiety index for
        # biologics, which Drugs@FDA does not cover.
        for r in data:
            d = _pb_date(r[idx["Approval Date"]])
            if not re.match(r"\d{4}-\d{2}-\d{2}", d or ""):
                continue
            _index_history(_components(r[idx["Proper Name"]]),
                           r[idx["Route of Administration"]],
                           r[idx["Proprietary Name"]], d)
        for r in data:
            if r[idx["N/R/U"]] != "N":
                continue
            if r[idx["Submission Type"]] != "Original":
                continue
            if "351(k)" in r[idx["BLA Type"]]:       # biosimilar / interchangeable
                continue
            if r[idx["Center"]] != "CBER":           # CDER products come from Drugs@FDA
                continue
            date = _pb_date(r[idx["Approval Date"]])
            if since and date < since:
                continue
            if until and date > until:
                continue
            rec = _rec(r[idx["BLA Number"]], "CBER", r[idx["Applicant"]],
                       r[idx["Proprietary Name"]], r[idx["Proper Name"]],
                       date, r[idx["BLA Type"]], f"PurpleBook {year}-{month}",
                       "BLA", r[idx["Route of Administration"]])
            if rec["key"] in seen:
                continue
            seen.add(rec["key"])
            out.append(rec)
    out.sort(key=lambda r: r["approval_date"])
    if verbose:
        print(f"  Purple Book: {len(out)} new CBER original licences "
              f"across {len(urls)} monthly reports", file=sys.stderr)
    return out


_MON = {"jan": "01", "feb": "02", "mar": "03", "apr": "04", "may": "05",
        "jun": "06", "jul": "07", "aug": "08", "sep": "09", "oct": "10",
        "nov": "11", "dec": "12"}


def _pb_date(s):
    """Normalise Purple Book approval dates to ISO.

    The column uses at least two formats -- '25-Mar-25' and 'April 28, 2025'.
    Missing this second one silently defeats every date filter downstream,
    because the raw string sorts after any ISO date.
    """
    s = (s or "").strip()
    m = re.match(r"(\d{1,2})-([A-Za-z]{3})-(\d{2})$", s)
    if m:
        day, mon, yy = m.groups()
        return f"20{yy}-{_MON.get(mon.lower(), '01')}-{int(day):02d}"
    m = re.match(r"([A-Za-z]+)\s+(\d{1,2}),\s*(\d{4})$", s)
    if m:
        mon, day, yyyy = m.groups()
        return f"{yyyy}-{_MON.get(mon[:3].lower(), '01')}-{int(day):02d}"
    m = re.match(r"(\d{4})-(\d{2})-(\d{2})", s)
    if m:
        return m.group(0)
    if s and s.lower() != "invalid date":   # FDA's own sentinel, not our problem
        print(f"  WARNING: unparsed approval date {s!r}", file=sys.stderr)
    return s


GENERIC_NAME_MARKERS = (
    "concentrate", "immune globulin", "plasma", "albumin (human)",
    "red blood cells", "platelets", "allergenic extract", "tuberculin",
)


def is_novel_agent(rec, grace_days=400):
    """Is this a genuinely new agent, or a new application for an old one?

    Returns (bool, reason). The NME submission class already excludes generics
    (ANDAs) and 505(b)(2) reformulations, but it does NOT exclude a brand-new
    BLA for a molecule that has been marketed for years -- e.g. Outlook's
    ophthalmic bevacizumab, or a reformulated COVID-19 vaccine. Those are new
    products, not new mechanisms, and are not MOA mini-review material.

    A moiety counts as previously known if the FDA record contains an approval
    of the same moiety more than ``grace_days`` before this one. The grace
    window absorbs same-molecule sibling applications filed around one launch.
    """
    from datetime import date as _date, timedelta

    if not rec.get("components"):
        return False, "no parsable active moiety"

    name = (rec.get("ingredient_raw") or "").lower()
    for marker in GENERIC_NAME_MARKERS:
        if marker in name:
            return False, f"plasma-derived / non-novel class ({marker})"

    try:
        this = _date.fromisoformat(rec["approval_date"])
    except (ValueError, TypeError):
        return True, "novel (approval date unparsed; not excluded)"

    cutoff = this - timedelta(days=grace_days)
    novel_parts, known_parts = [], []
    for comp in rec["components"]:
        prior = _MOIETY_INDEX.get(comp)
        older = False
        if prior:
            try:
                older = _date.fromisoformat(prior) < cutoff
            except ValueError:
                older = False
        (known_parts if older else novel_parts).append(
            f"{comp}{f' (approved {prior})' if older else ''}")

    if novel_parts and known_parts:
        return True, ("novel component " + ", ".join(novel_parts)
                      + " in combination with " + ", ".join(known_parts))
    if novel_parts:
        return True, "first approval of this moiety"

    # Every moiety is previously approved -- but a known molecule delivered by
    # a route it has never used before is a real clinical-pharmacology story
    # (SC versions of IV biologics, ophthalmic bevacizumab). Keep those,
    # especially when they launch under their own brand name.
    new_routes, new_brand = set(), None
    for comp in rec["components"]:
        prior_routes = {r: d for r, d in _ROUTE_INDEX.get(comp, {}).items()
                        if _before(d, cutoff)}
        for r in _norm_route(rec.get("route")):
            if r and r not in prior_routes:
                new_routes.add(r)
        prior_brands = {b for b, d in _BRAND_INDEX.get(comp, {}).items()
                        if _before(d, cutoff)}
        nb = _norm_brand(rec.get("brand"))
        if nb and nb not in prior_brands:
            new_brand = rec.get("brand")

    if new_routes:
        return True, ("new route: " + ", ".join(sorted(new_routes))
                      + (f"; new brand {new_brand}" if new_brand else "")
                      + " -- moiety previously approved (" + "; ".join(known_parts) + ")")

    # No usable route data (Drugs@FDA often encodes a presentation instead of a
    # route). Fall back to the marker that FDA itself applied: an ORIGINAL
    # application classed as a new molecular entity, launching under a brand
    # this moiety has never carried, is a reformulation/new-route story worth
    # keeping -- e.g. ophthalmic bevacizumab as LYTENAVA.
    if new_brand and rec.get("class_code") in config.NME_CLASSES:
        return True, (f"new brand {new_brand} on an FDA NME-classed original "
                      f"application (likely new route/formulation) -- moiety "
                      f"previously approved (" + "; ".join(known_parts) + ")")

    return False, "previously approved moiety and route: " + "; ".join(known_parts)


def _before(iso, cutoff):
    from datetime import date as _date
    try:
        return _date.fromisoformat(iso) < cutoff
    except (ValueError, TypeError):
        return False


def collect(since=None, until=None, verbose=True, novel_only=True):
    """Both feeds, deduplicated, filtered to genuinely novel agents."""
    recs, seen, rejected = [], set(), []
    for r in drugs_at_fda_nmes(since, until, verbose) + \
             purple_book_new_cber(since, until, verbose):
        if r["key"] in seen or not r["ingredient"]:
            continue
        seen.add(r["key"])
        novel, reason = is_novel_agent(r)
        r["novelty_reason"] = reason
        if novel_only and not novel:
            rejected.append(r)
            continue
        recs.append(r)
    recs.sort(key=lambda r: r["approval_date"])
    if verbose and rejected:
        print(f"  excluded {len(rejected)} non-novel agent(s):", file=sys.stderr)
        for r in rejected:
            print(f"     {r['ingredient'][:34]:34s} {r['novelty_reason']}",
                  file=sys.stderr)
    return recs
