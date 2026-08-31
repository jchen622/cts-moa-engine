"""PubMed enrichment via E-utilities.

Two questions per candidate:
  1. Has a MOA-style review already been written? (if so, don't invite)
  2. Who publishes clinical pharmacology on this drug? (candidate authors)

No API key required. NCBI asks for <=3 requests/second without a key; the
throttle here is deliberate and should not be removed.
"""
import json
import re
import sys
import time
import urllib.parse
import urllib.request

import authors as authors_mod
import config

EUTILS = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils"
TOOL = "cts-moa-sourcing"
# NCBI asks that automated callers identify themselves. Optional, and kept
# out of the code so no one's address ships with the tool: set "ncbi_email"
# in settings.json and it is sent, otherwise only the tool name is.
EMAIL = config.ncbi_email()

_last_call = [0.0]
MIN_INTERVAL = 0.34                   # ~3 req/s


def _throttle():
    dt = time.time() - _last_call[0]
    if dt < MIN_INTERVAL:
        time.sleep(MIN_INTERVAL - dt)
    _last_call[0] = time.time()


def _get(path, params, retries=3):
    params = dict(params, tool=TOOL, retmode="json")
    if EMAIL:                    # omit rather than send an empty email=
        params["email"] = EMAIL
    url = f"{EUTILS}/{path}?{urllib.parse.urlencode(params)}"
    for attempt in range(retries):
        _throttle()
        try:
            with urllib.request.urlopen(url, timeout=30) as r:
                return json.loads(r.read().decode("utf-8", errors="replace"))
        except Exception as e:
            if attempt == retries - 1:
                print(f"  PubMed error ({e}) for {params.get('term','')[:60]}",
                      file=sys.stderr)
                return {}
            time.sleep(1.5 * (attempt + 1))
    return {}


def _esearch(term, retmax=20):
    d = _get("esearch.fcgi", {"db": "pubmed", "term": term, "retmax": retmax})
    res = d.get("esearchresult", {})
    return res.get("idlist", []), int(res.get("count", 0) or 0)


def has_moa_review(drug):
    """Is there already a mechanism-of-action review for this drug anywhere?

    Checks the CTS series convention first, then any journal, so we don't
    invite an author to duplicate an existing piece.
    """
    d = re.sub(r'["\\]', " ", drug).strip()
    if not d:
        return False, ""
    ids, n = _esearch(
        f'"{d}"[Title] AND ("mechanism of action"[Title] OR '
        f'"mechanisms of action"[Title])', retmax=5)
    if n:
        return True, f"{n} MOA-titled paper(s); PMID {', '.join(ids[:3])}"
    return False, ""


def _efetch_xml(pmids, retries=3):
    """Raw efetch XML. esummary carries no affiliations, so it cannot be used."""
    if not pmids:
        return b""
    params = {"db": "pubmed", "id": ",".join(pmids), "retmode": "xml",
              "tool": TOOL}
    if EMAIL:
        params["email"] = EMAIL
    url = f"{EUTILS}/efetch.fcgi?{urllib.parse.urlencode(params)}"
    for attempt in range(retries):
        _throttle()
        try:
            with urllib.request.urlopen(url, timeout=45) as r:
                return r.read()
        except Exception as e:
            if attempt == retries - 1:
                print(f"  PubMed efetch error ({e})", file=sys.stderr)
                return b""
            time.sleep(1.5 * (attempt + 1))
    return b""


def clinpharm_papers(drug, per_tier=8):
    """Papers about this drug, tagged with which evidence tier found them.

    Tiers are searched in order and the results merged: a person appearing in
    more than one tier is a stronger candidate, so nothing is discarded once a
    higher tier hits.
    """
    d = re.sub(r'["\\]', " ", drug).strip()
    if not d:
        return [], 0
    found, total = {}, 0
    for tier, _label, query in authors_mod.TIERS:
        ids, n = _esearch(f'"{d}"[Title/Abstract] AND {query}', retmax=per_tier)
        total += n
        for pmid in ids:
            found.setdefault(pmid, tier)      # keep the strongest (lowest) tier
    if not found:
        return [], 0
    arts = authors_mod.parse_articles(_efetch_xml(list(found)))
    for a in arts:
        a["tier"] = found.get(a["pmid"], 3)
    return arts, total


def sponsor_authors(drug, sponsor="", members=None):
    """Ranked clinical pharmacologists for this drug, with their evidence."""
    arts, total = clinpharm_papers(drug)
    if not arts:
        return [], 0
    return authors_mod.rank(arts, sponsor, members=members), total


def enrich(rec, verbose=False, members=None):
    """Attach PubMed findings to one candidate record."""
    drug = rec.get("ingredient") or rec.get("ingredient_raw") or ""
    sponsor = rec.get("sponsor_raw") or rec.get("Sponsor") or ""
    prior, detail = has_moa_review(drug)
    people, npapers = sponsor_authors(drug, sponsor, members=members)
    if verbose:
        top = people[0]["name"] if people else "-"
        print(f"    {drug[:26]:26s} prior={prior!s:5s} papers={npapers:4d} "
              f"people={len(people):2d} top={top}", file=sys.stderr)
    out = dict(rec)
    out.update({
        "prior_review": prior,
        "prior_review_detail": detail,
        "clinpharm_people": people,
        "candidate_authors": [p["name"] for p in people],
        "clinpharm_contacts": authors_mod.format_contacts(people),
        "clinpharm_evidence": authors_mod.format_evidence(people),
        "clinpharm_paper_count": npapers,
    })
    return out
