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


def sponsor_authors(drug, retmax=12):
    """Clinical-pharmacology authors already publishing on this drug.

    These are the people most likely to accept an invitation -- typically the
    sponsor's own clin pharm group, which is who has written most of the
    existing MOA mini-reviews.
    """
    d = re.sub(r'["\\]', " ", drug).strip()
    if not d:
        return [], 0
    ids, n = _esearch(
        f'"{d}"[Title/Abstract] AND (pharmacokinetics[Title/Abstract] OR '
        f'"clinical pharmacology"[Title/Abstract] OR '
        f'"exposure-response"[Title/Abstract] OR pharmacodynamics[Title/Abstract])',
        retmax=retmax)
    if not ids:
        return [], 0
    d2 = _get("esummary.fcgi", {"db": "pubmed", "id": ",".join(ids)})
    result = d2.get("result", {})
    seen, authors = set(), []
    for pmid in ids:
        rec = result.get(pmid) or {}
        for a in (rec.get("authors") or []):
            nm = a.get("name", "").strip()
            if nm and nm.lower() not in seen:
                seen.add(nm.lower())
                authors.append(nm)
    # last authors tend to be the senior clin pharm contact; keep order but cap
    return authors[:8], n


def enrich(rec, verbose=False):
    """Attach PubMed findings to one candidate record."""
    drug = rec.get("ingredient") or rec.get("ingredient_raw") or ""
    prior, detail = has_moa_review(drug)
    authors, npapers = sponsor_authors(drug)
    if verbose:
        print(f"    {drug[:28]:28s} prior_review={prior!s:5s} "
              f"clinpharm_papers={npapers:3d} authors={len(authors)}",
              file=sys.stderr)
    out = dict(rec)
    out.update({
        "prior_review": prior,
        "prior_review_detail": detail,
        "candidate_authors": authors,
        "clinpharm_paper_count": npapers,
    })
    return out
