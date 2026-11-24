# Disc Tools

An ImgBurn-class disc app for Linux: build images, burn them
(including dual-layer with a set layer break for Xbox 360
discs), dump discs back to images, and verify burns — the jobs
ImgBurn does on Windows, done here by open-source engines
behind one queue-driven GUI.

## Modes

- **Write** — ISO or BIN/CUE to disc. Drive, engine, speed,
  copies, test mode, verify-after-write, eject. Layer break:
  XGD2 / XGD3 presets, a `.dvd` sidecar (read automatically),
  or a custom sector count.
- **Build** — files/folders to an ISO file or straight to
  disc. ISO 9660 + Joliet / Rock Ridge / UDF, volume label,
  El Torito bootable images.
- **Read** — disc to ISO; disc to BIN/CUE; **Preservation
  dump** via redumper (dump + MD5/SHA1/SHA256 + optional
  Redump DAT verdict); **Damaged-disc mode** via GNU ddrescue
  with a resumable mapfile.
- **Verify** — sector-by-sector compare of an image against a
  disc, plus MD5 / SHA1 / SHA256 of the image. Standalone or
  chained after a burn.
- **Discovery** — drives, media, and the capacity the drive
  itself reports. This app never imposes an artificial
  image-size cap: if a BurnerMax-flashed drive reports full
  XGD3 capacity, the app honours it, exactly like ImgBurn
  does with those drives.

Xbox 360 workflow: check the ISO with the ABGX360 GUI first,
then burn it here, then Verify.

## Network shares

Every file/folder picker has a **Network...** button beside
Browse.... It asks for a server + share (servers, shares and
recent shares are remembered; passwords are never saved),
mounts the share, and opens the picker in the mounted folder:

- **Linux** — the share is mounted through GVFS (`gio mount
  smb://server/share`) and the app uses the real FUSE path
  under `$XDG_RUNTIME_DIR/gvfs`. The engines only ever see a
  plain local path, so cue-relative track lookups and every
  other proven behaviour are unchanged.
- **Windows** — the `\\server\share` UNC path is used
  directly; Windows resolves it natively.

Images can be picked straight off the server for Write /
Verify, Build sources can come from a share, and Read dumps
can be saved back onto one.

## First run

The app sets itself up. Distro-package engines (xorriso,
growisofs, cdrdao, GNU ddrescue) are detected; if one is
missing you get a one-click install offer (one password
prompt via pkexec) with a clean manual fallback — never a
dead-end. redumper is downloaded from its GitHub releases
and verified against a pinned SHA-256. Everything lives
under `~/.local/share/disc-tools/`; later launches skip
setup.

## Honest limits

- The 360 layer-break presets burn through growisofs'
  `-use-the-force-luke=break:<sectors>` flag and are
  **BENCH-PROOF-PENDING**: they are proven only when a disc
  burned on a real drive reads its layer break back as
  exactly the requested value.
- The stock-drive BurnerMax *payload* unlock is phase 2 and
  is **not in this build** — the app says so in the
  Discovery tab. Flashed drives need none of it and work
  from v1.
- DVD-Video authoring is out of scope (xorriso cannot
  author DVD-Video-spec UDF). MDS/MDF images are phase 2
  (libmirage).

## Engines and credits

- **xorriso** / libburnia (GNU, Thomas Schmitt) — Build,
  plain Write, Discovery.
- **growisofs** (dvd+rw-tools) — Write engine; the 360
  layer-break path.
- **cdrdao** — BIN/CUE write and read.
- **GNU ddrescue** (Antonio Diaz Diaz) — damaged-disc reads.
- **redumper** (superg, GPLv3) — preservation dumps.

ImgBurn is the design reference only — no code, assets, or
text from it.
