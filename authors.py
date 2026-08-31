"""Find the clinical pharmacologist who actually worked on a drug.

The naive approach -- search the drug name, take every author -- cannot tell a
sponsor's clinical pharmacology lead from a site investigator or a statistician.
Two refinements do most of the work.

**It has to be a Phase 1 CLINICAL PHARMACOLOGY study, not Phase 1 generally.** A
dose-escalation trial is led by the treating clinician. The dedicated ClinPharm
studies -- food effect, DDI, relative bioavailability, organ impairment, mass
balance/ADME, thorough QT, healthy-volunteer PK -- are the ones the clinical
pharmacologist leads and first-authors.

**Author position means different things in different papers.** Compare two real
records:

    [14C]-tebipenem mass balance (Spero), 7 authors
        FIRST  Vipul K Gupta        Spero Therapeutics      <- the clin pharmacologist
        LAST   Angela Talley        Spero Therapeutics

    pivekimab sunirine dose escalation (AbbVie, JCO), 24 authors
        FIRST  Naveen Pemmaraju     MD Anderson             <- oncologist
        ...
        20-22  Yining Du            AbbVie                  <- the clin pharm group
               Sribalaji Lakshmikanth  AbbVie
               Jalaja Potluri       AbbVie
        LAST   Naval G Daver        MD Anderson             <- oncologist

So on a tier-1 paper the front of the list is the signal; on a dose-escalation
paper the sponsor affiliation is the only signal, and position is noise. Both
patterns are pinned in selftest.py.

A drug usually has more than one clinical pharmacologist, so this returns a
ranked list with the evidence behind each name, never a single answer.
"""
import re
import xml.etree.ElementTree as ET

import config
import sheets

# ------------------------------------------------------------------ the tiers
# Tier 1: studies a clinical pharmacologist designs, runs and first-authors.
CLINPHARM_STUDY = (
    '("food effect"[Title/Abstract] OR "effect of food"[Title/Abstract] OR '
    '"drug-drug interaction"[Title/Abstract] OR "drug interaction"[Title/Abstract] OR '
    '"relative bioavailability"[Title/Abstract] OR "absolute bioavailability"[Title/Abstract] OR '
    'bioequivalence[Title/Abstract] OR '
    '"hepatic impairment"[Title/Abstract] OR "renal impairment"[Title/Abstract] OR '
    '"organ impairment"[Title/Abstract] OR "mass balance"[Title/Abstract] OR '
    '"excretion balance"[Title/Abstract] OR ADME[Title/Abstract] OR '
    '"absorption, distribution, metabolism"[Title/Abstract] OR '
    '"thorough QT"[Title/Abstract] OR "QT interval"[Title/Abstract] OR '
    '"healthy subjects"[Title/Abstract] OR "healthy volunteers"[Title/Abstract] OR '
    '"healthy participants"[Title/Abstract])')

# Tier 2: the modelling side of the same job.
PHARMACOMETRICS = (
    '("population pharmacokinetic*"[Title/Abstract] OR "popPK"[Title/Abstract] OR '
    '"exposure-response"[Title/Abstract] OR "exposure response"[Title/Abstract] OR '
    '"physiologically based pharmacokinetic"[Title/Abstract] OR "PBPK"[Title/Abstract] OR '
    '"model-informed"[Title/Abstract] OR pharmacometric*[Title/Abstract])')

# Tier 3: last resort. Position is meaningless here; affiliation is everything.
DOSE_ESCALATION = (
    '("Clinical Trial, Phase I"[Publication Type] OR '
    '"dose escalation"[Title/Abstract] OR "dose-escalation"[Title/Abstract] OR '
    '"first-in-human"[Title/Abstract])')

TIERS = [(1, "ClinPharm study", CLINPHARM_STUDY),
         (2, "Pharmacometrics", PHARMACOMETRICS),
         (3, "Dose escalation", DOSE_ESCALATION)]

# Journals where a clinical pharmacology paper lives.
CP_JOURNALS = re.compile(
    r"clinical pharmacolog|translational science|pharmacometric|systems pharmacol|"
    r"pharmacokinet|drug metabolism|drug dispos|br j clin pharmacol|"
    r"journal of clinical pharmacology|clin transl sci|cpt:", re.I)

