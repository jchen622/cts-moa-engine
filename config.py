"""Static configuration for the MOA sourcing engine.

Everything here is data the group may want to edit without touching logic.

Portability: nothing here is tied to one machine or one person. Every path is
resolved relative to this file, and the tool runs with no settings file at all
-- output lands in ``output/`` next to the code. ``settings.json`` only exists
to move those folders somewhere else. To hand the tool to another editor they
copy the folder and double-click the launcher; there is nothing to install, no
account to create and no credential to share.
"""
import datetime
import json
import os
import re

HERE = os.path.dirname(os.path.abspath(__file__))

# Set by the single-file bundle at startup so the page can show which build is
# running. Empty when running from source, where the git log is the record.
BUILD_STAMP = ""
CACHE = os.path.join(HERE, "cache")
SETTINGS_PATH = os.environ.get("MOA_SETTINGS", os.path.join(HERE, "settings.json"))


class SettingsError(RuntimeError):
    pass


def load_settings(required=True):
    """Read settings.json. A missing file is fine -- the defaults all work.

    ``required`` is kept as a parameter because callers pass it, but nothing is
    actually required any more: the tool must run out of the box for whoever it
    is handed to.
    """
    if not os.path.exists(SETTINGS_PATH):
        return {}
    with open(SETTINGS_PATH) as fh:
        return json.load(fh)


def save_settings(s):
    with open(SETTINGS_PATH, "w") as fh:
        json.dump(s, fh, indent=2)
        fh.write("\n")


# ---------------------------------------------------------------- local paths
# _OUTPUT_OVERRIDE lets --output-dir redirect every write for a single run,
# which is how the test workflow avoids touching the real output folder.
_OUTPUT_OVERRIDE = [None]


def set_output_dir(path):
    _OUTPUT_OVERRIDE[0] = os.path.abspath(os.path.expanduser(path)) if path else None


def _resolve(value, default):
    if not value:
        return default
    return os.path.abspath(os.path.expanduser(
        value if os.path.isabs(os.path.expanduser(value))
        else os.path.join(HERE, value)))


def output_dir():
    if _OUTPUT_OVERRIDE[0]:
        return _OUTPUT_OVERRIDE[0]
    return _resolve(load_settings(required=False).get("output_dir"),
                    os.path.join(HERE, "output"))


def input_dir():
    return _resolve(load_settings(required=False).get("input_dir"),
                    os.path.join(HERE, "input"))


def contacts_file():
    return _resolve(load_settings(required=False).get("contacts_file"),
                    os.path.join(input_dir(), "contacts.xlsx"))


# ---------------------------------------------------------------- meeting files
# Programme and attendee exports are keyed by meeting year, so the upcoming
# meeting and previous ones stay distinct. They used to be one fixed path, which
# meant a 2027 dossier was quietly matched against the 2026 programme and
# offered poster times that had already happened.
PROGRAM_PATTERN = "ascpt program {year}.xlsx"
ATTENDEES_PATTERN = "ascpt attendees {year}.xlsx"

_YEAR_RE = re.compile(r"^ascpt (program|attendees) (\d{4})\.xlsx$", re.I)


def program_file(year):
    """Legacy `ascpt_program_file` still wins, but only for its own year."""
    legacy = load_settings(required=False).get("ascpt_program_file")
    if legacy and str(year) in os.path.basename(legacy):
        return _resolve(legacy, "")
    return os.path.join(input_dir(), PROGRAM_PATTERN.format(year=year))


def attendees_file(year):
    return os.path.join(input_dir(), ATTENDEES_PATTERN.format(year=year))


# Membership is standing, not tied to a meeting, so this file is NOT year-keyed.
MEMBERS_FILE = "ascpt members.xlsx"


def members_file():
    return os.path.join(input_dir(), MEMBERS_FILE)


# Headers that only a membership export carries. Used to tell a member
# directory apart from an attendee list -- both are just name + organisation,
# so without one of these the file is treated as an attendee list.
MEMBER_ONLY_COLUMNS = ("member since", "membership type", "member id",
                       "member type", "membership status", "join date",
                       "member number", "community", "communities")

# Where a membership export states what someone works on. ASCPT's own term is
# "community"; other exports call it a discipline or an area of interest.
MEMBER_DISCIPLINE_COLUMNS = ("primary discipline", "discipline", "community",
                             "communities", "area of interest",
                             "areas of interest", "interest area", "specialty",
                             "speciality", "focus area", "primary focus")

