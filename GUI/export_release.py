"""Export a committed Convenify version with clean configuration and no local runs."""
from __future__ import annotations

import argparse
import json
from pathlib import Path, PurePosixPath
import shutil
import stat
import subprocess
import tempfile
import zipfile

ROOT = Path(__file__).resolve().parent.parent


def git(root, *args, **kwargs):
    return subprocess.run(["git", "-C", str(root), *args], check=True,
                          stderr=subprocess.PIPE, **kwargs)


def safe_name(name):
    path = PurePosixPath(name)
    if not name or "\\" in name or ":" in name or path.is_absolute() or any(p in ("", ".", "..") for p in name.split("/")):
        raise ValueError(f"Invalid release path: {name}")
    return path


def read_plan(root, revision):
    commit = git(root, "rev-parse", "--verify", revision + "^{commit}", stdout=subprocess.PIPE).stdout.decode().strip()
    try:
        manifest = json.loads(git(root, "show", commit + ":GUI/defaults.json", stdout=subprocess.PIPE).stdout)
    except subprocess.CalledProcessError as exc:
        raise ValueError("This revision has no GUI/defaults.json. Commit the release/export changes first, then export that commit.") from exc
    if manifest.get("schema") != 1:
        raise ValueError("Unsupported defaults manifest.")
    defaults = {}
    for item in manifest["files"]:
        name = item["path"]
        safe_name(name)
        if name.casefold() == "gui-data/runs" or name.casefold().startswith("gui-data/runs/"):
            raise ValueError("Saved run files cannot be registered as resettable configuration.")
        if name.casefold() in defaults or item["scope"] not in ("settings", "all"):
            raise ValueError("Duplicate or invalid default file: " + name)
        defaults[name.casefold()] = item
    if "config.json" not in defaults or not isinstance(defaults["config.json"]["value"], dict):
        raise ValueError("The default shared configuration is missing.")
    directories = set(n.casefold() for n in manifest["release_exclude_directories"])
    names = set(n.casefold() for n in manifest["release_exclude_names"])
    suffixes = tuple(n.casefold() for n in manifest["release_exclude_suffixes"])

    def included(name):
        path = safe_name(name)
        parts = [p.casefold() for p in path.parts]
        return (name.casefold() not in defaults
                and not any(p in directories or ".savify-" in p for p in parts[:-1])
                and parts[-1] not in names and not parts[-1].startswith(".env.")
                and not parts[-1].endswith(suffixes))

    tracked = git(root, "ls-tree", "-r", "--name-only", "-z", commit, stdout=subprocess.PIPE).stdout.decode("utf-8").split("\0")
    selected = [name for name in tracked if name and included(name)]
    replacements = {item["path"]: item["value"] for item in defaults.values() if item["value"] is not None}
    return commit, selected, replacements


def export_release(root, revision, destination, *, check=False):
    """Read Git objects, never copy the working tree or reset the developer's files."""
    root, destination = Path(root).resolve(), Path(destination).resolve()
    commit, selected, replacements = read_plan(root, revision)
    if check:
        return dict(commit=commit, files=len(selected) + len(replacements))
    if destination.exists():
        raise FileExistsError("Choose a new output filename; this file already exists: " + str(destination))
    destination.parent.mkdir(parents=True, exist_ok=True)
    allowed = set(selected)
    with tempfile.TemporaryDirectory(prefix="convenify-release-") as temporary:
        source = Path(temporary) / "tracked.zip"
        git(root, "archive", "--format=zip", "--output=" + str(source), commit)
        created = False
        try:
            with destination.open("xb") as output:
                created = True
                with zipfile.ZipFile(source) as original, zipfile.ZipFile(output, "w", compression=zipfile.ZIP_DEFLATED) as release:
                    copied = set()
                    for info in original.infolist():
                        if info.is_dir() or info.filename not in allowed:
                            continue
                        if stat.S_ISLNK(info.external_attr >> 16):
                            raise ValueError("Release export refuses symlinks: " + info.filename)
                        with original.open(info) as src, release.open(info.filename, "w", force_zip64=True) as dst:
                            shutil.copyfileobj(src, dst, length=1024 * 1024)
                        copied.add(info.filename)
                    if copied != allowed:
                        raise ValueError("Git export attributes excluded required files; review .gitattributes before releasing.")
                    for name, value in replacements.items():
                        release.writestr(name, json.dumps(value, ensure_ascii=False, indent=2) + "\n")
            # Validate CRCs and exact membership before reporting a usable release.
            with zipfile.ZipFile(destination) as release:
                if release.testzip() is not None or set(release.namelist()) != allowed | set(replacements):
                    raise ValueError("Release ZIP verification failed.")
        except BaseException:
            if created:
                destination.unlink(missing_ok=True)
            raise
    return dict(commit=commit, files=len(selected) + len(replacements), output=str(destination))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--revision", default="HEAD", help="Commit, branch or tag to export (default: HEAD).")
    parser.add_argument("--output", type=Path, default=ROOT / "releases" / "Convenify.zip", help="New ZIP filename; existing files are never replaced.")
    parser.add_argument("--check", action="store_true", help="Validate the committed defaults and list the file count without creating a ZIP.")
    args = parser.parse_args()
    try:
        result = export_release(ROOT, args.revision, args.output, check=args.check)
    except (ValueError, OSError, subprocess.CalledProcessError) as exc:
        parser.exit(1, "Release export failed: " + str(exc) + "\n")
    print(json.dumps(result, indent=2))
    print("Uses committed files only. Local settings, credentials, queues and run history are excluded; shared settings use bundled defaults.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