# An affiliation naming one of these is industry, not academia or a hospital.
INDUSTRY_HINT = re.compile(
    r"\b(inc|llc|ltd|limited|corp|corporation|gmbh|a/s|ab|plc|pharmaceutical|"
    r"pharma|therapeutics|biosciences|biopharma|laboratories|company)\b", re.I)
ACADEMIC_HINT = re.compile(
    r"\b(universit|college|school of|hospital|clinic|cancer cent|cancer cent|"
    r"institute of|medical cent|nhs|inserm|academy)\b", re.I)


def _text(el):
    return re.sub(r"\s+", " ", "".join(el.itertext())).strip() if el is not None else ""


def parse_articles(xml_bytes):
    """efetch XML -> [{pmid, journal, year, authors:[{name, affil, pos, n}]}].

    esummary carries no affiliations, which is why this uses efetch.
    """
    out = []
    try:
        root = ET.fromstring(xml_bytes)
    except ET.ParseError:
        return out
    for art in root.iter("PubmedArticle"):
        pmid = _text(art.find(".//PMID"))
        journal = _text(art.find(".//Journal/Title"))
        year = _text(art.find(".//JournalIssue/PubDate/Year"))
        alist = art.findall(".//AuthorList/Author")
        authors = []
        for i, a in enumerate(alist):
            last = _text(a.find("LastName"))
            fore = _text(a.find("ForeName"))
            if not last:
                continue                      # collective/group author
            affs = [_text(x) for x in a.findall(".//Affiliation")]
            authors.append({
                "name": f"{fore} {last}".strip(),
                "affil": affs[0] if affs else "",
                "pos": i,
                "n": len(alist),
            })
        if authors:
            out.append({"pmid": pmid, "journal": journal, "year": year,
                        "authors": authors})
    return out


def person_key(name):
    """Collapse middle initials so 'Angela Talley' and 'Angela K Talley' are one.

    Keyed on first-initial + surname. Two genuinely different people who share
    both are rare enough, and merging them is a smaller error than listing the
    same person twice with a split evidence trail.
    """
    n = re.sub(r"[^a-z ]", "", (name or "").lower()).strip()
    parts = [x for x in n.split() if x]
    if len(parts) < 2:
        return n
    return f"{parts[0][0]} {parts[-1]}"


def _email(affil):
    m = re.search(r"[\w\.\-\+]+@[\w\.\-]+\.\w{2,}", affil or "")
    return m.group(0).rstrip(".") if m else ""


# Chunks that name a sub-unit rather than the employer. "Department of DMPK-B,
# BeiGene" should report BeiGene, not the department.
_SUBUNIT = re.compile(
    r"^(department|dept|division|unit|group|centre|center|institute|laboratory|"
    r"lab|school|faculty|section|office|programme|program)\b", re.I)
# Bare functional-team names used as the first chunk, e.g.
# "Clinical Pharmacology, Bristol Myers Squibb, Princeton, NJ".
_FUNCTION = re.compile(
    r"^(clinical pharmacolog|pharmacokinetic|pharmacometric|dmpk|"
    r"quantitative |translational (medicine|science)|biostatistic|"
    r"clinical development|drug metabolism|early (clinical )?development|"
    r"biopharmaceutic|bioanalys|modeling and simulation|modelling and simulation)",
    re.I)


def _is_subunit(chunk):
    """A team or department name rather than an employer.

    A chunk starting with a function name is a sub-unit UNLESS it also carries a
    corporate marker -- "Pharmacokinetics Ltd" would be a real company.
    """
    if _SUBUNIT.match(chunk):
        return True
    return bool(_FUNCTION.match(chunk)) and not INDUSTRY_HINT.search(chunk)


def _org_of(affil):
    """The employer named in an affiliation string.

    Usually the first comma-delimited chunk, but skip leading sub-units.
    """
    a = re.sub(r"Electronic address:.*$", "", affil or "", flags=re.I).strip()
    if not a:
        return ""
    chunks = [c.strip(" .") for c in a.split(",") if c.strip(" .")]
    for c in chunks[:4]:
        if not _is_subunit(c):
            return c
    return chunks[0] if chunks else ""


