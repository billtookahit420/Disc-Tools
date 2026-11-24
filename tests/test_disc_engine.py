"""Unit tests for the Disc Tools engine layer (stdlib unittest).

Covers: .dvd parsing, dep table/info shape (incl. the gddrescue
package-name trap), command construction for every mode, verify
compare, DAT matching, and result parsing. No GUI, no drives.
"""
import importlib.util
import os
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent
                       / "src" / "DiscTools" / "app"))

import disc_engine as engine  # noqa: E402


class DvdParseTests(unittest.TestCase):
    def test_two_line_format(self):
        self.assertEqual(
            engine.parse_dvd_text("2084960\nGame.iso\n"),
            2084960)

    def test_layerbreak_prefix(self):
        self.assertEqual(
            engine.parse_dvd_text("LayerBreak=1913760\n"),
            1913760)

    def test_no_break(self):
        self.assertIsNone(engine.parse_dvd_text("Game.iso\n"))

    def test_dvd_for_image(self):
        with tempfile.TemporaryDirectory() as td:
            iso = Path(td) / "Game.iso"
            iso.write_bytes(b"x")
            (Path(td) / "Game.dvd").write_text(
                "2084960\nGame.iso\n")
            self.assertEqual(engine.dvd_for_image(str(iso)),
                             2084960)
            self.assertIsNone(
                engine.dvd_for_image(str(Path(td) / "Other.iso")))


class DepTests(unittest.TestCase):
    def test_table_covers_four_engines(self):
        keys = [c[0] for c in engine.check_deps()]
        self.assertEqual(keys, ["xorriso", "growisofs",
                                "cdrdao", "ddrescue"])

    def test_ddrescue_apt_package_is_gddrescue(self):
        row = [c for c in engine.check_deps()
               if c[0] == "ddrescue"][0]
        self.assertEqual(row[3], "gddrescue")
        self.assertEqual(row[4], "ddrescue")

    def test_growisofs_apt_package_is_dvd_rw_tools(self):
        row = [c for c in engine.check_deps()
               if c[0] == "growisofs"][0]
        self.assertEqual(row[3], "dvd+rw-tools")

    def test_dep_info_shape(self):
        fake = [("ddrescue", "GNU ddrescue (damaged-disc reads)",
                 False, "gddrescue", "ddrescue")]
        info = engine.dep_info(fake)
        for key in ("missing", "packages", "alt_packages",
                    "family", "manual_command", "manual_text"):
            self.assertIn(key, info)
        self.assertIn("gddrescue", info["packages"])

    def test_redumper_pin_shape(self):
        self.assertEqual(len(engine.REDUMPER_SHA256), 64)
        self.assertIn(engine.REDUMPER_BUILD, engine.REDUMPER_URL)


class BuildCommandTests(unittest.TestCase):
    def _job(self, **kw):
        job = {"kind": "build", "sources": ["/data/files"],
               "output": "iso", "iso_path": "/tmp/out.iso",
               "label": "MYDISC", "joliet": True,
               "rockridge": True, "udf": False,
               "bootable": False, "boot_image": ""}
        job.update(kw)
        return job

    def test_iso_basic(self):
        argv = engine.build_commands(self._job())[0]
        self.assertEqual(argv[:3], ["xorriso", "-as", "mkisofs"])
        self.assertIn("-J", argv)
        self.assertIn("-r", argv)
        self.assertIn("MYDISC", argv)
        self.assertNotIn("-udf", argv)

    def test_iso_udf(self):
        argv = engine.build_commands(self._job(udf=True))[0]
        self.assertIn("-udf", argv)

    def test_disc_udf_two_step(self):
        cmds = engine.build_commands(self._job(
            output="disc", device="/dev/sr0", udf=True))
        self.assertEqual(len(cmds), 2)
        self.assertIn("-udf", cmds[0])
        self.assertIn("dev=/dev/sr0", cmds[1])

    def test_disc_native(self):
        cmds = engine.build_commands(self._job(
            output="disc", device="/dev/sr0"))
        self.assertEqual(len(cmds), 1)
        self.assertEqual(cmds[0][1], "-dev")


class WriteCommandTests(unittest.TestCase):
    def _job(self, **kw):
        job = {"kind": "write", "image": "/tmp/game.iso",
               "device": "/dev/sr0", "engine": "auto",
               "speed": "max", "layerbreak": None,
               "test": False, "verify_after": False,
               "eject": False}
        job.update(kw)
        return job

    def test_plain_iso_xorriso(self):
        argv = engine.build_commands(self._job())[0]
        self.assertEqual(argv[:3], ["xorriso", "-as", "cdrecord"])
        self.assertIn("-data", argv)

    def test_xgd3_preset_routes_to_growisofs_break(self):
        argv = engine.build_commands(
            self._job(layerbreak=engine.XGD3_BREAK))[0]
        self.assertEqual(argv[0], "growisofs")
        self.assertIn("-use-the-force-luke=break:2084960", argv)
        self.assertIn("/dev/sr0=/tmp/game.iso", argv)

    def test_xgd2_preset_value(self):
        argv = engine.build_commands(
            self._job(layerbreak=engine.XGD2_BREAK))[0]
        self.assertIn("-use-the-force-luke=break:1913760", argv)

    def test_growisofs_engine_no_break(self):
        argv = engine.build_commands(
            self._job(engine="growisofs"))[0]
        self.assertEqual(argv[0], "growisofs")
        self.assertFalse(any("break:" in a for a in argv))

    def test_test_mode_flags(self):
        argv = engine.build_commands(self._job(test=True))[0]
        self.assertIn("-dummy", argv)
        argv = engine.build_commands(
            self._job(layerbreak=engine.XGD3_BREAK,
                      test=True))[0]
        self.assertIn("-dry-run", argv)

    def test_cue_routes_to_cdrdao(self):
        argv = engine.build_commands(
            self._job(image="/tmp/game.cue"))[0]
        self.assertEqual(argv[0], "cdrdao")
        self.assertIn("--device", argv)

    def test_cue_write_swaps_audio(self):
        # Field fix 4 (burn-bench finding B): cdrdao's BIN
        # convention is big-endian audio, Redump/redumper sets
        # are little-endian. The bench burn without --swap
        # came back with track 01 bit-identical and all 50
        # audio tracks byte-swapped.
        argv = engine.build_commands(
            self._job(image="/tmp/game.cue"))[0]
        self.assertIn("--swap", argv)
        self.assertLess(argv.index("--swap"),
                        argv.index("/tmp/game.cue"))