# What counts as clinical pharmacology for the purpose of that column. Written
# out rather than inferred so the filter can be argued with, and deliberately
# NOT including "clinical pharmacy", which is a different profession.
CLINPHARM_DISCIPLINES = ("clinical pharmacolog", "translational", "pharmacometric",
                         "pharmacokinetic", "pharmacodynamic", "pk/pd", "pkpd",
                         "quantitative pharmacolog", "drug metabolism",
                         "drug disposition", "dmpk", "model-informed",
                         "exposure-response", "clinical pharmacology")

# Where a membership export states an address.
MEMBER_EMAIL_COLUMNS = ("email", "e-mail", "email address", "e-mail address",
                        "primary email", "work email", "business email")


def ncbi_email():
    """Contact address sent to NCBI E-utilities, if the user supplies one.

    NCBI asks automated callers to identify themselves so they can get in touch
    about a misbehaving script. That should be whoever is actually running it,
    which is why it lives in settings.json rather than being baked in -- and it
    keeps a personal address out of a public repository.
    """
    return (load_settings(required=False).get("ncbi_email") or "").strip()


def meeting_year(today=None):
    """Which ASCPT meeting are we working towards?

    The annual meeting is in March, so from June onwards the next one is next
    calendar year; before that it is this one. Lives here, not in the CLI,
    because the GUI labels the buttons with it -- computing it twice would let
    the page say "ASCPT 2027" while the dossier quietly built 2028.

    Known soft edge: between the March meeting and 1 June, this still returns
    the year of the meeting that has just happened. That is deliberate -- you
    are still writing up and following up from it, and `--year` overrides.
    """
    today = today or datetime.date.today()
    return today.year + 1 if today.month >= 6 else today.year


def meeting_files():
    """{year: {'program': path|None, 'attendees': path|None}} found on disk.

    Discovered by globbing rather than configured, so dropping in an older
    export makes it available with no code or settings change.
    """
    found = {}
    d = input_dir()
    if not os.path.isdir(d):
        return found
    for name in sorted(os.listdir(d)):
        m = _YEAR_RE.match(name)
        if not m:
            continue
        kind, year = m.group(1).lower(), int(m.group(2))
        found.setdefault(year, {"program": None, "attendees": None})
        found[year][kind] = os.path.join(d, name)
    return found


def queue_path():
    return os.path.join(output_dir(), f"{QUEUE_SHEET_NAME}.xlsx")


def dossier_name(year):
    """The objective is reaching company clinical pharmacologists.

    This used to be called "ASCPT {year} MOA recruiting dossier", which framed
    the whole tool around one annual meeting. The meeting is one channel for
    reaching the right person, not the point of the exercise, so the file is
    named for the job it does. The year stays because the ASCPT columns in it
    are year-specific and the list is naturally refreshed each cycle.
    """
    return f"MOA author outreach list {year}"


# --------------------------------------------------------------- hot list
# Columns are exactly what the team asked for and nothing else. "Clin pharm
# contact" ships EMPTY and the engine never writes to it: it is the question
# being put to the AE team, not an answer the engine offers. Pre-filling it with
# a paper's author would be worse than blank, because that person is frequently
# not the right one to approach.
# The last three are the team's, not the engine's. It ships them blank and
# never writes a value into any of them, on any run. The ASCPT column is
# deliberately manual: the member directory is behind a login and its terms do
# not permit scraping, so an AE looks the person up and types the name in.
# The last TWO are the engine's and are index-aligned: name 2 in one cell is
# the person whose address is line 2 of the other, so the pair can be read
# across. Every match is listed, not a sample, because a company can have
# several clinical pharmacologists and picking one for the editor is not the
# engine's call.
# "ClinPharm ASCPT Member" is the one contact-ish column the ENGINE fills. It
# is populated from an ASCPT membership export the user imports with
# `roster --kind members`, and it is blank until one exists. Keep it last and
# keep it distinct from the manual directory column beside it: that one is a
# lookup an AE did by hand, and nothing regenerated must ever overwrite it.
HOTLIST_COLUMNS = [
    "Drug name", "MOA", "Indication", "NDA/BLA number", "Approval date",
    "Company name", "AE owner", "Clin pharm contact",
    "ClinPharm contact from ASCPT Membership Directory",
    "ClinPharm ASCPT Member", "ClinPharm ASCPT Member email",
]

