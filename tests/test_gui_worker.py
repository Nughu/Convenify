"""Offline integration coverage for the GUI adapter; no credentials or downloads."""
import contextlib
import importlib.util
import io
import json
import logging
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
os.environ["CONVENIFY_GUI"] = "1"
spec = importlib.util.spec_from_file_location("convenify_worker", ROOT / "GUI" / "convenify_worker.py")
worker = importlib.util.module_from_spec(spec)
spec.loader.exec_module(worker)


class WorkerTests(unittest.TestCase):
    def test_gui_skips_banner_and_terminal_clear(self):
        from savify.cli import show_banner
        output = io.StringIO()
        with patch.dict(os.environ, CONVENIFY_GUI="1"), patch("savify.cli.click.clear") as clear, contextlib.redirect_stdout(output):
            show_banner()
            show_banner(clear=False)
        self.assertEqual(output.getvalue(), "")
        clear.assert_not_called()

    def test_command_line_keeps_banner_and_clear_behavior(self):
        from savify.cli import show_banner, BANNER
        for gui_value in (None, ""):
            with self.subTest(gui_value=gui_value), patch.dict(os.environ):
                if gui_value is None:
                    os.environ.pop("CONVENIFY_GUI", None)
                else:
                    os.environ["CONVENIFY_GUI"] = gui_value
                output = io.StringIO()
                with patch("savify.cli.click.clear") as clear, contextlib.redirect_stdout(output):
                    show_banner()
                    show_banner(clear=False)
                self.assertEqual(output.getvalue(), (BANNER + "\n") * 2)
                clear.assert_called_once_with()

    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix="convenify-test-")
        self.folder = Path(self.temporary.name)

    def tearDown(self):
        self.temporary.cleanup()

    def file(self, name, text=""):
        path = self.folder / name
        path.write_text(text, encoding="utf-8")
        return str(path)

    def download(self, **overrides):
        return dict(action="download", output=str(self.folder / "music with spaces"),
                    query="https://open.spotify.com/track/abc", **overrides)

    def test_genre_blocks_overrides_comments_and_line_numbers(self):
        queue = '\n# Artist\ngenre:"House"\nhttps://open.spotify.com/track/a\nspotify:album:b genre:""\n\nhttps://open.spotify.com/playlist/c genre:"Techno"\n'
        result = worker.parse_queue(queue, "Default")
        self.assertEqual([item["genre"] for item in result], ["House", "", "Techno"])
        self.assertEqual([item["line"] for item in result], [4, 5, 7])
        self.assertEqual(result[1]["query"], "https://open.spotify.com/album/b")

    def test_blank_and_comment_reset_genre(self):
        result = worker.parse_queue('genre:"House"\n# reset\nspotify:track:a\ngenre:"Dub"\n\nspotify:track:b', "Default")
        self.assertEqual([item["genre"] for item in result], ["Default", "Default"])

    def test_queue_rejects_bad_lines_instead_of_silently_dropping_them(self):
        with self.assertRaisesRegex(ValueError, "line 2"):
            worker.parse_queue("spotify:track:a\nnot a link")
        with self.assertRaisesRegex(ValueError, "no Spotify links"):
            worker.parse_queue("# only comments")

    def test_localized_spotify_url_and_query_parameters(self):
        self.assertEqual(worker.normalize_query("https://open.spotify.com/intl-fr/album/abc?si=xyz"), ("https://open.spotify.com/album/abc", "album"))

    def test_shell_metacharacters_stay_data(self):
        job = self.download()
        job["query"] = 'Artist & song | $(do-not-run) "quoted"'
        result = worker.download_plan(job)[0]
        self.assertIn(job["query"], result["args"])
        self.assertFalse(Path(result["destination"]).exists())

    def test_lossless_ignores_bitrate_and_mp3_artwork(self):
        plan = worker.download_plan(self.download(format="flac", quality="bad-unused-value", skip_cover_art=True))[0]
        self.assertNotIn("--quality", plan["args"])
        self.assertNotIn("--skip-cover-art", plan["args"])

    def test_options_arrive_at_real_click_parser_with_transport_values(self):
        from savify.cli import main
        cookie = self.file("cookies with spaces.txt")
        job = self.download(cookies=cookie, proxy="http://localhost:1234", request_delay="2", group="%artist%", use_default_group=False)
        plan = worker.download_plan(job)[0]
        Path(plan["destination"]).mkdir(parents=True)
        args = plan["args"]
        with main.make_context("savify", args) as ctx:
            self.assertEqual(ctx.params["query"], job["query"])
            self.assertEqual(ctx.params["group"], "%artist%")
            self.assertEqual(ctx.params["format"], "mp3")
            self.assertEqual(ctx.args, ["--cookies", cookie, "--proxy", "http://localhost:1234", "--sleep-requests", "2.0"])
            from savify.ydl_options import parse_extra_options
            parsed = parse_extra_options(ctx.args)
            self.assertEqual(parsed["cookiefile"], cookie)
            self.assertEqual(parsed["sleep_interval_requests"], 2)

    def test_artist_options_apply_only_to_artist_entries(self):
        job = self.download(source="queue", queue="spotify:artist:a\nspotify:track:b", artist_albums=True)
        first, second = worker.download_plan(job)
        self.assertIn("--artist-albums", first["args"])
        self.assertNotIn("--artist-albums", second["args"])

    def test_numeric_boundaries_nan_and_bad_group_rejected(self):
        for extra in ({"workers": 0}, {"workers": 1.5}, {"workers": 17}, {"match_threshold": "nan"}, {"search_results": 51}, {"group": "../../escape"}):
            with self.subTest(extra=extra), self.assertRaises(ValueError):
                worker.download_plan(self.download(**extra))

    def test_sync_preserves_selected_destinations_and_genres(self):
        absolute = str(self.folder / "absolute destination")
        job = dict(action="sync", output=str(self.folder), playlists=[
            dict(name="One", link="spotify:playlist:one", path="House", genre="House"),
            dict(name="Two", link="spotify:playlist:two", path=absolute, genre="")])
        plan = worker.download_plan(job)
        self.assertEqual(plan[0]["destination"], str(self.folder / "House"))
        self.assertEqual(plan[1]["destination"], absolute)
        self.assertEqual(plan[0]["genre"], "House")

    def test_empty_sync_and_nonplaylist_rejected(self):
        for selected in ([], [dict(name="Bad", link="spotify:track:one", path="x")]):
            with self.assertRaises(ValueError):
                worker.download_plan(dict(action="sync", output=str(self.folder), playlists=selected))

    def test_audit_does_not_receive_hidden_repair_options(self):
        args = worker.audit_args(dict(mode="audit", directory=str(self.folder), repair_unknown=True, max_candidates="bad", cookies="missing", match_overrides="missing"), self.folder)
        self.assertNotIn("--repair", args)
        self.assertNotIn("--cookies", args)
        self.assertNotIn("--repair-unknown", args)
        self.assertNotIn("--max-candidates", args)

    def test_nml_drops_folder_only_options(self):
        args = worker.audit_args(dict(mode="nml", nml=self.file("export.nml"), directory="missing", full_verification=True, rescan=True, repair_unknown=True), self.folder)
        for flag in ("--full-verification", "--rescan", "--repair-unknown"):
            self.assertNotIn(flag, args)
        self.assertIn("--run-log", args)

    def test_duration_bounds_and_state_directory_validation(self):
        with self.assertRaisesRegex(ValueError, "Minimum duration"):
            worker.audit_args(dict(mode="audit", directory=str(self.folder), min_duration="5:00", max_duration="1:00"), self.folder)
        with self.assertRaisesRegex(ValueError, "separate folder"):
            worker.audit_args(dict(mode="audit", directory=str(self.folder), state_dir=str(self.folder)), self.folder)

    def test_restore_ignores_cleanup_only_fields(self):
        args = worker.traktor_args(dict(mode="restore", collection=self.file("collection.nml"), manifest=self.file("manifest.json", "{}"), repair_logs="missing.log", log_encoding="bad-unused-encoding"), self.folder)
        self.assertIn("--restore-playlists", args)
        self.assertNotIn("--repair-log", args)
        self.assertNotIn("--log-encoding", args)

    def test_traktor_mappings_validate_before_running(self):
        with self.assertRaisesRegex(Exception, "NAME=ROOT"):
            worker.traktor_args(dict(mode="cleanup", collection=self.file("collection.nml"), repair_logs=self.file("events.jsonl"), volume_maps="invalid"), self.folder)

    def test_savify_nonzero_return_is_preserved(self):
        with patch("savify.cli.main.main", return_value=2):
            self.assertEqual(worker.savify_call(["test"]), 2)

    def test_savify_reported_track_failures_are_not_reported_as_success(self):
        import logging
        def report(**kwargs):
            logging.getLogger("savify").warning("Failed Tracks:\nSong: fixture\nReason: fixture failure")
            return 0
        with patch("savify.cli.main.main", side_effect=report):
            self.assertEqual(worker.savify_call(["test"]), 1)

    def test_batch_continues_and_writes_retry_queue(self):
        job = self.download(source="queue", queue="spotify:track:a\nspotify:track:b", genre="", save_run_files=True)
        plan = worker.download_plan(job)
        with patch.object(worker, "check_credentials"), patch.object(worker, "run_command", side_effect=[2, 0]), contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(worker.run_downloads(job, plan, self.folder), 1)
        summary = json.loads((self.folder / "summary.json").read_text())
        self.assertEqual([item["exit_code"] for item in summary], [2, 0])
        self.assertEqual(worker.parse_queue((self.folder / "retry-queue.txt").read_text())[0]["query"], "https://open.spotify.com/track/a")

    def test_download_without_saving_keeps_results_in_console_only(self):
        job = self.download(source="queue", queue="spotify:track:a\nspotify:track:b", genre="")
        output = io.StringIO()
        with patch.object(worker, "check_credentials"), patch.object(worker, "run_command", side_effect=[2, 0]), contextlib.redirect_stdout(output):
            self.assertEqual(worker.run_downloads(job, worker.download_plan(job), self.folder), 1)
        self.assertIn("1 successful, 1 needing attention", output.getvalue())
        self.assertIn("FAILED: https://open.spotify.com/track/a", output.getvalue())
        self.assertNotIn('"event": "artifact"', output.getvalue())
        self.assertFalse((self.folder / "summary.json").exists())
        self.assertFalse((self.folder / "retry-queue.txt").exists())

    def test_repair_records_survive_without_saved_run_files(self):
        workspace = self.folder / "temporary-job"
        state = self.folder / "repair-state"
        job = dict(mode="repair", directory=str(self.folder), state_dir=str(state))
        args = worker.audit_args(job, workspace)
        path = Path(args[args.index("--run-log") + 1])
        self.assertTrue(path.is_relative_to(state))
        self.assertFalse(path.is_relative_to(workspace))
        job["save_run_files"] = True
        self.assertEqual(worker.repair_log_path(job, workspace), workspace / "replacements.jsonl")

    def run_job(self, job):
        job_path = self.folder / "job.json"
        job_path.write_text(json.dumps(job), encoding="utf-8")
        return subprocess.run([sys.executable, "-u", str(ROOT / "GUI" / "convenify_worker.py"), "--handshake", "--job", str(job_path)], input="START\n", capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=30)

    def test_real_empty_folder_audit_is_offline_and_writes_report(self):
        music = self.folder / "empty music"
        music.mkdir()
        result = self.run_job(dict(action="audit", mode="audit", directory=str(music), spotify="disabled", state_dir=str(self.folder / "state")))
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertTrue((self.folder / "state" / "report.csv").is_file())
        self.assertFalse((self.folder / "state" / "audit.log").exists())
        self.assertFalse((self.folder / "audit.log").exists())
        self.assertIn('"event": "artifact"', result.stdout)

    def test_saved_audit_logs_stay_with_the_run(self):
        music = self.folder / "music"
        music.mkdir()
        result = self.run_job(dict(action="audit", mode="audit", directory=str(music), spotify="disabled", save_run_files=True))
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertTrue((self.folder / "audit.log").is_file())
        self.assertFalse((music / "_savify_audit" / "audit.log").exists())

    def test_cli_audit_still_saves_its_log(self):
        music = self.folder / "music"
        music.mkdir()
        env = dict(os.environ, CONVENIFY_SAVE_RUN_FILES="0")
        env.pop("CONVENIFY_GUI", None)
        result = subprocess.run([sys.executable, str(ROOT / "Savify_automation" / "run_savify_audit.py"), str(music), "--no-spotify"],
                                capture_output=True, text=True, env=env, timeout=30)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertTrue((music / "_savify_audit" / "audit.log").is_file())

    def test_real_traktor_preview_retains_original(self):
        original = '<NML VERSION="19"><COLLECTION ENTRIES="1"><ENTRY TITLE="Fixture" ARTIST="Test"><LOCATION VOLUME="C:" DIR="/:Music/:" FILE="test.mp3"/></ENTRY></COLLECTION></NML>'
        collection = self.file("collection.nml", original)
        repair_log = self.file("events.jsonl", json.dumps(dict(event="file_result", status="repaired", path="C:/Music/test.mp3")) + "\n")
        for save in (False, True):
            with self.subTest(save_run_files=save):
                job = dict(action="traktor", mode="cleanup", collection=collection, repair_logs=repair_log, save_run_files=save)
                result = self.run_job(job)
                self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
                self.assertEqual(Path(collection).read_text(encoding="utf-8"), original)
                report_dir = worker.traktor_report_dir(job, self.folder)
                manifest = json.loads((report_dir / "manifest.json").read_text(encoding="utf-8"))
                self.assertFalse(manifest["applied"])
                self.assertEqual(manifest["removed_entry_count"], 1)
                self.assertTrue((report_dir / "reimport-repaired.nml").exists())

    def test_handshake_eof_exits_without_running_job(self):
        result = subprocess.run([sys.executable, str(ROOT / "GUI" / "convenify_worker.py"), "--handshake", "--job", "missing.json"], input="", capture_output=True, text=True, timeout=10)
        self.assertEqual(result.returncode, 2)
        self.assertNotIn("started", result.stdout)

    def test_failed_validation_is_noninteractive(self):
        result = self.run_job(self.download(workers="not-a-number"))
        self.assertEqual(result.returncode, 2)
        self.assertIn("Workers must be a number", result.stderr)

    def test_legacy_entrypoints_still_offer_help(self):
        for script in (ROOT / "Savify_automation" / "run_savify_audit.py", ROOT / "Savify_automation" / "traktor_reset_repaired.py", ROOT / "Savify_automation" / "collect_discography.py"):
            with self.subTest(script=script.name):
                result = subprocess.run([sys.executable, str(script), "--help"], capture_output=True, text=True, timeout=20)
                self.assertEqual(result.returncode, 0, result.stderr)


class LoggerTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix="convenify-logger-")
        self.folder = Path(self.temporary.name)
        self.logger = logging.getLogger("savify")
        self.handlers = self.logger.handlers[:]
        self.level, self.propagate = self.logger.level, self.logger.propagate
        self.logger.handlers = []
        self.logger.propagate = False

    def tearDown(self):
        for handler in self.logger.handlers:
            handler.close()
        self.logger.handlers = self.handlers
        self.logger.setLevel(self.level)
        self.logger.propagate = self.propagate
        self.temporary.cleanup()

    def emit(self, gui, save=False, log_level=logging.INFO):
        from savify.logger import Logger
        output = io.StringIO()
        with patch.dict(os.environ, CONVENIFY_GUI="1" if gui else "", CONVENIFY_SAVE_RUN_FILES="1" if save else "0", CONVENIFY_RUN_DIR=str(self.folder / "run")), contextlib.redirect_stderr(output):
            logger = Logger(str(self.folder / "legacy"), log_level)
            logger.debug("DEBUG diagnostic fixture")
            logger.info("INFO fixture")
        return output.getvalue()

    def test_gui_delivers_debug_without_verbose_or_log_files(self):
        output = self.emit(gui=True)
        self.assertIn("[DEBUG]\tDEBUG diagnostic fixture", output)
        self.assertIn("[INFO]\tINFO fixture", output)
        self.assertEqual(list(self.folder.rglob("*.log")), [])

    def test_gui_saved_debug_logs_use_run_folder(self):
        output = self.emit(gui=True, save=True)
        self.assertIn("DEBUG diagnostic fixture", output)
        logs = list((self.folder / "run").rglob("*.log"))
        self.assertEqual(len(logs), 1)
        self.assertIn("DEBUG diagnostic fixture", logs[0].read_text(encoding="utf-8"))
        self.assertFalse((self.folder / "legacy").exists())

    def test_cli_keeps_default_info_console_and_debug_log(self):
        output = self.emit(gui=False)
        self.assertNotIn("DEBUG diagnostic fixture", output)
        self.assertIn("INFO fixture", output)
        logs = list((self.folder / "legacy").rglob("*.log"))
        self.assertEqual(len(logs), 1)
        self.assertIn("DEBUG diagnostic fixture", logs[0].read_text(encoding="utf-8"))

    def test_cli_keeps_silent_console_and_file_logging(self):
        self.assertEqual(self.emit(gui=False, log_level=None), "")
        self.assertEqual(len(list((self.folder / "legacy").rglob("*.log"))), 1)


if __name__ == "__main__":
    unittest.main(verbosity=2)
