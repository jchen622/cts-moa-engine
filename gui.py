#!/usr/bin/env python3
"""A small local web app, so the engine can be used without a terminal.

Why a browser and not a real window: the Python on this machine has no
tkinter, and requiring anyone we hand this to install one would defeat the
point. http.server is in the standard library everywhere.

The server binds to 127.0.0.1 only, on an ephemeral port, and every request
must carry a token minted at startup -- so a page open in another tab cannot
poke at it. It runs one job at a time and streams that job's output to the
page.

Run:  python3 gui.py        (or double-click "Start MOA engine.command")
"""
import base64
import http.server
import json
import os
import queue
import secrets
import socketserver
import subprocess
import sys
import tempfile
import threading
import time
import urllib.parse
import webbrowser

import config

HERE = config.HERE
TOKEN = secrets.token_urlsafe(16)
WINDOWS = sys.platform.startswith("win")

# Windows pops a console window for every subprocess when the parent has none
# (i.e. when launched via pythonw). Suppress it, or the user gets a black
# rectangle flashing up each time they press a button.
_NO_WINDOW = {"creationflags": 0x08000000} if WINDOWS else {}


def reveal(folder):
    """Show a folder in the desktop file manager."""
    if WINDOWS:
        os.startfile(folder)                                # noqa: S606
    elif sys.platform == "darwin":
        subprocess.Popen(["open", folder])
    else:
        subprocess.Popen(["xdg-open", folder])

# Every action the page can trigger. Keeping this as a table rather than
# letting the page name a command means the browser can never ask the server
# to run something arbitrary.
ACTIONS = {
    "check":   {"argv": ["check"],           "writes": False, "outdir": True},
    "scan":    {"argv": ["scan"],            "writes": False, "outdir": False},
    "update":  {"argv": ["update", "--go"],  "writes": True,  "outdir": True},
    "hotlist": {"argv": ["hotlist", "--go"], "writes": True,  "outdir": True},
    "invites": {"argv": ["invites", "--go"], "writes": True,  "outdir": True},
    # Takes an uploaded workbook. The page still only names the action; the
    # path is minted here and never supplied by the browser.
    "roster":  {"argv": ["roster", "--go"],  "writes": True,  "outdir": False,
                "upload": True},
}

MAX_UPLOAD = 40 * 1024 * 1024      # an attendee list is tens of KB, not tens of MB


def _stash_upload(b64):
    """Decode an uploaded workbook to a temp file and return its path.

    Only the bytes come from the browser. Whatever filename it offers is
    discarded -- a page-supplied name is a path-traversal invitation, and the
    roster command needs nothing but the contents and a .xlsx extension.
    """
    raw = base64.b64decode(b64, validate=True)
    if not raw:
        raise ValueError("that file was empty")
    if len(raw) > MAX_UPLOAD:
        raise ValueError(f"file is larger than {MAX_UPLOAD // (1024 * 1024)} MB")
    if raw[:2] != b"PK":               # every .xlsx is a zip
        raise ValueError("that does not look like an .xlsx file")
    fd, path = tempfile.mkstemp(prefix="moa-upload-", suffix=".xlsx")
    with os.fdopen(fd, "wb") as fh:
        fh.write(raw)
    return path


def _validate_outdir(raw):
    """-> (absolute path, "") or (None, reason).

    Deliberately fussy. A typo like "/" or a path inside a system directory
    would scatter workbooks somewhere unhelpful or fail confusingly halfway
    through a run, so it is rejected before anything is written.
    """
    if not raw:
        return None, "Type a folder path first."
    path = os.path.abspath(os.path.expanduser(raw))
    home = os.path.abspath(os.path.expanduser("~"))
    if path == os.sep:
        return None, "That is the whole disk. Pick a folder inside your home."
    if not (path == home or path.startswith(home + os.sep)):
        return None, ("Pick a folder inside your home folder, so the results "
                      "are somewhere you can find and back up.")
    try:
        os.makedirs(path, exist_ok=True)
        probe = os.path.join(path, ".moa-writable")
        with open(probe, "w") as fh:
            fh.write("ok")
        os.remove(probe)
    except OSError as e:
        return None, f"Cannot write there ({e.strerror or e})."
    return path, ""


