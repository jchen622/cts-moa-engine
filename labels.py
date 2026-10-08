"""Mechanism of action and approved indications, from the FDA label via DailyMed.

Neither is in Drugs@FDA. The approved label carries both as structured SPL
sections, which makes DailyMed the authoritative free source: no key, no login,
and the text is what FDA approved rather than someone's summary of it.

    MOA          LOINC 43679-0   "12.1 Mechanism of Action"
    Indications  LOINC 34067-9   "1 Indications and Usage"

Three traps, each of which silently returns the wrong thing:

1. **Reading only the section's own <text> loses nested content.** Pluvicto
   keeps its indications in child <component><section> nodes, so a direct read
   returns an empty string and the drug looks like it has no indication at all.
   The whole subtree has to be walked.

2. **The SPL states the indications twice**, once in the Highlights summary and
   once in the full section. Taking every match produced four indications for a
   two-indication drug. The full section is always the longer one.

3. **Multi-indication labels use child sections, not prose.** Pluvicto has two
   `1.1` / `1.2` child sections. Flattening them and regex-splitting produced
   three, because "in combination with ARPI therapy" is a clause of its own
   indication and not a new one. So the structure is used where it exists and
   splitting is only the fallback for a single prose block.

4. **The section heading is sometimes numbered with a trailing period.**
   Fruquintinib's label reads "12.1. Mechanism of Action", which defeated a
   prefix strip written for "12.1 " and left the MOA as the string "12.1.".

Responses are cached on disk: this is one extra HTTP round trip per drug and a
label does not change between runs.
"""
import json
import os
import re
import sys
import time
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET

import config

BASE = "https://dailymed.nlm.nih.gov/dailymed/services/v2"
HL7 = "urn:hl7-org:v3"
NS = {"h": HL7}
MOA_CODE = "43679-0"
IND_CODE = "34067-9"

_last = [0.0]
MIN_INTERVAL = 0.34


def _cache_path(name):
    d = os.path.join(config.CACHE, "labels")
    os.makedirs(d, exist_ok=True)
    safe = re.sub(r"[^a-z0-9]+", "_", name.lower()).strip("_")[:80]
    return os.path.join(d, f"{safe}.json")


def _get(url, raw=False, retries=3):
    for attempt in range(retries):
        dt = time.time() - _last[0]
        if dt < MIN_INTERVAL:
            time.sleep(MIN_INTERVAL - dt)
        _last[0] = time.time()
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "cts-moa-sourcing"})
            with urllib.request.urlopen(req, timeout=45) as r:
                body = r.read()
            return body if raw else json.loads(body.decode("utf-8", "replace"))
        except Exception as e:
            if attempt == retries - 1:
                print(f"  DailyMed error ({e})", file=sys.stderr)
                return b"" if raw else {}
            time.sleep(1.5 * (attempt + 1))
    return b"" if raw else {}


def _child_sections(root, code):
    """Text of each immediate child section under the section carrying `code`.

    A label with several approved uses splits them into numbered children
    (Pluvicto: 1.1 and 1.2). Using that structure is exact, where flattening
    the text and splitting on prose is guesswork.
    """
    best, best_len = None, -1
    for sec in root.iter(f"{{{HL7}}}section"):
        c = sec.find("h:code", NS)
        if c is None or c.get("code") != code:
            continue
        n = len("".join(sec.itertext()))
        if n > best_len:
            best, best_len = sec, n
    if best is None:
        return []
    # Not every child of the indications section is an indication. Orforglipron
    # has exactly one child and it is "Limitations of Use", so taking children
    # blindly produced a restriction where an indication should be, and then
    # stripping the restriction left nothing at all.
    skip = re.compile(r"^\s*(?:\d+(?:\.\d+)?\.?\s*)?"
                      r"(limitations? of use|important limitations|"
                      r"use in specific populations|dosage|"
                      r"patient selection)\b", re.I)
    out = []
    for comp in best.findall("h:component", NS):
        for kid in comp.findall("h:section", NS):
            t = re.sub(r"\s+", " ", "".join(kid.itertext())).strip()
            if len(t) > 20 and not skip.match(t):
                out.append(t)
    return out


