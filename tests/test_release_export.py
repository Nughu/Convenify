"""Clean-release verification against a disposable Git repo, never the user's branch."""
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest
import zipfile

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("export_release", ROOT / "GUI" / "export_release.py")
exporter = importlib.util.module_from_spec(spec)
spec.loader.exec_module(exporter)


class ReleaseTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="convenify-release-test-")
        self.root = Path(self.temp.name) / "repo"
        self.root.mkdir()
        self.git("init", "--quiet")
        self.manifest = json.loads((ROOT / "GUI" / "defaults.json").read_text())
        self.manifest["files"].append(dict(path="future-settings.json", scope="all", value={"new_setting": "default"}))
        self.write("GUI/defaults.json", json.dumps(self.manifest))
        self.write("app.py", "print('committed source')\n")
        for name in ("config.json", "config.json.bak", "playlists.json", "download-queue.txt", "future-settings.json",
                     "Savify_automation/spotify_credentials.json", "cookies.txt", ".env.local", "gui-data/preferences.json",
                     "gui-data/runs/run-1/console.log", "gui-data/runs/run-1/traktor/collection.original.nml"):
            self.write(name, "PRIVATE-FIXTURE")
        self.git("add", ".")
        self.git("commit", "--quiet", "-m", "fixture")
        self.write("app.py", "UNCOMMITTED-FIXTURE")
        self.write("untracked-personal-data.json", "UNTRACKED-FIXTURE")
        self.output = Path(self.temp.name) / "clean.zip"

    def tearDown(self):
        self.temp.cleanup()

    def write(self, name, text):
        path = self.root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")

    def git(self, *args):
        result = subprocess.run(["git", "-C", str(self.root), "-c", "user.name=Release Test", "-c", "user.email=fixture@example.invalid",
                                 "-c", "core.hooksPath=" + str(self.root / ".disabled-hooks"), *args], capture_output=True, text=True)
        if result.returncode:
            self.fail("Git fixture failed: " + result.stderr)

    def test_export_has_only_committed_code_and_default_configuration(self):
        previous = os.environ.get("SPOTIPY_CLIENT_SECRET")
        os.environ["SPOTIPY_CLIENT_SECRET"] = "ENVIRONMENT-FIXTURE"
        try:
            exporter.export_release(self.root, "HEAD", self.output)
        finally:
            if previous is None:
                os.environ.pop("SPOTIPY_CLIENT_SECRET", None)
            else:
                os.environ["SPOTIPY_CLIENT_SECRET"] = previous
        with zipfile.ZipFile(self.output) as release:
            self.assertEqual(set(release.namelist()), {"app.py", "GUI/defaults.json", "config.json", "playlists.json", "future-settings.json"})
            self.assertEqual(json.loads(release.read("config.json"))["library_path"], "")
            self.assertEqual(json.loads(release.read("playlists.json")), {})
            self.assertEqual(json.loads(release.read("future-settings.json")), {"new_setting": "default"})
            combined = b"".join(release.read(name) for name in release.namelist())
            for private in (b"PRIVATE-FIXTURE", b"ENVIRONMENT-FIXTURE", b"UNCOMMITTED-FIXTURE", b"UNTRACKED-FIXTURE"):
                self.assertNotIn(private, combined)
        self.assertEqual((self.root / "config.json").read_text(), "PRIVATE-FIXTURE")
        self.assertEqual((self.root / "gui-data/runs/run-1/traktor/collection.original.nml").read_text(), "PRIVATE-FIXTURE")

    def test_check_writes_nothing_and_export_never_overwrites(self):
        self.assertEqual(exporter.export_release(self.root, "HEAD", self.output, check=True)["files"], 5)
        self.assertFalse(self.output.exists())
        self.output.write_bytes(b"EXISTING")
        with self.assertRaises(FileExistsError):
            exporter.export_release(self.root, "HEAD", self.output)
        self.assertEqual(self.output.read_bytes(), b"EXISTING")

    def test_invalid_manifest_cannot_write_outside_release(self):
        for invalid in ("../outside.json", "gui-data/runs/existing/console.log"):
            with self.subTest(path=invalid):
                self.manifest["files"].append(dict(path=invalid, scope="all", value={}))
                self.write("GUI/defaults.json", json.dumps(self.manifest))
                self.git("add", "GUI/defaults.json")
                self.git("commit", "--quiet", "-m", "invalid fixture")
                with self.assertRaises(ValueError):
                    exporter.export_release(self.root, "HEAD", self.output)
                self.assertFalse(self.output.exists())
                self.manifest["files"].pop()


if __name__ == "__main__":
    unittest.main(verbosity=2)
