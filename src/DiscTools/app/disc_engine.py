"""Disc Tools -- engine layer.

Owns everything that is not widgets:

* first-run setup: the distro-package engines (xorriso, growisofs,
  cdrdao, GNU ddrescue) are detected, and if one is missing the GUI
  offers a one-click pkexec install -- never a dead-end (the abgx360
  GUI libcurl lesson, 2026-11-21). redumper ships as a release
  binary: it is downloaded from superg's GitHub releases and
  verified against a pinned SHA-256 before it is used;
* drive discovery (real reported capacity -- this app never imposes
  an artificial image-size cap);
* .dvd sidecar parsing (carries the Xbox 360 layer break);
* job command construction for the four engines, job running with
  streamed output, and per-job result parsing.

The app writes no burner code of its own: burning, building and
dumping are done by xorriso (GNU libburnia, Thomas Schmitt),
growisofs (dvd+rw-tools), cdrdao, GNU ddrescue and redumper
(superg, GPLv3). ImgBurn is the design reference only.

Layer-break honesty: the 360 presets (XGD2 1913760, XGD3 2084960)
burn through growisofs' -use-the-force-luke=break:<sectors> flag --
the classic Linux XGD3 path. No fleet machine has a burner drive,
so the presets ship BENCH-PROOF-PENDING: the gate closes only when
a disc burned on Duane's bench reads its layer break back as
exactly the requested value.
"""
import hashlib
import os
import re
import time
import shutil
import signal
import subprocess
import sys
import tempfile
import urllib.request
import xml.etree.ElementTree as ET
import zipfile
import zlib
from pathlib import Path

APP_DIR = Path(__file__).resolve().parent.parent
# AppRun points these at a per-user folder; unpackaged (dev) runs
# fall back to folders beside the app.
DATA_DIR = Path(os.environ.get(
    "DISC_TOOLS_DATA_DIR", str(APP_DIR)))
ENGINE_DIR = Path(os.environ.get(
    "DISC_TOOLS_ENGINE_DIR", str(DATA_DIR / "engine")))
SETTINGS_PATH = DATA_DIR / "settings.json"
WINDOW_PATH = DATA_DIR / "window.json"

XGD2_BREAK = 1913760
XGD3_BREAK = 2084960

REDUMPER_BUILD = "b770"
REDUMPER_URL = ("https://github.com/superg/redumper/releases/"
                "download/b770/redumper-b770-linux-x64.zip")
REDUMPER_SHA256 = ("53bc7815d318b18c468fd47d04bd1d5644133a16c5a"
                   "55cda040d12c45912ca9b")
REDUMPER_ZIP_INNER = "redumper-b770-linux-x64/bin/redumper"


def _noop(_msg):
    pass


class EngineError(Exception):
    pass


# ------------------------------------------------------------------
# Distro-package engines -- detection + the pkexec install offer.
# (Idiom shared with the abgx360 GUI: pre-flight, offer, re-check
# is the judge -- never a bare "install X yourself" dead-end.)
# ------------------------------------------------------------------

# (key, display name, binary on PATH, apt package, fedora package)
DEP_TABLE = [
    ("xorriso", "xorriso (Build, plain Write, Discovery)",
     "xorriso", "xorriso", "xorriso"),
    ("growisofs", "growisofs (Write engine, 360 layer-break burns)",
     "growisofs", "dvd+rw-tools", "dvd+rw-tools"),
    ("cdrdao", "cdrdao (BIN/CUE write and read)",
     "cdrdao", "cdrdao", "cdrdao"),
    ("ddrescue", "GNU ddrescue (damaged-disc reads)",
     "ddrescue", "gddrescue", "ddrescue"),
]


def _os_release():
    data = {}
    try:
        for line in Path("/etc/os-release").read_text().splitlines():
            if "=" in line:
                k, v = line.split("=", 1)
                data[k.strip()] = v.strip().strip('"')
    except Exception:  # noqa: BLE001
        pass
    return data


def distro_family(os_release=None):
    """'debian' | 'fedora' | 'other', from /etc/os-release."""
    if os_release is None:
        os_release = _os_release()
    ids = (os_release.get("ID", "") + " "
           + os_release.get("ID_LIKE", "")).lower()
    if "debian" in ids or "ubuntu" in ids:
        return "debian"
    if "fedora" in ids or "rhel" in ids or "centos" in ids:
        return "fedora"
    return "other"


def check_deps():
    """[(key, name, ok, apt package, fedora package)] for the
    distro-package engines."""
    return [(key, name, shutil.which(binary) is not None, apt, fed)
            for key, name, binary, apt, fed in DEP_TABLE]


def missing_deps():
    return [c for c in check_deps() if not c[2]]


class DepsMissingError(EngineError):
    """Distro engines are missing. .info carries everything the
    GUI needs to offer a one-click install."""

    def __init__(self, info):
        self.info = info
        super().__init__(info["manual_text"])


def dep_info(missing=None):
    if missing is None:
        missing = missing_deps()
    family = distro_family()
    names = [c[1] for c in missing]
    apt = sorted({c[3] for c in missing})
    fedora = sorted({c[4] for c in missing})
    if family == "debian":
        manual_command = "sudo apt-get install -y " + " ".join(apt)
    elif family == "fedora":
        manual_command = "sudo dnf install -y " + " ".join(fedora)
    else:
        manual_command = ("(your distro's equivalents of: "
                          + ", ".join(fedora) + ")")
    manual_text = (
        "Disc Tools needs " + ", ".join(names)
        + ", which are not installed. Install them with: "
        + manual_command
        + " -- then use Help > Re-run first-run setup.")
    return {"missing": names, "packages": apt,
            "alt_packages": fedora, "family": family,
            "manual_command": manual_command,
            "manual_text": manual_text}


def _reset_engine_signals():
    """preexec_fn for engine children: undo an inherited
    SIGINT/SIGQUIT ignore before the engine starts.

    A non-interactive shell runs its background jobs with
    SIGINT and SIGQUIT set to SIG_IGN, and that disposition is
    inherited across exec by the whole process tree. redumper
    b770 refuses to start in that state -- "error: signal
    handler already set (signal: 2)", exit 255 -- because it
    installs its own SIGINT handler for graceful stops. Bench
    finding, 2026-11-21: the app launched as a shell background
    job made every preservation dump die before sector one,
    while a normal desktop launch (Duane's rig included) has
    default dispositions and never sees it. Resetting both
    signals to SIG_DFL in the child makes the engine layer
    immune to how the app itself was launched."""
    signal.signal(signal.SIGINT, signal.SIG_DFL)
    signal.signal(signal.SIGQUIT, signal.SIG_DFL)


def _run_stream(cmd, cwd, log, timeout=None):
    log("+ " + " ".join(str(c) for c in cmd))
    proc = subprocess.Popen(
        [str(c) for c in cmd], cwd=str(cwd),
        stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
        text=True, errors="replace", bufsize=1,
        preexec_fn=_reset_engine_signals)
    try:
        for line in proc.stdout:
            log(line.rstrip("\n"))
        proc.wait(timeout=timeout)
    except Exception:
        proc.kill()
        raise
    return proc.returncode