class ReadCommandTests(unittest.TestCase):
    def test_preservation_argv(self):
        job = {"kind": "read", "sub": "preservation",
               "device": "/dev/sr0", "output_dir": "/tmp/dumps",
               "image_name": "game", "media": "cd",
               "retries": 20, "dat_path": ""}
        argv = engine.build_commands(job)[0]
        self.assertTrue(argv[0].endswith("redumper"))
        self.assertTrue(argv[1].startswith("--"),
                        "no subcommand token may follow the "
                        "redumper binary; got %r" % argv[1])
        self.assertIn("--drive=/dev/sr0", argv)
        self.assertIn("--image-name=game", argv)

    def test_damaged_two_passes_with_mapfile(self):
        job = {"kind": "read", "sub": "damaged",
               "device": "/dev/sr0", "output": "/tmp/o.iso",
               "mapfile": "/tmp/o.iso.map"}
        cmds = engine.build_commands(job)
        self.assertEqual(len(cmds), 2)
        self.assertIn("-n", cmds[0])
        self.assertTrue(all("/tmp/o.iso.map" in c for c in cmds))

    def test_bincue_two_commands(self):
        job = {"kind": "read", "sub": "bincue",
               "device": "/dev/sg0", "output_base": "/tmp/g"}
        cmds = engine.build_commands(job)
        self.assertEqual(cmds[0][0], "cdrdao")
        self.assertEqual(cmds[1][0], "toc2cue")

    def test_bincue_read_swaps_audio(self):
        # Round-trip coherence with the --swap cue write
        # (field fix 4): read-cd must land its dump in the
        # same little-endian convention, or a dump read by
        # the app and burned back through it would burn
        # byte-swapped.
        job = {"kind": "read", "sub": "bincue",
               "device": "/dev/sg0", "output_base": "/tmp/g"}
        argv = engine.build_commands(job)[0]
        self.assertIn("--swap", argv)


class RedumperCommandTests(unittest.TestCase):
    # Command list captured 2026-11-21 from the pinned redumper
    # b770 binary itself (`redumper --help`, build b770). b770
    # has NO cd/dvd/bd command -- a bare invocation runs the
    # default aggregate "disc" mode and the disc type is
    # auto-detected (override: --disc-type=CD|DVD|BLURAY|
    # BLURAY-R|HD-DVD). Field bug, 2026-11-21: the app passed
    # "cd" as a subcommand and b770 answered "error: unknown
    # command (command: cd)" before touching the drive.
    B770_COMMANDS = {
        "disc", "dump", "spiral::dump", "dump::extra", "refine",
        "dvdkey", "eject", "dvdisokey", "protection", "split",
        "hash", "info", "skeleton", "flash::mt1339",
        "flash::mt1959", "flash::sd616", "flash::plextor",
        "tools::fixmsf", "tools::fixmsf::shift", "tools::trim",
    }
    B770_DISC_TYPES = {"CD", "DVD", "BLURAY", "BLURAY-R",
                       "HD-DVD"}

    def _preservation_argv(self, **kw):
        job = {"kind": "read", "sub": "preservation",
               "device": "/dev/sr0", "output_dir": "/tmp/dumps",
               "image_name": "game", "retries": 20,
               "dat_path": ""}
        job.update(kw)
        return engine.build_commands(job)[0]

    def _assert_valid_b770_argv(self, argv):
        self.assertTrue(argv[0].endswith("redumper"))
        rest = argv[1:]
        if rest and not rest[0].startswith("-"):
            self.assertIn(
                rest[0], self.B770_COMMANDS,
                "subcommand %r is not a redumper b770 command"
                % rest[0])
        for a in rest:
            if a.startswith("--disc-type="):
                self.assertIn(a.split("=", 1)[1],
                              self.B770_DISC_TYPES)

    def test_preservation_has_no_subcommand(self):
        argv = self._preservation_argv()
        self.assertTrue(
            argv[1].startswith("--"),
            "preservation dump must invoke redumper with no "
            "subcommand (default disc mode); got %r" % argv[1])
        self.assertNotIn("cd", argv)
        self.assertNotIn("dvd", argv)

    def test_preservation_ignores_legacy_media_pick(self):
        # Regression guard for the field failure: a job dict
        # still carrying the old GUI "media" value must build a
        # bare redumper invocation for either pick.
        for media in ("cd", "dvd"):
            argv = self._preservation_argv(media=media)
            self.assertTrue(argv[1].startswith("--"))
            self._assert_valid_b770_argv(argv)

    def test_every_redumper_command_uses_b770_tokens(self):
        jobs = [
            {"kind": "read", "sub": "preservation",
             "device": "/dev/sr0", "output_dir": "/tmp/d",
             "image_name": "g", "retries": 5, "dat_path": ""},
        ]
        for job in jobs:
            for argv in engine.build_commands(job):
                if argv[0].endswith("redumper"):
                    self._assert_valid_b770_argv(argv)