def _argv_for(action, upload_path=None):
    """Resolve the output folder here rather than letting the subprocess work
    it out again -- otherwise the folder the page shows and the folder the job
    writes to can disagree whenever one is overridden."""
    spec = ACTIONS[action]
    argv = list(spec["argv"])
    if spec["outdir"]:
        argv += ["--output-dir", config.output_dir()]
    if spec.get("upload"):
        argv += ["--file", upload_path]
    return argv


# How to invoke the engine as a subprocess. The single-file build has no
# moa_engine.py on disk, so it overrides this to re-invoke the bundle itself.
ENGINE_ARGV = [sys.executable, "-u", os.path.join(HERE, "moa_engine.py")]


class Job:
    """One running subprocess and the lines it has printed so far."""

    PROGRESS_PREFIX = "::progress"

    def __init__(self):
        self.lines = []
        self.proc = None
        self.exit = None
        self.label = ""
        self.progress = None          # (done, total, label) or None
        self.lock = threading.Lock()

    @property
    def running(self):
        return self.proc is not None and self.exit is None

    def start(self, label, argv):
        with self.lock:
            if self.running:
                return False
            self.lines = [f"$ moa-engine {' '.join(argv)}", ""]
            self.progress = None      # reset, or a finished run leaves a stale bar
            self.exit = None
            self.label = label
            self.proc = subprocess.Popen(
                list(ENGINE_ARGV) + argv,
                cwd=HERE, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                text=True, bufsize=1, **_NO_WINDOW)
        threading.Thread(target=self._pump, daemon=True).start()
        return True

    def _pump(self):
        p = self.proc
        for line in p.stdout:
            line = line.rstrip("\n")
            # Progress markers drive the bar and are kept OUT of the log: one
            # per candidate would bury everything worth reading.
            if line.startswith(self.PROGRESS_PREFIX):
                bits = line[len(self.PROGRESS_PREFIX):].strip().split(None, 2)
                try:
                    done, total = int(bits[0]), int(bits[1])
                except (IndexError, ValueError):
                    continue
                with self.lock:
                    self.progress = (done, total,
                                     bits[2] if len(bits) > 2 else "")
                continue
            with self.lock:
                self.lines.append(line)
        p.wait()
        with self.lock:
            self.progress = None
            self.exit = p.returncode
            self.lines.append("")
            self.lines.append("--- finished ---" if p.returncode == 0
                              else f"--- finished with errors (code {p.returncode}) ---")

    def snapshot(self, since):
        with self.lock:
            return {"lines": self.lines[since:], "total": len(self.lines),
                    "running": self.running, "exit": self.exit,
                    "label": self.label, "progress": self.progress}

    def stop(self):
        with self.lock:
            if self.running:
                self.proc.terminate()


JOB = Job()


# ------------------------------------------------------------------ lifetime
# The app is started detached by the .app bundle, so nothing in the UI closing
# would otherwise stop it -- you had to force-quit Python. The page therefore
# holds a heartbeat: while a tab is open it pings, and when the pings stop the
# server exits on its own.
#
# A beacon on tab-close handles the tidy case, but beacons get dropped (browser
# crash, laptop sleep, force-quit of the browser), so the heartbeat is the one
# that actually guarantees no stray process.
HEARTBEAT_GRACE = 120     # seconds to wait for the browser to open at all
# 25 seconds was far too short and this was the bug behind "it dies while I am
# still using it". Browsers THROTTLE setInterval in a background tab, to about
# once a minute in Chrome and Safari, so switching tabs for half a minute looked
# identical to closing the page and the server shut itself down. Then a refresh
# hit a dead port. Ten minutes sits well clear of any throttling, and the page
# also beats immediately whenever it becomes visible again.
HEARTBEAT_TIMEOUT = 600
_last_beat = [None]


def touch():
    _last_beat[0] = time.monotonic()