def install_missing_deps(log=_noop):
    """Install the missing distro engines via pkexec apt (Debian
    family only; one GUI password prompt -- the family pattern).
    Returns True when nothing is missing afterwards; the re-check
    is the judge, not the installer's exit code."""
    missing = missing_deps()
    if not missing:
        return True
    info = dep_info(missing)
    if info["family"] != "debian":
        log("Automatic install is only wired for Debian/Ubuntu; "
            "on this system install manually: "
            + info["manual_command"])
        return False
    if not shutil.which("pkexec"):
        log("pkexec is not available, so I cannot install them "
            "for you. Install manually: " + info["manual_command"])
        return False
    pkgs = info["packages"]
    log("Asking for your password once (pkexec) to install: "
        + " ".join(pkgs))
    _run_stream(["pkexec", "sh", "-c",
                 "apt-get update -qq && apt-get install -y "
                 + " ".join(pkgs)], Path("/"), log)
    still = missing_deps()
    if still:
        log("Still missing after the install attempt: "
            + ", ".join(c[1] for c in still))
        return False
    log("Engine packages installed.")
    return True


# ------------------------------------------------------------------
# redumper -- release binary, downloaded + hash-verified
# ------------------------------------------------------------------

def redumper_path():
    return ENGINE_DIR / "redumper" / "redumper"


def redumper_ready():
    b = redumper_path()
    if not b.is_file():
        return False
    try:
        r = subprocess.run([str(b), "--version"], capture_output=True,
                           timeout=30)
        out = (r.stdout + r.stderr).decode("utf-8", "replace")
        return r.returncode == 0 and "redumper" in out
    except Exception:  # noqa: BLE001
        return False


def _fetch_redumper(log):
    dest_dir = ENGINE_DIR / "redumper"
    dest_dir.mkdir(parents=True, exist_ok=True)
    zip_path = ENGINE_DIR / "redumper.zip"
    log("Downloading " + REDUMPER_URL)
    req = urllib.request.Request(
        REDUMPER_URL, headers={"User-Agent": "disc-tools-setup"})
    digest = hashlib.sha256()
    with urllib.request.urlopen(req, timeout=300) as r, \
            open(zip_path, "wb") as f:
        while True:
            chunk = r.read(1 << 20)
            if not chunk:
                break
            digest.update(chunk)
            f.write(chunk)
    got = digest.hexdigest()
    if got != REDUMPER_SHA256:
        zip_path.unlink(missing_ok=True)
        raise EngineError(
            "redumper download failed its SHA-256 check (expected "
            + REDUMPER_SHA256 + ", got " + got + "). The file was "
            "discarded; nothing was installed from it.")
    log("redumper download verified (SHA-256 matches the pin).")
    extract = ENGINE_DIR / "redumper-extract"
    if extract.exists():
        shutil.rmtree(extract)
    extract.mkdir(parents=True)
    with zipfile.ZipFile(zip_path) as zf:
        zf.extractall(extract)
    inner = extract / REDUMPER_ZIP_INNER
    if not inner.is_file():
        raise EngineError("redumper zip layout was unexpected "
                          "(no " + REDUMPER_ZIP_INNER + ").")
    dest = redumper_path()
    shutil.copy2(inner, dest)
    dest.chmod(0o755)
    shutil.rmtree(extract, ignore_errors=True)
    zip_path.unlink(missing_ok=True)


def engines_ready():
    return redumper_ready() and not missing_deps()


def ensure_engines(log=_noop, stage=_noop, install_deps=False):
    """First-run setup. Returns True when every engine is usable.

    Missing distro engines raise DepsMissingError (after the
    pkexec install attempt, if the GUI said the user accepted it)
    so the GUI can make the offer; other failures raise
    EngineError with a plain-language explanation. The app stays
    open either way.
    """
    ENGINE_DIR.mkdir(parents=True, exist_ok=True)
    if not redumper_ready():
        stage("First-run setup: downloading redumper "
              + REDUMPER_BUILD + "...")
        _fetch_redumper(log)
        stage("First-run setup: verifying redumper...")
        if not redumper_ready():
            raise EngineError("redumper was downloaded but does "
                              "not run (--version failed). See "
                              "the log.")
    missing = missing_deps()
    if missing:
        if install_deps:
            stage("First-run setup: installing engine "
                  "packages...")
            install_missing_deps(log)
            missing = missing_deps()
        if missing:
            raise DepsMissingError(dep_info(missing))
    return True


# ------------------------------------------------------------------
# .dvd sidecars + layer breaks
# ------------------------------------------------------------------

def parse_dvd_text(text):
    """Layer break from .dvd file contents, or None.

    The .dvd format abgx360 writes is two lines: the layer break
    sector count, then the ISO file name. Variants with a
    'LayerBreak=' prefix are accepted too."""
    for raw in text.splitlines():
        line = raw.strip()
        if not line:
            continue
        m = re.match(r"(?i)layerbreak\s*[=:]\s*(\d+)", line)
        if m:
            return int(m.group(1))
        if line.isdigit():
            return int(line)
    return None


def dvd_for_image(image_path):
    """The layer break from a same-stem .dvd sidecar, or None."""
    p = Path(image_path)
    side = p.with_suffix(".dvd")
    if side.is_file():
        try:
            return parse_dvd_text(side.read_text(errors="replace"))
        except Exception:  # noqa: BLE001
            return None
    return None


def layerbreak_label(lb):
    if lb == XGD2_BREAK:
        return "XGD2 preset"
    if lb == XGD3_BREAK:
        return "XGD3 preset"
    return "custom"


# ------------------------------------------------------------------
# Network shares (SMB) -- Duane's disc library lives on his home
# server, so every picker must be able to reach it. This layer owns
# the string logic (parse / build / locate) so it stays unit-
# testable; main.py owns the dialogs. A share ALWAYS resolves to a
# real local path before a job is built -- the engines never see
# smb:// or a UNC path on Linux:
#
# * Windows: the UNC path itself (\\server\share\...) is a real
#   path as far as the OS and the file dialogs are concerned.
# * Linux: the share is mounted through gio/GVFS (smb://) and the
#   job uses the FUSE path under $XDG_RUNTIME_DIR/gvfs -- a plain
#   filesystem path, so cue-relative lookups, ddrescue mapfiles
#   and every other engine behaviour are untouched.
# ------------------------------------------------------------------

def parse_share_ref(text):
    """Parse a share reference into {"server", "share", "subpath"}
    or return None. Accepts smb://server/share/sub/path,
    \\\\server\\share\\sub\\path and //server/share/sub/path.
    A bare "server/share" is NOT a reference here -- it is
    indistinguishable from a relative path; the share dialog
    takes server and share as separate fields instead."""
    if not text:
        return None
    t = text.strip()
    low = t.lower()
    if low.startswith("smb://"):
        parts = [p for p in t[6:].split("/") if p]
    elif t.startswith("\\\\"):
        parts = [p for p in t.replace("\\", "/").split("/") if p]
    elif t.startswith("//"):
        parts = [p for p in t.split("/") if p]
    else:
        return None
    if len(parts) < 2 or not parts[0] or not parts[1]:
        return None
    return {"server": parts[0], "share": parts[1],
            "subpath": "/".join(parts[2:])}


def is_share_ref(text):
    return parse_share_ref(text) is not None


def smb_url(server, share, subpath="", user=""):
    url = "smb://"
    if user:
        url += user + "@"
    url += server + "/" + share
    if subpath:
        url += "/" + subpath.strip("/")
    return url


def unc_path(server, share, subpath=""):
    p = "\\\\" + server + "\\" + share
    if subpath:
        p += "\\" + subpath.strip("/\\").replace("/", "\\")
    return p


def gvfs_base(runtime_dir=None):
    """The GVFS FUSE root for this session."""
    rd = runtime_dir or os.environ.get("XDG_RUNTIME_DIR")
    if not rd:
        try:
            rd = "/run/user/%d" % os.getuid()
        except Exception:  # noqa: BLE001
            return None
    return Path(rd) / "gvfs"