class VerifyTests(unittest.TestCase):
    def test_compare_match_and_mismatch(self):
        with tempfile.TemporaryDirectory() as td:
            img = Path(td) / "a.iso"
            img.write_bytes(b"A" * 5000 + b"B" * 3000)
            same = Path(td) / "b.iso"
            same.write_bytes(b"A" * 5000 + b"B" * 3000)
            rc, rows = engine.run_job(
                {"kind": "verify", "image": str(img),
                 "device": str(same)}, lambda _m: None)
            self.assertEqual(rc, 0)
            self.assertTrue(any(
                r["check"] == "Sector compare"
                and r["result"] == "PASS" for r in rows))
            diff = Path(td) / "c.iso"
            diff.write_bytes(b"A" * 5000 + b"X" * 3000)
            rc, rows = engine.run_job(
                {"kind": "verify", "image": str(img),
                 "device": str(diff)}, lambda _m: None)
            self.assertEqual(rc, 1)
            self.assertTrue(any(
                r["result"] == "FAIL" for r in rows))


class CueVerifyTests(unittest.TestCase):
    """Field fix 4, 2026-11-21 (burn-bench finding A): the
    flat verify hashed the .cue text file itself and compared
    it against the raw device, so a BIN/CUE burn could not be
    verified at all. Verify must judge a cue set per track,
    hash against hash -- and it must FAIL a track whose bytes
    were byte-swapped on the disc, which is exactly the bench
    disc's condition (finding B: data bit-identical, audio
    swapped). The redumper read-back is stubbed with fixture
    track sets; the real-disc verdict is the bench's job."""

    def _swap(self, data):
        out = bytearray(len(data))
        out[0::2] = data[1::2]
        out[1::2] = data[0::2]
        return bytes(out)

    def _source_set(self, td):
        d = Path(td) / "src"
        d.mkdir()
        t1 = bytes(range(256)) * 40
        t2 = bytes(range(255, -1, -1)) * 40
        (d / "Game (Track 01).bin").write_bytes(t1)
        (d / "Game (Track 02).bin").write_bytes(t2)
        cue = d / "Game.cue"
        cue.write_text(
            'FILE "Game (Track 01).bin" BINARY\n'
            "  TRACK 01 MODE2/2352\n"
            "    INDEX 01 00:00:00\n"
            'FILE "Game (Track 02).bin" BINARY\n'
            "  TRACK 02 AUDIO\n"
            "    INDEX 01 00:00:00\n")
        return d, cue, t1, t2

    def _fake_readback(self, t1_back, t2_back, rc=0):
        def fake(_target, outdir, name, _log):
            if rc == 0:
                (outdir / (name + " (Track 01).bin")).write_bytes(
                    t1_back)
                (outdir / (name + " (Track 02).bin")).write_bytes(
                    t2_back)
                (outdir / (name + ".cue")).write_text(
                    'FILE "%s (Track 01).bin" BINARY\n'
                    "  TRACK 01 MODE2/2352\n"
                    "    INDEX 01 00:00:00\n"
                    'FILE "%s (Track 02).bin" BINARY\n'
                    "  TRACK 02 AUDIO\n"
                    "    INDEX 01 00:00:00\n" % (name, name))
            return rc, (None if rc == 0
                        else "error: fake failure")
        return fake

    def _run(self, td, cue, fake):
        from unittest import mock
        with mock.patch.object(engine, "redumper_ready",
                               lambda: True), \
                mock.patch.object(engine, "DATA_DIR", Path(td)), \
                mock.patch.object(engine, "_redumper_readback",
                                  fake):
            return engine.run_job(
                {"kind": "verify", "image": str(cue),
                 "device": "/dev/sr0"}, lambda _m: None)

    def _row(self, rows, check):
        return [r for r in rows if r["check"] == check][0]

    def test_identical_disc_passes_all_tracks(self):
        with tempfile.TemporaryDirectory() as td:
            _d, cue, t1, t2 = self._source_set(td)
            rc, rows = self._run(
                td, cue, self._fake_readback(t1, t2))
            self.assertEqual(rc, 0)
            self.assertEqual(
                self._row(rows, "Track 01")["result"], "PASS")
            self.assertEqual(
                self._row(rows, "Track 02")["result"], "PASS")

    def test_byte_swapped_track_fails_data_track_passes(self):
        with tempfile.TemporaryDirectory() as td:
            _d, cue, t1, t2 = self._source_set(td)
            rc, rows = self._run(
                td, cue, self._fake_readback(t1, self._swap(t2)))
            self.assertEqual(rc, 1)
            self.assertEqual(
                self._row(rows, "Track 01")["result"], "PASS")
            self.assertEqual(
                self._row(rows, "Track 02")["result"], "FAIL")

    def test_readback_failure_is_a_fail_not_a_pass(self):
        with tempfile.TemporaryDirectory() as td:
            _d, cue, _t1, _t2 = self._source_set(td)
            rc, rows = self._run(
                td, cue, self._fake_readback(b"", b"", rc=255))
            self.assertEqual(rc, 2)
            self.assertEqual(
                self._row(rows, "Disc read-back")["result"],
                "FAIL")

    def test_missing_source_track_fails_that_track(self):
        with tempfile.TemporaryDirectory() as td:
            d, cue, t1, t2 = self._source_set(td)
            (d / "Game (Track 02).bin").unlink()
            rc, rows = self._run(
                td, cue, self._fake_readback(t1, t2))
            self.assertEqual(rc, 1)
            row = self._row(rows, "Track 02")
            self.assertEqual(row["result"], "FAIL")
            self.assertIn("missing", row["detail"])


class DatMatchTests(unittest.TestCase):
    DAT = """<?xml version="1.0"?>
<datafile><game name="Test Game (USA)"><rom name="g.bin"
 md5="d41d8cd98f00b204e9800998ecf8427e"
 sha1="da39a3ee5e6b4b0d3255bfef95601890afd80709"/></game>
</datafile>"""

    def test_empty_file_hashes_match(self):
        with tempfile.TemporaryDirectory() as td:
            dat = Path(td) / "t.dat"
            dat.write_text(self.DAT)
            empty = Path(td) / "e.bin"
            empty.write_bytes(b"")
            hashes = engine.hash_file(empty)
            matched, name = engine.dat_match(str(dat), hashes)
            self.assertTrue(matched)
            self.assertEqual(name, "Test Game (USA)")
            matched, _ = engine.dat_match(
                str(dat), {"md5": "0" * 32, "sha1": "0" * 40})
            self.assertFalse(matched)