def watchdog(srv):
    """Shut the server down once no page has pinged for a while.

    A running job keeps it alive: closing the tab during a five-minute scan
    should not throw the scan away.
    """
    started = time.monotonic()
    while True:
        time.sleep(2)
        if JOB.running:
            continue
        last = _last_beat[0]
        quiet = (time.monotonic() - last) if last else (time.monotonic() - started)
        limit = HEARTBEAT_TIMEOUT if last else HEARTBEAT_GRACE
        if quiet > limit:
            print("\nBrowser window closed — shutting down." if last
                  else "\nNo browser connected — shutting down.")
            srv.shutdown()
            return


PAGE = """<!DOCTYPE html>
<html><head><meta charset="utf-8">
<title>CTS MOA engine</title>
<style>
 :root {{ --ink:#1a1d21; --dim:#5f6b76; --line:#dbe1e8; --blue:#1a5fb4;
          --warm:#8a4b00; --bg:#f6f8fa; }}
 * {{ box-sizing:border-box }}
 body {{ font:16px/1.5 -apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif;
        margin:0; background:var(--bg); color:var(--ink); }}
 .wrap {{ max-width:780px; margin:0 auto; padding:32px 24px 64px }}
 h1 {{ font-size:26px; margin:0 0 4px }}
 .sub {{ color:var(--dim); margin:0 0 28px }}
 .step {{ display:flex; gap:16px; align-items:flex-start; background:#fff;
         border:1px solid var(--line); border-radius:12px; padding:16px 18px;
         margin-bottom:12px }}
 .num {{ flex:0 0 34px; height:34px; border-radius:50%; background:var(--bg);
        border:1px solid var(--line); display:flex; align-items:center;
        justify-content:center; font-weight:600; color:var(--dim) }}
 .body {{ flex:1; min-width:0 }}
 .title {{ font-weight:600; font-size:17px }}
 .desc {{ color:var(--dim); font-size:14px; margin-top:2px }}
 button {{ font:inherit; font-weight:600; padding:9px 18px; border-radius:8px;
          border:1px solid var(--blue); background:var(--blue); color:#fff;
          cursor:pointer; white-space:nowrap }}
 button.safe {{ background:#fff; color:var(--blue) }}
 button:disabled {{ opacity:.4; cursor:not-allowed }}
 .tag {{ font-size:12px; color:var(--dim); margin-top:6px }}
 .tag.w {{ color:var(--warm) }}
 #log {{ display:none; margin-top:24px; background:#11151a; color:#dfe6ee;
        border-radius:12px; padding:16px 18px; font:13px/1.55 ui-monospace,
        SFMono-Regular,Menlo,monospace; white-space:pre-wrap; word-break:break-word;
        max-height:46vh; overflow:auto }}
 #status {{ display:none; margin-top:18px; align-items:center; gap:10px;
           font-weight:600 }}
 /* Determinate where the engine knows the total, a moving stripe where it
    does not. A phase with no countable work still has to look alive. */
 .pwrap {{ margin-top:10px; display:none }}
 .ptrack {{ height:7px; background:var(--line); border-radius:4px; overflow:hidden }}
 .pfill {{ height:100%; width:0; background:var(--accent); border-radius:4px;
           transition:width .35s ease }}
 .pfill.indet {{ width:35%; animation:slide 1.1s ease-in-out infinite }}
 @keyframes slide {{ 0%{{margin-left:-35%}} 100%{{margin-left:100%}} }}
 .ptext {{ margin-top:5px; font-size:12px; color:var(--muted) }}
 .spin {{ width:15px; height:15px; border:2px solid var(--line);
         border-top-color:var(--blue); border-radius:50%;
         animation:s .8s linear infinite }}
 @keyframes s {{ to {{ transform:rotate(360deg) }} }}
 .foot {{ margin-top:28px; color:var(--dim); font-size:13px;
         border-top:1px solid var(--line); padding-top:16px }}
 .foot b {{ color:var(--ink) }}
</style></head><body><div class="wrap">

<h1>CTS MOA engine</h1>
<p class="sub">Finds newly approved novel drugs that need a Mechanism of Action
mini&#8209;review. Work down the list.</p>

<div class="step"><div class="num">1</div><div class="body">
 <div class="title">Check that everything works</div>
 <div class="desc">Confirms the tool can reach the FDA and PubMed, and can find your files.</div>
 <div class="tag">Changes nothing &middot; about 30 seconds</div>
</div><button class="safe" data-a="check">Check</button></div>

<div class="step"><div class="num">2</div><div class="body">
 <div class="title">Preview new drug candidates</div>
 <div class="desc">Shows what it would find, ranked, without saving anything.</div>
 <div class="tag">Changes nothing &middot; a couple of minutes, it asks PubMed about each drug</div>
</div><button class="safe" data-a="scan">Preview</button></div>

<div class="step"><div class="num">3</div><div class="body">
 <div class="title">Update my candidate list</div>
 <div class="desc">Adds any newly approved drugs to your list. Safe to run as often as you like &mdash;
 it never adds the same drug twice, and never touches what you have typed in.</div>
 <div class="tag w">Saves to your results folder &middot; a couple of minutes</div>
</div><button data-a="update">Update</button></div>

<div class="step"><div class="num">4</div><div class="body">
 <div class="title">Build the outreach list</div>
 <div class="desc">Drugs with a <b>novel mechanism the mini-review series has not
 covered</b>, with the mechanism of action and approved indications from the FDA
 label. The top 20 are highlighted; 30 more follow. The <b>clin pharm contact
 column is left blank on purpose</b> &mdash; that is the column for the AE team
 to fill in. Anything already filled in is carried over.</div>
 <div class="desc" id="whopulled" style="margin-top:8px"></div>
 <div class="desc" style="margin-top:10px"><b>Drugs you want included anyway</b>
  &mdash; one per line, brand or generic. These are added to the top 20 rather
  than taking its places, and they can be any age, so an older drug of interest
  is fine. The engine fills in the mechanism, indications and application number.</div>
 <div style="margin-top:6px;display:flex;gap:8px;align-items:flex-start">
  <textarea id="mydrugs" rows="3" spellcheck="false" placeholder="vericiguat
Ozempic"
    style="flex:1;min-width:0;padding:7px 9px;font:13px/1.45 ui-monospace,Menlo,
           monospace;border:1px solid #b8c2ce;border-radius:5px;resize:vertical"></textarea>
  <button class="safe" id="savedrugs" style="white-space:nowrap">Save list</button>
 </div>
 <div class="desc" id="drugsmsg" style="margin-top:6px">&nbsp;</div>
 <div class="tag w">Saves to your results folder &middot; one file per day</div>
</div><button data-a="hotlist">Build</button></div>

<div class="step"><div class="num">5</div><div class="body">
 <div class="title">Write the invitation letters</div>
 <div class="desc">Drafts a letter <b>only for rows where someone has filled in a
 contact</b>. Rows still blank are skipped and counted, so nothing is addressed to
 a guess. Do step 4 first, fill in the contacts you know, then come here.</div>
 <div class="tag w">Saves to your results folder &middot; <b>no email is ever sent</b></div>
</div><button data-a="invites">Write letters</button></div>

<div class="step"><div class="num">+</div><div class="body">
 <div class="title">Add a file that helps you reach people
 <span style="font-weight:400;color:var(--dim)">&mdash; optional</span></div>
 <div class="desc">The goal is getting to the right clinical pharmacologist. Step&nbsp;4
 already finds them from the published literature; these just add more ways to reach
 them. Drop in any of the following and the tool works out which it is:</div>
 <div class="desc" style="margin-top:8px">
 &bull; an <b>ASCPT member directory</b> export &mdash; flags which of them are members<br>
 &bull; the <b>membership check list</b> the tool writes, once you have filled it in<br>
 &bull; an <b>attendee list</b> for the <span class="yr">upcoming</span> meeting &mdash;
 says who is registered, by name<br>
 &bull; the <b>programme export</b> &mdash; adds poster numbers and times</div>
 <div class="desc" id="haveatt" style="margin-top:8px"></div>
 <div class="tag">A .xlsx with a name column and an organisation column</div>
</div><button class="safe" data-a="roster">Choose file&hellip;</button></div>
<input type="file" id="rosterfile" accept=".xlsx" style="display:none">

<div class="step"><div class="num">&#128193;</div><div class="body">
 <div class="title">Where results are saved</div>
 <div class="desc">Change this if you would rather keep them somewhere else,
  a Drive folder for instance. The box starts on the current folder.</div>
 <div style="margin-top:8px;display:flex;gap:8px;align-items:center">
  <input type="text" id="outdir" spellcheck="false"
         style="flex:1;min-width:0;padding:7px 9px;font:13px/1.3 ui-monospace,
                Menlo,monospace;border:1px solid #b8c2ce;border-radius:5px">
  <button class="safe" id="saveoutdir" style="white-space:nowrap">Use this folder</button>
 </div>
 <div class="desc" id="outdirmsg" style="margin-top:6px">&nbsp;</div>
</div><button class="safe" data-a="open">Open</button></div>

<div id="status"><div class="spin"></div><span id="statustext"></span></div>
<div class="ptext" id="build" style="margin-top:14px;text-align:right">&nbsp;</div>
<div class="pwrap" id="pwrap">
 <div class="ptrack"><div class="pfill" id="pfill"></div></div>
 <div class="ptext" id="ptext"></div>
</div>
<pre id="log"></pre>

<div class="foot">
 <div style="display:flex;gap:14px;align-items:center;justify-content:space-between">
  <div style="flex:1">Everything stays on this computer. <b>No email is ever sent</b> &mdash; the
  letters are written to a file for you to read, edit and send yourself.<br>
  <span style="font-size:12px">Closing this tab stops the app automatically.</span></div>
  <button class="safe" id="quit" style="border-color:var(--dim);color:var(--dim)">Quit</button>
 </div>
</div>

</div><script>
const T = {token!r};
const $ = s => document.querySelector(s);
const btns = [...document.querySelectorAll('button[data-a]')];
let seen = 0, timer = null;

const CONFIRM = {{
  update:  "This will add any newly approved drugs to your candidate list.\\n\\nGo ahead?",
  dossier: "This will build the dossier for the next ASCPT meeting.\\n\\nWhere it has no list for that meeting yet, it uses who attended the previous one - shown in a separate 'Last year at ASCPT' column, not as somewhere to walk to.\\n\\nNotes you have already typed in will be kept.\\n\\nGo ahead?",
  invites: "This will draft the invitation letters from your dossier.\\n\\nNothing is emailed - they are saved to a file for you to review.\\n\\nGo ahead?"
}};

async function post(path, body) {{
  const r = await fetch(path + '?t=' + encodeURIComponent(T), {{
    method:'POST', headers:{{'Content-Type':'application/json'}},
    body: JSON.stringify(body || {{}})}});
  return r.json();
}}

function busy(on, label) {{
  btns.forEach(b => b.disabled = on);
  $('#status').style.display = on ? 'flex' : 'none';
  $('#statustext').textContent = label || '';
  renderProgress(null);
}}

async function poll() {{
  const r = await fetch('/log?t=' + encodeURIComponent(T) + '&since=' + seen);
  const d = await r.json();
  if (d.lines.length) {{
    seen = d.total;
    const el = $('#log');
    el.style.display = 'block';
    el.textContent += (el.textContent ? '\\n' : '') + d.lines.join('\\n');
    el.scrollTop = el.scrollHeight;
  }}
  renderProgress(d.running ? d.progress : null);
  if (!d.running) {{ clearInterval(timer); timer = null; busy(false); }}
}}

async function launch(a, label, extra) {{
  $('#log').textContent = ''; seen = 0;
  busy(true, label + '\\u2026');
  const r = await post('/run', Object.assign({{action: a}}, extra || {{}}));
  if (!r.ok) {{ busy(false); alert(r.error || 'Could not start.'); return; }}
  timer = setInterval(poll, 700); poll();
}}

btns.forEach(b => b.addEventListener('click', async () => {{
  const a = b.dataset.a;
  const label = b.previousElementSibling.querySelector('.title').textContent;
  if (a === 'open') {{ await post('/open'); return; }}
  // The file picker has to be opened by the click itself; the upload is sent
  // from its change handler below.
  if (a === 'roster') {{ $('#rosterfile').click(); return; }}
  if (CONFIRM[a] && !confirm(CONFIRM[a])) return;
  launch(a, label);
}}));

$('#rosterfile').addEventListener('change', async e => {{
  const f = e.target.files[0];
  if (!f) return;
  e.target.value = '';                      // so re-picking the same file fires again
  const b64 = await new Promise((res, rej) => {{
    const fr = new FileReader();
    fr.onload = () => res(fr.result.split(',')[1]);
    fr.onerror = rej;
    fr.readAsDataURL(f);
  }});
  launch('roster', 'Reading ' + f.name, {{file_b64: b64}});
}});

// Keep the server alive while this tab is open. When the pings stop it exits
// on its own, so nothing has to be force-quit.
//
// A background tab has its timers throttled to roughly once a minute, so this
// cannot be the only signal and the server's timeout has to tolerate the gap.
// Beating on visibilitychange covers the common case of coming back to the tab.
let closing = false;
let missed = 0;
function beat() {{
  if (closing) return;
  fetch('/alive?t=' + encodeURIComponent(T))
    .then(() => {{ missed = 0; banner(false); }})
    .catch(() => {{ if (++missed >= 2) banner(true); }});
}}
setInterval(beat, 15000);
addEventListener('visibilitychange', () => {{ if (!document.hidden) beat(); }});
addEventListener('focus', beat);

// If the engine really has exited, say so plainly. A browser cannot restart a
// local server by refreshing, so telling the reader to refresh would be a lie.
function banner(dead) {{
  let el = document.getElementById('deadbanner');
  if (!dead) {{ if (el) el.remove(); return; }}
  if (el) return;
  el = document.createElement('div');
  el.id = 'deadbanner';
  el.style.cssText = 'position:fixed;left:0;right:0;top:0;z-index:9999;'
    + 'background:#9B3A3A;color:#fff;padding:10px 16px;font:14px/1.4 Arial;';
  el.textContent = 'The engine has stopped. Double-click "CTS MOA Engine" again '
    + 'to restart it \u2014 refreshing this page will not bring it back.';
  document.body.appendChild(el);
}}

$('#savedrugs').addEventListener('click', () => {{
  const msg = $('#drugsmsg');
  msg.textContent = 'Saving\u2026';
  fetch('/setdrugs?t=' + encodeURIComponent(T)
        + '&text=' + encodeURIComponent($('#mydrugs').value))
    .then(r => r.json()).then(d => {{
      msg.style.color = d.ok ? '#1E6B52' : '#9B3A3A';
      msg.textContent = d.ok
        ? (d.count ? d.count + ' drug(s) saved. They go on the next list you build.'
                   : 'List cleared.')
        : d.error;
    }})
    .catch(() => {{ msg.style.color = '#9B3A3A';
                    msg.textContent = 'The engine is not responding.'; }});
}});

$('#saveoutdir').addEventListener('click', () => {{
  const v = $('#outdir').value.trim();
  const msg = $('#outdirmsg');
  msg.textContent = 'Checking\u2026';
  fetch('/setoutdir?t=' + encodeURIComponent(T) + '&dir=' + encodeURIComponent(v))
    .then(r => r.json()).then(d => {{
      if (d.ok) {{
        $('#outdir').value = d.output_dir;
  $('#mydrugs').value = d.manual_drugs || '';
  $('#build').textContent = 'build ' + (d.build || '?');
        msg.style.color = '#1E6B52';
        msg.textContent = 'Saved. Results will go here from now on.';
      }} else {{
        msg.style.color = '#9B3A3A';
        msg.textContent = d.error;
        $('#outdir').value = d.output_dir;
  $('#mydrugs').value = d.manual_drugs || '';
  $('#build').textContent = 'build ' + (d.build || '?');
      }}
    }})
    .catch(() => {{ msg.style.color = '#9B3A3A';
                    msg.textContent = 'The engine is not responding.'; }});
}});

function renderProgress(p) {{
  const wrap = $('#pwrap'), fill = $('#pfill'), text = $('#ptext');
  if (!p) {{ wrap.style.display = 'none'; return; }}
  const done = p[0], total = p[1], label = p[2] || '';
  wrap.style.display = 'block';
  if (total > 0) {{
    fill.classList.remove('indet');
    fill.style.width = Math.min(100, Math.round(done * 100 / total)) + '%';
    text.textContent = done + ' of ' + total + (label ? '  \u00b7  ' + label : '');
  }} else {{
    // No total to count against, so show motion and the phase name only.
    fill.classList.add('indet');
    fill.style.width = '';
    text.textContent = label || 'Working\u2026';
  }}
}}

// Tidy case: tell it immediately rather than waiting out the heartbeat.
// sendBeacon survives the page going away, where fetch() would be cancelled.
addEventListener('pagehide', () => {{
  navigator.sendBeacon('/quit?t=' + encodeURIComponent(T), new Blob([], {{type:'text/plain'}}));
}});

$('#quit').addEventListener('click', async e => {{
  e.preventDefault();
  if (!confirm('Close the MOA engine?')) return;
  closing = true;
  await post('/quit');
  document.body.innerHTML =
    '<div class="wrap"><h1>Closed</h1><p class="sub">The app has stopped. ' +
    'You can close this tab.</p></div>';
}});

// Say which meeting each button is about, using real years. "This year" and
// "last year" are exactly the ambiguity this tool used to get wrong.
fetch('/where?t=' + encodeURIComponent(T)).then(r => r.json()).then(d => {{
  $('#outdir').value = d.output_dir;
  $('#mydrugs').value = d.manual_drugs || '';
  $('#build').textContent = 'build ' + (d.build || '?');
  document.querySelectorAll('.yr').forEach(e => e.textContent = 'ASCPT ' + d.year);

  const src = [];
  if (d.have_program)   src.push('the <b>ASCPT ' + d.year + '</b> programme &mdash; posters, times and rooms');
  if (d.have_attendees) src.push('the <b>ASCPT ' + d.year + '</b> attendee list &mdash; who is registered');
  if (d.history.length) src.push('who attended <b>ASCPT ' +
      d.history.join('</b> and <b>ASCPT ') + '</b>, as a guide to who is likely back');

  $('#whopulled').innerHTML = src.length
    ? 'It draws on ' + src.join('; ') + '.'
    : 'No ASCPT files yet, so it will rank candidates but cannot suggest anyone to meet.';

  $('#haveatt').innerHTML = d.have_attendees
    ? '\\u2713 You have already added the ASCPT ' + d.year +
      ' list. Adding another replaces it.'
    : 'You do not have one yet. Until you do, step 4 falls back to ' +
      (d.history.length
        ? 'who attended <b>ASCPT ' + d.history[0] + '</b>.'
        : 'nothing &mdash; there is no earlier meeting on file either.');
}});
</script></body></html>"""


