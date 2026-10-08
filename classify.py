"""Modality tagging, coverage-gap flags and novelty scoring.

Scoring exists to rank a February dossier, not to make editorial decisions --
every candidate keeps its reasons in plain text so a human can disagree.
"""
import datetime as _dt
import re

import config


def classify_modality(rec):
    """-> (format, target_class, gap_category).

    Two axes, kept apart. They used to be one flat label, which made the
    coverage counts incomparable: a kinase inhibitor was not counted as a small
    molecule and an ADC was not counted as an antibody.

    `format` is the molecular format and rolls up via config.modality_chain().
    `target_class` is the pharmacologic class and may be None, in which case
    rxclass.epc() is the better source and the caller should try it.
    """
    name = " ".join([rec.get("ingredient_raw", ""), rec.get("brand", "")]).lower()

    for kw, fmt, target, gap in config.MODALITY_KEYWORDS:
        if kw in name:
            return fmt, target, gap

    # Both the normalised ingredient AND the raw one. Purple Book records keep
    # only the first word in `ingredient` ("afamitresgene"), so a stem carried
    # by the second word was invisible: every -leucel cell therapy fell through
    # to the fallback below. That is the opposite of where the stem table is
    # most needed, since CBER products are the cell, gene and vaccine gaps.
    stem_src = " ".join([rec.get("ingredient", "") or "",
                         (rec.get("ingredient_raw", "") or "").lower()])
    best = None
    for stem, fmt, target, gap in config.MODALITY_STEMS:
        if re.search(stem + r"\b", stem_src) or stem_src.endswith(stem):
            if best is None or len(stem) > len(best[0]):
                best = (stem, fmt, target, gap)
    if best:
        return best[1], best[2], best[3]
    return _fallback_format(rec, name), None, None


# Names that are biologics on their face, for the one case where the record
# carries no application number to decide it.
_BIOLOGIC_NAME = re.compile(
    r"(leucel|autotemcel|parvovec|vaccine|toxoid|globulin|allograft|autologous|"
    r"allogeneic|recombinant|\bcells?\b|\bhuman\b|\bplasma\b|\balfa\b|"
    r"\bbeta\b|\bgamma\b|tissue)")


def _fallback_format(rec, name):
    """The format when no stem and no keyword matched.

    Defaulting everything to "Small molecule" was wrong in both directions. It
    filed a fibrinogen concentrate and an acellular nerve allograft as small
    molecules, and every one of those inflated the small-molecule count that the
    gap test reads. The old CBER branch was worse: it asserted the named gap
    category "Cell & gene therapy" on nothing but the reviewing centre, so
    fibrinogen scored +40 as an untouched cell and gene therapy.

    The regulatory route is the authoritative answer and it is already on the
    record: an NDA is a small molecule, a BLA is a biologic. Only when there is
    no application number at all, as when a bare drug name is passed in from a
    PubMed title, does the shape of the name decide.
    """
    appl = (rec.get("appl_type") or "").upper()
    no = (rec.get("appl_no") or "").strip()
    if rec.get("center") == "CBER" or appl == "BLA" or no.startswith(("125", "761")):
        return "Biologic (unspecified)"
    if appl in ("NDA", "ANDA") or no:
        return "Small molecule"
    return ("Biologic (unspecified)" if _BIOLOGIC_NAME.search(name)
            else "Small molecule")


def modality(rec):
    """Back-compatible two-tuple: (format, gap_category).

    Kept because gaps.py and sheets.py call it in several places; new code
    should use classify_modality() and get the target class too.
    """
    fmt, _target, gap = classify_modality(rec)
    return fmt, gap


def gap_flag(rec, gap):
    """Does this candidate fill a coverage gap identified in the team deck?"""
    if gap:
        return gap
    name = (rec.get("ingredient_raw", "") or "").lower()
    if "vaccine" in name:
        return "Vaccines"
    return ""


