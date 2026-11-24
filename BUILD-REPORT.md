# Disc Tools — BUILD REPORT (2026-11-21)

Built on recomp-node2 per `~/workspace/imgburn-clone-linux-spec.md`
(Duane's word, 2026-11-21 18:32), in the family pattern of the
PopFE / ABGX360 GUIs. Nothing published anywhere.

## Deliverable

- **DiscTools-x86_64.AppImage** — 129,598,656 bytes,
  sha256 `40f2497f79c50ea2c04c256016884732b30f1708ae0b3edf46accac82c1eac7b`
  (rebuilt 2026-11-21 with all three field fixes — the cue-cwd
  fix, the redumper no-subcommand fix, and the split-set
  post-step + engine-signal fix; see the addenda at the end of
  this report; hash re-verified after the copy to the VM; the
  AppImage-embedded `app/main.py` and `app/disc_engine.py`
  md5-match the source tree).
- Source: `src/DiscTools/` (app/main.py + app/disc_engine.py,
  launcher, README). Parity inventory: `PARITY-CHECKLIST.md`.
  Unit tests: `tests/test_disc_engine.py` — **41/41 pass**.

## What the app is

Python 3 + PySide6 in the bundled-runtime AppImage. Five mode tabs
(Write / Build / Read / Verify / Discovery), a job queue where a
failed job never kills the queue, parsed results + raw engine log
per job, narrating status bar, live command preview. First-run
auto-setup under `~/.local/share/disc-tools/`: distro engines
(xorriso, **growisofs**, cdrdao, GNU ddrescue) detected with a
one-click pkexec install offer and a clean manual fallback;
redumper **b770** downloaded from superg's GitHub releases and
verified against a pinned SHA-256
(`53bc7815…12ca9b`, computed from the official release zip at
build time).

## Acceptance results (packaged AppImage, Xvfb, node 2)

Node 2 genuinely lacks a system ddrescue and has no burner drive —
both facts were used as test conditions, not worked around silently.

| # | Test | Result |
|---|---|---|
| S1 | Clean HOME, ddrescue missing, dep choice "manual" | **PASS** — exactly one offer, packages `[gddrescue]`, family debian, manual command `sudo apt-get install -y gddrescue`; setup stopped cleanly (not an error), exact command in the log |
| S2 | Clean HOME, all engines present (real ddrescue 1.30 from the official .deb in a PATH prefix — node 2 has no passwordless sudo, so the pkexec prompt itself is unproven headless, same boundary as the abgx360 fix) | **PASS** — redumper downloaded + hash-verified + installed; stage "Ready — engines installed." |
| S2b | Second launch, same HOME | **PASS** — "Ready — all engines found.", setup skipped |
| S3 | Queue: Build ISO → Verify → failing Write (nonexistent image) → Build ISO | **PASS** — ISO built (481,280 B, 235 sectors); Verify sector-compare PASS with MD5/SHA1/SHA256 rows; the bad Write job reported a preflight FAIL; the job after it still ran and passed. Extracted ISO contents are byte-identical to the source tree (`diff -r`), volume label TESTDISC, and the app's SHA256 row matches `sha256sum` of the ISO exactly |
| S4 | XGD3 preset Write in test mode, no drive present | **PASS (as far as a driveless machine allows)** — command was exactly `growisofs -dry-run -use-the-force-luke=break:2084960 -speed=4 -Z /dev/sr0=<iso>`; growisofs failed on the absent device, the job reported FAIL, and the Layer break row carried the BENCH-PROOF-PENDING wording |
| — | Discovery with no drives (every run) | **PASS** — scan completes, drive list empty, "No optical drives found" shown, no crash; redumper itself also reports "no ready drives" gracefully |
| — | Unit tests (engine layer) | **26/26 PASS** — .dvd parsing, dep table (incl. ddrescue→`gddrescue`, growisofs→`dvd+rw-tools`), command builders for all modes, verify compare match/mismatch, Redump DAT match, result parsing |

Screenshots from the smoke runs were eyeballed: queue verdicts,
the Write tab's layer-break group with the BENCH-PROOF-PENDING
note, results table and raw log all render as designed.

## The layer-break gate — candidates compared, status PENDING

Duane's addendum (2026-11-21 18:33) made growisofs a first-class
engine, and it is: it is in the first-run dependency table and
pkexec offer, and Write mode offers it as an explicit engine
choice for any ISO→disc burn — not only for presets.

| Candidate | Break mechanism | Verdict for v1 |
|---|---|---|
| **growisofs** (dvd+rw-tools 7.1) | `-use-the-force-luke=break:<sectors>` | **The preset path.** The classic Linux XGD3 burn route; implemented and command-verified (S4) |
| cdrecord (cdrtools) | `layerbreak=<sectors>` | Not a dependency: real cdrtools is rarely packaged — distros ship the wodim fork without feature parity, so the flag cannot be relied on |
| xorriso / libburn | No direct user-settable DVD+R DL layer break in the shipped versions | Used for plain writes, Build, and Discovery; not the preset path |

**Gate status: BENCH-PROOF-PENDING.** No fleet mini has a burner
drive, so no candidate could be proven here and none is claimed
proven. The presets (XGD2 1913760, XGD3 2084960) ship marked
BENCH-PROOF-PENDING in the Write tab, in every burn's result
rows, and in Help > About. The gate closes only via acceptance
test 4 on Duane's bench: burn a DVD+R DL with the XGD3 preset,
read the layer break back off the disc, show it equals 2084960.

## Unproven / bench-only (stated plainly)

- Acceptance tests 3 (burn a data DVD + Verify against the disc),
  4 (XGD3 DL burn + break read-back), 5 (redumper dump of a real
  PS1 disc + Redump match) and 7 (BurnerMax-flashed drive
  reporting full capacity) need real drives/media — bench tests
  for Duane, all unproven.
- The pkexec password prompt + apt install itself (headless
  boundary, same as the abgx360 GUI fix): detection, package
  mapping, offer contents, manual clean-stop, and install-flow
  routing up to the pkexec invocation are proven; the prompt is
  proven only by a run on Duane's rig/laptop.
- Verify-after-write chaining triggers only behind a successful
  burn (rc 0), so it is unexercised until a bench burn exists.
- Read modes (ISO / BIN-CUE / preservation / damaged) are
  command-built and unit-tested but unrun against real media.
- Build-straight-to-disc and bootable (El Torito) builds are
  implemented; only Build-to-ISO was run end-to-end here.
- **BurnerMax payload unlock for stock drives is phase 2 and is
  NOT in this build** — the app says so in the Discovery tab and
  About. Flashed drives work from v1: they report full capacity
  themselves and Discovery honours the reported number (the app
  never caps image size).
- The AppImage was run via `--appimage-extract-and-run` (node 2
  lacks libfuse2); on a desktop with libfuse2 it runs directly.

## Credits

xorriso / libburnia (Thomas Schmitt), growisofs (dvd+rw-tools),
redumper (superg), cdrdao, GNU ddrescue. ImgBurn is the design
reference only — no code, assets, or text from it.

---

## FIELD FIX addendum — 2026-11-21 (cue-relative track lookup)

**The bug, in Duane's terms.** The first real burn on his bench: a
Write job for Rayman (USA) — one .cue plus 51 separate .bin files
in a single folder — failed on every track:
`Cannot open data file "Rayman (USA) (Track 01).bin": No such file
or directory`, each followed by `Cannot determine length of track
data specification.` The bins were sitting right next to the cue.
Cause: cdrdao resolves a cue sheet's relative FILE entries
against the folder the *process* was started in, and the app
launched cdrdao from its own working folder — not the cue's.

**The fix** (`app/disc_engine.py` only; no command lines changed).
A new `command_cwds()` gives every cdrdao run that takes a
cue/toc sheet the sheet's own folder as its working directory,
and `run_job` passes that to the engine process. The Read BIN/CUE
pair (cdrdao read-cd + toc2cue) gets the same treatment with the
output folder. The other engines were audited for the same class:
xorriso, growisofs, ddrescue and redumper only ever receive plain
file paths with no relative internal references in this app, so
they were left as-is.

**New tests.** Three added to `tests/test_disc_engine.py`
(`CueCwdTests`): a multi-file cue in a temp dir with the job
launched from a *different* cwd asserts cdrdao is invoked from
the cue's folder; the read-cd pair runs from the output folder;
and a plain ISO write keeps the inherited cwd (no overreach).
Suite: **29/29 pass**, on the VM and on node 2.

**Acceptance (rebuilt packaged AppImage, Xvfb, node 2).**

- Control experiment with the system cdrdao and a 3-track cue set
  (spaced filenames, mirroring the field case): launched from the
  wrong folder it reproduced Duane's errors verbatim, track by
  track; launched from the cue's folder it opened every track
  file and failed only at the device stage.
- The packaged app under the exact field geometry (app cwd ≠ cue
  folder, `cdrdao write --device /dev/sr0 --speed 16 <cue>`): the
  job's raw log contains **zero** `Cannot open data file`,
  `Cannot open audio file`, or `Cannot determine length` errors;
  the only failure is `Cannot setup device /dev/sr0` — node 2 has
  no burner, so that is the expected end of the road there.
- No regressions: S2b re-run — "Ready — all engines found."; S3
  re-run — Build PASS → Verify PASS → the deliberately bad Write
  reported → final Build PASS ("Batch finished: 4 job(s) ran.").

**Still bench-pending, unchanged.** A real burn remains Duane's
proof: this fix is proven up to the device door, not through it.
The layer-break gate and the rest of the unproven list above are
untouched by this fix.

---

## FIELD FIX 2 addendum — 2026-11-21 (redumper has no "cd" command)

**The bug, in Duane's terms.** Minutes after the cue fix, the
first preservation Read on his bench died instantly. The app ran
`redumper cd --drive=/dev/sr0 --image-path=… --image-name=Rayman
--retries=20 --verbose` and redumper b770 answered
`error: unknown command (command: cd)`. The dump never started —
b770 was handed a command that does not exist.

**The b770 truth** (from the pinned binary's own `--help`,
re-checked before coding). Commands: `disc` (aggregate, the
DEFAULT), `dump`, `refine`, `split`, `hash`, `info`,
`protection`, `eject`, `skeleton`, `dvdisokey`, `dvdkey`,
`spiral::dump`, `dump::extra`, `tools::*`, `flash::*`. There is
no `cd`/`dvd`/`bd` command — the disc type is auto-detected,
with an override available as
`--disc-type=CD|DVD|BLURAY|BLURAY-R|HD-DVD`. Every option flag
the app used (`--drive=`, `--image-path=`, `--image-name=`,
`--retries=`, `--verbose`) exists in b770 and was never the
problem. The `cd` token came from the Read tab's Media (CD/DVD)
pick, which the engine passed straight through as a subcommand.

**The fix** (`app/disc_engine.py` + `app/main.py`). The
preservation command is now a bare redumper invocation — no
subcommand — which runs b770's default aggregate `disc` mode:
dump + refine + split + hash + info, exactly what the mode
promises (dump files + BIN/CUE split + hashes for the DAT
verdict). No `--disc-type=` override is passed: auto-detection
is b770's designed behavior and nothing in the app knows the
disc type better than the drive does. The Read tab's Media
selector is removed — it existed only to feed the bogus token,
and with auto-detection there is no setting left to get wrong.
Audit: the preservation command was the ONLY redumper
invocation the app builds (post-dump hashing and the DAT
verdict use the app's own code), so no other stale tokens
existed.

**New tests.** Three added (`RedumperCommandTests`): the b770
command set is hard-coded in the test, cited to the binary's
`--help`, and every redumper command the app constructs is
validated against it — a non-option token after the binary
must be a real b770 command, and any `--disc-type=` value must
be a real b770 disc type. The preservation command is asserted
to carry NO subcommand token, including when the job dict
still carries a legacy `media` pick (the exact field
regression). The existing preservation argv test now also
asserts the token after the binary is an option. Suite:
**32/32 pass**, on the VM and on node 2.

**Acceptance (rebuilt packaged AppImage, Xvfb, node 2).**
Preservation job pointed at /dev/sr0 (node 2 is driveless):
the app built `redumper --drive=/dev/sr0 --image-path=…
--image-name=Rayman --retries=20 --verbose` and the job's raw
log contains NO `unknown command` — redumper parsed, started,
and failed only at drive access:
`error: unable to open drive (/dev/sr0, SYSTEM: No such file or
directory)`. Cue-fix re-check: the 3-track cue Write job again
shows zero `Cannot open` / `Cannot determine length` errors,
failing only at device setup. S2b re-run: "Ready — all engines
found." No regressions from either fix.

**Still bench-pending, unchanged.** A real preservation dump
on Duane's drive (node 2 has no burner), and everything
already listed as unproven above — real burns, the
layer-break read-back, the pkexec prompt, real-media reads.

---

## BENCH — 2026-11-21 (first real-media preservation dump, node 2)

Duane attached his spare Blu-ray burner to recomp-node2 and put
his retail **Rayman (USA)** PS1 disc in it. The preservation
Read was run end-to-end **through the packaged AppImage**
(sha256 `1544b132…`, the build with both field fixes), driven
by the same smoke harness as the acceptance runs, job:
Read → Preservation, `/dev/sr0`, image name `Rayman`,
retries 20, output `~/disc-tools-build/bench-rayman/`.

**Bench hardware.** LG **HL-DT-ST BD-RE WH14NS40**, firmware
**1.05** (`/dev/sr0` + `/dev/sg1`). The drive is not in
redumper b770's drive database, so redumper ran it as GENERIC
(read offset +6, C2 shift 0, read method BE, sector order
DATA_C2_SUB) — auto-detected, no hand-tuning. Disc identity
from redumper's INFO phase: **SLUS-00005**, EXE SLUS-000.05
dated 1995-07-24, region USA, no anti-modchip, no libcrypt —
the retail Rayman (USA) disc, 51 tracks (1 data + 50 audio),
262,419 sectors.

**Two aborted attempts, for the record.**

- *Attempt 1 (20:24)* — harness operator error, not an app
  defect: the launch omitted the ddrescue PATH prefix every
  prior acceptance run used, so first-run setup stopped on
  the missing-ddrescue dependency and the run-queue guard
  dialog ("engines are not set up yet") held the smoke
  driver. Killed and relaunched correctly.
- *Attempt 2 (20:26)* — a real launch-context finding.
  Setup reached "Ready — all engines found", the job ran,
  redumper read the full TOC (all 51 tracks), then died
  before sector one: `error: signal handler already set
  (signal: 2)`, exit 255. Root cause, proven with a
  disposition probe: the app had been launched as a
  background job of a non-interactive shell, which forces
  **SIGINT to SIG_IGN for the whole process tree**;
  redumper b770 refuses to start with SIGINT ignored (it
  needs the signal for its own graceful-stop handling).
  Foreground disposition = default handler; background-job
  disposition = SIG_IGN (1). A normal desktop launch —
  Duane's rig included — has a default SIGINT and never
  sees this. **Robustness note for a future build:** the
  app could reset SIGINT/SIGQUIT to SIG_DFL for engine
  children in `_run_stream`, making the engine layer immune
  to launcher context. Not fixed in this build; recorded,
  not hidden. Attempt 2's partial output is preserved at
  `~/disc-tools-build/bench-rayman-attempt2/`.
- *Attempt 3 (20:30)* — relaunched with SIGINT/SIGQUIT
  reset to SIG_DFL. Clean run, below.

**The run.** App launched 20:30:16 EST; redumper started
20:30:20; total redumper time **≈208 s** — phase durations
(redumper stamps each phase header with the previous
phase's elapsed time): DUMP 164 s, SPLIT 43 s, INFO 1 s,
DUMP::EXTRA / PROTECTION / REFINE / DVDKEY / HASH each
<1 s. Whole app job ≈3¾ minutes including setup and drive
scan. App verdict:
**"Preservation dump — PASS: redumper finished cleanly."**

**Errors: none.** Redumper's media-error summary, verbatim:
`SCSI: 0 samples`, `C2: 0 samples`, `Q: 194` (subchannel Q
corrections — informational, not media errors). REFINE took
0 s: **zero sectors needed a re-read**, so the 20 retries
were never engaged and there are no error-filled or
zero-filled regions anywhere in the program area.
Protection scan: none found. Split-phase warnings are
drive-capability notes, not disc errors: this drive in
GENERIC configuration cannot read the lead-in / pre-gap /
lead-out edge areas (`incomplete pre-gap 150/150
unavailable`, `lead-in starts with unavailable sector`,
`lead-out ends with unavailable sector`) — all outside the
disc's 262,419-sector program area, which read completely.

**Output** (`~/disc-tools-build/bench-rayman/`, 1.4 GB
total, left in place):

- **51 track bins**, `Rayman (Track 01).bin` …
  `Rayman (Track 51).bin`, totalling **617,209,488 bytes —
  exactly 262,419 × 2352**, the complete disc area with no
  missing or extra sectors. Track 01: 98,043,120 bytes =
  41,685 sectors, matching redumper INFO's sector count.
- `Rayman.cue` (6,030 B) — 51 FILE/TRACK pairs; every bin
  size is an exact multiple of 2352; redumper's own INFO
  phase parsed the split output cleanly
  (**REDUMP.INFO errors: 0**, valid ISO9660 PVD, valid PSX
  block).
- `Rayman.scram` 723,402,264 B, `Rayman.subcode`
  29,526,624 B, `Rayman.state` 180,850,566 B,
  `Rayman.log` 38,607 B, plus `Rayman.toc` / `.fulltoc` /
  `.atip` / `.pma`.
- **Hash/DAT output:** redumper's HASH phase printed the
  full Redump-style DAT block (all 51 tracks, CRC/MD5/SHA1)
  into `Rayman.log`. Track 01: crc `f32c1f20`,
  md5 `8a6071e26694bce5e46c37d9f17abefc`,
  sha1 `ec765ae4ac98d9e335caeaa4624a4d58ddec6782`.

**App wart found (reporting, not data).** The job's second
row reads `Dump image — WARN: redumper finished but no
.bin/.iso was found in the output folder.` Cause:
`_preservation_post` looks only for a single file named
`<image-name>.bin` / `<image-name>.iso` (`Rayman.bin` /
`Rayman.iso`). Redumper's aggregate mode never creates
that file — it creates the per-track split bins plus
`.scram` — so on **every successful preservation dump**
this check WARNs, the app writes no `<name>.hashes.txt`,
and its optional Redump DAT-verdict step is skipped. The
dump itself is complete and correct (redumper's log
carries the full hashes); what is lost is the app's own
hash file and DAT verdict. A future fix should teach the
post-step redumper's split layout (hash the track bins /
parse the DAT block from the run). Recorded as a field
finding; not fixed in this build.

**Status updates (superseding the unproven list above).**

- Acceptance test 5's dump half — *redumper dump of a real
  PS1 disc* — is now **PROVEN on real media**, through the
  packaged app, with a zero-error read. The Redump-match
  half is still unproven via the app (see the wart above);
  the hashes exist in `Rayman.log` for manual DAT
  comparison.
- "Read modes … unrun against real media" — the
  preservation mode is now run and proven; ISO / BIN-CUE /
  damaged-disc reads are still unrun against real media.
- **Still bench-pending, unchanged:** a real **burn**
  (acceptance tests 3 and 4), the **XGD3 layer-break
  read-back of 2084960** (the WH14NS40 does not do XGD3
  overburn — that gate waits for the Sony Optiarc
  AD-5280S-CB-PLUS Duane is ordering), BurnerMax capacity
  reporting (test 7), the pkexec prompt, and
  verify-after-write chaining.

---

## FIELD FIX 3 addendum — 2026-11-21 (split-set post-step + engine signal dispositions)

**The wart, in plain terms.** The bench run above proved the
preservation dump end-to-end — and proved the app's post-dump
step wrong on every success. After redumper finished cleanly,
the job's second row WARNed `Dump image — redumper finished
but no .bin/.iso was found in the output folder.` Cause:
`_preservation_post` looked only for a single `<name>.bin` /
`<name>.iso`. Redumper's aggregate mode never creates that
file — it creates a per-track split set (`<name>.cue` + one
bin per track). Consequences on every successful preservation
dump: the false WARN, no `<name>.hashes.txt` written, and the
optional Redump DAT-verdict step skipped entirely — a specced
feature that could never fire.

**The fix** (`app/disc_engine.py` only). The post-step now
knows redumper's real output layout:

- **Split set is the primary layout.** When `<name>.cue`
  exists, completeness is judged against the cue's FILE
  entries: every listed track bin present and non-empty.
  A genuinely incomplete set still WARNs, now with specifics
  (which files are missing or empty). The legacy single
  `.bin`/`.iso` form is still accepted when one somehow
  exists; the WARN remains for "neither layout found".
- **`<name>.hashes.txt` is written for the split set** — one
  block per track file (size / CRC / MD5 / SHA1). The hashes
  are parsed from the DAT block redumper's own HASH phase
  printed into `<name>.log` — the dump is not re-hashed. A
  log entry whose size disagrees with the file on disk is
  distrusted and that track is hashed directly; a missing or
  unparseable log falls back to hashing the tracks.
- **The DAT verdict works on the set** (`dat_match_set`): a
  Redump game matches when EVERY track file of the dump is
  one of its roms with an equal MD5 or SHA1. PASS names the
  entry and the track count; FAIL reports how many files the
  best entry matched.
- **Signal robustness folded in** (the bench's attempt-2
  finding): engine children now get SIGINT and SIGQUIT reset
  to SIG_DFL via a `preexec_fn` in both spawn sites
  (`_run_stream` and `run_job`). A non-interactive shell runs
  background jobs with those signals ignored, the disposition
  is inherited across exec, and redumper b770 refuses to
  start in that state (`error: signal handler already set
  (signal: 2)`). The engine layer is now immune to how the
  app itself was launched; desktop launches were never
  affected.

**New tests.** Nine added: `PreservationPostTests` (7) —
complete split set → PASS + hashes.txt with the real track
hashes; missing track → WARN naming the file, no hashes.txt;
empty track → WARN naming it; the log's hashes are used
verbatim when its sizes agree (a doctored log entry proves
the source); fallback hashing with no log; DAT verdict PASS
for a split set (all track files); DAT verdict FAIL when one
track differs (best-entry count in the detail). And
`SignalResetTests` (2) — a child spawned through
`_run_stream` with SIGINT/SIGQUIT forced to SIG_IGN in the
parent reports non-ignored/default dispositions, and
`run_job` passes the preexec reset. Suite: **41/41 pass**,
on the VM and on node 2.

**Acceptance (rebuilt packaged AppImage, Xvfb, node 2, real
media).** A fresh preservation job through the packaged app
— same LG WH14NS40, same retail Rayman (USA) disc, output
`~/disc-tools-build/bench-fix3/`, and this time with a DAT
supplied (a test fixture built from the first bench dump's
own log DAT block, wrapped as a `Rayman (USA)` game entry).
The run was launched as a background job of a non-interactive
shell — the exact geometry that killed bench attempt 2 on
the signal issue — and completed clean, which also proves
the signal fix end-to-end. App verdicts:

- `Preservation dump — PASS: redumper finished cleanly.`
- `Dump image — PASS: Rayman.cue + 51 track file(s),
  617209488 bytes total; hashes in Rayman.hashes.txt`
  — **no false WARN.**
- Track 01 INFO rows: crc `f32c1f20`,
  md5 `8a6071e26694bce5e46c37d9f17abefc`,
  sha1 `ec765ae4ac98d9e335caeaa4624a4d58ddec6782` —
  identical to the first bench dump, as expected for the
  same disc.
- `Redump DAT — PASS: Matches Redump entry: Rayman (USA)
  (all 51 track files)` — the DAT-verdict feature fires for
  the first time. (The fixture DAT was built from the dump's
  own hashes, so this proves the app's matching path, not
  the disc against Redump's official DAT — that comparison
  is Duane's, with his DAT file.)
- `Rayman.hashes.txt` on disk carries all 51 track blocks.

Regressions re-run in the same build: S2b — "Ready — all
engines found."; the cue Write job — zero `Cannot open`
errors, failing only at the device (the drive now holds a
pressed retail disc, which is not recordable).

**Status updates (superseding the bench section above).**
Acceptance test 5 is now fully proven via the app — dump
**and** Redump-match path — on real media. Still
bench-pending, unchanged: a real **burn** (tests 3 and 4),
the **XGD3 layer-break read-back of 2084960** (waits for the
Sony Optiarc AD-5280S-CB-PLUS), BurnerMax capacity reporting
(test 7), the pkexec prompt, and verify-after-write
chaining. Nothing published.

---

## BURN BENCH — 2026-11-21 (first real-media burn, node 2)

Same LG **HL-DT-ST BD-RE WH14NS40** (fw 1.05, `/dev/sr0`) on
recomp-node2, now with a **blank Taiyo Yuden CD-R** in it
(cdrdao disk-info: 79:57:72, 359,847 blocks, CD-R empty: yes).
Source image: the verified preservation dump from the bench
above — `~/disc-tools-build/bench-rayman/Rayman.cue` + its
51 track bins (617,209,488 B, 262,419 sectors). Run through
the **packaged AppImage** (sha256 `83263ead…`, the fix-3
build) with the usual smoke harness: one Write job, engine
`auto`, speed **16**, `verify_after` ON, eject OFF.

**The burn.** The app built exactly the field command shape:
`cdrdao write --device /dev/sr0 --speed 16 …/bench-rayman/Rayman.cue`,
run from the cue's folder (the fix-1 geometry). Launched
20:51:28 EST; the whole queue was done by 20:55 — about
**3½ minutes end to end, ≈3¼ of it writing**. cdrdao's own
tail: `Wrote 262419 blocks. Buffer fill min 93%/max 100%. …
Writing finished successfully.` App verdict: **Write
(cdrdao) — PASS: Engine exited 0.** Buffer fill never
dropped below 93% — no underrun was ever approached. The
burn half of acceptance tests 3/4 is now **PROVEN on real
media**.

**FIELD FINDING A — the app's Verify cannot verify a
BIN/CUE burn.** The chained `Verify (after write)` job ran
(the chain itself works — the batch counted 2 jobs), but its
verdict is meaningless for a cue: `_run_verify` treats the
image as one flat binary, so it hashed the **.cue text file
itself** (6,030 bytes; MD5 `82b192a5…`) and byte-compared it
against the raw device, reporting `Sector compare — FAIL:
Target ended early at byte 0 (image is 6030 bytes)`. That
FAIL says nothing about the disc — Verify simply has no cue
awareness, standalone or chained. (For a flat ISO the same
code path is a real sector compare.) A future fix should
teach Verify the cue layout — judge the disc against the
cue's track files, the same lesson fix 3 taught the
preservation post-step.

**Independent proof (not the app).** The burned disc was
read back with redumper's aggregate mode (scratch:
`~/disc-tools-build/bench-burn-verify/`, 20:57:37–21:00:42
EST, exit 0; **SCSI: 0 samples, C2: 0 samples** — the burned
disc reads perfectly) and its per-track hashes compared
against the original dump's (`bench-fix3/Rayman.hashes.txt`):

- All **51 tracks present; every track size identical**.
- **Track 01 (data): BIT-IDENTICAL** — crc `f32c1f20`,
  md5 `8a6071e26694bce5e46c37d9f17abefc`,
  sha1 `ec765ae4ac98d9e335caeaa4624a4d58ddec6782`. The game
  data burned perfectly.
- **Tracks 02–51 (audio): every hash differs — and every
  one is proven byte-swapped, not corrupted and not
  offset-shifted.** For each spot-checked track (02, 03, 10,
  27, 40, 51) the burned bin equals the original bin with
  each 16-bit sample's two bytes exchanged, over the whole
  file (a 400,000-sample window of track 03 matched
  400000/400000 under the swap, and whole-file swap-compare
  passes on all six tracks). The waveform is 100% intact,
  in the opposite byte order.

**FIELD FINDING B — cdrdao burns Redump-convention audio
byte-swapped.** cdrdao's native BIN convention is
big-endian audio — its own `read-cd` output round-trips
with itself — while Redump/redumper BIN/CUE sets (Duane's
whole archive) are little-endian. The app's cdrdao write
therefore burns their CDDA swapped: on a real PS1 this
disc's game is perfect but its CD music would play as
garbled noise. Fix direction (NOT applied in this bench):
pass `--swap` on the cdrdao write for cue burns — cdrdao
swaps audio data only, data tracks are unaffected (track
01 above was written by the same un-swapped run and is
bit-exact). Until that lands, BIN/CUE burns of
Redump-convention sets are **not audio-correct**; burns of
the app's own cdrdao-read dumps are unaffected.

**Status updates.** A real burn through the packaged app:
**PROVEN** (clean completion, no underrun, data track
bit-exact on read-back). Verify-after-write chaining:
**runs mechanically; its BIN/CUE verdict is invalid**
(Finding A). Still bench-pending: the **XGD3 layer-break
read-back of 2084960** (the WH14NS40 does not overburn —
waits for the Sony Optiarc AD-5280S-CB-PLUS), BurnerMax
capacity reporting (test 7), the pkexec prompt — and, new
from this bench, an **audio-correct BIN/CUE burn** after
the Finding-B fix and a **meaningful BIN/CUE verify**
after the Finding-A fix. The burned disc was ejected at
the end of the bench and left in the tray. Nothing
published.

---

## FIELD FIX 4 addendum — 2026-11-21 (burn-bench findings A + B: cue Verify, `--swap`)

Both findings from the BURN BENCH above, fixed in one build.
Deliverable: AppImage sha256 `40f2497f…` (see top), embedded
`disc_engine.py`/`main.py` md5-match the source tree.

**Finding B (ship-blocker) — FIXED: `--swap` on the cdrdao
cue write.** `_write_commands`' cdrdao branch now passes
`--swap`. cdrdao's own man page states the mechanism: raw
audio files are expected in big-endian byte order
('MSBLeft LSBLeft MSBRight LSBRight …') and `--swap`
"reverses the expected byte order". Redump/redumper sets
are little-endian, so the un-swapped write exchanged every
audio sample's bytes on disc while leaving data tracks
alone — exactly the bench signature (track 01 bit-identical,
tracks 02–51 whole-file byte-swapped). `--swap` affects
audio tracks only.

**Read-pair audit (round-trip coherence).** The app's
BIN/CUE Read (`cdrdao read-cd` + `toc2cue`) produces
big-endian audio natively, so the moment the write gained
`--swap`, an app-read dump burned back through the app
would have burned swapped. `read-cd` in cdrdao 1.2.6
accepts `--swap` (verified against its strict parser:
`--bogusopt` errors "Illegal option", `--swap` proceeds to
the drive), and the man page defines it as the same
byte-order reversal for audio files — so `read-cd` now
also carries `--swap`, landing every dump this app makes
in the little-endian convention the write expects. The
on-disc confirmation of the read side is staged as a bench
step (below); two audit read-cd runs attempted tonight
were abandoned — through the USB bridge `read-cd` pulled
~2.3x (~25 min/disc) and only the data track had been
reached when they were stopped. The write side, the one
that was broken, is proven end-to-end below.

**Finding A — FIXED: Verify is cue-aware.** `_run_verify`
now dispatches a `.cue` source to a per-track path (the
fix-3 lesson): the disc is read back with redumper into a
temp folder (cleaned up afterwards), and each track's
read-back is hash-compared (size + MD5 + SHA1) against the
cue's corresponding FILE entry, in order, one PASS/FAIL
row per track. A track-count mismatch, a missing source
track, and a failed read-back each produce explicit FAIL
rows — never a pass. Flat ISO/IMG sources keep the
existing whole-image sector compare untouched. Chained
verify-after-write inherits the cue path automatically
(the chain's job carries the same image). The old
behaviour — hashing the 6,030-byte cue text file and
comparing it against the raw device — is gone.

**Tests: 47/47 pass** on the VM and on node 2 (was 41/41).
New: the cue-write argv carries `--swap` (before the
sheet); the read-cd argv carries `--swap`; and
`CueVerifyTests` (4) driving the real `_run_verify_cue`
with a stubbed read-back — an identical disc passes all
tracks (rc 0); a **byte-swapped audio track FAILs while
the data track PASSes** (rc 1 — the original bench disc's
exact condition, as a fixture); a failed read-back is a
FAIL (rc 2); a missing source track FAILs that track.

**Acceptance (rebuilt packaged AppImage, Xvfb, node 2,
real media).** Bench-plan change: Duane swapped the
original (byte-swapped) bench disc out and loaded a fresh
blank Taiyo Yuden CD-R, so the live swapped-disc verify
was replaced by the fixture test above plus this all-PASS
run. (An earlier standalone Verify attempt against the
blank reported a clean `Disc read-back — FAIL: redumper
could not read the disc (failed to read TOC)` — the
failure path behaving correctly on real media.)

- S2b re-run: **"Ready — all engines found."** Preservation
  post-step regression (engine-level, against the fix-3
  bench output + fixture DAT): **PASS** — split set judged
  complete, `Rayman.hashes.txt` written, "Matches Redump
  entry: Rayman (USA) (all 51 track files)".
- **Re-burn — the `--swap` proof.** Blank confirmed first
  (`cdrdao disk-info`: Taiyo Yuden, 79:57:72, CD-R empty:
  yes). One app Write job: bench-rayman `Rayman.cue`,
  speed **16**, verify-after-write ON. The live burn's
  command line, captured from the running process and the
  app's own record: `cdrdao write --device /dev/sr0
  --speed 16 --swap …/bench-rayman/Rayman.cue`, run from
  the cue's folder. **Write — PASS (engine exited 0).**
- **Chained Verify — all 51 tracks PASS.** The chained
  job reported per-track rows: "All checks passed (51)" —
  Track 01 PASS (md5 `8a6071e2…`) and Tracks 02–51 all
  PASS. The audio is on disc in the correct byte order
  this time.
- **Independent proof (not the app).** A separate
  redumper aggregate read-back of the newly burned disc
  (scratch `~/disc-tools-build/bench-burn-fix4-verify2/`;
  SCSI: 0 samples) compared per-track against the
  reference hashes (`bench-fix3/Rayman.hashes.txt`):
  **51/51 tracks match** — track 01 crc `f32c1f20`,
  md5 `8a6071e26694bce5e46c37d9f17abefc`,
  sha1 `ec765ae4ac98d9e335caeaa4624a4d58ddec6782`, and
  every audio track identical to the source dump. (The
  first independent attempt died at a phase boundary with
  redumper's "signal handler already set" error — the
  fix-3 launch-disposition issue, in a hand-run CLI this
  time; relaunched with dispositions reset, it ran
  clean.) The disc was ejected at the end.
- Drive note: the WH14NS40 on its USB-to-SATA bridge
  dropped off the bus repeatedly during this fix's bench
  (the device vanished from /dev entirely several times
  and always returned); it held steady through the burn
  and both verify read-backs.

**Status updates.** Finding A: **CLOSED** — BIN/CUE
verify is meaningful standalone and chained, proven on
real media in both directions (PASS on a correct disc;
fixture-FAIL on a swapped track; clean FAIL on an
unreadable disc). Finding B: **CLOSED** for
Redump-convention sets — an audio-correct BIN/CUE burn is
now **PROVEN** (51/51). Staged as the next bench step:
the read-cd round-trip confirmation (app BIN/CUE read of
a known disc → burn back through the app → hash
compare). Still bench-pending, unchanged: the **XGD3
layer-break read-back of 2084960** (waits for the Sony
Optiarc AD-5280S-CB-PLUS) and the pkexec prompt. Nothing
published.

---

## ADDENDUM — Network-share browsing + .zip/.7z archive input (2026-11-23)

Duane's requirement (2026-11-22): every app must "see
network shares" — his images live on his home server
(SMB) and local-only pickers force copy-first workarounds.
Scope addition the same night: .zip/.7z ARCHIVE INPUT on
Write/Verify SOURCE selection. Built, unit-tested, and
proven inside the packaged AppImage against localhost
fixtures only — no real server images were pulled, and
the bench drive/optical media were not involved at all.

**Mechanism, Linux.** A "Network..." / "Share..." button
beside every path picker opens a Share dialog (server —
port allowed as `host:port` — plus share name, optional
subpath, optional user/password). The engine resolves the
share to a real local path: already-mounted GVFS mounts
are found by name (including the port-first GVFS form
`smb-share:port=…,server=…,share=…`); otherwise the share
is mounted with `gio mount smb://…` (anonymous mounts use
`--anonymous`) and the resulting GVFS FUSE path is used.
Engines never see an `smb://` URL. Servers, shares and
dialog recents persist in settings; the password never
does — it is typed at gio's prompt and kept nowhere.

**Mechanism, Windows.** The same dialog resolves to the
UNC path (`\\SERVER\share\…`) directly, with a
reachability check; Qt's file dialogs and all engines
accept UNC paths end to end. Source-level support only:
the shipped package is the Linux AppImage, so Windows
UNC runtime proof is honestly listed as bench-pending.

**Credential prompts (family-wide fix, applied here).**
gio is driven through a pty, and only the PENDING prompt
— the text after the last newline — is matched. On
current glib, gio asks "Domain [WORKGROUP]:" FIRST and
"Password:" second, under a header that itself contains
the word "password"; whole-buffer matching types the
password into the Domain field and then hangs (the bug
found by the VB retrofit against a live authenticated
share). Answers are capped; the Domain prompt is answered
"WORKGROUP". A unit test with a stub gio reproducing the
exact prompt order guards the regression.

**Pickers changed.** Write image, Verify image, Verify
compare-against (Share... beside File...), Read output,
Build ISO output, Build Add Files / Add Folder (share
variants), Discovery profile folder — every picker now
has a network route, and plain Browse dialogs reopen at
the current field's folder. Read/dump DESTINATIONS stay
plain folders, per scope.

**Archive input.** The Write/Verify image pickers also
accept `.zip` / `.7z`. When such a job is ADDED (never in
the live preview), the image plus its cue/bin companions
are extracted to LOCAL scratch
(`<data dir>/archive-scratch/<stem>-<hash>/`, reused while
the archive is unchanged) and the job runs on the
extracted copy; the queue row shows "[from <archive>]".
The source archive is never modified and nothing is ever
extracted back onto a share; burning/verifying in place
from inside an archive does not happen. `.zip` uses the
stdlib; `.7z` uses py7zr 1.1.4, bundled into the package
(system `7z` is the fallback). Scratch is cleaned at
startup. An archive with no image inside fails the add
with its contents listed.

**Proof (packaged AppImage, node 2, dummy data only).**
Fixture: real Samba (user-space, port 4447) serving a
guest share and an authenticated share; game set = BIN/CUE
plus the same set inside `game.zip` and `game.7z`.
- Authenticated mount, live: the pty transcript shows the
  Domain prompt answered WORKGROUP, then the Password
  prompt answered, mount in ~0.3–8 s, share contents
  listed through the resolved GVFS path — against two
  independent Samba servers.
- Packaged smoke 1 (Verify): in-app mount + Share dialog
  driven for real; `game.zip` and `game.7z` picked from
  the share through the real file dialog; both extracted
  to local scratch (`.source` markers cite the share
  paths); flat verify of the extracted `.bin` against an
  identical file: **PASS** (sector compare, 352,800 bytes
  identical). The two cue verifies engaged the per-track
  compare on the extracted cues and failed only at disc
  read-back — by design a BIN/CUE verify reads the disc
  with redumper (bench finding A), and the target here
  was a plain file (redumper ioctl error), not a drive.
- Packaged smoke 2 (Write, test mode): `game.zip` picked
  from the share, extracted, and the job ran the preserved
  command form `cdrdao write --device /dev/null
  --simulate --swap <scratch>/game.cue`, cdrdao executing
  from the scratch folder, reading cue+bin with no
  file-open errors, failing only at device setup on
  /dev/null — the fix-1 acceptance pattern.
- Source archives' SHA-256 identical before and after
  every run; settings after the runs list the server/share
  under "network" with no password anywhere.

**Tests.** 73/73 on this VM, and on node 2 under both the
system python and the AppImage's bundled python (47
original + 15 share + 11 archive/prompt tests). Preserved
engine behavior re-verified in the packaged runs above:
cdrdao from the cue's folder, `--swap` on cue write,
cue-aware Verify, redumper b770, first-run engine
downloads + dep offer (setup completes in the proof
homes once engines are present; with engines absent the
app still warns modally before running a queue, as
designed).

**Package.** `DiscTools-x86_64.AppImage` replaced in this
tree: 135,828,672 bytes, sha256
`89e2ccbeadb9c299e627f822c2fc2517f97c176bead89fa7587ffd247caeb12f`
(previous build 129,598,656 bytes, `40f2497f…`). Proof
environment note: node 2 has no system GVFS, so the proof
ran the app inside a user-space GVFS session (private
dbus + gvfsd/gvfsd-fuse from extracted packages); the
FUSE daemon in that hand-rolled stack is crash-prone
under session churn, which the runner absorbed with
mount retries — on a normal desktop install (and in the
app's engine) the GVFS path is the standard one Nautilus
uses. NOT published — releases remain Duane's word.
