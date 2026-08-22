"""Local spreadsheet storage -- the .xlsx workbooks this tool reads and writes.

Standard library only. There is no openpyxl on the machines this runs on, and
adding a dependency would defeat the point: the whole tool is meant to be
copied to another editor's laptop and double-clicked, with nothing to install.

An .xlsx is a zip of XML parts, so both halves are hand-rolled here.

The asymmetry worth knowing about
---------------------------------
We *write* every string as an inline string (``t="inlineStr"``), which avoids
maintaining a shared-string table and keeps the writer simple. But Excel and
Numbers do not preserve that: when a human opens one of these files, types in
the ``Attending?`` column and saves, the file comes back with a
``sharedStrings.xml`` and ``t="s"`` cells instead. So the reader must handle
both encodings -- and ``t="str"`` (a formula result) besides. Reading only the
format we write would work perfectly until the first time someone edited a
file, which is exactly when it matters.
"""
import os
import re
import xml.etree.ElementTree as ET
import zipfile

MAIN_NS = "http://schemas.openxmlformats.org/spreadsheetml/2006/main"
REL_NS = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
PKG_REL_NS = "http://schemas.openxmlformats.org/package/2006/relationships"

_MAIN = f"{{{MAIN_NS}}}"
_REL = f"{{{REL_NS}}}"
_PKG_REL = f"{{{PKG_REL_NS}}}"


# ------------------------------------------------------------------ helpers
def col_letter(idx):
    """0-based column index -> 'A', 'B', ... 'Z', 'AA', ..."""
    s = ""
    idx += 1
    while idx:
        idx, rem = divmod(idx - 1, 26)
        s = chr(65 + rem) + s
    return s


def col_index(ref):
    """'C7' -> 2. Returns None if the reference has no column letters."""
    m = re.match(r"([A-Z]+)", ref or "")
    if not m:
        return None
    n = 0
    for ch in m.group(1):
        n = n * 26 + (ord(ch) - 64)
    return n - 1


def _esc(s):
    return (str(s).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;"))


# Excel rejects a file outright if a cell contains a character that is illegal
# in XML 1.0. Sponsor strings out of the FDA feed are dirty enough that this
# has to be defended against rather than assumed away.
_ILLEGAL = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f]")


def _clean(v):
    if v is None:
        return ""
    return _ILLEGAL.sub("", str(v))


# ------------------------------------------------------------------ writing
_CONTENT_TYPES = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">
<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>
<Default Extension="xml" ContentType="application/xml"/>
<Override PartName="/xl/workbook.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"/>
<Override PartName="/xl/styles.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.styles+xml"/>
{sheet_overrides}
</Types>"""

_ROOT_RELS = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">
<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="xl/workbook.xml"/>
</Relationships>"""