def gvfs_candidates(server, share, runtime_dir=None):
    """Possible GVFS mount-point paths for server/share. GVFS
    names an SMB mount "smb-share:server=<host>,share=<name>";
    the host is the URI host (no port) and case handling varies,
    so probe the combinations."""
    base = gvfs_base(runtime_dir)
    if base is None:
        return []
    host = server.split(":")[0]
    names = []
    for srv in {server, host, server.lower(), host.lower()}:
        for shr in {share, share.lower()}:
            name = "smb-share:server=%s,share=%s" % (srv, shr)
            if name not in names:
                names.append(name)
    return [base / n for n in names]


def find_share_mount(server, share, runtime_dir=None):
    """The local path of an already-mounted share, or None.
    Falls back to scanning the GVFS root for any smb-share entry
    naming this server+share (covers naming variants the
    candidate list missed)."""
    for cand in gvfs_candidates(server, share, runtime_dir):
        if cand.is_dir():
            return str(cand)
    base = gvfs_base(runtime_dir)
    if base is not None and base.is_dir():
        host = server.split(":")[0].lower()
        want_share = "share=" + share.lower()
        for entry in base.iterdir():
            low = entry.name.lower()
            if low.startswith("smb-share:") \
                    and want_share in low \
                    and ("server=" + host) in low:
                return str(entry)
    return None


def _run_gio_mount(cmd, user, password, timeout):
    """Run `gio mount ...` under a pty, answering its credential
    prompts, and return (exit code, output text).

    On current glib, gio asks "Domain [WORKGROUP]:" FIRST and
    "Password:" second, under a header line that itself contains
    the word "password" ("Enter password for share ...") -- so
    the output buffer must never be keyword-searched as a whole:
    only the PENDING prompt (the text after the last newline;
    prompts are printed without a trailing newline) says what is
    actually being asked. Answers are capped so a re-prompt loop
    cannot spin forever. The password is typed at the prompt and
    never stored anywhere by this app."""
    import pty

    master, slave = pty.openpty()
    try:
        proc = subprocess.Popen(cmd, stdin=slave, stdout=slave,
                                stderr=slave, close_fds=True)
    finally:
        os.close(slave)
    buf = b""
    answered = 0
    deadline = time.time() + timeout
    timed_out = False
    os.set_blocking(master, False)
    while True:
        if proc.poll() is not None:
            try:
                while True:
                    chunk = os.read(master, 4096)
                    if not chunk:
                        break
                    buf += chunk
            except OSError:
                pass
            break
        if time.time() > deadline:
            proc.kill()
            timed_out = True
            break
        try:
            chunk = os.read(master, 4096)
            if chunk:
                buf += chunk
        except BlockingIOError:
            time.sleep(0.05)
            continue
        except OSError:
            break
        text = buf.decode("utf-8", "replace")
        pending = text.replace("\r", "\n").rsplit("\n", 1)[-1]
        answer = None
        if "omain" in pending:
            answer = "WORKGROUP"
        elif "assword" in pending:
            answer = password
        elif "sername" in pending \
                or pending.rstrip().endswith("ser:"):
            answer = user
        if answer is not None and answered < 8:
            try:
                os.write(master, (answer + "\n").encode())
            except OSError:
                break
            answered += 1
            buf = b""
    rc = proc.wait()
    try:
        os.close(master)
    except OSError:
        pass
    out = buf.decode("utf-8", "replace")
    if timed_out:
        out += "\n(mount timed out after %ds)" % timeout
        rc = 124
    return rc, out


def mount_share(server, share, log=_noop, user="", password="",
                timeout=90):
    """Mount server/share via gio/GVFS and return its local path.
    Returns the existing path when already mounted. Raises
    EngineError carrying gio's own words on failure. Credential
    prompts are answered through a pty (see _run_gio_mount); the
    password is never stored anywhere by this app."""
    existing = find_share_mount(server, share)
    if existing:
        log("Share already mounted: " + existing)
        return existing
    gio = shutil.which("gio")
    if not gio:
        raise EngineError(
            "gio (from glib2) was not found on PATH, so network "
            "shares cannot be mounted. Install glib2 tools, or "
            "mount the share yourself and pick the mounted folder "
            "with Browse...")
    url = smb_url(server, share, user=user)
    log("+ gio mount " + smb_url(server, share))
    cmd = [gio, "mount"]
    if not user and not password:
        # Guest/anonymous share: gio's --anonymous avoids the
        # interactive credential prompts entirely.
        cmd.append("--anonymous")
    cmd.append(url)
    try:
        rc, text = _run_gio_mount(cmd, user, password, timeout)
    except OSError as e:
        raise EngineError("Could not run gio: %s" % e)

    def scrubbed(s):
        return s.strip() and "assword" not in s \
            and (not password or password not in s)

    for line in text.splitlines():
        if scrubbed(line):
            log("gio: " + line.strip())
    path = find_share_mount(server, share)
    if not path and rc == 0:
        # gio can take a moment to publish the mount point.
        for _ in range(50):
            time.sleep(0.2)
            path = find_share_mount(server, share)
            if path:
                break
    if path:
        return path
    if rc != 0:
        detail = " ".join(
            ln.strip() for ln in text.splitlines() if scrubbed(ln))
        raise EngineError(
            "Mounting %s failed (gio exit %d)%s"
            % (smb_url(server, share), rc,
               (": " + detail) if detail else ""))
    raise EngineError(
        "gio reported success for %s but no mount point appeared "
        "under %s -- is the GVFS daemon (gvfsd) running in this "
        "session?" % (smb_url(server, share), gvfs_base()))


def resolve_share(server, share, subpath="", log=_noop, user="",
                  password=""):
    """Resolve server/share[/subpath] to a real local path the
    engines can use. Windows: the UNC path itself. Linux: the
    GVFS mount path (mounting first when needed)."""
    if os.name == "nt":
        return unc_path(server, share, subpath)
    base = mount_share(server, share, log, user=user,
                       password=password)
    if subpath:
        return str(Path(base) / subpath)
    return base


# ------------------------------------------------------------------
# Archive input (.zip / .7z) for Write / Verify sources -- the
# server library is full of archived sets, so the pickers accept
# an archive directly. The archive is EXTRACTED to local scratch
# (under the app's data dir -- never back onto a share, and the
# source archive is only ever read), and the job runs on the
# extracted image with its cue/bin companions beside it. Nothing
# ever burns or verifies in place from inside an archive.
# Scratch is session-scoped: the GUI wipes it at startup.
# ------------------------------------------------------------------

ARCHIVE_SCRATCH = DATA_DIR / "archive-scratch"
ARCHIVE_SUFFIXES = (".zip", ".7z")
IMAGE_SUFFIXES = (".iso", ".img", ".bin")


def is_archive_path(path):
    return bool(path) and str(path).lower().endswith(
        ARCHIVE_SUFFIXES)


def archive_scratch_dir(archive_path):
    """The deterministic scratch folder for one archive
    (changes when the archive's size/mtime changes)."""
    src = Path(archive_path)
    try:
        st = src.stat()
        stamp = "%s|%d|%d" % (src.resolve(), st.st_size,
                               st.st_mtime_ns)
    except OSError:
        stamp = str(src.resolve())
    key = hashlib.sha1(stamp.encode()).hexdigest()[:10]
    stem = re.sub(r"[^A-Za-z0-9._-]+", "_", src.stem) or "archive"
    return ARCHIVE_SCRATCH / ("%s-%s" % (stem, key))