# The gap analysis still decides what is on the list and still drives the
# ranking. It is simply no longer a column; the methodology sheet carries it,
# so the sheet never becomes a list with no stated basis.

# 36 months, not 12. Measured from the series itself: joining the 21 published
# reviews to their Drugs@FDA approval dates gives a median approval-to-review
# lag of 2.8 years (quartiles 2.0 / 2.8 / 4.3), with 25% inside two years and
# 55% inside three. A 12-month window would catch only the three fastest
# turnarounds the series has ever managed.
HOTLIST_MONTHS = 36
HOTLIST_TOP = 20          # highlighted priority block
HOTLIST_TOTAL = 50        # rows on the sheet in all, so 30 below the priority block


HOTLIST_STEM = "MOA outreach list"

# Drugs the editor wants on the list regardless of what the filter thinks: one
# per line, blank lines and # comments ignored. These ADD to the priority block
# rather than competing for its 20 places, because a hand-typed drug reflects a
# judgement the engine cannot make. They also bypass the date window, so an
# older agent of interest can be included.
MANUAL_DRUGS_FILE = "my drugs.txt"


def manual_drugs_file():
    return os.path.join(input_dir(), MANUAL_DRUGS_FILE)


def manual_drugs():
    p = manual_drugs_file()
    if not os.path.exists(p):
        return []
    out = []
    with open(p, encoding="utf-8", errors="replace") as fh:
        for line in fh:
            line = line.split("#")[0].strip()
            if line:
                out.append(line)
    return out


def hotlist_path(today=None):
    """One file per date. A same-day re-run overwrites rather than piling up."""
    d = (today or datetime.date.today()).isoformat()
    return os.path.join(output_dir(), f"{HOTLIST_STEM} {d}.xlsx")


def latest_hotlist_path(before=None):
    """The most recent earlier outreach list, for carrying contacts forward.

    Without this, a run on a new date would hand the team a blank sheet and
    silently discard everything they had filled in. Chosen by the date in the
    filename rather than mtime, because a file copied or synced by Drive gets a
    fresh mtime and would otherwise look like the newest.
    """
    import glob
    import re as _re
    out = []
    for f in glob.glob(os.path.join(output_dir(), f"{HOTLIST_STEM} *.xlsx")):
        m = _re.search(r"(\d{4}-\d{2}-\d{2})\.xlsx$", f)
        if m and (before is None or m.group(1) < before):
            out.append((m.group(1), f))
    return max(out)[1] if out else None


def legacy_dossier_path(year):
    """Where the file used to live, so existing annotations are not orphaned."""
    return os.path.join(output_dir(), f"ASCPT {year} MOA recruiting dossier.xlsx")


def dossier_path(year, existing=False):
    """Current path. With existing=True, fall back to the old name if that is
    the only one on disk -- a rename must not silently discard someone's notes.
    """
    new = os.path.join(output_dir(), f"{dossier_name(year)}.xlsx")
    if existing and not os.path.exists(new):
        old = legacy_dossier_path(year)
        if os.path.exists(old):
            return old
    return new


def membership_check_path():
    """Names the engine wants a membership answer for.

    Deliberately a short list of specific people rather than a copy of the
    directory: checking thirty names you are already corresponding with is
    ordinary member behaviour, bulk-harvesting the directory is not.
    """
    return os.path.join(output_dir(), "membership check list.xlsx")


def invites_path(year):
    return os.path.join(output_dir(), f"MOA invitation drafts {year}.html")

# ---------------------------------------------------------------- data sources
DRUGS_AT_FDA_ZIP = "https://www.fda.gov/media/89850/download"
PURPLE_BOOK_DOWNLOADS = "https://purplebooksearch.fda.gov/downloads"

# Submission classes that mark a genuinely new molecular/biological entity.
# NOTE: the lookup table spells the combined code "TYPE 1/4", NOT "TYPE 1/TYPE 4".
NME_CLASSES = {"TYPE 1", "TYPE 1/4"}

