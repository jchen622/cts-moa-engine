"""What the MOA mini-review series has and has not covered, computed live.

The question the hot list exists to answer is "which novel mechanisms has the
series never published on?", and that answer changes every month. It cannot come
from a list in `config.py`: `PUBLISHED_MOA_DRUGS` sat at 19 while the series had
published 22, and `GAP_CATEGORIES` was copied by hand off a slide.

So the published set is fetched from PubMed on every run.

Two rules carry the weight:

**A paper with no publication date is not published.** Fruquintinib was indexed
with an October issue while still unreleased, and counting it put a wrong 22 on
a slide that was about to be presented. The PMC release date is the test.

**A bucket with zero published reviews is a gap, counted on two axes.** Molecular
format (small molecule, antibody, oligonucleotide) and target class (kinase
inhibitor, GLP-1 receptor agonist) are different questions and were previously
one flat set, which made the counts incomparable: 18 of the 22 published papers
were filed by format and 4 by target class.

Format counts ROLL UP. An antibody-drug conjugate is an antibody and a kinase
inhibitor is a small molecule, so an ADC increments both of its labels. Before
the roll-up small molecule read 10 when it was 14 and antibody read 3 when it
was 7, which let a modality be called uncovered that the series had published on
four times.

Buckets come from `classify.classify_modality()`, the engine's own vocabulary,
so the gap list cannot drift away from how candidates are tagged. A second
hand-maintained list would.

Contact availability is deliberately NOT part of this. A gap is a mechanism the
series has not covered; whether anyone knows someone at the company is the
question the hot list asks, not a filter on what belongs in it.
"""
import re
import sys
import xml.etree.ElementTree as ET

import classify
import config
import enrich

# The series title convention, as set out in the CTS Guide to Authors.
SERIES_QUERY = ('"Clin Transl Sci"[Journal] AND "mechanism of action"[Title]')

_CACHE = {}


def _is_series_title(title):
    """Drug before the colon, the convention after it.

    Excludes research articles that merely mention mechanism of action: one
    molnupiravir exposure-response comparison matches the words but is not a
    series entry.
    """
    return ":" in title and "echanism of" in title.split(":")[-1].lower()


def published_series(force=False):
    """-> [{'drug', 'pmid', 'year', 'month'}] for every RELEASED series paper."""
    if not force and "papers" in _CACHE:
        return _CACHE["papers"]
    ids, _ = enrich._esearch(SERIES_QUERY, retmax=200)
    if not ids:
        return []
    raw = enrich._efetch_xml(ids)
    if not raw:
        return []
    try:
        root = ET.fromstring(raw)
    except ET.ParseError:
        return []
    out = []
    for art in root.iter("PubmedArticle"):
        el = art.find(".//ArticleTitle")
        if el is None:
            continue
        title = " ".join("".join(el.itertext()).split())
        if not _is_series_title(title):
            continue
        rel = next((h for h in art.findall(".//PubMedPubDate")
                    if h.get("PubStatus") == "pmc-release"), None)
        if rel is None:
            continue                     # accepted but not released: not published
        drug = title.split(":")[0].strip()
        drug = re.sub(r",\s*(a|an)\s+.*$", "", drug, flags=re.I)
        drug = re.sub(r"\s+Nasal Spray$", "", drug, flags=re.I)
        out.append({"drug": drug, "pmid": art.findtext(".//PMID", ""),
                    "year": rel.findtext("Year", ""),
                    "month": rel.findtext("Month", "")})
    _CACHE["papers"] = out
    return out


def modality_coverage(force=False):
    """-> {format label: count published}, WITH ROLL-UP.

    An ADC increments both "Antibody-drug conjugate" and "Antibody"; a kinase
    inhibitor increments "Small molecule". Without the roll-up the counts were
    wrong in a way that mattered: small molecule read 10 when it was 14, and
    antibody read 3 when it was 7, so a modality could be called uncovered when
    the series had published in it four times.
    """
    if not force and "coverage" in _CACHE:
        return _CACHE["coverage"]
    cov = {}
    for p in published_series(force=force):
        fmt, _t, _g = classify.classify_modality(
            {"ingredient": p["drug"].lower(), "ingredient_raw": p["drug"]})
        for label in config.modality_chain(fmt):
            cov[label] = cov.get(label, 0) + 1
    _CACHE["coverage"] = cov
    return cov