def find_image_in(folder):
    """The image to use from an extracted set, or None. A cue
    whose companions are all present wins (a BIN/CUE set is the
    real source; the cue carries the track layout), then any
    cue, then the largest ISO/IMG, then the largest BIN."""
    folder = Path(folder)
    files = [p for p in folder.rglob("*") if p.is_file()
             and not p.name.startswith(".")]
    cues = sorted(p for p in files
                  if p.suffix.lower() == ".cue")
    for cue in cues:
        try:
            entries = _cue_file_entries(str(cue))
        except Exception:  # noqa: BLE001
            entries = []
        if entries and all(Path(e).is_file() for e in entries):
            return cue
    if cues:
        return cues[0]
    images = [p for p in files
              if p.suffix.lower() in IMAGE_SUFFIXES[:2]]
    if images:
        return max(images, key=lambda p: p.stat().st_size)
    bins = [p for p in files if p.suffix.lower() == ".bin"]
    if bins:
        return max(bins, key=lambda p: p.stat().st_size)
    return None


def _extract_zip(src, dest, log):
    with zipfile.ZipFile(str(src)) as zf:
        names = zf.namelist()
        zf.extractall(str(dest))
    return names


def _extract_7z(src, dest, log):
    try:
        import py7zr
    except ImportError:
        py7zr = None
    if py7zr is not None:
        with py7zr.SevenZipFile(str(src), mode="r") as zf:
            names = zf.getnames()
            zf.extractall(path=str(dest))
        return names
    tool = None
    for cand in ("7z", "7za", "7zr"):
        tool = shutil.which(cand)
        if tool:
            break
    if not tool:
        raise EngineError(
            "This build has no 7z extractor: py7zr is not "
            "bundled/importable and no 7z/7za/7zr tool is on "
            "PATH. Install p7zip, or use a .zip instead.")
    rc = _run_stream(
        [tool, "x", "-y", "-o" + str(dest), str(src)],
        dest, log)
    if rc != 0:
        raise EngineError(
            "%s could not extract %s (exit %d)."
            % (Path(tool).name, src.name, rc))
    return [str(p.relative_to(dest))
            for p in dest.rglob("*") if p.is_file()]


def extract_archive(archive_path, log=_noop):
    """Extract a .zip/.7z source archive to local scratch and
    return the path of the image inside it. The source archive
    is opened read-only and never modified; extraction never
    lands on the share the archive may have come from (scratch
    is under the local data dir by construction). Re-running
    with an unchanged archive reuses the extraction."""
    src = Path(archive_path)
    if not src.is_file():
        raise EngineError("Archive not found: %s" % archive_path)
    dest = archive_scratch_dir(src)
    marker = dest / ".source"
    stamp = None
    try:
        st = src.stat()
        stamp = "%s|%d|%d" % (src.resolve(), st.st_size,
                              st.st_mtime_ns)
    except OSError:
        pass
    if marker.is_file() and stamp is not None:
        try:
            if marker.read_text() == stamp:
                image = find_image_in(dest)
                if image is not None:
                    log("Archive already extracted: %s" % dest)
                    return str(image)
        except Exception:  # noqa: BLE001
            pass
    if dest.exists():
        shutil.rmtree(str(dest), ignore_errors=True)
    dest.mkdir(parents=True, exist_ok=True)
    log("Extracting %s -> %s" % (src, dest))
    suffix = src.suffix.lower()
    try:
        if suffix == ".zip":
            names = _extract_zip(src, dest, log)
        elif suffix == ".7z":
            names = _extract_7z(src, dest, log)
        else:
            raise EngineError(
                "Not an archive this app reads: %s" % src.name)
    except EngineError:
        raise
    except Exception as e:  # noqa: BLE001
        raise EngineError(
            "Could not extract %s: %s" % (src.name, e))
    image = find_image_in(dest)
    if image is None:
        found = ", ".join(sorted(
            Path(n).name for n in names if not n.endswith("/"))
            [:12]) or "nothing"
        raise EngineError(
            "No disc image (.cue/.iso/.img/.bin) inside %s -- "
            "it contains: %s" % (src.name, found))
    if stamp is not None:
        try:
            marker.write_text(stamp)
        except Exception:  # noqa: BLE001
            pass
    log("Archive image: %s" % image)
    return str(image)


def clean_archive_scratch():
    """Wipe the archive scratch tree (called at app startup;
    extractions are session-scoped)."""
    if ARCHIVE_SCRATCH.exists():
        shutil.rmtree(str(ARCHIVE_SCRATCH), ignore_errors=True)


# ------------------------------------------------------------------
# Drive discovery -- real reported capacity, never capped here
# ------------------------------------------------------------------

def discover_drives():
    """Optical drives as dicts: device, vendor, model, revision,
    media (type/status), capacity_sectors (real, as reported).
    Empty list when there are no drives. Never raises."""
    drives = []
    devices = sorted(Path("/dev").glob("sr[0-9]*"))
    for dev in devices:
        info = {"device": str(dev), "vendor": "", "model": "",
                "revision": "", "media": "", "media_status": "",
                "capacity_sectors": None}
        if shutil.which("xorriso"):
            try:
                r = subprocess.run(
                    ["xorriso", "-outdev", str(dev), "-inq", "-toc"],
                    capture_output=True, timeout=30)
                text = (r.stdout + r.stderr).decode("utf-8",
                                                    "replace")
                m = re.search(r"vendor '([^']*)' product '([^']*)' "
                              r"revision '([^']*)'", text)
                if m:
                    info["vendor"], info["model"], \
                        info["revision"] = m.groups()
                m = re.search(r"Media current:\s*(.+)", text)
                if m:
                    info["media"] = m.group(1).strip()
                m = re.search(r"Media status :\s*(.+)", text)
                if m:
                    info["media_status"] = m.group(1).strip()
                m = re.search(r"Media summary:.*?(\d+) data blocks",
                              text)
                if m:
                    info["capacity_sectors"] = int(m.group(1))
            except Exception:  # noqa: BLE001
                pass
        if not info["vendor"]:
            # /sys fallback (no xorriso, or inquiry failed)
            sysdir = Path("/sys/class/block") / dev.name / "device"
            for key, fname in (("vendor", "vendor"),
                               ("model", "model"),
                               ("revision", "rev")):
                try:
                    info[key] = (sysdir / fname).read_text().strip()
                except Exception:  # noqa: BLE001
                    pass
        drives.append(info)
    return drives


def drive_display(info):
    parts = [info["device"]]
    name = (info["vendor"] + " " + info["model"]).strip()
    if name:
        parts.append(name)
    if info["media"]:
        parts.append("media: " + info["media"])
    return " -- ".join(parts)


# ------------------------------------------------------------------
# Hashing + Redump DAT matching
# ------------------------------------------------------------------

def hash_file(path, log=None):
    """MD5 + SHA1 + SHA256 of a file in one pass."""
    hs = {"md5": hashlib.md5(), "sha1": hashlib.sha1(),
          "sha256": hashlib.sha256()}
    with open(path, "rb") as f:
        while True:
            chunk = f.read(1 << 22)
            if not chunk:
                break
            for h in hs.values():
                h.update(chunk)
    return {k: h.hexdigest() for k, h in hs.items()}


def dat_match(dat_path, hashes):
    """(matched, game_name) of a dump's hashes against a Redump
    DAT. Matches on MD5 or SHA1."""
    try:
        root = ET.parse(dat_path).getroot()
    except Exception as e:  # noqa: BLE001
        return None, "DAT could not be parsed: %s" % e
    for game in root.iter("game"):
        for rom in game.iter("rom"):
            md5 = (rom.get("md5") or "").lower()
            sha1 = (rom.get("sha1") or "").lower()
            if (md5 and md5 == hashes.get("md5")) or \
                    (sha1 and sha1 == hashes.get("sha1")):
                return True, game.get("name") or rom.get("name")
    return False, ""