# Type 2 is a NEW ACTIVE INGREDIENT -- an enantiomer, ester or salt of an
# already-approved moiety. Not first-in-class, but not a generic either, and
# the series has published one: esketamine (NDA 211243) is Type 2, and without
# this tier the backtest misses it. Included, but scored below a true NME.
NEW_ACTIVE_INGREDIENT_CLASSES = {"TYPE 2", "TYPE 2/3", "TYPE 2/4"}

# Everything the approval feed will consider at all. Generics (ANDAs),
# reformulations (Type 3/5), new combinations of old drugs (Type 4) and
# biosimilars are excluded by omission.
CANDIDATE_CLASSES = NME_CLASSES | NEW_ACTIVE_INGREDIENT_CLASSES

# ---------------------------------------------------------------- output names
QUEUE_SHEET_NAME = "MOA candidate queue"
QUEUE_TAB = "Queue"

QUEUE_COLUMNS = [
    "Key", "Approval date", "Drug (INN)", "Brand", "Sponsor", "Center",
    "Modality", "Gap flag", "Novelty", "Prior review?",
    "Clin pharm contacts", "Contact evidence (PMIDs)", "Candidate authors",
    "Contact", "AE owner", "Status", "First seen",
]

# Mirrors the vocabulary already in use in "CTS coverage AM 2026".
#
# "ASCPT presence" and "Poster / session detail" describe the UPCOMING meeting
# only. "Last year at ASCPT" and "Who to find" carry the history, on the theory
# that people who came last year tend to come again. Keeping them in separate
# columns is the point: they used to be merged, so a 2027 dossier offered poster
# times from March 2026 as somewhere to walk to.
# Order follows the objective: who to write to comes before how else you might
# bump into them. The ASCPT columns are one route among several, so they sit
# after the contacts rather than leading.
DOSSIER_COLUMNS = [
    "Rank", "Drug (INN)", "Brand", "Sponsor", "Approval date", "Modality",
    "Gap flag", "Novelty", "Prior review?",
    "Clin pharm contacts", "Contact evidence (PMIDs)", "Contact",
    "ASCPT presence", "Poster / session detail",
    "Last year at ASCPT", "Who to find",
    "Candidate authors", "AE owner", "Attending?", "Comments",
]

# Development codes and trade names to search alongside the INN.
#
# A newly approved drug is often absent from the literature under its INN: the
# Phase 1 papers were written years earlier under the development code. Four of
# seven candidates checked in Sep 2026 returned nothing until searched this way.
# Keys are matched on the normalised INN stem, so a salt or suffix still hits.
DRUG_ALIASES = {
    "lunsotogene parvec": ["DB-OTO"],
    # No alias for vusolimogene: its development code "RP1" is also a gene, a
    # protein and a plasmid, and as a bare term it pulled in unrelated authors.
    # An alias has to be more distinctive than the INN, not less.
    "vipivotide tetraxetan": ["PSMA-617", "177Lu-PSMA-617"],
    "enlicitide": ["MK-0616"],
    "obecabtagene autoleucel": ["obe-cel", "AUTO1"],
    "plozasiran": ["ARO-APOC3"],
    "olezarsen": ["AKCEA-APOCIII-LRx", "ISIS 678354"],
}


def aliases_for(ingredient):
    """Extra search terms for a drug, matched on its normalised stem."""
    key = (ingredient or "").lower().strip()
    if key in DRUG_ALIASES:
        return DRUG_ALIASES[key]
    for k, v in DRUG_ALIASES.items():          # tolerate salt/suffix drift
        if k in key or key in k:
            return v
    return []


# ---------------------------------------------------------------- editorial state
# The 19 published MOA mini-reviews (Dec 2023 - Aug 2026), by INN.
# Source: PubMed title-convention search; see the team deck, slide 2.
# backtest.py's labelled set, and ONLY that. It is pinned rather than fetched
# so the regression gate is reproducible offline. The live gap analysis does not
# read it: gaps.published_series() queries PubMed on every run, which is why
# this list sat at 19 while the series had published 22 without anyone noticing.
#
# Refreshed 2026-10-08 from gaps.published_series(): added vericiguat,
# fruquintinib and xanomeline + trospium chloride.
PUBLISHED_MOA_DRUGS = {
    "upadacitinib", "maribavir", "teclistamab", "ubrogepant", "risankizumab",
    "atogepant", "molnupiravir", "mobocertinib", "evinacumab", "dupilumab",
    "momelotinib", "imetelstat", "ibrexafungerp", "suzetrigine",
    "mirvetuximab soravtansine", "esketamine", "elranatamab", "talquetamab",
    "brexanolone", "vericiguat", "fruquintinib",
    "xanomeline and trospium chloride",
}

