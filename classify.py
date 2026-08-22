"""Modality tagging, coverage-gap flags and novelty scoring.

Scoring exists to rank a February dossier, not to make editorial decisions --
every candidate keeps its reasons in plain text so a human can disagree.
"""
import datetime as _dt
import re

import config


def modality(rec):
    """Best guess at modality from the proper name, INN stem and dosage route."""
    name = " ".join([rec.get("ingredient_raw", ""), rec.get("brand", "")]).lower()

    for kw, label, gap in config.MODALITY_KEYWORDS:
        if kw in name:
            return label, gap

    stem_src = rec.get("ingredient", "")
    best = None
    for stem, label, gap in config.MODALITY_STEMS:
        if re.search(stem + r"\b", stem_src) or stem_src.endswith(stem):
            if best is None or len(stem) > len(best[0]):
                best = (stem, label, gap)
    if best:
        return best[1], best[2]

    if rec.get("center") == "CBER":
        return "Biologic (CBER)", "Cell & gene therapy"
    return "Small molecule", None


def gap_flag(rec, gap):
    """Does this candidate fill a coverage gap identified in the team deck?"""
    if gap:
        return gap
    name = (rec.get("ingredient_raw", "") or "").lower()
    if "vaccine" in name:
        return "Vaccines"
    return ""


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