def dat_match_set(dat_path, roms):
    """(matched, game_name, best_count) of a multi-file dump
    against a Redump DAT. roms is {filename: {"md5":..., "sha1":
    ...}}. A game matches when EVERY file of the dump is one of
    its roms with an equal MD5 or SHA1 (a Redump split set is
    one game with one rom per track file). When nothing matches
    outright, best_count is the largest number of the dump's
    files that any single game matched -- for the verdict's
    specifics. (None, message, 0) when the DAT cannot be
    parsed."""
    try:
        root = ET.parse(dat_path).getroot()
    except Exception as e:  # noqa: BLE001
        return None, "DAT could not be parsed: %s" % e, 0
    best = 0
    for game in root.iter("game"):
        by_name = {}
        for rom in game.iter("rom"):
            by_name[rom.get("name")] = (
                (rom.get("md5") or "").lower(),
                (rom.get("sha1") or "").lower())
        n = 0
        for fname, h in roms.items():
            cand = by_name.get(fname)
            if cand and ((cand[0] and cand[0] == h.get("md5"))
                         or (cand[1]
                             and cand[1] == h.get("sha1"))):
                n += 1
        if roms and n == len(roms):
            return True, game.get("name") or "", n
        best = max(best, n)
    return False, "", best


# ------------------------------------------------------------------
# Job model + command construction
# ------------------------------------------------------------------

def job_name(job):
    k = job["kind"]
    if k == "build":
        if job["output"] == "iso":
            return "Build ISO: %d item(s) -> %s" % (
                len(job["sources"]), Path(job["iso_path"]).name)
        return "Build Disc: %d item(s) -> %s" % (
            len(job["sources"]), job["device"])
    if k == "write":
        return "Write: %s -> %s" % (
            Path(job["image"]).name, job["device"])
    if k == "read":
        sub = {"iso": "ISO", "bincue": "BIN/CUE",
               "preservation": "Preservation",
               "damaged": "Damaged-disc"}[job["sub"]]
        return "Read (%s): %s" % (sub, job["device"])
    if k == "verify":
        return "Verify: %s vs %s" % (
            Path(job["image"]).name, job["device"])
    return k


def _speed_cdrecord(job):
    s = job.get("speed", "max")
    return [] if s == "max" else ["speed=" + s]


def build_commands(job):
    """The engine commands for a job, as a list of argv lists.
    Verify returns [] (it runs in-process). Read-ISO returns one
    dd command whose count= is filled in at run time."""
    k = job["kind"]
    if k == "build":
        return _build_commands(job)
    if k == "write":
        return _write_commands(job)
    if k == "read":
        return _read_commands(job)
    return []


def _build_commands(job):
    label = job.get("label") or "DISC_TOOLS"
    if job["output"] == "iso":
        argv = ["xorriso", "-as", "mkisofs", "-o", job["iso_path"],
                "-V", label, "-iso-level", "3"]
        if job.get("joliet"):
            argv.append("-J")
        if job.get("rockridge"):
            argv.append("-r")
        if job.get("udf"):
            argv.append("-udf")
        if job.get("bootable") and job.get("boot_image_rel"):
            argv += ["-b", job["boot_image_rel"], "-c",
                     "boot.catalog", "-no-emul-boot",
                     "-boot-load-size", "4", "-boot-info-table"]
        argv += [str(s) for s in job["sources"]]
        return [argv]
    # straight to disc
    if job.get("udf"):
        # Native xorriso burning has no UDF; build a temp ISO with
        # the mkisofs emulation (which does), then write it.
        tmp = str(DATA_DIR / "tmp-build.iso")
        sub = dict(job, output="iso", iso_path=tmp)
        burn = ["xorriso", "-as", "cdrecord", "-v",
                "dev=" + job["device"], "-data", tmp]
        return [_build_commands(sub)[0], burn]
    argv = ["xorriso", "-dev", job["device"], "-volid", label,
            "-joliet", "on" if job.get("joliet") else "off",
            "-rockridge", "on" if job.get("rockridge") else "off",
            "-add"] + [str(s) for s in job["sources"]] + ["--"]
    return [argv]


def _write_commands(job):
    image = job["image"]
    dev = job["device"]
    speed = job.get("speed", "max")
    lb = job.get("layerbreak")
    engine = job.get("engine", "auto")
    if image.lower().endswith(".cue") or engine == "cdrdao":
        argv = ["cdrdao", "write", "--device", dev]
        if speed != "max":
            argv += ["--speed", speed]
        if job.get("test"):
            argv.append("--simulate")
        if job.get("eject"):
            argv.append("--eject")
        # --swap: cdrdao's native BIN convention is big-endian
        # audio, while Redump/redumper sets -- Duane's whole
        # archive -- are little-endian. Without the swap a
        # Redump set burns with every audio sample's bytes
        # exchanged. Ship-blocker proven on the 2026-11-21 burn
        # bench: track 01 (data) came back bit-identical while
        # all 50 audio tracks read back byte-swapped. cdrdao
        # swaps audio tracks only; data tracks are untouched.
        # The app's own read-cd output is swapped at read time
        # for the same reason (see _read_commands), so every
        # bin in this app's world is little-endian and burns
        # correctly through this path.
        argv.append("--swap")
        argv.append(image)
        return [argv]
    if lb or engine == "growisofs":
        argv = ["growisofs"]
        if job.get("test"):
            argv.append("-dry-run")
        if lb:
            argv.append("-use-the-force-luke=break:%d" % lb)
        if speed != "max":
            argv.append("-speed=" + speed)
        argv += ["-Z", "%s=%s" % (dev, image)]
        cmds = [argv]
        if job.get("eject"):
            cmds.append(["eject", dev])
        return cmds
    argv = ["xorriso", "-as", "cdrecord", "-v", "dev=" + dev]
    argv += _speed_cdrecord(job)
    if job.get("test"):
        argv.append("-dummy")
    if job.get("eject"):
        argv.append("-eject")
    argv += ["-data", image]
    return [argv]


def _read_commands(job):
    sub = job["sub"]
    dev = job["device"]
    if sub == "iso":
        # count= is replaced with the disc's real sector count at
        # run time (isosize); the placeholder keeps previews honest.
        return [["dd", "if=" + dev, "of=" + job["output"],
                 "bs=2048", "count=<disc sectors>",
                 "status=progress"]]
    if sub == "bincue":
        base = job["output_base"]
        # --swap: read-cd's native output is big-endian audio.
        # Swapping at read time lands the dump in the same
        # little-endian convention as Redump/redumper sets --
        # and as the --swap cue write expects -- so a dump read
        # by this app and burned back through it round-trips
        # byte-correct (audit + bench proof in BUILD-REPORT,
        # field fix 4, 2026-11-21). Without it the pair was
        # incoherent the moment the write gained --swap: an
        # app-read (big-endian) dump would have burned swapped.
        return [["cdrdao", "read-cd", "--device", dev, "--swap",
                 "--datafile", base + ".bin", base + ".toc"],
                ["toc2cue", base + ".toc", base + ".cue"]]
    if sub == "preservation":
        # NO subcommand: redumper b770 has no cd/dvd command --
        # a bare invocation is its default aggregate "disc" mode
        # (dump + refine + split + hash + info), which is
        # exactly what this mode promises. The disc type is
        # auto-detected; b770 does have a --disc-type= override
        # but nothing here needs it. Field bug, 2026-11-21: the
        # app passed the GUI's media pick ("cd") as a subcommand
        # and b770 answered "error: unknown command (command:
        # cd)", so every preservation dump died before touching
        # the drive.
        return [[str(redumper_path()),
                 "--drive=" + dev,
                 "--image-path=" + job["output_dir"],
                 "--image-name=" + job["image_name"],
                 "--retries=" + str(job.get("retries", 20)),
                 "--verbose"]]
    if sub == "damaged":
        out, mp = job["output"], job["mapfile"]
        return [["ddrescue", "-n", dev, out, mp],
                ["ddrescue", "-r", "3", dev, out, mp]]
    return []