# Fallback only. gaps.gap_report() derives the open gaps live from what the
# series has actually published and uses this list solely when PubMed is
# unreachable, in which case it says so in the output.
#
# Originally copied off slide 4 of the team deck. The live analysis now confirms
# six of these seven and retires "siRNA / ASO", which imetelstat covered in
# Nov 2024.
GAP_CATEGORIES = [
    "Cell & gene therapy", "CAR-T", "Radioligand therapy", "siRNA / ASO",
    "Incretins & cardiometabolic", "Vaccines", "AI-derived assets",
]

# INN stem -> (modality label, gap category or None).
# Matched longest-stem-wins, so specificity beats ordering.
#
# The antibody stems below follow the 2021 WHO revision, which replaced the old
# "-mab" ending with -tug (unmodified immunoglobulin), -bart (artificial),
# -mig (multi-specific) and -ment (fragment). Without these, current approvals
# such as veligrotug get misfiled as small molecules.
# --------------------------------------------------------------- two axes
# The labels used to be ONE flat set mixing molecular format ("Small molecule"),
# target class ("Kinase inhibitor") and antibody provenance ("(humanised)").
# Measured on the 22 published papers, 18 were classified by format and 4 by
# target class, so the coverage counts were not comparable quantities. The two
# visible consequences: kinase inhibitors were excluded from small molecules
# (10 reported, 14 actual) and ADCs and bispecifics from antibodies (3 reported,
# 7 actual), either of which can call a modality uncovered when the series has
# published in it repeatedly.
#
# MODALITY_PARENT is child -> parent for the FORMAT axis only. Roll-up is the
# point: "has the series covered antibodies?" must count an ADC, while "has it
# covered ADCs?" must not count a naked antibody.
MODALITY_PARENT = {
    # antibodies and antibody-derived formats
    "Monoclonal antibody":        "Antibody",
    "Antibody-drug conjugate":    "Antibody",
    "Bispecific antibody":        "Antibody",
    "Bispecific T-cell engager":  "Bispecific antibody",
    "T-cell engager":             "Bispecific antibody",
    "Multispecific immunoglobulin": "Bispecific antibody",
    "Antibody fragment":          "Antibody",
    "Engineered antibody":        "Antibody",
    # nucleic acids
    "Antisense oligonucleotide":  "Oligonucleotide",
    "siRNA":                      "Oligonucleotide",
    # proteins that are not antibodies
    "Enzyme":                     "Protein (non-antibody)",
    "Enzyme replacement":         "Protein (non-antibody)",
    "Fusion protein":             "Protein (non-antibody)",
    "Fc-fusion protein":          "Fusion protein",
    "Insulin analogue":           "Peptide",
    "PEGylated peptide":          "Peptide",
    "Natriuretic peptide":        "Peptide",
    "Entry-inhibitor peptide":    "Peptide",
    # cells and genes
    "CAR-T cell therapy":         "Cell therapy",
    "Engineered cell therapy":    "Cell therapy",
    "Gene therapy (AAV)":         "Gene therapy",
    "Gene therapy (viral vector)": "Gene therapy",
    # imaging
    "PET imaging agent":          "Imaging agent",
    "MRI contrast agent":         "Imaging agent",
}

# The format axis, in full. Anything not here is a target class, never a format.
MODALITY_FORMATS = set(MODALITY_PARENT) | {
    "Small molecule", "Peptide", "Oligonucleotide", "Antibody",
    "Protein (non-antibody)", "Cell therapy", "Gene therapy", "Vaccine",
    "Radioligand therapy", "Imaging agent", "Conjugate",
    # The fallback when no stem or keyword matches. A declared root with no
    # parent, so it rolls up to nothing and reads as uncovered rather than
    # borrowing the small-molecule count.
    "Biologic (unspecified)",
}


def modality_chain(label):
    """A format label plus every parent above it, most specific first.

    Used for roll-up, so one published ADC registers as coverage of ADCs AND of
    antibodies, without a naked antibody counting as coverage of ADCs.
    """
    out, seen = [], set()
    while label and label not in seen:
        out.append(label)
        seen.add(label)
        label = MODALITY_PARENT.get(label)
    return out


