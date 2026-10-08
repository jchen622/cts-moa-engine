"""FDA's Established Pharmacologic Class, via NLM RxClass.

The target-class axis is better answered by FDA than by a stem table. RxClass
exposes the Established Pharmacologic Class that FDA assigns in the structured
label, free and without a key, and where it answers it is far more precise than
anything hand-written: "Calcitonin Gene-related Peptide Receptor Antagonist",
"Oligonucleotide Telomerase Inhibitor", "Soluble Guanylate Cyclase Stimulator".

It is NOT a replacement for the local table. Measured against the 22 published
MOA papers it answered 10, about 45%, and the misses are the newer drugs and
most biologics: teclistamab, talquetamab, mirvetuximab, olezarsen, fruquintinib,
suzetrigine. So this is tried first and the stem table is the fallback, and the
caller records WHICH source answered, because a hand-guess must never be
presented as an FDA assignment on a sheet people act on.

Fails soft in every direction: no network, no answer, odd payload all return
None rather than raising. A classification is a nice-to-have; a failed run is
not acceptable.
"""
import json
import os
import re
import sys
import time
import urllib.parse
import urllib.request

import config

BASE = "https://rxnav.nlm.nih.gov/REST/rxclass/class/byDrugName.json"
_last = [0.0]
MIN_INTERVAL = 0.25
_MEM = {}


def _cache_path(name):
    d = os.path.join(config.CACHE, "rxclass")
    os.makedirs(d, exist_ok=True)
    safe = re.sub(r"[^a-z0-9]+", "_", name.lower()).strip("_")[:80]
    return os.path.join(d, f"{safe}.json")


def _get(url, retries=2):
    for attempt in range(retries):
        dt = time.time() - _last[0]
        if dt < MIN_INTERVAL:
            time.sleep(MIN_INTERVAL - dt)
        _last[0] = time.time()
        try:
            req = urllib.request.Request(
                url, headers={"User-Agent": "cts-moa-sourcing"})
            with urllib.request.urlopen(req, timeout=25) as r:
                return json.loads(r.read().decode("utf-8", "replace"))
        except Exception:
            if attempt == retries - 1:
                return None
            time.sleep(1.0 * (attempt + 1))
    return None


def epc(drug, use_cache=True):
    """-> the FDA Established Pharmacologic Class, or None.

    Tries the full ingredient then its first word, because Drugs@FDA holds
    salts and combinations ("XANOMELINE TARTRATE", "TROSPIUM CHLORIDE;
    XANOMELINE TARTRATE") that RxNorm will not match verbatim.
    """
    name = re.sub(r"[^A-Za-z0-9 \-]", " ", (drug or "")).strip()
    if not name:
        return None
    if name in _MEM:
        return _MEM[name]
    cp = _cache_path(name)
    if use_cache and os.path.exists(cp):
        try:
            val = json.load(open(cp)).get("epc")
            _MEM[name] = val
            return val
        except Exception:
            pass

    found = None
    for term in dict.fromkeys([name, name.split()[0]]):
        d = _get(f"{BASE}?" + urllib.parse.urlencode(
            {"drugName": term, "relaSource": "DAILYMED", "relas": "has_EPC"}))
        if d is None:
            return None                  # network trouble: do not cache a miss
        items = (d.get("rxclassDrugInfoList") or {}).get("rxclassDrugInfo", [])
        names = sorted({i.get("rxclassMinConceptItem", {}).get("className")
                        for i in items
                        if i.get("rxclassMinConceptItem", {}).get("className")})
        if names:
            found = names[0]
            break
    try:
        json.dump({"epc": found}, open(cp, "w"))
    except OSError:
        pass
    _MEM[name] = found
    return found


def target_class(rec, local=None):
    """-> (class, source). FDA first, the local stem table second.

    `source` is reported on the sheet so the reader can tell an FDA assignment
    from a stem-based guess.
    """
    # `epc` already swallows network trouble, but a classification is a
    # nice-to-have and a failed hot-list run is not acceptable, so anything at
    # all coming out of this lookup is contained here rather than at the caller.
    try:
        fda = epc(rec.get("ingredient_raw") or rec.get("ingredient") or "")
    except Exception:
        fda = None
    if fda:
        return fda, "FDA established pharmacologic class"
    if local:
        return local, "INN stem"
    return "", ""