def preview_command(job):
    if job["kind"] == "verify":
        if job["image"].lower().endswith(".cue"):
            return ("(built-in) per-track hash compare of %s "
                    "against %s (redumper read-back)"
                    % (job["image"], job["device"]))
        return ("(built-in) sector compare of %s against %s + "
                "MD5/SHA1/SHA256" % (job["image"], job["device"]))
    cmds = build_commands(job)
    return " && ".join(" ".join(str(a) for a in c) for c in cmds)


def command_cwds(job):
    """Working directory for each command from build_commands()
    (None = inherit the app's own cwd).

    cdrdao resolves a cue/toc sheet's relative FILE entries
    against the PROCESS working directory -- not the sheet's own
    folder. Field bug, 2026-11-21: Duane's 51-track Rayman (USA)
    burn failed on every track ("Cannot open data file ... No
    such file or directory") because the app launched cdrdao from
    its own cwd while the .bin files sat next to the .cue. So
    every cdrdao run that takes a sheet runs with cwd = the
    sheet's folder, and track resolution no longer depends on
    where the app itself was launched from. The read-cd pair gets
    the same treatment: the .toc cdrdao writes (and toc2cue then
    reads) belongs to the output folder. Other engines only ever
    receive plain file paths with no relative internal
    references, so they keep the inherited cwd.
    """
    k = job["kind"]
    if k == "write":
        image = job["image"]
        if image.lower().endswith(".cue") \
                or job.get("engine") == "cdrdao":
            return [str(Path(image).resolve().parent)]
        return [None] * len(build_commands(job))
    if k == "read" and job["sub"] == "bincue":
        parent = str(Path(job["output_base"]).resolve().parent)
        return [parent] * len(build_commands(job))
    return [None] * len(build_commands(job))


# ------------------------------------------------------------------
# Running jobs
# ------------------------------------------------------------------

def _preflight(job):
    """Problems that should fail the job before any engine runs,
    as result rows. Empty list = clear to run."""
    rows = []

    def fail(detail):
        rows.append({"check": "Preflight", "result": "FAIL",
                     "detail": detail})

    k = job["kind"]
    if k == "build":
        for s in job["sources"]:
            if not Path(s).exists():
                fail("Source does not exist: " + str(s))
        if job["output"] == "iso":
            if not job.get("iso_path"):
                fail("No output ISO path chosen.")
            if job.get("bootable"):
                rel = _boot_image_rel(job)
                if rel is None:
                    fail("Boot image is not inside any source "
                         "folder -- El Torito needs it in the tree.")
                else:
                    job["boot_image_rel"] = rel
        else:
            if job.get("bootable"):
                fail("Bootable builds go to an ISO file in v1, "
                     "not straight to disc.")
            if not job.get("device"):
                fail("No destination drive chosen.")
    elif k == "write":
        if not Path(job["image"]).is_file():
            fail("Image file does not exist: " + job["image"])
        if not job.get("device"):
            fail("No destination drive chosen.")
    elif k == "read":
        if not job.get("device"):
            fail("No source drive chosen.")
        if job["sub"] == "preservation" and not redumper_ready():
            fail("redumper is not installed (first-run setup has "
                 "not completed).")
    elif k == "verify":
        if not Path(job["image"]).is_file():
            fail("Image file does not exist: " + job["image"])
        if not job.get("device"):
            fail("No device (or file) to compare against.")
    return rows


def _boot_image_rel(job):
    boot = job.get("boot_image")
    if not boot:
        return None
    bp = Path(boot).resolve()
    for s in job["sources"]:
        sp = Path(s).resolve()
        if sp.is_dir():
            try:
                return str(bp.relative_to(sp))
            except ValueError:
                continue
        elif bp == sp:
            return bp.name
    return None


def _disc_sectors(dev):
    """Sector count of the inserted disc (isosize), or None."""
    if shutil.which("isosize"):
        try:
            r = subprocess.run(["isosize", "-x", dev],
                               capture_output=True, timeout=30)
            m = re.search(r"sector count:\s*(\d+)",
                          (r.stdout + r.stderr).decode("utf-8",
                                                        "replace"))
            if m:
                return int(m.group(1))
        except Exception:  # noqa: BLE001
            pass
    return None


def run_job(job, log, control=None):
    """Run one job, streaming engine output to log(). Returns
    (returncode, results rows). control, if given, is a dict the
    GUI owns: control['proc'] holds the live process so Stop can
    terminate it."""
    if control is None:
        control = {}
    pre = _preflight(job)
    if pre:
        for r in pre:
            log("PREFLIGHT: " + r["detail"])
        return 2, pre
    if job["kind"] == "verify":
        return _run_verify(job, log)
    cmds = build_commands(job)
    if job["kind"] == "read" and job["sub"] == "iso":
        n = _disc_sectors(job["device"])
        if n is None:
            rows = [{"check": "Read", "result": "FAIL",
                     "detail": "Could not determine the disc's "
                               "sector count (isosize) -- is a "
                               "data disc inserted?"}]
            log("READ: " + rows[0]["detail"])
            return 2, rows
        cmds[0][4] = "count=%d" % n
    raw_lines = []
    rc = 0
    cwds = command_cwds(job)
    for argv, cwd in zip(cmds, cwds):
        log("+ " + " ".join(str(a) for a in argv))
        try:
            proc = subprocess.Popen(
                [str(a) for a in argv], cwd=cwd,
                stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                text=True, errors="replace", bufsize=1,
                preexec_fn=_reset_engine_signals)
        except FileNotFoundError:
            raw_lines.append("Engine not found: %s" % argv[0])
            log(raw_lines[-1])
            rc = 127
            break
        control["proc"] = proc
        try:
            for line in proc.stdout:
                raw_lines.append(line)
                log(line.rstrip("\n"))
            proc.wait()
        finally:
            control["proc"] = None
        if proc.returncode != 0:
            rc = proc.returncode
            break
    raw = "".join(raw_lines)
    results = parse_results(job, raw, rc)
    if job["kind"] == "read" and job["sub"] == "preservation" \
            and rc == 0:
        results += _preservation_post(job, log)
    return rc, results


def _redumper_readback(target, outdir, name, log):
    """Dump the disc in `target` into outdir with redumper's
    aggregate mode (the preservation invocation: bare command,
    no subcommand). Returns (rc, first error line or None);
    output streams to log as it comes."""
    lines = []

    def capture(msg):
        lines.append(msg)
        log(msg)

    argv = [str(redumper_path()),
            "--drive=" + target,
            "--image-path=" + str(outdir),
            "--image-name=" + name,
            "--retries=20",
            "--verbose"]
    rc = _run_stream(argv, outdir, capture)
    err = next((ln.strip() for ln in lines
                if "error" in ln.lower()), None)
    return rc, err