class ParseResultsTests(unittest.TestCase):
    def test_write_layerbreak_row_is_pending(self):
        job = {"kind": "write", "image": "/tmp/g.iso",
               "device": "/dev/sr0", "engine": "auto",
               "speed": "max", "layerbreak": engine.XGD3_BREAK,
               "test": False, "verify_after": False,
               "eject": False}
        rows = engine.parse_results(job, "", 0)
        lb = [r for r in rows if r["check"] == "Layer break"]
        self.assertTrue(lb)
        self.assertIn("BENCH-PROOF-PENDING", lb[0]["detail"])

    def test_preflight_missing_image(self):
        rows = engine._preflight(
            {"kind": "write", "image": "/nonexistent/x.iso",
             "device": "/dev/sr0"})
        self.assertTrue(any(r["result"] == "FAIL" for r in rows))


class CueCwdTests(unittest.TestCase):
    """Regression for the 2026-11-21 field failure: cdrdao
    resolves a cue sheet's relative FILE entries against the
    process cwd, so a Write job must run cdrdao from the cue's
    own folder, not from wherever the app was launched."""

    def _cue_set(self, td):
        d = Path(td) / "disc"
        d.mkdir()
        (d / "track01.bin").write_bytes(b"\0" * 2352 * 4)
        (d / "track02.bin").write_bytes(b"\0" * 2352 * 4)
        cue = d / "game.cue"
        cue.write_text(
            'FILE "track01.bin" BINARY\n'
            "  TRACK 01 AUDIO\n"
            "    INDEX 01 00:00:00\n"
            'FILE "track02.bin" BINARY\n'
            "  TRACK 02 AUDIO\n"
            "    INDEX 01 00:00:00\n")
        return d, cue

    def _write_job(self, cue):
        return {"kind": "write", "image": str(cue),
                "device": "/dev/sr0", "engine": "auto",
                "speed": "max", "layerbreak": None,
                "test": False, "verify_after": False,
                "eject": False}

    def test_cdrdao_write_runs_from_cue_folder(self):
        import os
        from unittest import mock
        with tempfile.TemporaryDirectory() as td:
            d, cue = self._cue_set(td)
            elsewhere = Path(td) / "elsewhere"
            elsewhere.mkdir()
            calls = []

            class FakePopen:
                def __init__(self, argv, **kw):
                    calls.append((argv, kw.get("cwd")))
                    self.stdout = iter([])
                    self.returncode = 0

                def wait(self):
                    return 0

            old = os.getcwd()
            try:
                os.chdir(elsewhere)
                with mock.patch.object(engine.subprocess, "Popen",
                                       FakePopen):
                    rc, _rows = engine.run_job(
                        self._write_job(cue), lambda _m: None)
            finally:
                os.chdir(old)
            self.assertEqual(rc, 0)
            self.assertEqual(len(calls), 1)
            self.assertEqual(calls[0][0][0], "cdrdao")
            self.assertEqual(calls[0][1], str(d.resolve()))

    def test_bincue_read_runs_from_output_folder(self):
        job = {"kind": "read", "sub": "bincue",
               "device": "/dev/sg0", "output_base": "/tmp/x/g"}
        self.assertEqual(engine.command_cwds(job),
                         ["/tmp/x", "/tmp/x"])

    def test_plain_iso_write_keeps_inherited_cwd(self):
        job = self._write_job("/tmp/game.iso")
        self.assertEqual(engine.command_cwds(job), [None])