def hot_score(rec, gap_tier, gap_reason, attention):
    """How worth chasing is this drug? -> (score, [reasons])

    Three families, which are the three things the team named as making a drug
    "hot": an interesting mechanism, news-worthiness, and attention. Every term
    is written out as text so a weight can be argued with rather than trusted.

    Ordering only. It does not decide what belongs on the list: that is the gap
    test, and the gap test does not consider whether a contact exists.
    """
    score, why = 0, []

    # --- interesting mechanism. Tiered: a named coverage gap the team has
    # already agreed matters outranks "no review exists in this mechanism
    # class", which is true of most things and so says much less.
    if gap_tier == "category":
        score += 40
        why.append(f"named coverage gap (+40) [{gap_reason}]")
    elif gap_tier == "modality":
        score += 12
        why.append(f"no review of this modality (+12) [{gap_reason}]")
    elif gap_tier == "target":
        # Weakest of the three. A format the series has covered repeatedly can
        # still hide an unexamined target class, which is worth surfacing but
        # says less than an untouched modality: there are far more target
        # classes than formats, so nearly everything is a gap on this axis.
        score += 8
        why.append(f"no review of this target class (+8) [{gap_reason}]")
    nov = (rec.get("novelty_reason") or rec.get("Novelty") or "")
    if "first approval of this moiety" in nov.lower():
        score += 20
        why.append("first approval of this moiety (+20)")
    elif "new route" in nov.lower() or "new brand" in nov.lower():
        score += 8
        why.append("known moiety by a new route (+8)")

    # --- news-worthy, from Drugs@FDA review designations
    if rec.get("priority_review"):
        score += 12
        why.append("FDA priority review (+12)")
    if rec.get("orphan"):
        score += 8
        why.append("orphan designation (+8)")

    # --- attention
    papers = int(attention.get("papers_24m") or 0)
    if papers >= 100:
        score += 20; why.append(f"{papers} papers in 24 months (+20)")
    elif papers >= 40:
        score += 14; why.append(f"{papers} papers in 24 months (+14)")
    elif papers >= 15:
        score += 8;  why.append(f"{papers} papers in 24 months (+8)")
    elif papers:
        score += 3;  why.append(f"{papers} papers in 24 months (+3)")
    else:
        why.append("no papers found in 24 months (0)")
    trials = int(attention.get("trials") or 0)
    if trials >= 20:
        score += 10; why.append(f"{trials} trials (+10)")
    elif trials >= 5:
        score += 6;  why.append(f"{trials} trials (+6)")
    elif trials:
        score += 2;  why.append(f"{trials} trials (+2)")
    return score, why


def novelty_score(rec, mod, gap, prior_review):
    """0-100. Higher = more worth chasing for a MOA mini-review.

    Deliberately simple and legible: the group should be able to argue with
    each term. Weights live here, not scattered through the pipeline.
    """
    score, why = 0, []

    reason = rec.get("novelty_reason", "")
    is_nai = rec.get("class_code") in config.NEW_ACTIVE_INGREDIENT_CLASSES
    if is_nai:
        # New active ingredient: an enantiomer/salt/ester of a known moiety.
        # Real pharmacology to explain, but not first-in-class.
        score += 22
        why.append(f"new active ingredient, FDA class {rec.get('class_code')} (+22)")
    elif reason.startswith("first approval"):
        score += 40
        why.append("first-in-human moiety (+40)")
    elif reason.startswith("novel component"):
        score += 26
        why.append("novel component in a combination (+26)")
    else:
        score += 12
        why.append("known moiety, new route/brand (+12)")

    if gap:
        score += 22
        why.append(f"fills coverage gap: {gap} (+22)")

    if mod in config.COMPLEX_MODALITIES:
        score += 12
        why.append(f"mechanistically complex modality: {mod} (+12)")
    if mod in config.DIAGNOSTIC_MODALITIES:
        score -= 15
        why.append(f"diagnostic rather than therapeutic: {mod} (-15)")

    if rec.get("center") == "CBER":
        score += 10
        why.append("CBER biologic, under-represented (+10)")

    if prior_review:
        score -= 30
        why.append("a MOA-style review already exists (-30)")
    else:
        score += 8
        why.append("no existing MOA review found (+8)")

    # A drug with a clinical-pharmacology literature already has findable
    # authors; one with none is much harder to source a writer for.
    n = rec.get("clinpharm_paper_count", 0) or 0
    if n >= 20:
        score += 10
        why.append(f"deep clin pharm literature, {n} papers (+10)")
    elif n >= 5:
        score += 6
        why.append(f"clin pharm literature exists, {n} papers (+6)")
    elif n == 0:
        score -= 8
        why.append("no clin pharm literature found, authors unclear (-8)")

    # Recency: the series reads best close to approval.
    date = rec.get("approval_date", "")
    cutoff_year = str(_dt.date.today().year)
    if date >= f"{cutoff_year}-01-01":
        score += 8
        why.append("approved this year (+8)")

    return max(0, min(100, score)), "; ".join(why)


def enrich_record(rec, prior_review=False):
    mod, gap_hint = modality(rec)
    gap = gap_flag(rec, gap_hint)
    score, why = novelty_score(rec, mod, gap, prior_review)
    rec = dict(rec)
    rec.update({
        "modality": mod,
        "gap": gap,
        "score": score,
        "score_why": why,
        "prior_review": prior_review,
    })
    return rec
