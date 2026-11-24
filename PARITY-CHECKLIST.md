# Disc Tools — ImgBurn parity inventory

Build step 1 (per `~/workspace/imgburn-clone-linux-spec.md`): ImgBurn's own
UI / options surface, mapped to Disc Tools modes, engines, and v1 status.
ImgBurn is the design reference only — no code, assets, or text from it.
Statuses: **V1** = in this build · **P2** = phase 2 · **OOS** = out of scope
for v1 per the spec · **N/A** = Windows-only concept, no Linux equivalent
needed.

## ImgBurn's modes (main window mode picker)

| ImgBurn mode | Disc Tools | Engine | Status |
|---|---|---|---|
| Write Image File to Disc | **Write** tab | growisofs / xorriso / cdrdao | V1 |
| Create Image File from Disc | **Read** tab → ISO / BIN-CUE | dd (isosize-bounded) / cdrdao | V1 |
| Verify Disc | **Verify** tab (+ verify-after-write chain) | built-in sector compare + hashes | V1 |
| Write Files/Folders to Disc | **Build** tab → output Disc | xorriso | V1 |
| Create Image File from Files/Folders | **Build** tab → output ISO | xorriso (-as mkisofs) | V1 |
| Discovery | **Discovery** tab | xorriso inquiry/TOC + /dev scan | V1 |
| — (no ImgBurn equivalent) | **Read** → Preservation dump | redumper | V1 (our addition; Redump workflow) |
| — (no ImgBurn equivalent) | **Read** → Damaged-disc mode | GNU ddrescue + mapfile | V1 (our addition) |

## Write mode options