class PreservationPostTests(unittest.TestCase):
    """Field fix 3, 2026-11-21: redumper's aggregate mode
    writes a split set (<name>.cue + one bin per track), never
    a single <name>.bin/.iso. The post-step originally looked
    only for the single file, so the fleet's first real-media
    dump (Rayman (USA), 51 tracks, zero errors) still got a
    false "no .bin/.iso was found" WARN, no <name>.hashes.txt,
    and no Redump DAT verdict. The post-step must now accept
    the split set, write hashes from redumper's own log DAT
    block, and verdict the set against a DAT per track file."""

    def _rom_line(self, fname, data):
        import hashlib as hl
        import zlib as zl
        return ('<rom name="%s" size="%d" crc="%08x" md5="%s" '
                'sha1="%s" />' % (
                    fname, len(data),
                    zl.crc32(data) & 0xFFFFFFFF,
                    hl.md5(data).hexdigest(),
                    hl.sha1(data).hexdigest()))

    def _split_set(self, td, tracks=3, drop=None, empty=None,
                   with_log=True):
        d = Path(td)
        names = []
        cue_lines = []
        rom_lines = []
        for i in range(1, tracks + 1):
            fname = "Game (Track %02d).bin" % i
            names.append(fname)
            data = bytes([i]) * (2352 * (10 + i))
            if drop != fname:
                (d / fname).write_bytes(
                    b"" if empty == fname else data)
            cue_lines.append(
                'FILE "%s" BINARY\n  TRACK %02d AUDIO\n'
                "    INDEX 01 00:00:00\n" % (fname, i))
            rom_lines.append(self._rom_line(fname, data))
        (d / "Game.cue").write_text("".join(cue_lines))
        if with_log:
            (d / "Game.log").write_text(
                "*** HASH\n\ndat:\n" + "\n".join(rom_lines)
                + "\n")
        return d, names, rom_lines

    def _job(self, td, **kw):
        job = {"kind": "read", "sub": "preservation",
               "device": "/dev/sr0", "output_dir": str(td),
               "image_name": "Game", "retries": 20,
               "dat_path": ""}
        job.update(kw)
        return job

    def _dump_row(self, rows):
        return [r for r in rows
                if r["check"] == "Dump image"][0]

    def test_complete_split_set_passes_and_writes_hashes(self):
        import hashlib as hl
        with tempfile.TemporaryDirectory() as td:
            d, names, _roms = self._split_set(td)
            rows = engine._preservation_post(
                self._job(td), lambda _m: None)
            self.assertEqual(self._dump_row(rows)["result"],
                             "PASS")
            self.assertFalse(
                any(r["result"] == "WARN" for r in rows))
            hashes = (d / "Game.hashes.txt").read_text()
            for n in names:
                self.assertIn(n, hashes)
            data = (d / names[0]).read_bytes()
            self.assertIn(hl.md5(data).hexdigest(), hashes)
            self.assertIn(hl.sha1(data).hexdigest(), hashes)

    def test_missing_track_warns_with_specifics(self):
        with tempfile.TemporaryDirectory() as td:
            d, _names, _roms = self._split_set(
                td, drop="Game (Track 02).bin")
            rows = engine._preservation_post(
                self._job(td), lambda _m: None)
            row = self._dump_row(rows)
            self.assertEqual(row["result"], "WARN")
            self.assertIn("Game (Track 02).bin", row["detail"])
            self.assertIn("missing", row["detail"])
            self.assertFalse((d / "Game.hashes.txt").exists())

    def test_empty_track_warns_with_specifics(self):
        with tempfile.TemporaryDirectory() as td:
            _d, _names, _roms = self._split_set(
                td, empty="Game (Track 03).bin")
            rows = engine._preservation_post(
                self._job(td), lambda _m: None)
            row = self._dump_row(rows)
            self.assertEqual(row["result"], "WARN")
            self.assertIn("Game (Track 03).bin", row["detail"])
            self.assertIn("empty", row["detail"])

    def test_log_hashes_are_used_not_recomputed(self):
        # The log's DAT block is the run's own hash record: when
        # its size agrees with the file, its hashes are written
        # verbatim (no 1.4 GB re-hash). A doctored log entry
        # proves which source the hashes file came from.
        with tempfile.TemporaryDirectory() as td:
            d, names, _roms = self._split_set(td, with_log=False)
            data = (d / names[0]).read_bytes()
            fake = ('<rom name="%s" size="%d" crc="deadbeef" '
                    'md5="%s" sha1="%s" />'
                    % (names[0], len(data), "f" * 32, "e" * 40))
            rest = [self._rom_line(n, (d / n).read_bytes())
                    for n in names[1:]]
            (d / "Game.log").write_text(
                "dat:\n" + "\n".join([fake] + rest) + "\n")
            rows = engine._preservation_post(
                self._job(td), lambda _m: None)
            self.assertEqual(self._dump_row(rows)["result"],
                             "PASS")
            hashes = (d / "Game.hashes.txt").read_text()
            self.assertIn("deadbeef", hashes)
            self.assertIn("f" * 32, hashes)

    def test_fallback_hashing_without_log(self):
        import hashlib as hl
        import zlib as zl
        with tempfile.TemporaryDirectory() as td:
            d, names, _roms = self._split_set(td, with_log=False)
            rows = engine._preservation_post(
                self._job(td), lambda _m: None)
            self.assertEqual(self._dump_row(rows)["result"],
                             "PASS")
            hashes = (d / "Game.hashes.txt").read_text()
            data = (d / names[1]).read_bytes()
            self.assertIn(hl.md5(data).hexdigest(), hashes)
            self.assertIn("%08x" % (zl.crc32(data) & 0xFFFFFFFF),
                          hashes)

    def _write_dat(self, td, rom_lines, name="Game (USA)"):
        dat = Path(td) / "test.dat"
        dat.write_text(
            '<?xml version="1.0"?>\n<datafile>'
            '<game name="%s">' % name
            + "\n".join(rom_lines) + "</game></datafile>")
        return dat

    def test_dat_verdict_fires_for_split_set(self):
        with tempfile.TemporaryDirectory() as td:
            _d, _names, rom_lines = self._split_set(td)
            dat = self._write_dat(td, rom_lines)
            rows = engine._preservation_post(
                self._job(td, dat_path=str(dat)),
                lambda _m: None)
            verdict = [r for r in rows
                       if r["check"] == "Redump DAT"]
            self.assertEqual(len(verdict), 1)
            self.assertEqual(verdict[0]["result"], "PASS")
            self.assertIn("Game (USA)", verdict[0]["detail"])
            self.assertIn("3 track files", verdict[0]["detail"])

    def test_dat_verdict_fails_when_a_track_differs(self):
        with tempfile.TemporaryDirectory() as td:
            d, names, rom_lines = self._split_set(td)
            dat = self._write_dat(td, rom_lines)
            victim = d / names[1]
            victim.write_bytes(victim.read_bytes()[:2352])
            rows = engine._preservation_post(
                self._job(td, dat_path=str(dat)),
                lambda _m: None)
            self.assertEqual(self._dump_row(rows)["result"],
                             "PASS")
            verdict = [r for r in rows
                       if r["check"] == "Redump DAT"][0]
            self.assertEqual(verdict["result"], "FAIL")
            self.assertIn("matched 2", verdict["detail"])