def target_coverage(force=False):
    """-> {target class: count published}, the second axis.

    Local stem classes only. RxClass is not consulted here: it is one HTTP call
    per drug and the published set is re-read on every run, so the cost would
    land on every invocation for a list that changes monthly.
    """
    if not force and "targets" in _CACHE:
        return _CACHE["targets"]
    cov = {}
    for p in published_series(force=force):
        _f, target, _g = classify.classify_modality(
            {"ingredient": p["drug"].lower(), "ingredient_raw": p["drug"]})
        if target:
            cov[target] = cov.get(target, 0) + 1
    _CACHE["targets"] = cov
    return cov


def gap_report(force=False, verbose=False):
    """-> {'live': bool, 'published': n, 'coverage': {...}, 'gap_mods': set,
            'gap_categories': set, 'note': str}

    A failed fetch falls back to the pinned list in config and says so. The tool
    has to keep working without the network; it must not pretend the answer is
    current when it is not.
    """
    papers = published_series(force=force)
    if not papers:
        note = ("gap analysis NOT live: PubMed unreachable, using the pinned list "
                "in config.GAP_CATEGORIES")
        print("  " + note, file=sys.stderr)
        return {"live": False, "published": 0, "coverage": {}, "targets": {},
                "gap_mods": set(), "gap_categories": set(config.GAP_CATEGORIES),
                "note": note}

    cov = modality_coverage(force=force)
    tcov = target_coverage(force=force)
    covered_cats, covered_mods = set(), set(cov)
    for p in papers:
        _f, _t, gap = classify.classify_modality(
            {"ingredient": p["drug"].lower(), "ingredient_raw": p["drug"]})
        if gap:
            covered_cats.add(gap)
    gap_cats = set(config.GAP_CATEGORIES) - covered_cats
    note = (f"gap analysis live from PubMed: {len(papers)} published series "
            f"papers across {len(cov)} modalities")
    if verbose:
        print(f"  {note}")
        for m, n in sorted(cov.items(), key=lambda kv: -kv[1]):
            print(f"    {n:>3}  {m}")
        print(f"    gaps: {', '.join(sorted(gap_cats)) or 'none'}")
    return {"live": True, "published": len(papers), "coverage": cov,
            "targets": tcov, "gap_mods": covered_mods,
            "gap_categories": gap_cats, "note": note}


def is_gap(rec, report):
    """Does this candidate sit in a mechanism the series has never covered?

    Returns (bool, tier, reason).

    Two tiers, because they are not equally interesting and a flat test ranked
    them wrongly. "MRI contrast agent" and "Carbapenem antibacterial" are
    technically modalities with no published review, and on a flat test they
    outranked obecabtagene, an actual CAR-T gap the team named on slide 4.

        "category"  one of the named coverage gaps, still open
        "modality"  a molecular format with nothing published in the branch
        "target"    a pharmacologic class with nothing published

    A drug in a covered mechanism is still a candidate, just not gap-filling,
    so the reason is written out either way.
    """
    fmt, target, gap_cat = classify.classify_modality(rec)

    # The named gap CATEGORY decides first. Fine labels differ in granularity:
    # olezarsen is "Antisense oligonucleotide" while the published imetelstat is
    # "Oligonucleotide", so comparing those called siRNA / ASO an untouched gap
    # when the series had covered it. The category is the stable comparison.
    if gap_cat:
        if gap_cat in report["gap_categories"]:
            return True, "category", f"{gap_cat} - a named coverage gap"
        return False, "", f"{gap_cat}: already covered by the series"

    # Format axis, after roll-up. Nothing anywhere in this branch means a real
    # format gap; a parent with coverage means the branch is represented.
    chain = config.modality_chain(fmt)
    if report["live"] and not any(report["coverage"].get(c) for c in chain):
        first = "first approval of this moiety" in (
            rec.get("novelty_reason") or rec.get("Novelty") or "").lower()
        why = f"{fmt} - no published review of this modality"
        return True, "modality", (why + "; first-in-class" if first else why)

    # Target-class axis, independent of format. A well-covered format can still
    # hide an unexamined mechanism: the series has eleven small molecules and
    # no aldosterone synthase inhibitor.
    if target and report["live"] and not report.get("targets", {}).get(target):
        return True, "target", f"{target} - no published review of this target class"

    nf = next((report["coverage"].get(c) for c in chain
               if report["coverage"].get(c)), 0)
    bits = [f"{fmt}: {nf} published"]
    if target:
        bits.append(f"{target}: {report.get('targets', {}).get(target, 0)}")
    return False, "", "; ".join(bits)