| ImgBurn option | Disc Tools control | Status |
|---|---|---|
| Source image (ISO / BIN-CUE / MDS / ZIP / 7Z) | Image picker — ISO, BIN/CUE, .zip/.7z extracted to local scratch; every picker browses network shares (GVFS on Linux, UNC on Windows) (MDS = P2 via libmirage) | V1 / P2 |
| Destination drive list | Drive combo (Discovery-fed, manual entry allowed) | V1 |
| Write Speed (AWS / MAX / fixed list) | Speed combo: Max, 1x–24x | V1 |
| Copies | Copies spin — Add expands to N queue entries | V1 |
| Test Mode | "Test mode (simulate, no burn)" checkbox | V1 |
| Verify (after write) | "Verify after write" checkbox — chained Verify job | V1 |
| Eject Tray (on completion) | "Eject when finished" checkbox | V1 |
| Delete Image (after successful burn) | — | OOS (we never delete the user's images implicitly) |
| Layer Break: Automatic / Calculate Optimal / User Specified | Layer break: None / XGD2 preset / XGD3 preset / From .dvd / Custom | V1 — presets **BENCH-PROOF-PENDING** (see below) |
| `.dvd` file supplies the layer break | `.dvd` sidecar auto-read when an ISO is picked; explicit .dvd picker too | V1 |
| Perform OPC Before Write | — (drive/engine decides; no user control in libburn/growisofs) | N/A |
| Enable Burn-Proof | Always on in the engines (underrun protection is not optional) | N/A |
| DVD+R Book Type setting (drive advanced) | — | P2 |
| Write engine choice (ImgBurn has one) | Engine combo: Auto / growisofs / xorriso / cdrdao (CUE) | V1 (growisofs is first-class: classic XGD3 break path) |

## Build mode options

| ImgBurn option | Disc Tools control | Status |
|---|---|---|
| Source file/folder list (add / remove) | Source list + Add Files / Add Folder / Remove | V1 |
| Destination: image file or drive | Output radio: ISO file / Disc | V1 |
| File System: ISO9660 / +Joliet / +UDF / combos | Joliet, Rock Ridge, UDF checkboxes (ISO 9660 always) | V1 |
| Volume Label (separate ISO/Joliet/UDF labels in Settings) | Single Volume Label field (xorriso applies it to all) | V1 (simplified, noted) |
| Make Image Bootable — emulation type, load segment, sectors to load, patch boot info table | Bootable checkbox + boot image picker (El Torito, no-emulation, boot info table patched) | V1 (subset: no floppy/hard-disk emulation choice) |
| Advanced restrictions (allow >4 GB files, full pathnames…) | ISO level 3 always (large files OK) | V1 (fixed, noted) |
| DVD-Video authoring (VIDEO_TS → DVD-Video UDF) | — | OOS (xorriso cannot author DVD-Video-spec UDF; spec says noted, not hidden) |

## Read mode options

| ImgBurn option | Disc Tools control | Status |
|---|---|---|
| Source drive | Drive combo | V1 |
| Destination file | Output path (per sub-mode: .iso / .bin+.cue / dump folder+name) | V1 |
| Read Speed | — (engines run at drive speed; redumper/ddrescue manage their own retries) | N/A |
| Ignore Read Errors / Fast Skip Errors | Damaged-disc mode covers this properly (ddrescue mapfile, resumable) | V1 (as its own mode) |
| Create .dvd / cue sidecars | BIN-CUE read produces .bin + .cue (via toc2cue) | V1 |

## Verify mode options

| ImgBurn option | Disc Tools | Status |
|---|---|---|
| Disc vs image file, sector compare | Verify tab: image vs device (device may also be a file, for testing) | V1 |
| Image details: MD5 / SHA1 shown | MD5 + SHA1 + SHA256 computed and shown as result rows | V1 |
| Verify speed | — | N/A |
| Chained after a burn | Write tab "Verify after write" | V1 |

## Discovery mode

| ImgBurn shows | Disc Tools | Status |
|---|---|---|
| Drive: vendor / model / firmware | Inquiry row per drive | V1 |
| Capabilities (read/write feature list) | Media + feature summary from xorriso TOC/inquiry parse | V1 (summary level) |
| Media: type, MID, capacity, free space | Media row incl. **real reported capacity in sectors** | V1 |
| Layer break position on inserted DL media | Shown when the TOC reports it | V1 |
| "No artificial size cap" (ImgBurn + BurnerMax behaviour) | App never caps image size; a flashed drive's reported capacity is honoured as-is | V1 |
| Stock-drive BurnerMax *payload* unlock | — | **P2** — app states plainly it is not there yet |

## ImgBurn Settings dialog (tab by tab)

| ImgBurn settings tab | Disc Tools equivalent | Status |
|---|---|---|
| General | App settings auto-saved (window + last form values) | V1 (subset) |
| Build | Build tab controls | V1 |
| Device | Discovery tab (read-only info) | V1 (subset) |
| File System | Build tab checkboxes | V1 |
| Graph Data | — (burn-graph eye candy) | OOS |
| I/O | Engine layer (SG passthrough via the engines) | N/A |
| Labels | Build tab Volume Label | V1 |
| Queue | The job queue itself (add/remove/clear, failures never kill it) | V1 |
| Read | Read tab controls | V1 |
| Regions | — (no CSS/region handling in v1) | OOS |
| Registry | — | N/A |
| Sounds | — | OOS |
| Write | Write tab controls (speed / test / eject / layer break) | V1 |

## Tools menu extras ImgBurn has

| Feature | Status |
|---|---|
| Erase / format rewritable media | OOS for v1 (not in the spec's mode list) |
| Change volume label of a disc | OOS |
| Create MDS / CUE from files | OOS (CUE creation from BIN happens in Read via toc2cue) |
| Drive tray load / eject | Eject-after-write only; standalone tray control OOS |

## Layer-break gate (spec-named)

Candidates compared for the 360 presets (XGD2 **1913760**, XGD3 **2084960**):

| Engine | Break mechanism | Assessment |
|---|---|---|
| **growisofs** (dvd+rw-tools) | `-use-the-force-luke=break:<sectors>` | The classic Linux XGD3 path; first-class Write engine in this app; presets route here |
| cdrecord (cdrtools) | `layerbreak=<sectors>` | Real cdrtools is rarely packaged (distros ship the wodim fork without feature parity); not a v1 dependency |
| xorriso / libburn | No direct user-settable DVD+R DL break in the shipped versions | Used for plain writes and Build; not the preset path |

The fleet minis have **no burner drive**, so no candidate can be proven here.
The presets ship marked **BENCH-PROOF-PENDING**: they burn through growisofs'
break flag, and the gate closes only when a disc burned on Duane's bench reads
its layer break back as exactly the requested value (acceptance test 4).