class Handler(http.server.BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    # ------------------------------------------------------------- plumbing
    def log_message(self, *a):
        pass                                    # keep the terminal clean

    def _authed(self):
        q = urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query)
        return secrets.compare_digest(q.get("t", [""])[0], TOKEN)

    def _send(self, code, body, ctype="application/json; charset=utf-8"):
        data = body if isinstance(body, bytes) else body.encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(data)

    def _json(self, obj, code=200):
        self._send(code, json.dumps(obj))

    def _read_json(self):
        n = int(self.headers.get("Content-Length") or 0)
        if not n:
            return {}
        try:
            return json.loads(self.rfile.read(n) or b"{}")
        except ValueError:
            return {}

    # ------------------------------------------------------------- routes
    def do_GET(self):
        # Parse the query ONCE here. Both /setdrugs and /setoutdir referred to a
        # `qs` that was never defined, so each raised NameError, the connection
        # closed with no response, and the page reported "the engine is not
        # responding" while the engine was in fact answering everything else.
        parsed = urllib.parse.urlparse(self.path)
        path, qs = parsed.path, urllib.parse.parse_qs(parsed.query)
        if path == "/":
            if not self._authed():
                self._send(403, "Open the address the launcher printed.",
                           "text/plain; charset=utf-8")
                return
            touch()
            self._send(200, PAGE.format(token=TOKEN), "text/html; charset=utf-8")
            return
        if not self._authed():
            self._json({"error": "forbidden"}, 403)
            return
        if path == "/alive":
            touch()
            self._json({"ok": True})
            return
        if path == "/log":
            try:
                since = int(qs.get("since", ["0"])[0])
            except ValueError:
                since = 0
            touch()
            self._json(JOB.snapshot(since))
            return
        touch()
        if path == "/setdrugs":
            # Free text, one drug per line, saved to input/my drugs.txt. The
            # value is data for a lookup, not a command; the engine resolves
            # each name against Drugs@FDA and silently reports the misses.
            text = qs.get("text", [""])[0]
            lines = [l.split("#")[0].strip() for l in text.replace("\r", "").split("\n")]
            lines = [l for l in lines if l]
            try:
                os.makedirs(config.input_dir(), exist_ok=True)
                with open(config.manual_drugs_file(), "w", encoding="utf-8") as fh:
                    fh.write("# Drugs to include regardless of the filter.\n"
                             "# One per line. Brand or generic. Older drugs are fine.\n")
                    for l in lines:
                        fh.write(l + "\n")
                self._json({"ok": True, "count": len(lines)})
            except OSError as e:
                self._json({"ok": False, "error": str(e)})
            return
        if path == "/setoutdir":
            # The page may choose WHERE output goes. That is not the same as
            # letting it name a command: the value is a destination on the
            # user's own machine, validated here, and every action still gets
            # its argv built by _argv_for().
            want = (qs.get("dir", [""])[0] or "").strip()
            ok, msg = _validate_outdir(want)
            if ok:
                st = config.load_settings(required=False)
                st["output_dir"] = ok
                config.save_settings(st)
                config.set_output_dir(None)        # drop any per-run override
                self._json({"ok": True, "output_dir": config.output_dir()})
            else:
                self._json({"ok": False, "error": msg,
                            "output_dir": config.output_dir()})
            return
        if path == "/where":
            # The page states the actual years rather than saying "this year"
            # and "last year", so it is never ambiguous which meeting a button
            # is talking about.
            year = config.meeting_year()
            files = config.meeting_files()
            history = sorted((y for y in files if y < year), reverse=True)
            self._json({
                "output_dir": config.output_dir(),
                "build": config.BUILD_STAMP or "running from source",
                "manual_drugs": "\n".join(config.manual_drugs()),
                "year": year,
                "history": history,
                "have_program": bool((files.get(year) or {}).get("program")),
                "have_attendees": bool((files.get(year) or {}).get("attendees")),
            })
            return
        self._json({"error": "not found"}, 404)

    def do_POST(self):
        path = urllib.parse.urlparse(self.path).path
        if not self._authed():
            self._json({"error": "forbidden"}, 403)
            return

        if path == "/run":
            body = self._read_json()
            action = (body.get("action") or "").strip()
            if action not in ACTIONS:
                self._json({"ok": False, "error": "unknown action"}, 400)
                return
            if JOB.running:
                self._json({"ok": False, "error": "something is already running"}, 409)
                return
            upload = None
            if ACTIONS[action].get("upload"):
                try:
                    upload = _stash_upload(body.get("file_b64") or "")
                except Exception as e:
                    self._json({"ok": False, "error": str(e)}, 400)
                    return
            self._json({"ok": JOB.start(action, _argv_for(action, upload))})
            return

        if path == "/open":
            out = config.output_dir()
            os.makedirs(out, exist_ok=True)
            try:
                reveal(out)
            except Exception as e:
                self._json({"ok": False, "error": str(e)})
                return
            self._json({"ok": True})
            return

        if path == "/quit":
            self._json({"ok": True})
            threading.Thread(target=self.server.shutdown, daemon=True).start()
            return

        self._json({"error": "not found"}, 404)


class Server(socketserver.ThreadingMixIn, http.server.HTTPServer):
    daemon_threads = True
    allow_reuse_address = True


def main():
    srv = Server(("127.0.0.1", 0), Handler)
    port = srv.server_address[1]
    url = f"http://127.0.0.1:{port}/?t={TOKEN}"

    print("CTS MOA engine")
    print("-" * 60)
    print("A window should open in your web browser.")
    print("If it does not, copy this address into the browser yourself:\n")
    print(f"  {url}\n")
    print("Close the browser tab when you are done — this stops by itself a few")
    print("seconds later. No need to force-quit anything.")
    print("-" * 60)

    threading.Thread(target=watchdog, args=(srv,), daemon=True).start()
    threading.Timer(0.4, lambda: webbrowser.open(url)).start()
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        JOB.stop()
        srv.server_close()
    print("\nClosed.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