def _section(root, code):
    """Longest section carrying this code, walking the full subtree.

    Longest, because the SPL repeats indications in Highlights and the full
    section is always the longer of the two. Subtree, because Pluvicto nests
    its indications in child sections.
    """
    best = ""
    for sec in root.iter(f"{{{HL7}}}section"):
        c = sec.find("h:code", NS)
        if c is None or c.get("code") != code:
            continue
        txt = re.sub(r"\s+", " ", "".join(sec.itertext())).strip()
        if len(txt) > len(best):
            best = txt
    return best


# A new indication starts at one of these. "In " is deliberately absent: it
# matched "in combination with ARPI therapy", which is a clause of the
# indication before it, and split Pluvicto's two indications into three.
_SPLIT = re.compile(
    r"(?<=[a-z\)\.])\s+(?=(?:To reduce|To treat|To improve|To decrease|"
    r"For the treatment|For treatment|for the treatment of adult|"
    r"as an adjunct|As an adjunct))")


def split_indications(text, brand=""):
    """A label's indications block -> the separate approved uses."""
    if not text:
        return []
    t = re.sub(r"^\s*\d+(\.\d+)?\.?\s*(INDICATIONS AND USAGE|[A-Z][^.]{0,70}?)\s*(?=[A-Z]{3,})",
               "", text, flags=re.I)
    # Everything before "is/are indicated" is the heading and the brand names.
    # "are" matters: a combined label reads "RYBELSUS and OZEMPIC tablets are
    # indicated", and matching only "is indicated" left the heading in place.
    m = re.search(r"\b(?:is|are)\s+indicated\b", t, re.I)
    if m:
        t = t[m.end():]
    t = re.sub(r"\(\s*\d+(\.\d+)?\s*\)", " ", t)        # "( 1)" cross-references
    t = re.sub(r"\[see [^\]]*\]", " ", t, flags=re.I)
    t = re.split(r"\bLimitations? of Use\b", t, flags=re.I)[0]
    # A bulleted label is unambiguous, so prefer the bullets over prose patterns.
    parts = ([p for p in re.split(r"\s*[\u2022\u00b7\u25aa\u25cf]\s*", t) if p.strip()]
             if re.search(r"[\u2022\u00b7\u25aa\u25cf]", t) else _SPLIT.split(t))
    out = []
    for part in parts:
        part = re.sub(r"\s+", " ", part).strip(" .;:,")
        # A trailing "BRAND is indicated..." fragment is the start of the
        # duplicate copy, not part of this indication.
        if brand:
            part = re.split(rf"\b{re.escape(brand)}\b", part, flags=re.I)[0]
        part = re.sub(r"\s+", " ", part).strip(" .;:,")
        if len(part) < 12:
            continue
        if _is_duplicate(part, out):
            continue
        out.append(part)
    return out


_FILLER = {"the", "a", "an", "of", "in", "to", "and", "or", "with", "for",
           "adults", "adult", "patients", "patient", "who", "have", "has",
           "been", "risk", "reduce", "treatment", "established", "non"}


def _is_duplicate(part, seen):
    """Is this the same indication already captured, worded differently?

    A prefix comparison is not enough. The Highlights copy restates an
    indication in different words: "major adverse cardiovascular (CV) events
    (CV death, non-fatal myocardial infarction or non-fatal stroke)" against
    "major adverse cardiovascular events (cardiovascular death, non-fatal
    myocardial infarction, or non-fatal stroke)". Those diverge within the first
    thirty characters but mean the same thing, so compare the significant words.
    """
    def bag(x):
        w = re.findall(r"[a-z]{3,}", x.lower())
        return {t for t in w if t not in _FILLER}
    b = bag(part)
    if not b:
        return False
    for o in seen:
        ob = bag(o)
        if not ob:
            continue
        overlap = len(b & ob) / min(len(b), len(ob))
        if overlap >= 0.75:
            return True
    return False