class SignalResetTests(unittest.TestCase):
    """Bench finding, 2026-11-21: launched as a background job
    of a non-interactive shell, the app inherits SIGINT/SIGQUIT
    as SIG_IGN, and redumper b770 refuses to start ("error:
    signal handler already set (signal: 2)"). Engine children
    must get both signals reset to SIG_DFL before exec."""

    def test_run_stream_child_sees_default_dispositions(self):
        import signal
        old_int = signal.getsignal(signal.SIGINT)
        old_quit = signal.getsignal(signal.SIGQUIT)
        lines = []
        try:
            signal.signal(signal.SIGINT, signal.SIG_IGN)
            signal.signal(signal.SIGQUIT, signal.SIG_IGN)
            rc = engine._run_stream(
                [sys.executable, "-c",
                 "import signal; "
                 "print(signal.getsignal(signal.SIGINT)"
                 " == signal.SIG_IGN); "
                 "print(int(signal.getsignal("
                 "signal.SIGQUIT)))"],
                Path(tempfile.gettempdir()), lines.append)
        finally:
            signal.signal(signal.SIGINT, old_int)
            signal.signal(signal.SIGQUIT, old_quit)
        self.assertEqual(rc, 0)
        # SIGINT: a Python child that inherits SIG_IGN keeps it;
        # one reset to SIG_DFL gets Python's own handler instead
        # -- either way it must NOT report ignored. SIGQUIT is
        # never touched by Python, so it shows the raw reset:
        # 0 == SIG_DFL.
        self.assertIn("False", lines)
        self.assertIn("0", lines)

    def test_run_job_passes_the_preexec_reset(self):
        from unittest import mock
        with tempfile.TemporaryDirectory() as td:
            d = Path(td)
            (d / "t.bin").write_bytes(b"\0" * 2352)
            cue = d / "game.cue"
            cue.write_text(
                'FILE "t.bin" BINARY\n  TRACK 01 AUDIO\n'
                "    INDEX 01 00:00:00\n")
            seen = {}

            class FakePopen:
                def __init__(self, argv, **kw):
                    seen.update(kw)
                    self.stdout = iter([])
                    self.returncode = 0

                def wait(self):
                    return 0

            job = {"kind": "write", "image": str(cue),
                   "device": "/dev/sr0", "engine": "auto",
                   "speed": "max", "layerbreak": None,
                   "test": False, "verify_after": False,
                   "eject": False}
            with mock.patch.object(engine.subprocess, "Popen",
                                   FakePopen):
                rc, _rows = engine.run_job(job, lambda _m: None)
            self.assertEqual(rc, 0)
            self.assertIs(seen.get("preexec_fn"),
                          engine._reset_engine_signals)


class ShareRefTests(unittest.TestCase):
    def test_smb_url_ref(self):
        self.assertEqual(
            engine.parse_share_ref("smb://192.168.0.102/PS1 ARCHIVE"),
            {"server": "192.168.0.102", "share": "PS1 ARCHIVE",
             "subpath": ""})

    def test_smb_url_ref_with_subpath(self):
        self.assertEqual(
            engine.parse_share_ref("smb://server/games/ps1/x"),
            {"server": "server", "share": "games",
             "subpath": "ps1/x"})

    def test_unc_ref(self):
        self.assertEqual(
            engine.parse_share_ref(
                "\\\\192.168.0.102\\games\\disc.iso"),
            {"server": "192.168.0.102", "share": "games",
             "subpath": "disc.iso"})

    def test_double_slash_ref(self):
        self.assertEqual(
            engine.parse_share_ref("//server/share/f"),
            {"server": "server", "share": "share",
             "subpath": "f"})

    def test_plain_paths_are_not_refs(self):
        for p in ("/home/duane/game.iso", "games/disc.iso",
                  "server/share", "C:\\games\\x.iso", "", "smb://"):
            self.assertIsNone(engine.parse_share_ref(p), p)
            self.assertFalse(engine.is_share_ref(p), p)

    def test_missing_share_is_not_a_ref(self):
        self.assertIsNone(engine.parse_share_ref("smb://server"))
        self.assertIsNone(engine.parse_share_ref("smb://server/"))

    def test_smb_url_build(self):
        self.assertEqual(engine.smb_url("srv", "games"),
                         "smb://srv/games")
        self.assertEqual(
            engine.smb_url("srv", "games", "ps1/x", user="duane"),
            "smb://duane@srv/games/ps1/x")

    def test_unc_path_build(self):
        self.assertEqual(engine.unc_path("srv", "games"),
                         "\\\\srv\\games")
        self.assertEqual(
            engine.unc_path("srv", "games", "ps1/x.iso"),
            "\\\\srv\\games\\ps1\\x.iso")

    def test_gvfs_candidates_and_find(self):
        with tempfile.TemporaryDirectory() as td:
            mount = Path(td) / "gvfs" / \
                "smb-share:server=srv,share=games"
            mount.mkdir(parents=True)
            self.assertEqual(
                engine.find_share_mount("srv", "games",
                                        runtime_dir=td),
                str(mount))
            self.assertIsNone(
                engine.find_share_mount("srv", "other",
                                        runtime_dir=td))

    def test_find_share_mount_case_insensitive(self):
        with tempfile.TemporaryDirectory() as td:
            mount = Path(td) / "gvfs" / \
                "smb-share:server=SRV,share=Games"
            mount.mkdir(parents=True)
            self.assertEqual(
                engine.find_share_mount("srv", "games",
                                        runtime_dir=td),
                str(mount))