MODALITY_STEMS = [
    # --- cell and gene therapy
    ("cabtagene",  "CAR-T cell therapy",          None, "CAR-T"),
    ("leucel",     "Engineered cell therapy", None, "Cell & gene therapy"),
    ("tocel",      "Engineered cell therapy",     None, "Cell & gene therapy"),
    ("temcel",     "Cell therapy",                None, "Cell & gene therapy"),
    ("keracel",    "Cell therapy",                None, "Cell & gene therapy"),
    ("parvovec",   "Gene therapy (AAV)",          None, "Cell & gene therapy"),
    ("abeparvovec", "Gene therapy (AAV)",         None, "Cell & gene therapy"),
    ("nogene",     "Gene therapy",                None, "Cell & gene therapy"),
    ("vec",        "Gene therapy (viral vector)", None, "Cell & gene therapy"),
    # --- oligonucleotides
    ("siran",      "siRNA",                     "RNA interference",      "siRNA / ASO"),
    ("telstat",    "Oligonucleotide",           "Telomerase inhibitor",  "siRNA / ASO"),
    ("rsen",       "Antisense oligonucleotide", None,                    "siRNA / ASO"),
    ("mersen",     "Antisense oligonucleotide", None,                    "siRNA / ASO"),
    ("nusinersen", "Antisense oligonucleotide", None,                    "siRNA / ASO"),
    # --- antibodies. The 2021 WHO stems replaced -mab for new antibodies.
    # Humanisation is NOT a modality: -ximab/-zumab/-umab all collapse to
    # Monoclonal antibody, because splitting three antibodies across three
    # labels is what made antibody coverage read 1+1+1 instead of 3.
    ("tamab",      "Bispecific T-cell engager",  None, None),
    ("bamab",      "Bispecific antibody",        None, None),
    ("ximab",      "Monoclonal antibody",        None, None),
    ("zumab",      "Monoclonal antibody",        None, None),
    ("umab",       "Monoclonal antibody",        None, None),
    ("mab",        "Monoclonal antibody",        None, None),
    ("tug",        "Monoclonal antibody",        None, None),
    ("bart",       "Engineered antibody",        None, None),
    ("mig",        "Multispecific immunoglobulin", None, None),
    ("ment",       "Antibody fragment",          None, None),
    # --- other proteins
    ("fusp",       "Fusion protein",             None, None),
    ("bcept",      "Fc-fusion protein",          None, None),
    ("cept",       "Fusion protein",             None, None),
    ("arginase",   "Enzyme replacement",         None, None),
    ("ase",        "Enzyme",                     None, None),
    # --- peptides and incretins. Note the two GLP-1 formats.
    ("glipron",    "Small molecule", "GLP-1 receptor agonist",  "Incretins & cardiometabolic"),
    ("glutide",    "Peptide",        "GLP-1 receptor agonist",  "Incretins & cardiometabolic"),
    ("pegritide",  "PEGylated peptide",   None,                 None),
    ("ritide",     "Natriuretic peptide", None,                 None),
    ("virtide",    "Peptide",        "Viral entry inhibitor",   None),
    ("tide",       "Peptide",        None,                      None),
    ("insulin",    "Insulin analogue", None,  "Incretins & cardiometabolic"),
    # --- small molecules, with their target class kept separate
    ("gliptin",    "Small molecule", "DPP-4 inhibitor",   "Incretins & cardiometabolic"),
    ("flozin",     "Small molecule", "SGLT2 inhibitor",   "Incretins & cardiometabolic"),
    ("degrastrant", "Small molecule", "Targeted protein degrader", None),
    ("gestrant",   "Small molecule", "Targeted protein degrader", None),
    ("domide",     "Small molecule", "Cereblon E3 ligase modulator", None),
    ("toclax",     "Small molecule", "BCL-2 inhibitor",   None),
    ("trelvir",    "Small molecule", "Viral protease inhibitor", None),
    ("previr",     "Small molecule", "Viral protease inhibitor", None),
    ("ciclib",     "Small molecule", "CDK inhibitor",     None),
    ("parib",      "Small molecule", "PARP inhibitor",    None),
    ("lisib",      "Small molecule", "PI3K/mTOR inhibitor", None),
    ("tinib",      "Small molecule", "Kinase inhibitor",  None),
    ("ostat",      "Small molecule", "HDAC inhibitor",    None),
    ("drostat",    "Small molecule", "Aldosterone synthase inhibitor", None),
    ("penem",      "Small molecule", "Carbapenem antibacterial", None),
    ("bactam",     "Small molecule", "Beta-lactamase inhibitor", None),
    ("xibat",      "Small molecule", "IBAT inhibitor",    None),
    ("corilant",   "Small molecule", "Glucocorticoid receptor modulator", None),
    ("orexton",    "Small molecule", "Orexin receptor agonist", None),
    ("fadine",     "Small molecule", "Monoamine reuptake inhibitor", None),
    ("milast",     "Small molecule", "PDE4 inhibitor",    None),
    ("peridone",   "Small molecule", "Atypical antipsychotic", None),
    ("profol",     "Small molecule", "GABA-A anaesthetic", None),
    ("pofol",      "Small molecule", "GABA-A anaesthetic", None),
    ("vastatin",   "Small molecule", "HMG-CoA reductase inhibitor", None),
]

