"""Noninteractive desktop adapter. The existing console entry points stay independent.

Jobs contain data, never shell commands. stdout/stderr are streamed by the desktop;
small prefixed JSON events carry progress and artifact paths alongside that output.
"""
from __future__ import annotations

import argparse
import importlib.util
import json
import math
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import time
from urllib.parse import urlparse

ROOT = Path(__file__).resolve().parent.parent
sys.path[:0] = [str(ROOT / "savify-new"), str(ROOT / "Savify_automation")]
EVENT_PREFIX = "@@CONVENIFY@@"
FORMATS = ("mp3", "aac", "flac", "m4a", "opus", "vorbis", "wav")
QUALITIES = ("best", "320k", "256k", "192k", "128k", "96k", "32k", "worst")
KINDS = ("track", "album", "playlist", "artist")
GROUP = re.compile(r"%(?:artist|album|playlist)%(?:/%(?:artist|album|playlist)%)*$")
GENRE = re.compile(r'genre:"([^"\r\n]*)"')


def event(kind, **data):
    print(EVENT_PREFIX + json.dumps(dict(event=kind, **data), ensure_ascii=False), flush=True)


def write_json(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    os.replace(temporary, path)


def required(value, label):
    value = str(value or "").strip()
    if not value:
        raise ValueError(f"{label} is required.")
    return value


def path_value(value, label, *, exists=False, directory=False):
    path = Path(os.path.expandvars(required(value, label))).expanduser().absolute()
    if exists and not (path.is_dir() if directory else path.is_file()):
        raise ValueError(f"{label} does not point to an existing {'folder' if directory else 'file'}: {path}")
    if not exists and path.exists() and not (path.is_dir() if directory else path.is_file()):
        raise ValueError(f"{label} must be a {'folder' if directory else 'file'}: {path}")
    return path


def number(value, label, minimum, maximum=None, integer=False):
    try:
        result = float(value)
    except (ValueError, TypeError):
        raise ValueError(f"{label} must be a number.") from None
    if not math.isfinite(result) or result < minimum or (maximum is not None and result > maximum):
        raise ValueError(f"{label} must be between {minimum} and {maximum if maximum is not None else 'a finite positive value'}.")
    if integer and not result.is_integer():
        raise ValueError(f"{label} must be a whole number.")
    return int(result) if integer else result


def choice(value, choices, label):
    if value not in choices:
        raise ValueError(f"Choose a valid {label}.")
    return value


def normalize_query(value, *, links_only=False):
    value = required(value, "Link or search query")
    if value.startswith("spotify:"):
        match = re.fullmatch(r"spotify:(track|album|playlist|artist):([A-Za-z0-9]+)", value)
        if not match:
            raise ValueError("Use a Spotify track, album, playlist or artist URI.")
        return f"https://open.spotify.com/{match[1]}/{match[2]}", match[1]
    if value.startswith(("http://", "https://")):
        parsed = urlparse(value)
        if parsed.hostname == "open.spotify.com":
            match = re.fullmatch(r"/(?:intl-[a-zA-Z-]+/)?(track|album|playlist|artist)/([A-Za-z0-9]+)/?", parsed.path)
            if not match:
                raise ValueError("Use a full Spotify track, album, playlist or artist link.")
            return f"https://open.spotify.com/{match[1]}/{match[2]}", match[1]
        raise ValueError("Use an open.spotify.com link. Downloads use Spotify metadata to find validated recordings.")
    if links_only:
        raise ValueError("Queue entries must be Spotify links or URIs.")
    return value, None


def parse_queue(text, default_genre=""):
    """CLI-compatible genre blocks, comments and per-link overrides, with errors by line."""
    entries, block = [], None
    for line_number, line in enumerate(str(text).splitlines(), 1):
        line = line.strip()
        if not line or line.startswith("#"):
            block = None
            continue
        match = GENRE.fullmatch(line)
        if match:
            block = match[1]
            continue
        match = GENRE.search(line)
        genre = block if block is not None else default_genre
        if match:
            if line[match.end():].strip() or not line[:match.start()].strip():
                raise ValueError(f"Queue line {line_number}: put genre:\"...\" after the link.")
            genre, line = match[1], line[:match.start()].strip()
        try:
            query, kind = normalize_query(line, links_only=True)
        except ValueError as exc:
            raise ValueError(f"Queue line {line_number}: {exc}") from exc
        entries.append(dict(query=query, kind=kind, genre=genre, line=line_number))
    if not entries:
        raise ValueError("The queue contains no Spotify links.")
    return entries


def environment():
    env = os.environ.copy()
    env.update(PYTHONUNBUFFERED="1", PYTHONIOENCODING="utf-8", NO_COLOR="1", CONVENIFY_GUI="1")
    env["PATH"] = str(ROOT / "ffmpeg") + os.pathsep + env.get("PATH", "")
    return env


def configure_run_logging(job, job_dir):
    """Propagate the desktop's retention choice to Savify subprocesses."""
    os.environ["CONVENIFY_SAVE_RUN_FILES"] = "1" if job.get("save_run_files", False) else "0"
    if job.get("save_run_files", False):
        os.environ["CONVENIFY_RUN_DIR"] = str(job_dir)
    else:
        os.environ.pop("CONVENIFY_RUN_DIR", None)


def audit_state_dir(job):
    if job.get("state_dir"):
        return path_value(job["state_dir"], "Reports folder", directory=True)
    nml = job.get("mode") == "nml"
    source = path_value(job.get("nml") if nml else job.get("directory"), "Source", directory=not nml)
    return source.parent / "_savify_audit" / ("nml-" + source.stem) if nml else source / "_savify_audit"


def repair_log_path(job, job_dir):
    # Replacement records are required for later Traktor cleanup, even when
    # diagnostic logs/history are not retained. Keep them with the repair state.
    if job.get("save_run_files", False):
        return job_dir / "replacements.jsonl"
    return audit_state_dir(job) / "replacement-runs" / (job_dir.name + ".jsonl")


def traktor_report_dir(job, job_dir):
    if job.get("save_run_files", False):
        return job_dir / "traktor"
    collection = path_value(job.get("collection"), "Active Traktor collection", exists=True)
    # The manifest, re-import collection and original backup are task outputs,
    # not disposable diagnostics. Follow the CLI's adjacent-collection layout.
    return collection.with_name(collection.name + ".savify-" + job_dir.name)


def run_command(args):
    """No shell and no stdin prompts. The desktop's Job Object owns descendants."""
    process = subprocess.Popen(
        [str(a) for a in args], cwd=ROOT, env=environment(), stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
        encoding="utf-8", errors="replace", bufsize=1,
        creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
    )
    try:
        for line in process.stdout:
            print(line, end="", flush=True)
        return process.wait()
    finally:
        process.stdout.close()
        if process.poll() is None:
            process.terminate()
            process.wait()


def common_download_options(job):
    fmt = choice(job.get("format", "mp3"), FORMATS, "audio format")
    args = ["--format", fmt, "--workers", str(number(job.get("workers", 4), "Workers", 1, 16, True)),
            "--search-results", str(number(job.get("search_results", 10), "Search candidates", 1, 50, True)),
            "--match-threshold", str(number(job.get("match_threshold", .82), "Match threshold", 0, 1))]
    if fmt not in ("flac", "wav"):
        args += ["--quality", choice(job.get("quality", "best"), QUALITIES, "quality")]
    for key, flag in (("m3u", "--m3u"), ("no_youtube_music", "--no-youtube-music"),
                      ("no_match_cache", "--no-match-cache"), ("verify_existing", "--verify-existing"),
                      ("redownload", "--redownload")):
        if job.get(key) and not (key == "verify_existing" and job.get("redownload")):
            args.append(flag)
    if fmt == "mp3" and job.get("skip_cover_art"):
        args.append("--skip-cover-art")
    if job.get("verbose"):
        args.append("-vv")
    for key, flag, label in (("match_overrides", "--match-overrides", "Match overrides"),):
        if job.get(key):
            args += [flag, str(path_value(job[key], label, exists=True))]
    if job.get("temp_path"):
        args += ["--path", str(path_value(job["temp_path"], "Temporary folder", directory=True))]
    group = str(job.get("group", ""))
    if group and not GROUP.fullmatch(group):
        raise ValueError("Folder grouping must use %artist%, %album% or %playlist%, separated by /.")
    return args


def download_transport_options(job):
    # Keep yt-dlp's unknown options AFTER the positional query, otherwise Click
    # can consume an option's value as the query (e.g. the cookies filename).
    args = []
    if job.get("cookies"):
        args += ["--cookies", str(path_value(job["cookies"], "Cookies file", exists=True))]
    if job.get("proxy"):
        args += ["--proxy", str(job["proxy"])]
    if job.get("request_delay") not in (None, ""):
        args += ["--sleep-requests", str(number(job["request_delay"], "Request delay", 0))]
    return args


def download_plan(job):
    common = common_download_options(job)
    transport = download_transport_options(job)
    output = path_value(job.get("output"), "Download folder", directory=True)
    if job["action"] == "sync":
        entries = []
        for item in job.get("playlists", []):
            query, kind = normalize_query(item.get("link", ""), links_only=True)
            if kind != "playlist":
                raise ValueError("Playlist sync requires Spotify playlist links.")
            folder = Path(required(item.get("path"), "Playlist folder"))
            folder = folder if folder.is_absolute() else output / folder
            entries.append(dict(query=query, kind=kind, genre=item.get("genre", ""),
                                name=required(item.get("name"), "Playlist name"), destination=str(folder.absolute())))
        if not entries:
            raise ValueError("Select at least one playlist to sync.")
    elif job.get("source") == "queue":
        entries = parse_queue(job.get("queue", ""), job.get("genre", ""))
    else:
        query, kind = normalize_query(job.get("query", ""))
        kind = kind or choice(job.get("search_type", "track"), KINDS, "search type")
        entries = [dict(query=query, kind=kind, genre=job.get("genre", ""))]
    for entry in entries:
        kind = entry["kind"]
        destination = Path(entry.get("destination", output))
        if job["action"] != "sync" and job.get("organize", True):
            destination /= kind.title()
        group = job.get("group", "")
        if job.get("use_default_group", True) and job["action"] != "sync":
            group = {"track": "%artist%", "album": "%artist%/%album%",
                     "playlist": "%playlist%", "artist": "%artist%/%album%"}[kind]
        args = list(common) + ["--output", str(destination)]
        if group:
            args += ["--group", group]
        if kind == "artist" and job.get("artist_albums"):
            args.append("--artist-albums")
        args += ["--type", kind, "--", entry["query"]] + transport
        entry.update(args=args, destination=str(destination))
    return entries


def audit_args(job, job_dir):
    nml = job.get("mode") == "nml"
    repair = nml or job.get("mode") == "repair"
    full = not nml and bool(job.get("full_verification"))
    args = []
    if nml:
        args += ["--nml", str(path_value(job.get("nml"), "Traktor NML export", exists=True))]
    else:
        choice(job.get("mode"), ("audit", "repair"), "library operation")
        args.append(str(path_value(job.get("directory"), "Music folder", exists=True, directory=True)))
        if repair:
            args.append("--repair")
        if full:
            args.append("--full-verification")
        if job.get("rescan"):
            args.append("--rescan")
    if repair and full:
        if job.get("repair_unknown"):
            args.append("--repair-unknown")
        if job.get("no_reference_check"):
            args.append("--no-reference-check")
    args += ["--duration-tolerance", str(number(job.get("duration_tolerance", 8), "Duration tolerance", 0))]
    # Importing the parser is safe: it does not run an audit or prompt.
    from savify.library_audit import duration_bound_argument
    bounds = {}
    for key in ("min_duration", "max_duration"):
        if job.get(key):
            bounds[key] = duration_bound_argument(job[key])
            args += ["--" + key.replace("_", "-"), str(bounds[key])]
    if len(bounds) == 2 and bounds["min_duration"] > bounds["max_duration"]:
        raise ValueError("Minimum duration must not exceed maximum duration.")
    if job.get("limit"):
        args += ["--limit", str(number(job["limit"], "File limit", 1, integer=True))]
    if job.get("state_dir"):
        state = path_value(job["state_dir"], "Reports and backups folder", directory=True)
        if not nml:
            library = Path(job["directory"]).resolve()
            if state.resolve() == library or state.resolve() in library.parents:
                raise ValueError("Reports and backups must be in a separate folder, not the music folder or its parent.")
        args += ["--state-dir", str(state)]
    args += ["--ffmpeg-location", str(job.get("ffmpeg") or ROOT / "ffmpeg")]
    if (repair or full) and job.get("fpcalc"):
        args += ["--fpcalc", str(path_value(job["fpcalc"], "fpcalc executable", exists=True))]
    catalog = choice(job.get("spotify", "auto"), ("auto", "enabled", "disabled"), "Spotify catalog setting")
    if catalog != "auto":
        args.append("--spotify" if catalog == "enabled" else "--no-spotify")
    if repair:
        args += ["--run-log", str(repair_log_path(job, job_dir)), "--max-candidates",
                 str(number(job.get("max_candidates", 5), "Replacement candidates", 1, integer=True))]
        for key, flag in (("cookies", "--cookies"), ("match_overrides", "--match-overrides")):
            if job.get(key):
                args += [flag, str(path_value(job[key], key.replace("_", " ").title(), exists=True))]
    if job.get("verbose"):
        args.append("--verbose")
    return args


def traktor_args(job, job_dir):
    args = [str(path_value(job.get("collection"), "Active Traktor collection", exists=True))]
    if job.get("mode") == "restore":
        args += ["--restore-playlists", str(path_value(job.get("manifest"), "Applied cleanup manifest", exists=True))]
    else:
        choice(job.get("mode"), ("cleanup",), "Traktor operation")
        sources = [s.strip().strip('"') for s in str(job.get("repair_logs", "")).splitlines() if s.strip()]
        if not sources:
            raise ValueError("Choose at least one repair log.")
        for source in sources:
            args += ["--repair-log", str(path_value(source, "Repair log", exists=True))]
        if job.get("log_encoding"):
            import codecs
            codecs.lookup(job["log_encoding"])
            args += ["--log-encoding", job["log_encoding"]]
    mappings = [line.strip() for line in str(job.get("volume_maps", "")).splitlines() if line.strip()]
    from traktor_reset_repaired import volume_maps
    volume_maps(mappings)
    for mapping in mappings:
        args += ["--volume-map", mapping]
    if job.get("apply"):
        args.append("--apply")
    args += ["--report-dir", str(traktor_report_dir(job, job_dir))]
    return args


def validate(job, job_dir):
    action = choice(job.get("action"), ("download", "sync", "discography", "audit", "traktor", "diagnostics", "install_deno", "update_downloader"), "task")
    if action in ("download", "sync"):
        return download_plan(job)
    if action == "audit":
        return audit_args(job, job_dir)
    if action == "traktor":
        return traktor_args(job, job_dir)
    if action == "discography":
        from collect_discography import parse_artist_id
        parse_artist_id(required(job.get("artist"), "Artist"))
        if not re.fullmatch("[A-Za-z]{2}", str(job.get("market", "DE"))):
            raise ValueError("Market must be a two-letter country code, such as DE or US.")
        path_value(job.get("output"), "Discography output file")
    return None


def check_credentials():
    if not all(os.environ.get(key, "").strip() for key in ("SPOTIPY_CLIENT_ID", "SPOTIPY_CLIENT_SECRET")):
        raise ValueError("Save your Spotify client ID and secret on the Settings page first.")


def run_downloads(job, entries, job_dir):
    check_credentials()
    results = []
    for index, entry in enumerate(entries, 1):
        event("progress", current=index - 1, total=len(entries), message=f"Item {index} of {len(entries)}")
        print(f"\n[{index}/{len(entries)}] {entry.get('name', entry['query'])}", flush=True)
        destination = Path(entry["destination"])
        try:
            destination.mkdir(parents=True, exist_ok=True)
            if job.get("temp_path"):
                path_value(job["temp_path"], "Temporary folder", directory=True).mkdir(parents=True, exist_ok=True)
            # Genre behavior matches the wrapper: only newly created MP3s are tagged.
            before = {p.resolve() for p in destination.rglob("*") if p.suffix.lower() == ".mp3"} if entry.get("genre") and job.get("format", "mp3") == "mp3" else None
            code = run_command([sys.executable, "-u", __file__, "--savify-call"] + entry["args"])
            if before is not None:
                import music_tag
                new_files = [p for p in destination.rglob("*") if p.is_file() and p.suffix.lower() == ".mp3" and p.resolve() not in before]
                for path in new_files:
                    metadata = music_tag.load_file(path)
                    metadata["genre"] = entry["genre"]
                    metadata.save()
                print(f"Genre set on {len(new_files)} new MP3 file(s).")
            result = dict(query=entry["query"], name=entry.get("name", ""), genre=entry.get("genre", ""), line=entry.get("line"), exit_code=code)
        except Exception as exc:
            print(f"ERROR: {exc}", flush=True)
            result = dict(query=entry["query"], genre=entry.get("genre", ""), line=entry.get("line"), exit_code=2, error=str(exc))
        results.append(result)
        if job.get("save_run_files", False):
            write_json(job_dir / "summary.json", results)
        event("progress", current=index, total=len(entries), message=f"Processed {index} of {len(entries)}")
    failed = [item for item in results if item["exit_code"]]
    print(f"\nFinal report: {len(results) - len(failed)} successful, {len(failed)} needing attention.")
    for item in failed:
        print(f"FAILED: {item['query']}" + (f" (queue line {item['line']})" if item.get("line") else ""))
    retry_links = [item for item in failed if item["query"].startswith("https://open.spotify.com/")]
    if retry_links and job.get("save_run_files", False):
        (job_dir / "retry-queue.txt").write_text("\n".join(item["query"] + ' genre:"' + item.get("genre", "").replace('"', "'") + '"' for item in retry_links) + "\n", encoding="utf-8")
    if job.get("save_run_files", False):
        event("artifact", path=str(job_dir / "summary.json"), label="Download report")
    return 1 if failed else 0


def diagnostics():
    missing = []
    print("Convenify environment check (offline)\n")
    print("Python: " + sys.version.split()[0])
    for module in ("savify", "yt_dlp", "ytmusicapi", "spotipy", "music_tag", "requests"):
        found = importlib.util.find_spec(module) is not None
        print(f"{'OK' if found else 'MISSING'}  {module}")
        if not found:
            missing.append(module)
    for name in ("SPOTIPY_CLIENT_ID", "SPOTIPY_CLIENT_SECRET", "ACOUSTID_API_KEY"):
        present = bool(os.environ.get(name, "").strip())
        print(f"{'SET' if present else 'NOT SET'}  {name}" + (" (for repairs/fingerprint verification)" if name == "ACOUSTID_API_KEY" else ""))
        if not present and name != "ACOUSTID_API_KEY":
            missing.append(name)
    for executable, flag in ((str(ROOT / "ffmpeg" / "ffmpeg.exe"), "-version"), ("deno", "--version")):
        resolved = executable if Path(executable).is_file() else shutil.which(executable, path=environment()["PATH"])
        if not resolved:
            print("MISSING  " + executable)
            missing.append(executable)
            continue
        result = subprocess.run([resolved, flag], capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=20, env=environment(), creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0)
        print((result.stdout or result.stderr).splitlines()[0] if (result.stdout or result.stderr) else str(result.returncode))
        if result.returncode:
            missing.append(executable)
        if "ffmpeg" in executable:
            print("Chromaprint: " + ("available" if "--enable-chromaprint" in result.stdout else "not built in; select fpcalc for fingerprint checks"))
    print("\n" + ("Ready for downloads." if not missing else "Review missing items in Settings; local duration audits and Traktor tools can still run without Spotify."))
    return 1 if missing else 0


def execute(job, job_dir):
    plan = validate(job, job_dir)
    action = job["action"]
    if action in ("download", "sync"):
        return run_downloads(job, plan, job_dir)
    if action == "discography":
        check_credentials()
        from collect_discography import extract_discography
        output = extract_discography(job["artist"], market=job.get("market", "DE"), output_file=job["output"],
                                     append_queue=bool(job.get("append_queue")), queue_file=job["output"])
        event("artifact", path=str(output), label="Discography links")
        return 0
    if action == "audit":
        if job.get("mode") in ("repair", "nml") or job.get("full_verification"):
            if not os.environ.get("ACOUSTID_API_KEY", "").strip():
                raise ValueError("Save an AcoustID application key in Settings for repair or fingerprint verification.")
        code = run_command([sys.executable, "-u", ROOT / "Savify_automation" / "run_savify_audit.py"] + plan)
        state = audit_state_dir(job)
        if (state / "report.csv").is_file():
            event("artifact", path=str((state / "report.csv").absolute()), label="Library report")
        replacements = repair_log_path(job, job_dir)
        if replacements.is_file():
            print("Replacement records: " + str(replacements), flush=True)
            event("repair_log", path=str(replacements))
        return code
    if action == "traktor":
        code = run_command([sys.executable, "-u", ROOT / "Savify_automation" / "traktor_reset_repaired.py"] + plan)
        manifest = traktor_report_dir(job, job_dir) / "manifest.json"
        if manifest.is_file():
            event("artifact", path=str(manifest), label="Traktor manifest")
        return code
    if action == "diagnostics":
        return diagnostics()
    if action == "install_deno":
        winget = shutil.which("winget")
        if not winget:
            raise ValueError("Install Microsoft's App Installer to enable winget, then retry.")
        return run_command([winget, "install", "--id", "DenoLand.Deno", "--exact", "--silent", "--disable-interactivity", "--accept-source-agreements", "--accept-package-agreements"])
    if action == "update_downloader":
        return run_command([sys.executable, "-u", "-m", "pip", "install", "--upgrade", "yt-dlp[default]", "ytmusicapi"])
    raise ValueError("Unsupported task.")


def savify_call(arguments):
    """Click's standalone wrapper discards return values; explicitly propagate them here."""
    import logging
    os.environ["CONVENIFY_GUI"] = "1"
    from savify.cli import main as command

    class Outcome(logging.Handler):
        failed = False

        def emit(self, record):
            message = record.getMessage()
            if "Failed Tracks:" in message or "Nothing found using the given query." in message:
                self.failed = True

    outcome = Outcome()
    logging.getLogger("savify").addHandler(outcome)
    try:
        code = command.main(args=arguments, standalone_mode=False)
        return code or (1 if outcome.failed else 0)
    finally:
        logging.getLogger("savify").removeHandler(outcome)


def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    if argv[:1] == ["--savify-call"]:
        return savify_call(argv[1:])
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--job", type=Path)
    parser.add_argument("--handshake", action="store_true")
    parser.add_argument("--validate", action="store_true")
    parser.add_argument("--diagnostics", action="store_true")
    args = parser.parse_args(argv)
    if args.handshake and sys.stdin.readline().strip() != "START":
        return 2
    os.environ.update(environment())
    try:
        if args.diagnostics:
            return diagnostics()
        if not args.job:
            parser.error("--job is required")
        job = json.loads(args.job.read_text(encoding="utf-8-sig"))
        job_dir = args.job.resolve().parent
        configure_run_logging(job, job_dir)
        if args.validate:
            validate(job, job_dir)
            print("Task settings are valid.")
            return 0
        started = time.time()
        event("started", action=job.get("action"))
        code = execute(job, job_dir)
        event("finished", exit_code=code, elapsed=round(time.time() - started, 1))
        return code
    except (Exception, KeyboardInterrupt) as exc:
        print(f"ERROR: {exc}", file=sys.stderr, flush=True)
        return 130 if isinstance(exc, KeyboardInterrupt) else 2


if __name__ == "__main__":
    raise SystemExit(main())