def _run_verify_cue(job, log):
    """Per-track verify for a BIN/CUE source.

    Field finding A of the 2026-11-21 burn bench: the flat
    verify treated the image as one binary, so for a cue it
    hashed the .cue TEXT file (6,030 bytes) and byte-compared
    that against the raw device -- "Target ended early at
    byte 0" -- a verdict that says nothing about the disc.
    A cue set is verified the way fix 3 taught the
    preservation post-step to judge one: per track. The disc
    is read back with redumper, whose split output is the
    same little-endian per-track layout as the source set,
    and each track's read-back is hash-compared against the
    cue's corresponding FILE entry, in order. (This is also
    the comparison that caught the byte-swapped audio of
    finding B: track 01 identical, audio tracks different --
    the fixed verify must reproduce exactly that split.)"""
    image = Path(job["image"])
    target = job["device"]
    rows = []
    entries = _cue_file_entries(image)
    if not entries:
        rows.append({"check": "Cue verify", "result": "FAIL",
                     "detail": "%s lists no FILE entries -- "
                               "nothing to verify against."
                               % image.name})
        return 2, rows
    if not redumper_ready():
        rows.append({"check": "Cue verify", "result": "FAIL",
                     "detail": "redumper is not installed "
                               "(first-run setup has not "
                               "completed) -- a BIN/CUE verify "
                               "reads the disc back with it."})
        return 2, rows
    rows.append({"check": "Cue verify", "result": "INFO",
                 "detail": "Per-track hash compare of %s "
                           "(%d track(s)) against %s."
                           % (image.name, len(entries), target)})
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    tmp = Path(tempfile.mkdtemp(prefix="verify-",
                                 dir=str(DATA_DIR)))
    try:
        rc, err = _redumper_readback(target, tmp, "Verify", log)
        back_cue = tmp / "Verify.cue"
        if rc != 0 or not back_cue.is_file():
            rows.append({"check": "Disc read-back",
                         "result": "FAIL",
                         "detail": "redumper could not read "
                                   "the disc (%s)."
                                   % (err or "exit %d" % rc)})
            return 2, rows
        back = _cue_file_entries(back_cue)
        count_bad = len(back) != len(entries)
        if count_bad:
            rows.append({"check": "Track count",
                         "result": "FAIL",
                         "detail": "Source cue lists %d "
                                   "track(s); the disc read "
                                   "back %d."
                                   % (len(entries), len(back))})
        bad = 0
        for i, src in enumerate(entries):
            label = "Track %02d" % (i + 1)
            if i >= len(back):
                rows.append({"check": label, "result": "FAIL",
                             "detail": "No read-back track "
                                       "for %s." % src.name})
                bad += 1
                continue
            if not src.is_file():
                rows.append({"check": label, "result": "FAIL",
                             "detail": "Source track file is "
                                       "missing: %s" % src.name})
                bad += 1
                continue
            hs = _hash_track_file(src)
            hb = _hash_track_file(back[i])
            if hs["size"] == hb["size"] \
                    and hs["md5"] == hb["md5"] \
                    and hs["sha1"] == hb["sha1"]:
                rows.append({"check": label, "result": "PASS",
                             "detail": "%s -- md5 %s"
                                       % (src.name, hs["md5"])})
            else:
                bad += 1
                rows.append({"check": label, "result": "FAIL",
                             "detail": "%s differs on the disc "
                                       "(source md5 %s, disc "
                                       "md5 %s)."
                                       % (src.name, hs["md5"],
                                          hb["md5"])})
        return (1 if bad or count_bad else 0), rows
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def _run_verify(job, log):
    image = Path(job["image"])
    if image.suffix.lower() == ".cue":
        return _run_verify_cue(job, log)
    target = job["device"]
    rows = []
    size = image.stat().st_size
    log("Hashing %s (%d bytes)..." % (image, size))
    hashes = hash_file(image)
    for algo in ("md5", "sha1", "sha256"):
        rows.append({"check": algo.upper(), "result": "INFO",
                     "detail": hashes[algo]})
    log("Comparing sectors against %s ..." % target)
    compared = 0
    mismatch = None
    try:
        with open(image, "rb") as fi, open(target, "rb") as ft:
            while True:
                a = fi.read(1 << 22)
                if not a:
                    break
                b = ft.read(len(a))
                if len(b) < len(a):
                    mismatch = compared
                    rows.append({
                        "check": "Sector compare", "result": "FAIL",
                        "detail": "Target ended early at byte %d "
                                  "(image is %d bytes)."
                                  % (compared, size)})
                    break
                if a != b:
                    off = compared + next(
                        i for i in range(len(a)) if a[i] != b[i])
                    mismatch = off
                    rows.append({
                        "check": "Sector compare", "result": "FAIL",
                        "detail": "First difference at byte %d "
                                  "(sector %d)."
                                  % (off, off // 2048)})
                    break
                compared += len(a)
    except OSError as e:
        rows.append({"check": "Sector compare", "result": "FAIL",
                     "detail": "Could not read the target: %s" % e})
        return 2, rows
    if mismatch is None:
        rows.append({
            "check": "Sector compare", "result": "PASS",
            "detail": "%d bytes (%d sectors) identical."
                      % (compared, compared // 2048)})
        return 0, rows
    return 1, rows


_CUE_FILE_RE = re.compile(
    r'^\s*FILE\s+(?:"([^"]+)"|(\S+))', re.MULTILINE)

_ROM_LINE_RE = re.compile(
    r'<rom\s+name="([^"]+)"\s+size="(\d+)"\s+'
    r'crc="([0-9a-fA-F]+)"\s+md5="([0-9a-fA-F]+)"\s+'
    r'sha1="([0-9a-fA-F]+)"')


def _cue_file_entries(cue_path):
    """The FILE entries of a cue sheet, resolved against the
    sheet's own folder (cue FILE entries are relative to it)."""
    text = Path(cue_path).read_text(errors="replace")
    base = Path(cue_path).resolve().parent
    out = []
    for m in _CUE_FILE_RE.finditer(text):
        p = Path(m.group(1) or m.group(2))
        out.append(p if p.is_absolute() else base / p)
    return out


def _redumper_log_hashes(log_path):
    """Per-file hashes from the DAT block redumper's HASH phase
    prints into its <name>.log: {filename: {"size", "crc",
    "md5", "sha1"}}. {} when the log is missing or carries no
    block."""
    try:
        text = Path(log_path).read_text(errors="replace")
    except OSError:
        return {}
    out = {}
    for m in _ROM_LINE_RE.finditer(text):
        out[m.group(1)] = {"size": int(m.group(2)),
                           "crc": m.group(3).lower(),
                           "md5": m.group(4).lower(),
                           "sha1": m.group(5).lower()}
    return out


def _hash_track_file(path):
    """size + CRC32 + MD5 + SHA1 of one file in a single pass --
    the Redump DAT field set. Fallback for when redumper's own
    log hashes are unavailable for a track."""
    crc = 0
    md5 = hashlib.md5()
    sha1 = hashlib.sha1()
    with open(path, "rb") as f:
        while True:
            chunk = f.read(1 << 22)
            if not chunk:
                break
            crc = zlib.crc32(chunk, crc)
            md5.update(chunk)
            sha1.update(chunk)
    return {"size": path.stat().st_size,
            "crc": "%08x" % (crc & 0xFFFFFFFF),
            "md5": md5.hexdigest(),
            "sha1": sha1.hexdigest()}


def _preservation_post(job, log):
    """After a redumper run: prove the dump is complete, write
    <name>.hashes.txt beside it, and (if a DAT was chosen)
    verdict the dump against the Redump DAT.

    redumper's aggregate mode writes a SPLIT set -- <name>.cue
    plus one bin per track -- never a single <name>.bin/.iso.
    Field finding, 2026-11-21 (the first real-media bench dump,
    Rayman (USA), 51 tracks): the old post-step looked only for
    the single file, so every successful dump got a false
    "no .bin/.iso was found" WARN, no hashes file, and no DAT
    verdict. The split set is now the primary layout; the
    single-file form is still accepted when one somehow
    exists."""
    rows = []
    outdir = Path(job["output_dir"])
    name = job["image_name"]
    cue = outdir / (name + ".cue")
    if cue.is_file():
        return _preservation_post_split(job, outdir, name, cue,
                                          rows)
    image = None
    for ext in (".bin", ".iso"):
        cand = outdir / (name + ext)
        if cand.is_file():
            image = cand
            break
    if image is None:
        rows.append({"check": "Dump image", "result": "WARN",
                     "detail": "redumper finished but neither a "
                               "%s split set nor a .bin/.iso was "
                               "found in the output folder."
                               % (name + ".cue")})
        return rows
    hashes = hash_file(image)
    hash_path = outdir / (name + ".hashes.txt")
    hash_path.write_text(
        "".join("%s  %s\n" % (hashes[a], image.name)
                for a in ("md5", "sha1", "sha256")))
    rows.append({"check": "Dump image", "result": "PASS",
                 "detail": "%s (%d bytes); hashes in %s"
                           % (image.name, image.stat().st_size,
                              hash_path.name)})
    for algo in ("md5", "sha1", "sha256"):
        rows.append({"check": algo.upper(), "result": "INFO",
                     "detail": hashes[algo]})
    if job.get("dat_path"):
        matched, gname = dat_match(job["dat_path"], hashes)
        if matched is None:
            rows.append({"check": "Redump DAT", "result": "WARN",
                         "detail": gname})
        elif matched:
            rows.append({"check": "Redump DAT", "result": "PASS",
                         "detail": "Matches Redump entry: " + gname})
        else:
            rows.append({"check": "Redump DAT", "result": "FAIL",
                         "detail": "No Redump DAT entry matches "
                                   "this dump's hashes."})
    return rows


def _preservation_post_split(job, outdir, name, cue, rows):
    """The split-set half of _preservation_post: completeness
    is judged against the cue's FILE entries (every one present
    and non-empty); hashes come from the DAT block in
    redumper's own <name>.log when it covers a track (no
    re-hash of the dump), falling back to hashing that track
    file directly."""
    entries = _cue_file_entries(cue)
    if not entries:
        rows.append({"check": "Dump image", "result": "WARN",
                     "detail": "%s lists no FILE entries -- the "
                               "split set cannot be verified."
                               % cue.name})
        return rows
    logged = _redumper_log_hashes(outdir / (name + ".log"))
    problems = []
    roms = {}
    total = 0
    for p in entries:
        if not p.is_file():
            problems.append("%s is missing" % p.name)
            continue
        size = p.stat().st_size
        if size == 0:
            problems.append("%s is empty" % p.name)
            continue
        total += size
        h = logged.get(p.name)
        if h is not None and h["size"] != size:
            h = None  # log entry disagrees -- trust the file
        if h is None:
            h = _hash_track_file(p)
        roms[p.name] = h
    if problems:
        shown = "; ".join(problems[:4])
        if len(problems) > 4:
            shown += " (+%d more)" % (len(problems) - 4)
        rows.append({"check": "Dump image", "result": "WARN",
                     "detail": "Split set from %s is incomplete: "
                               "%s." % (cue.name, shown)})
        return rows
    hash_path = outdir / (name + ".hashes.txt")
    hash_path.write_text("".join(
        "%s\n  size %d\n  crc %s\n  md5 %s\n  sha1 %s\n"
        % (p.name, roms[p.name]["size"], roms[p.name]["crc"],
           roms[p.name]["md5"], roms[p.name]["sha1"])
        for p in entries))
    rows.append({"check": "Dump image", "result": "PASS",
                 "detail": "%s + %d track file(s), %d bytes "
                           "total; hashes in %s"
                           % (cue.name, len(entries), total,
                              hash_path.name)})
    first = entries[0].name
    for algo in ("crc", "md5", "sha1"):
        rows.append({"check": "%s (%s)" % (algo.upper(), first),
                     "result": "INFO",
                     "detail": roms[first][algo]})
    if job.get("dat_path"):
        matched, gname, best = dat_match_set(job["dat_path"],
                                             roms)
        if matched is None:
            rows.append({"check": "Redump DAT", "result": "WARN",
                         "detail": gname})
        elif matched:
            rows.append({"check": "Redump DAT", "result": "PASS",
                         "detail": "Matches Redump entry: %s "
                                   "(all %d track files)"
                                   % (gname, len(roms))})
        else:
            rows.append({"check": "Redump DAT", "result": "FAIL",
                         "detail": "No single Redump entry "
                                   "matches all %d track files "
                                   "(best entry matched %d)."
                                   % (len(roms), best)})
    return rows


# ------------------------------------------------------------------
# Result parsing
# ------------------------------------------------------------------

def parse_results(job, raw, rc):
    k = job["kind"]
    rows = []

    def add(check, result, detail=""):
        rows.append({"check": check, "result": result,
                     "detail": detail.strip()})

    ok = (rc == 0)
    if k == "build":
        m = re.search(r"ISO image produced:\s*(\d+) sectors", raw)
        if job["output"] == "iso":
            p = Path(job["iso_path"])
            if ok and p.is_file():
                add("ISO image", "PASS",
                    "%s created (%d bytes%s)."
                    % (p.name, p.stat().st_size,
                       ", " + m.group(1) + " sectors"
                       if m else ""))
            else:
                add("ISO image", "FAIL",
                    "xorriso exited %d; see the raw log." % rc)
        else:
            add("Build to disc", "PASS" if ok else "FAIL",
                "xorriso exited %d." % rc)
    elif k == "write":
        eng = ("cdrdao" if job["image"].lower().endswith(".cue")
               else "growisofs" if job.get("layerbreak")
               or job.get("engine") == "growisofs" else "xorriso")
        add("Write (%s)" % eng, "PASS" if ok else "FAIL",
            "Engine exited %d.%s" % (
                rc, " Test mode -- nothing was burned."
                if job.get("test") else ""))
        if job.get("layerbreak"):
            add("Layer break", "INFO",
                "%d (%s) requested via growisofs break flag -- "
                "BENCH-PROOF-PENDING: closes when a burned disc "
                "reads this value back."
                % (job["layerbreak"],
                   layerbreak_label(job["layerbreak"])))
    elif k == "read":
        sub = job["sub"]
        if sub == "iso":
            p = Path(job["output"])
            if ok and p.is_file():
                add("Disc image", "PASS",
                    "%s (%d bytes)." % (p.name,
                                        p.stat().st_size))
            else:
                add("Disc image", "FAIL", "dd exited %d." % rc)
        elif sub == "bincue":
            base = job["output_base"]
            if ok and Path(base + ".bin").is_file() \
                    and Path(base + ".cue").is_file():
                add("BIN/CUE", "PASS",
                    "%s + %s created."
                    % (Path(base + ".bin").name,
                       Path(base + ".cue").name))
            else:
                add("BIN/CUE", "FAIL", "cdrdao exited %d." % rc)
        elif sub == "preservation":
            errs = [ln.strip() for ln in raw.splitlines()
                    if "error" in ln.lower()]
            if ok:
                add("Preservation dump", "PASS",
                    "redumper finished cleanly.")
            else:
                add("Preservation dump", "FAIL",
                    "redumper exited %d. %s"
                    % (rc, errs[0] if errs else ""))
        elif sub == "damaged":
            add("Damaged-disc read", "PASS" if ok else "FAIL",
                "ddrescue exited %d. Mapfile kept at %s -- "
                "re-running the same job resumes where it left "
                "off." % (rc, job["mapfile"]))
    return rows
