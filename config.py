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
    return f"ASCPT {year} MOA recruiting dossier"


def dossier_path(year):
    return os.path.join(output_dir(), f"{dossier_name(year)}.xlsx")


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
    "Modality", "Gap flag", "Novelty", "Prior review?", "Candidate authors",
    "Contact", "AE owner", "Status", "First seen",
]

# Mirrors the vocabulary already in use in "CTS coverage AM 2026".
#
# "ASCPT presence" and "Poster / session detail" describe the UPCOMING meeting
# only. "Last year at ASCPT" and "Who to find" carry the history, on the theory
# that people who came last year tend to come again. Keeping them in separate
# columns is the point: they used to be merged, so a 2027 dossier offered poster
# times from March 2026 as somewhere to walk to.
DOSSIER_COLUMNS = [
    "Rank", "Drug (INN)", "Brand", "Sponsor", "Approval date", "Modality",
    "Gap flag", "Novelty", "Prior review?", "ASCPT presence", "Poster / session detail",
    "Last year at ASCPT", "Who to find",
    "Contact", "Candidate authors", "AE owner", "Attending?", "Comments",
]

# ---------------------------------------------------------------- editorial state
# The 19 published MOA mini-reviews (Dec 2023 - Aug 2026), by INN.
# Source: PubMed title-convention search; see the team deck, slide 2.
PUBLISHED_MOA_DRUGS = {
    "upadacitinib", "maribavir", "teclistamab", "ubrogepant", "risankizumab",
    "atogepant", "molnupiravir", "mobocertinib", "evinacumab", "dupilumab",
    "momelotinib", "imetelstat", "ibrexafungerp", "suzetrigine",
    "mirvetuximab soravtansine", "esketamine", "elranatamab", "talquetamab",
    "brexanolone",
}

# Coverage gaps identified on slide 4 of the team deck.
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
MODALITY_STEMS = [
    # cell and gene therapy
    ("cabtagene",  "CAR-T cell therapy",            "CAR-T"),
    ("leucel",     "CAR-T cell therapy",            "CAR-T"),
    ("tocel",      "Engineered cell therapy",       "Cell & gene therapy"),
    ("temcel",     "Cell therapy",                  "Cell & gene therapy"),
    ("keracel",    "Cell therapy",                  "Cell & gene therapy"),
    ("parvovec",   "Gene therapy (AAV)",            "Cell & gene therapy"),
    ("abeparvovec", "Gene therapy (AAV)",           "Cell & gene therapy"),
    ("nogene",     "Gene therapy",                  "Cell & gene therapy"),
    ("vec",        "Gene therapy (viral vector)",   "Cell & gene therapy"),
    # oligonucleotides
    ("siran",      "siRNA",                         "siRNA / ASO"),
    ("rsen",       "Antisense oligonucleotide",     "siRNA / ASO"),
    ("mersen",     "Antisense oligonucleotide",     "siRNA / ASO"),
    ("nusinersen", "Antisense oligonucleotide",     "siRNA / ASO"),
    # antibodies and antibody-like
    ("tamab",      "Bispecific T-cell engager",     None),
    ("bamab",      "Bispecific antibody",           None),
    ("ximab",      "Monoclonal antibody (chimeric)", None),
    ("zumab",      "Monoclonal antibody (humanised)", None),
    ("umab",       "Monoclonal antibody (human)",   None),
    ("mab",        "Monoclonal antibody",           None),
    ("tug",        "Monoclonal antibody",           None),
    ("bart",       "Engineered antibody",           None),
    ("mig",        "Multispecific immunoglobulin",  None),
    ("ment",       "Antibody fragment",             None),
    # fusion proteins and peptides
    ("fusp",       "Fusion protein",                None),
    ("bcept",      "Fc-fusion protein",             None),
    ("cept",       "Fusion protein",                None),
    ("glipron",    "Oral GLP-1 receptor agonist",   "Incretins & cardiometabolic"),
    ("glutide",    "GLP-1 receptor agonist",        "Incretins & cardiometabolic"),
    ("gliptin",    "DPP-4 inhibitor",               "Incretins & cardiometabolic"),
    ("flozin",     "SGLT2 inhibitor",               "Incretins & cardiometabolic"),
    ("pegritide",  "PEGylated peptide",             None),
    ("ritide",     "Natriuretic peptide",           None),
    ("virtide",    "Entry-inhibitor peptide",       None),
    ("tide",       "Peptide",                       None),
    ("insulin",    "Insulin analogue",              "Incretins & cardiometabolic"),
    ("arginase",   "Enzyme replacement",            None),
    ("ase",        "Enzyme",                        None),
    # small molecules, by target class
    ("degrastrant", "Targeted protein degrader",    None),
    ("gestrant",   "Targeted protein degrader",     None),
    ("domide",     "Cereblon E3 ligase modulator",  None),
    ("toclax",     "BCL-2 inhibitor",               None),
    ("trelvir",    "Viral protease inhibitor",      None),
    ("previr",     "Viral protease inhibitor",      None),
    ("ciclib",     "CDK inhibitor",                 None),
    ("parib",      "PARP inhibitor",                None),
    ("lisib",      "PI3K/mTOR inhibitor",           None),
    ("tinib",      "Kinase inhibitor",              None),
    ("ostat",      "HDAC inhibitor",                None),
    ("drostat",    "Aldosterone synthase inhibitor", None),
    ("penem",      "Carbapenem antibacterial",      None),
    ("bactam",     "Beta-lactamase inhibitor",      None),
    ("xibat",      "IBAT inhibitor",                None),
    ("corilant",   "Glucocorticoid receptor modulator", None),
    ("orexton",    "Orexin receptor agonist",       None),
    ("fadine",     "Monoamine reuptake inhibitor",  None),
    ("milast",     "PDE4 inhibitor",                None),
    ("peridone",   "Atypical antipsychotic",        None),
    ("profol",     "GABA-A anaesthetic",            None),
    ("pofol",      "GABA-A anaesthetic",            None),
    ("vastatin",   "Statin",                        None),
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
    ("antibody-drug conjugate", "Antibody-drug conjugate", None),
    ("conjugate",               "Conjugate",               None),
    ("engager",                 "T-cell engager",          None),
    ("chimeric antigen",        "CAR-T cell therapy",      "CAR-T"),
    ("vaccine",                 "Vaccine",                 "Vaccines"),
    ("keratinocyte",            "Cell therapy",            "Cell & gene therapy"),
    ("fibroblast",              "Cell therapy",            "Cell & gene therapy"),
    ("cultured",                "Cell therapy",            "Cell & gene therapy"),
    ("f 18",                    "PET imaging agent",       None),
    ("f18",                     "PET imaging agent",       None),
    ("lutetium",                "Radioligand therapy",     "Radioligand therapy"),
    ("actinium",                "Radioligand therapy",     "Radioligand therapy"),
    ("gadolinium",              "MRI contrast agent",      None),
    ("gado",                    "MRI contrast agent",      None),
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