class ShareMountTests(unittest.TestCase):
    """mount_share against a stub `gio` on PATH: the stub
    simulates gio's side effects (creating the GVFS mount dir,
    failing, asking for a password) without any server."""

    def _fixture(self, td, script):
        bindir = Path(td) / "bin"
        bindir.mkdir()
        stub = bindir / "gio"
        stub.write_text(script)
        stub.chmod(0o755)
        runtime = Path(td) / "run"
        (runtime / "gvfs").mkdir(parents=True)
        return bindir, runtime

    def _patched_env(self, bindir, runtime):
        old_path = os.environ.get("PATH", "")
        old_xdg = os.environ.get("XDG_RUNTIME_DIR")
        os.environ["PATH"] = str(bindir) + os.pathsep + old_path
        os.environ["XDG_RUNTIME_DIR"] = str(runtime)
        return old_path, old_xdg

    def _restore_env(self, old_path, old_xdg):
        os.environ["PATH"] = old_path
        if old_xdg is None:
            os.environ.pop("XDG_RUNTIME_DIR", None)
        else:
            os.environ["XDG_RUNTIME_DIR"] = old_xdg

    def test_mount_success_returns_gvfs_path(self):
        with tempfile.TemporaryDirectory() as td:
            bindir, runtime = self._fixture(td, (
                "#!/bin/sh\n"
                "mkdir -p \"$XDG_RUNTIME_DIR/gvfs/"
                "smb-share:server=srv,share=games\"\n"))
            old = self._patched_env(bindir, runtime)
            try:
                got = engine.mount_share("srv", "games")
            finally:
                self._restore_env(*old)
            self.assertEqual(
                got, str(runtime / "gvfs"
                         / "smb-share:server=srv,share=games"))

    def test_mount_already_mounted_skips_gio(self):
        with tempfile.TemporaryDirectory() as td:
            bindir, runtime = self._fixture(td, (
                "#!/bin/sh\nexit 99\n"))
            mount = runtime / "gvfs" / \
                "smb-share:server=srv,share=games"
            mount.mkdir(parents=True)
            old = self._patched_env(bindir, runtime)
            try:
                got = engine.mount_share("srv", "games")
            finally:
                self._restore_env(*old)
            self.assertEqual(got, str(mount))

    def test_mount_failure_carries_gio_words(self):
        with tempfile.TemporaryDirectory() as td:
            bindir, runtime = self._fixture(td, (
                "#!/bin/sh\n"
                "echo 'gio: Server srv not found' >&2\n"
                "exit 2\n"))
            old = self._patched_env(bindir, runtime)
            try:
                with self.assertRaises(engine.EngineError) as ctx:
                    engine.mount_share("srv", "games")
            finally:
                self._restore_env(*old)
            self.assertIn("Server srv not found",
                          str(ctx.exception))

    # A stub gio that prompts the way real gio does on current
    # glib: a header line that itself contains the word
    # "password", then "Domain [WORKGROUP]:" FIRST, then
    # "Password:". The answers it received are recorded so the
    # tests can check each answer landed at its own prompt.
    _AUTH_STUB = (
        "#!/bin/sh\n"
        "echo 'Enter password for share games on srv'\n"
        "printf 'Domain [WORKGROUP]: '\n"
        "read dom\n"
        "printf 'Password: '\n"
        "read pw\n"
        "echo \"$dom\" > \"$XDG_RUNTIME_DIR/domain.txt\"\n"
        "if [ \"$pw\" = \"s3cret\" ]; then\n"
        "  mkdir -p \"$XDG_RUNTIME_DIR/gvfs/"
        "smb-share:server=srv,share=games\"\n"
        "else\n"
        "  echo 'gio: Authentication failed' >&2\n"
        "  exit 1\n"
        "fi\n")

    def test_mount_password_fed_on_stdin(self):
        with tempfile.TemporaryDirectory() as td:
            bindir, runtime = self._fixture(td, self._AUTH_STUB)
            old = self._patched_env(bindir, runtime)
            try:
                got = engine.mount_share(
                    "srv", "games", user="duane",
                    password="s3cret")
            finally:
                self._restore_env(*old)
            self.assertTrue(got.endswith(
                "smb-share:server=srv,share=games"))
            old = self._patched_env(bindir, runtime)
            try:
                # Wrong password against an unmounted share:
                # the stub fails and gio's words surface.
                with self.assertRaises(engine.EngineError):
                    engine.mount_share(
                        "srv", "other", user="duane",
                        password="nope")
            finally:
                self._restore_env(*old)

    def test_mount_password_without_user(self):
        # No user given: the same prompt sequence is answered
        # (Domain, then Password) without a username.
        with tempfile.TemporaryDirectory() as td:
            bindir, runtime = self._fixture(td, self._AUTH_STUB)
            old = self._patched_env(bindir, runtime)
            try:
                got = engine.mount_share("srv", "games",
                                         password="s3cret")
            finally:
                self._restore_env(*old)
            self.assertTrue(got.endswith(
                "smb-share:server=srv,share=games"))

    def test_mount_domain_prompt_gets_workgroup_not_password(self):
        # Regression: gio asks Domain BEFORE Password, under a
        # header containing the word "password". Matching the
        # whole output buffer would type the password into the
        # Domain prompt and never answer the real Password one;
        # only the pending prompt may be matched.
        with tempfile.TemporaryDirectory() as td:
            bindir, runtime = self._fixture(td, self._AUTH_STUB)
            old = self._patched_env(bindir, runtime)
            try:
                got = engine.mount_share(
                    "srv", "games", user="duane",
                    password="s3cret")
            finally:
                self._restore_env(*old)
            self.assertTrue(got.endswith(
                "smb-share:server=srv,share=games"))
            self.assertEqual(
                (runtime / "domain.txt").read_text().strip(),
                "WORKGROUP")

    def test_mount_anonymous_without_credentials(self):
        with tempfile.TemporaryDirectory() as td:
            bindir, runtime = self._fixture(td, (
                "#!/bin/sh\n"
                "echo \"$@\" > \"$XDG_RUNTIME_DIR/argv.txt\"\n"
                "mkdir -p \"$XDG_RUNTIME_DIR/gvfs/"
                "smb-share:server=srv,share=games\"\n"))
            old = self._patched_env(bindir, runtime)
            try:
                got = engine.mount_share("srv", "games")
            finally:
                self._restore_env(*old)
            self.assertTrue(got.endswith(
                "smb-share:server=srv,share=games"))
            argv = (runtime / "argv.txt").read_text()
            self.assertIn("--anonymous", argv)
            self.assertIn("smb://srv/games", argv)


CUE_TEXT = ('FILE "game.bin" BINARY\n'
            '  TRACK 01 MODE2/2352\n'
            '    INDEX 01 00:00:00\n')