def _is_sponsor(affil, sponsor):
    """Does this affiliation belong to the drug's sponsor?

    Reuses the hardened company matcher rather than inventing another one --
    sheets._prefix_match already carries the fix for VERA THERAPEUTICS matching
    verastem oncology, and selftest.py pins it.
    """
    if not affil or not sponsor:
        return False
    want = sheets._normalise_company(sponsor)
    if not want:
        return False
    # Test each comma-chunk: "Eli Lilly and Company, Indianapolis, Indiana, USA."
    for chunk in re.split(r"[,;]", affil)[:3]:
        got = sheets._normalise_company(chunk)
        if got and (got == want or sheets._prefix_match(got, want)):
            return True
    return False


def _other_pharma(affil, sponsor):
    """Industry affiliation, but a different company -- they may have moved.

    PubMed records the affiliation at publication. On the tebipenem mass-balance
    paper the co-authors are listed at Takeda, ImmunoGen and Nurix, which is
    almost certainly where they went afterwards, not where they did the work.
    Worth flagging rather than trusting or discarding.
    """
    if not affil or _is_sponsor(affil, sponsor):
        return False
    return bool(INDUSTRY_HINT.search(affil)) and not ACADEMIC_HINT.search(affil)


def programme_org(articles, min_papers=2):
    """The industry organisation that recurs across a drug's papers.

    The FDA sponsor of record is not always who ran the clinical pharmacology
    programme. Tebipenem is approved to GlaxoSmithKline but every Phase 1 paper
    is authored out of Spero Therapeutics, which GSK acquired -- so matching on
    the FDA sponsor alone finds nobody. The company that keeps appearing across
    a drug's own papers is a good stand-in, and it also covers licensing deals
    and co-development.
    """
    counts = {}
    for art in articles:
        seen = set()
        for a in art["authors"]:
            aff = a["affil"]
            if not aff or ACADEMIC_HINT.search(aff) or not INDUSTRY_HINT.search(aff):
                continue
            org = _org_of(aff)
            key = sheets._normalise_company(org)
            if key and key not in seen:
                seen.add(key)
                counts.setdefault(key, [0, org])
                counts[key][0] += 1
    if not counts:
        return ""
    key, (n, org) = max(counts.items(), key=lambda kv: kv[1][0])
    return org if n >= min_papers else ""