# Two fonts (normal, bold) and two cell formats: index 0 plain, index 1 bold on
# a light grey fill -- the same header treatment the Drive version applied.
_STYLES = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<styleSheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">
<fonts count="2">
<font><sz val="11"/><name val="Calibri"/></font>
<font><b/><sz val="11"/><name val="Calibri"/></font>
</fonts>
<fills count="3">
<fill><patternFill patternType="none"/></fill>
<fill><patternFill patternType="gray125"/></fill>
<fill><patternFill patternType="solid"><fgColor rgb="FFE8EEF4"/><bgColor indexed="64"/></patternFill></fill>
</fills>
<borders count="1"><border><left/><right/><top/><bottom/><diagonal/></border></borders>
<cellStyleXfs count="1"><xf numFmtId="0" fontId="0" fillId="0" borderId="0"/></cellStyleXfs>
<cellXfs count="2">
<xf numFmtId="0" fontId="0" fillId="0" borderId="0" xfId="0"/>
<xf numFmtId="0" fontId="1" fillId="2" borderId="0" xfId="0" applyFont="1" applyFill="1"/>
</cellXfs>
</styleSheet>"""


def _sheet_xml(rows, freeze_header=True):
    out = ['<?xml version="1.0" encoding="UTF-8" standalone="yes"?>',
           f'<worksheet xmlns="{MAIN_NS}">']

    if freeze_header and rows:
        out.append('<sheetViews><sheetView workbookViewId="0">'
                   '<pane ySplit="1" topLeftCell="A2" activePane="bottomLeft" state="frozen"/>'
                   '</sheetView></sheetViews>')

    # Width from the longest cell in each column, clamped so one long abstract
    # does not produce a 400-character column.
    if rows:
        ncols = max(len(r) for r in rows)
        widths = []
        for c in range(ncols):
            longest = max((len(_clean(r[c])) for r in rows if c < len(r)), default=0)
            widths.append(max(9, min(52, longest + 2)))
        out.append("<cols>" + "".join(
            f'<col min="{i+1}" max="{i+1}" width="{w}" customWidth="1"/>'
            for i, w in enumerate(widths)) + "</cols>")

    out.append("<sheetData>")
    for r_i, row in enumerate(rows, 1):
        if not row:
            continue
        style = ' s="1"' if (freeze_header and r_i == 1) else ""
        cells = []
        for c_i, val in enumerate(row):
            v = _clean(val)
            if v == "":
                continue
            ref = f"{col_letter(c_i)}{r_i}"
            cells.append(f'<c r="{ref}"{style} t="inlineStr">'
                         f'<is><t xml:space="preserve">{_esc(v)}</t></is></c>')
        out.append(f'<row r="{r_i}">' + "".join(cells) + "</row>")
    out.append("</sheetData></worksheet>")
    return "".join(out)


def xlsx_write(path, tabs, freeze_header=True):
    """Write a workbook. ``tabs`` is an ordered {tab name: list of row lists}.

    The whole file is rewritten every time, which is what makes re-runs safe:
    a shorter run cannot leave stale rows behind, because there is no previous
    content to leave behind.
    """
    if not tabs:
        raise ValueError("a workbook needs at least one tab")
    os.makedirs(os.path.dirname(os.path.abspath(path)) or ".", exist_ok=True)

    names = list(tabs)
    overrides = "\n".join(
        f'<Override PartName="/xl/worksheets/sheet{i+1}.xml" '
        f'ContentType="application/vnd.openxmlformats-officedocument.'
        f'spreadsheetml.worksheet+xml"/>' for i in range(len(names)))

    wb = ['<?xml version="1.0" encoding="UTF-8" standalone="yes"?>',
          f'<workbook xmlns="{MAIN_NS}" xmlns:r="{REL_NS}"><sheets>']
    for i, n in enumerate(names):
        wb.append(f'<sheet name="{_esc(n)}" sheetId="{i+1}" r:id="rId{i+1}"/>')
    wb.append("</sheets></workbook>")

    wb_rels = ['<?xml version="1.0" encoding="UTF-8" standalone="yes"?>',
               f'<Relationships xmlns="{PKG_REL_NS}">']
    for i in range(len(names)):
        wb_rels.append(
            f'<Relationship Id="rId{i+1}" Type="http://schemas.openxmlformats.org/'
            f'officeDocument/2006/relationships/worksheet" '
            f'Target="worksheets/sheet{i+1}.xml"/>')
    wb_rels.append(
        f'<Relationship Id="rId{len(names)+1}" Type="http://schemas.openxmlformats.org/'
        f'officeDocument/2006/relationships/styles" Target="styles.xml"/>')
    wb_rels.append("</Relationships>")

    tmp = path + ".tmp"
    with zipfile.ZipFile(tmp, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("[Content_Types].xml",
                   _CONTENT_TYPES.format(sheet_overrides=overrides))
        z.writestr("_rels/.rels", _ROOT_RELS)
        z.writestr("xl/workbook.xml", "".join(wb))
        z.writestr("xl/_rels/workbook.xml.rels", "".join(wb_rels))
        z.writestr("xl/styles.xml", _STYLES)
        for i, n in enumerate(names):
            z.writestr(f"xl/worksheets/sheet{i+1}.xml",
                       _sheet_xml(tabs[n], freeze_header))
    os.replace(tmp, path)          # atomic: never leave a half-written workbook
    return path


# ------------------------------------------------------------------ reading
def _shared_strings(z):
    try:
        xml = z.read("xl/sharedStrings.xml")
    except KeyError:
        return []
    out = []
    for si in ET.fromstring(xml).findall(f"{_MAIN}si"):
        # A single <t>, or several <r><t> runs when part of the text was
        # styled differently -- which is what happens after a human bolds a
        # word in a comment.
        out.append("".join(t.text or "" for t in si.iter(f"{_MAIN}t")))
    return out


def _sheet_targets(z):
    """tab name -> zip path of its worksheet XML."""
    rels = {}
    for r in ET.fromstring(z.read("xl/_rels/workbook.xml.rels")):
        target = r.get("Target", "")
        if target.startswith("/"):
            target = target[1:]
        elif not target.startswith("xl/"):
            target = "xl/" + target
        rels[r.get("Id")] = target
    out = {}
    for sh in ET.fromstring(z.read("xl/workbook.xml")).iter(f"{_MAIN}sheet"):
        rid = sh.get(f"{_REL}id")
        if rid in rels:
            out[sh.get("name")] = rels[rid]
    return out


def _cell_value(c, shared):
    t = c.get("t")
    if t == "inlineStr":
        is_el = c.find(f"{_MAIN}is")
        return "".join(x.text or "" for x in is_el.iter(f"{_MAIN}t")) if is_el is not None else ""
    v = c.find(f"{_MAIN}v")
    if t == "s":
        if v is None or not (v.text or "").strip():
            return ""
        i = int(v.text)
        return shared[i] if 0 <= i < len(shared) else ""
    if t == "b":
        return "TRUE" if (v is not None and v.text == "1") else "FALSE"
    if t == "e":                                  # #REF!, #N/A ...
        return v.text or "" if v is not None else ""
    if v is None:
        return ""
    text = v.text or ""
    # Numbers come back as '84' or '84.0'; the callers all treat cells as
    # strings and _score_of() parses via float(), so trim a pointless '.0'
    # rather than turning scores into '84.0' in the output.
    if text.endswith(".0") and text[:-2].lstrip("-").isdigit():
        return text[:-2]
    return text


def xlsx_read(path, tab=None):
    """Read one tab as a list of row lists of strings.

    ``tab=None`` reads the first sheet. Missing file or missing tab returns [],
    so callers can treat "no workbook yet" as "no rows" without special-casing.
    Ragged rows are padded to the widest row so ``dict(zip(header, row))``
    never silently drops trailing columns -- which is how a blank ``Comments``
    cell would otherwise disappear.
    """
    if not path or not os.path.exists(path):
        return []
    with zipfile.ZipFile(path) as z:
        targets = _sheet_targets(z)
        if not targets:
            return []
        if tab is None:
            name = next(iter(targets))
        elif tab in targets:
            name = tab
        else:
            return []
        shared = _shared_strings(z)
        root = ET.fromstring(z.read(targets[name]))

        rows, max_row = {}, 0
        for row_el in root.iter(f"{_MAIN}row"):
            r_attr = row_el.get("r")
            r_i = int(r_attr) if r_attr else max_row + 1
            cells = {}
            next_c = 0
            for c in row_el.findall(f"{_MAIN}c"):
                c_i = col_index(c.get("r", ""))
                if c_i is None:
                    c_i = next_c
                next_c = c_i + 1
                val = _cell_value(c, shared)
                if val != "":
                    cells[c_i] = val
            rows[r_i] = cells
            max_row = max(max_row, r_i)

    if not rows:
        return []
    width = max((max(c) + 1 for c in rows.values() if c), default=0)
    out = []
    for r_i in range(1, max_row + 1):
        cells = rows.get(r_i, {})
        out.append([cells.get(c, "") for c in range(width)])
    # Drop trailing all-blank rows: Excel keeps styled-but-empty rows around
    # and they would otherwise read as candidates with no name.
    while out and not any(v.strip() for v in out[-1]):
        out.pop()
    return out


def tab_names(path):
    if not path or not os.path.exists(path):
        return []
    with zipfile.ZipFile(path) as z:
        return list(_sheet_targets(z))