def _make_set(folder, with_iso=False):
    folder = Path(folder)
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "game.bin").write_bytes(b"\x00" * 2352 * 4)
    (folder / "game.cue").write_text(CUE_TEXT)
    if with_iso:
        (folder / "game.iso").write_bytes(b"\x01" * 4096)
    return folder


class ArchiveInputTests(unittest.TestCase):
    def setUp(self):
        self._old_scratch = engine.ARCHIVE_SCRATCH
        self._td = tempfile.TemporaryDirectory()
        engine.ARCHIVE_SCRATCH = Path(self._td.name) / "scratch"

    def tearDown(self):
        engine.ARCHIVE_SCRATCH = self._old_scratch
        self._td.cleanup()

    def test_is_archive_path(self):
        for p in ("a.zip", "A.ZIP", "/x/y.7z", "\\\\s\\sh\\g.7Z"):
            self.assertTrue(engine.is_archive_path(p), p)
        for p in ("a.iso", "a.cue", "", None, "zip"):
            self.assertFalse(engine.is_archive_path(p), p)

    def test_find_image_prefers_complete_cue(self):
        d = _make_set(Path(self._td.name) / "set", with_iso=True)
        self.assertEqual(engine.find_image_in(d),
                         d / "game.cue")

    def test_find_image_iso_only_and_bin_only(self):
        d = Path(self._td.name) / "isoonly"
        d.mkdir()
        (d / "x.iso").write_bytes(b"\x00" * 10)
        self.assertEqual(engine.find_image_in(d), d / "x.iso")
        d2 = Path(self._td.name) / "binonly"
        d2.mkdir()
        (d2 / "x.bin").write_bytes(b"\x00" * 10)
        self.assertEqual(engine.find_image_in(d2), d2 / "x.bin")

    def test_find_image_nested_and_empty(self):
        d = _make_set(Path(self._td.name) / "outer" / "inner")
        self.assertEqual(
            engine.find_image_in(Path(self._td.name) / "outer"),
            d / "game.cue")
        empty = Path(self._td.name) / "empty"
        empty.mkdir()
        (empty / "readme.txt").write_text("hi")
        self.assertIsNone(engine.find_image_in(empty))

    def test_extract_zip_set(self):
        src_dir = _make_set(Path(self._td.name) / "src")
        arc = Path(self._td.name) / "game.zip"
        import zipfile as _zf
        with _zf.ZipFile(str(arc), "w") as zf:
            zf.write(str(src_dir / "game.bin"), "game.bin")
            zf.write(str(src_dir / "game.cue"), "game.cue")
        before = arc.read_bytes()
        logs = []
        image = engine.extract_archive(str(arc), logs.append)
        self.assertTrue(image.endswith("game.cue"), image)
        scratch = Path(image).parent
        self.assertTrue(str(scratch).startswith(
            str(engine.ARCHIVE_SCRATCH)))
        self.assertTrue((scratch / "game.bin").is_file())
        self.assertEqual(arc.read_bytes(), before)
        # Second run reuses the extraction.
        logs2 = []
        again = engine.extract_archive(str(arc), logs2.append)
        self.assertEqual(again, image)
        self.assertTrue(any("already extracted" in ln
                            for ln in logs2))

    def test_extract_zip_no_image(self):
        arc = Path(self._td.name) / "docs.zip"
        import zipfile as _zf
        with _zf.ZipFile(str(arc), "w") as zf:
            zf.writestr("readme.txt", "hello")
        with self.assertRaises(engine.EngineError) as ctx:
            engine.extract_archive(str(arc))
        self.assertIn("No disc image", str(ctx.exception))

    def test_extract_missing_archive(self):
        with self.assertRaises(engine.EngineError):
            engine.extract_archive(
                str(Path(self._td.name) / "nope.zip"))

    def test_clean_scratch(self):
        d = engine.ARCHIVE_SCRATCH / "x"
        d.mkdir(parents=True)
        (d / "f").write_text("x")
        engine.clean_archive_scratch()
        self.assertFalse(engine.ARCHIVE_SCRATCH.exists())


@unittest.skipUnless(
    importlib.util.find_spec("py7zr") or shutil.which("7z"),
    "no 7z backend available (py7zr or 7z tool)")
class Archive7zTests(unittest.TestCase):
    def setUp(self):
        self._old_scratch = engine.ARCHIVE_SCRATCH
        self._td = tempfile.TemporaryDirectory()
        engine.ARCHIVE_SCRATCH = Path(self._td.name) / "scratch"

    def tearDown(self):
        engine.ARCHIVE_SCRATCH = self._old_scratch
        self._td.cleanup()

    def _make_7z(self):
        src_dir = _make_set(Path(self._td.name) / "src7")
        arc = Path(self._td.name) / "game.7z"
        try:
            import py7zr
        except ImportError:
            py7zr = None
        if py7zr is not None:
            with py7zr.SevenZipFile(str(arc), "w") as zf:
                zf.writeall(str(src_dir / "game.bin"), "game.bin")
                zf.writeall(str(src_dir / "game.cue"), "game.cue")
        else:
            import subprocess
            subprocess.run(
                ["7z", "a", str(arc), "game.bin", "game.cue"],
                cwd=str(src_dir), check=True,
                stdout=subprocess.DEVNULL)
        return arc

    def test_extract_7z_set(self):
        arc = self._make_7z()
        before = arc.read_bytes()
        image = engine.extract_archive(str(arc))
        self.assertTrue(image.endswith("game.cue"), image)
        scratch = Path(image).parent
        self.assertTrue((scratch / "game.bin").is_file())
        self.assertEqual(
            (scratch / "game.bin").stat().st_size, 2352 * 4)
        self.assertEqual(arc.read_bytes(), before)


if __name__ == "__main__":
    unittest.main()