def rank(articles, sponsor, members=None, limit=6):
    """Rank people by how likely they are the drug's clinical pharmacologist.

    articles: [{pmid, journal, tier, authors:[...]}]
    members:  optional {normalised name -> org} from an ASCPT directory
    """
    prog_org = programme_org(articles)
    people = {}
    for art in articles:
        tier = art.get("tier", 3)
        for a in art["authors"]:
            key = person_key(a["name"])
            p = people.setdefault(key, {
                "name": a["name"], "affils": [], "pmids": [], "tiers": set(),
                "first": False, "last": False, "best_pos": 99,
                "sponsor": False, "moved": False, "programme": False,
                "cp_journal": False, "email": "",
            })
            if len(a["name"]) > len(p["name"]):
                p["name"] = a["name"]
            p["pmids"].append(art["pmid"])
            p["tiers"].add(tier)
            if a["affil"] and a["affil"] not in p["affils"]:
                p["affils"].append(a["affil"])
            if _is_sponsor(a["affil"], sponsor):
                p["sponsor"] = True
            elif prog_org and _is_sponsor(a["affil"], prog_org):
                p["programme"] = True
            elif _other_pharma(a["affil"], sponsor):
                p["moved"] = True
            # Position only counts on tiers 1-2. On a dose-escalation paper the
            # first and last authors are the clinicians, not the pharmacologist.
            #
            # First outranks last: on a clin pharm study the first author ran it
            # and the last author supervised. Both are worth contacting, but if
            # only one name goes on the invitation it should be the first.
            if tier in (1, 2):
                if a["pos"] == 0:
                    p["first"] = True
                elif a["pos"] == a["n"] - 1:
                    p["last"] = True
            p["best_pos"] = min(p["best_pos"], a["pos"])
            if CP_JOURNALS.search(art.get("journal", "")):
                p["cp_journal"] = True
            if not p["email"]:
                p["email"] = _email(a["affil"])

    mem = members or {}
    ranked = []
    for p in people.values():
        score, why = 0, []
        if p["sponsor"]:
            score += 50
            why.append("at the sponsor")
        elif p["programme"]:
            score += 40
            why.append(f"at {prog_org}, which ran the programme")
        if p["first"]:
            score += 25
            why.append("first author on a clin pharm study")
        elif p["last"]:
            score += 18
            why.append("senior author on a clin pharm study")
        if 1 in p["tiers"]:
            score += 15
            why.append("clin pharm study")
        elif 2 in p["tiers"]:
            score += 10
            why.append("pharmacometrics")
        if (p["sponsor"] or p["programme"]) and p["tiers"] == {3}:
            score += 12
            why.append("sponsor author on the Phase 1")
        npapers = len(set(p["pmids"]))
        if npapers > 1:
            score += min(12, 4 * (npapers - 1))
            why.append(f"{npapers} papers")
        if len(p["tiers"]) > 1:
            score += 8
            why.append("across study types")
        if p["cp_journal"]:
            score += 6
            why.append("clin pharm journal")
        if p["moved"] and not p["sponsor"]:
            score -= 10
            why.append("industry, but a different company — may have moved")

        p["member"] = _member_of(p["name"], mem)
        if p["member"]:
            score += 10
            why.append("ASCPT member")

        # Someone with no sponsor tie and only a dose-escalation paper is almost
        # certainly a site investigator. Drop them rather than pad the list.
        if not (p["sponsor"] or p["programme"]) and p["tiers"] == {3}:
            continue
        # Show the org where they did the work, and flag it if their latest
        # affiliation is somewhere else. People move between the study and the
        # approval, and "worked on it at Spero, now at Takeda" is far more
        # useful for outreach than either fact alone.
        home = ""
        for aff in p["affils"]:
            if _is_sponsor(aff, sponsor) or (prog_org and _is_sponsor(aff, prog_org)):
                home = _org_of(aff)
                break
        others = [_org_of(a) for a in p["affils"]
                  if _org_of(a) and _org_of(a) != home
                  and INDUSTRY_HINT.search(a) and not ACADEMIC_HINT.search(a)]
        p["org"] = home or (_org_of(p["affils"][0]) if p["affils"] else "")
        if home and others:
            why.append(f"now at {others[-1]}")

        p["score"] = score
        p["why"] = "; ".join(why)
        ranked.append(p)

    # Tie-break on where they appear before falling back to the name, so two
    # equally-scored people are ordered by seniority in the author list rather
    # than by alphabet.
    ranked.sort(key=lambda x: (-x["score"], x["best_pos"], x["name"]))
    return ranked[:limit]


def _member_of(name, members):
    if not members:
        return ""
    k = re.sub(r"[^a-z ]", "", (name or "").lower()).strip()
    if k in members:
        return members[k]
    # "Xiaosu Ma" vs "Ma, Xiaosu" and middle initials
    parts = k.split()
    if len(parts) >= 2:
        alt = f"{parts[-1]} {parts[0]}"
        if alt in members:
            return members[alt]
    return ""


def format_contacts(ranked):
    """One human-readable line per person, for a spreadsheet cell."""
    bits = []
    for p in ranked:
        org = f" ({p['org']})" if p["org"] else ""
        mem = " · ASCPT member" if p.get("member") else ""
        mail = f" · {p['email']}" if p["email"] else ""
        bits.append(f"{p['name']}{org} — {p['why']}{mem}{mail}")
    return " | ".join(bits)


def format_evidence(ranked):
    seen = []
    for p in ranked:
        for pm in p["pmids"]:
            if pm not in seen:
                seen.append(pm)
    return ", ".join(seen[:12])