# Modalities that are intrinsically harder to explain and therefore make
# especially good mini-review subjects.
COMPLEX_MODALITIES = {
    "CAR-T cell therapy", "Engineered cell therapy", "Cell therapy",
    "Gene therapy (AAV)", "Gene therapy", "Gene therapy (viral vector)",
    "siRNA", "Antisense oligonucleotide", "Bispecific T-cell engager",
    "Bispecific antibody", "Antibody-drug conjugate", "Targeted protein degrader",
    "Fusion protein", "Fc-fusion protein", "Multispecific immunoglobulin",
    "Cereblon E3 ligase modulator", "Radioligand therapy", "T-cell engager",
}

# Diagnostics explain an imaging mechanism rather than a therapeutic one, so
# they rank below therapeutics without being excluded outright.
DIAGNOSTIC_MODALITIES = {"MRI contrast agent", "PET imaging agent"}

# Words in the proper/brand name that override or refine the stem guess.
MODALITY_KEYWORDS = [
    ("antibody-drug conjugate", "Antibody-drug conjugate", None, None),
    ("soravtansine",            "Antibody-drug conjugate", None, None),
    ("mertansine",              "Antibody-drug conjugate", None, None),
    ("deruxtecan",              "Antibody-drug conjugate", None, None),
    ("vedotin",                 "Antibody-drug conjugate", None, None),
    ("govitecan",               "Antibody-drug conjugate", None, None),
    ("ozogamicin",              "Antibody-drug conjugate", None, None),
    ("tirumotecan",             "Antibody-drug conjugate", None, None),
    ("conjugate",               "Conjugate",               None, None),
    ("engager",                 "Bispecific T-cell engager", None, None),
    ("chimeric antigen",        "CAR-T cell therapy",      None, "CAR-T"),
    ("vaccine",                 "Vaccine",                 None, "Vaccines"),
    ("keratinocyte",            "Cell therapy",            None, "Cell & gene therapy"),
    ("fibroblast",              "Cell therapy",            None, "Cell & gene therapy"),
    ("cultured",                "Cell therapy",            None, "Cell & gene therapy"),
    ("f 18",                    "PET imaging agent",       None, None),
    ("f18",                     "PET imaging agent",       None, None),
    ("lutetium",                "Radioligand therapy",     None, "Radioligand therapy"),
    ("actinium",                "Radioligand therapy",     None, "Radioligand therapy"),
    ("gadolinium",              "MRI contrast agent",      None, None),
    ("gado",                    "MRI contrast agent",      None, None),
]

# Company-name noise stripped before fuzzy-matching sponsors to the contact grid.
COMPANY_NOISE = [
    "incorporated", "inc", "llc", "ltd", "limited", "corp", "corporation",
    "company", "co", "plc", "gmbh", "ag", "ab", "a/s", "bv", "b.v.", "nv",
    "sa", "s.a.", "usa", "us", "america", "american", "pharmaceuticals",
    "pharmaceutical", "pharms", "pharma", "therapeutics", "theraps", "biopharma",
    "biosciences", "bioscience", "biologics", "healthcare", "hlthcare", "health",
    "sciences", "science", "group", "holdings", "division", "intl", "international",
]

CONTACT_MATCH_THRESHOLD = 0.82   # below this -> "NEEDS LOOKUP", never a guess