def _trim_duplicate(t, brand, min_offset=40):
    """Cut the Highlights copy that follows, without cutting the heading.

    Truncating at the first mention of the brand was wrong: a combined label
    starts "RYBELSUS and OZEMPIC tablets are indicated", so cutting at the brand
    left the single fragment "RYBELSUS and". Only a mention well past the start
    can be the beginning of the repeated block.
    """
    if not brand:
        return t
    m = re.search(rf"\b{re.escape(brand)}\b", t[min_offset:], re.I)
    return t[:min_offset + m.start()] if m else t


def split_one(text, brand=""):
    """Tidy ONE child section into a single indication string."""
    t = re.sub(r"^\s*\d+(\.\d+)?\.?\s*", "", text or "")
    m = re.search(r"\bis indicated\b", t, re.I)
    if m:
        t = t[m.end():]
    t = re.sub(r"\(\s*\d+(\.\d+)?\s*\)", " ", t)
    t = re.sub(r"\[see [^\]]*\]", " ", t, flags=re.I)
    # "Limitations of Use" is a restriction on the indication, not an indication.
    t = re.split(r"\bLimitations? of Use\b", t, flags=re.I)[0]
    t = _trim_duplicate(t, brand)
    return re.sub(r"\s+", " ", t).strip(" .;:,")


def first_sentence(text, limit=240):
    """The usable one-line MOA. Labels run 260 to 640 characters."""
    t = re.sub(r"^\s*12\.1\.?\s*Mechanism of Action\s*", "", text or "", flags=re.I)
    t = re.sub(r"\s+", " ", t).strip()
    if not t:
        return ""
    m = re.search(r"(?<=[a-z0-9\)])\.\s+(?=[A-Z])", t)
    s = t[:m.start() + 1] if m else t
    return s if len(s) <= limit else s[:limit].rsplit(" ", 1)[0] + "..."


def label_facts(drug, brand="", use_cache=True):
    """-> {'moa': str, 'indications': [str], 'setid': str, 'source': str}

    Empty strings rather than an exception when the drug has no SPL: a missing
    label must not stop a run, it just leaves two cells blank.
    """
    blank = {"moa": "", "indications": [], "setid": "", "source": ""}
    name = (drug or "").strip()
    if not name:
        return blank
    cp = _cache_path(name)
    if use_cache and os.path.exists(cp):
        try:
            return json.load(open(cp))
        except Exception:
            pass

    hits = []
    for term in (name, brand):
        if not term:
            continue
        d = _get(f"{BASE}/spls.json?drug_name={urllib.parse.quote(term)}&pagesize=5")
        hits = d.get("data") or []
        if hits:
            break
    if not hits:
        return blank

    setid = hits[0].get("setid", "")
    xml = _get(f"{BASE}/spls/{setid}.xml", raw=True)
    if not xml:
        return blank
    try:
        root = ET.fromstring(xml)
    except ET.ParseError:
        return blank

    title = hits[0].get("title", "")
    bk = brand or (re.match(r"^([A-Z][A-Z0-9\-]{2,})", title).group(1)
                   if re.match(r"^([A-Z][A-Z0-9\-]{2,})", title) else "")
    kids = _child_sections(root, IND_CODE)
    if kids:
        inds = [i for i in (split_one(k, brand=bk) for k in kids) if i]
    else:
        inds = split_indications(_section(root, IND_CODE), brand=bk)
    out = {
        "moa": first_sentence(_section(root, MOA_CODE)),
        "indications": inds,
        "setid": setid,
        "source": f"DailyMed SPL {setid}",
    }
    try:
        json.dump(out, open(cp, "w"))
    except Exception:
        pass
    return out
