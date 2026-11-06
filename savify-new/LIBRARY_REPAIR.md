# Verify and repair an existing music collection

Run the tool against a directory; it scans that directory and all subdirectories.
It reads the intended artist/title from each file's existing tags and checks
independently known duration before identifying the audio using Chromaprint and
AcoustID. With `--repair`, it attempts replacements for confirmed mismatches and
corrupt files.

Every replacement must pass duration checks, full audio decode, and tag/artwork
preservation checks. Identity is normally confirmed by fingerprint. For a proven
duration mismatch, a strong artist/title/version and length match can also qualify
when AcoustID does not recognize the replacement (details below). The original is backed up
before an atomic replacement at the **same path and filename**. Verified files
are left byte-for-byte unchanged, including their existing Traktor data.

This tool cannot certify every recording. AcoustID coverage and recording links
are incomplete, especially for obscure releases. No match, conflicting links,
uncertain version metadata, and insufficient evidence produce `unknown` when
duration has not already disproved the file. Missing fingerprints alone never
declare a file correct or wrong. Database matches are evidence,
not a guarantee of the exact release/master. The first 120 seconds are fingerprinted;
the entire file is decoded unless an initial duration check already proves it
wrong. All downloaded replacements are fully decoded. Listening remains
necessary for cases the report cannot resolve.

## Setup

Use Python 3.11+ and run these commands from the extracted package folder (the
folder containing `savify/`). You can also merge this entire `savify/` folder into
your existing installation. Install the dependencies in that Python environment:

```bash
python -m pip install -r requirements.txt
```

FFmpeg is required. For fingerprinting, either use an FFmpeg build with its
`chromaprint` muxer, or install the [Chromaprint fpcalc utility](https://acoustid.org/chromaprint).
The program checks this before starting. Pass `--ffmpeg-location PATH` for a custom
FFmpeg executable/directory; pass `--fpcalc PATH` if using fpcalc. An FFmpeg
executable kept inside `savify/` should be passed explicitly to this audit command.

Register an [AcoustID application](https://acoustid.org/new-application) and use
its **application/client API key**, not a personal fingerprint-submission key.
The [AcoustID service documentation](https://acoustid.org/webservice) explains the
two key types. Supply it as the `ACOUSTID_API_KEY` environment variable:

Windows PowerShell:

```powershell
$env:ACOUSTID_API_KEY = "YOUR_APPLICATION_KEY"
python -m savify.library_audit "D:\Music" --limit 25
```

macOS/Linux:

```bash
export ACOUSTID_API_KEY='YOUR_APPLICATION_KEY'
python -m savify.library_audit "/path/to/Music" --limit 25
```

The key is not written to reports or the lookup cache. Fingerprint data and full
file duration are sent to AcoustID; the audio file itself is not uploaded. Requests
are sequential and spaced at least 0.4 seconds apart, below the documented service
limit of three requests per second, with bounded retries for temporary failures.

## Audit and repair

Audit the whole tree without changing music files:

```bash
python -m savify.library_audit "/path/to/Music"
```

Close Traktor and any tag editors, then enable automatic repair:

```bash
python -m savify.library_audit "/path/to/Music" --repair
```

Replace `/path/to/Music` with your actual path, including `D:\Music` on Windows.
No per-file prompts occur. Repaired files retain their extension and container
type; bitrate/encoding may differ because the source audio is new. Downloading
from YouTube cannot restore lossless quality to a lossless file format.

The same command resumes a stopped run. Unchanged verified/repaired files are
recognized by a full-file SHA-256 checksum and skipped. Unknown and failed files
are revisited; successful AcoustID responses are cached for seven days. Use
`--rescan` to recheck previously accepted files and refresh fingerprint lookups.
`Ctrl+C` stops the run without undoing completed repairs.

If you want to attempt a new source even for unrecognized/ambiguous originals:

```bash
python -m savify.library_audit "/path/to/Music" --repair --repair-unknown
```

This may replace a file that was already correct but unrecognized. It still never
publishes an unverified replacement. Without this option, unknown originals are
left untouched. Sources that cannot be positively identified remain unresolved.

## Optional catalog evidence and source choices

Existing title and artist tags are necessary. Spotify credentials are **optional**.
If available, enable independent duration/ISRC lookup:

```powershell
$env:SPOTIPY_CLIENT_ID = "YOUR_CLIENT_ID"
$env:SPOTIPY_CLIENT_SECRET = "YOUR_CLIENT_SECRET"
python -m savify.library_audit "D:\Music" --repair --spotify
```

On macOS/Linux, set the same names with `export`. This helps catch short clips and
album-length files that a fingerprint service cannot identify. Catalog results
must agree with the existing artist/title/version; ambiguous lengths are ignored.
No catalog values overwrite the original basic tags. A wrong artist/title tag
cannot be distinguished from an incorrectly downloaded file by this workflow;
correct the intended tags before running the tool if those are also wrong.

### Duration takes priority

With `--spotify`, a reliable catalog duration is compared with the file's measured
duration **before fingerprint generation, lookup, or full decoding of that file**.
A clear difference marks it `mismatch` immediately and triggers replacement search
when `--repair` is enabled. An unknown fingerprint is not required to reach this
decision. Expected length must come from the independently matched catalog; it is
never taken from the suspect file, its embedded duration tags, or the proposed
YouTube replacement.

The mismatch threshold allows modest intros/outros: for tracks at least 60 seconds,
12% of expected duration, with an 8-second minimum and 30-second maximum. Other
lengths use the existing ordinary tolerance (6%, clamped to 3–15 seconds; below
30 seconds, 12% with a one-second minimum). For example, a 5-second clip or a
one-hour upload cannot pass for a three-minute track. A small encoding difference
does not trigger replacement; normal fingerprint verification continues instead.

For these duration-proven mismatches only, an unrecognized replacement fingerprint
does not prevent repair if the inspected source has a strong title match (at least
0.94), strong artist evidence (at least 0.90), and the requested version. Both its
declared duration and downloaded audio duration must fit the **ordinary, tighter**
duration tolerance. Album, label and track IDs are still not requirements.

A fingerprint that identifies the replacement as wrong, conflicting recording
links, lookup/network errors, or weak source metadata still rejects the candidate.
Manual selections still require a positive fingerprint match because they can
bypass normal search gates. Repairs triggered by fingerprints, corruption, or
`--repair-unknown` also retain the positive-fingerprint requirement.

Duration plus metadata is less conclusive than fingerprint identification. The
report and recovery journal explicitly distinguish `verification=metadata_duration`
from `verification=fingerprint`; the former retains the unknown fingerprint result
and the original duration-mismatch evidence. Backups and Traktor tag removal work
the same way in either case.

The YouTube matcher retains the previous accuracy improvements: multiple candidates,
title/artist/version comparison, duration checks where independently available,
and fallbacks. **Album, label, ISRC and track IDs are optional evidence**, not
required fields on reuploads. Popularity never overrides song identity.

`--max-candidates 5` limits downloaded alternatives per file (default five).
`--cookies /path/to/cookies.txt` supplies an optional yt-dlp cookie file.
`--match-overrides overrides.json` accepts explicit source selections keyed by the
report's `track_id` (a Spotify ID where available, otherwise a stable `local-...`
identifier), for example:

```json
{
  "TRACK_ID_FROM_REPORT": "https://www.youtube.com/watch?v=VIDEO_ID"
}
```

Even a manually selected video must pass audio fingerprint verification before
replacement. The original file's own duration is never used as the desired length.

## What metadata survives

Metadata is copied from the original file, not substituted with YouTube tags.
Only explicitly allowed song fields are copied into a freshly remuxed audio file.
Embedded artwork bytes and the retained metadata are compared again after writing.

| Preserved when present | Removed from replacement files |
| --- | --- |
| Title/subtitle, artist(s), album, album artist | Traktor `PRIV`, `GEOB`, `TRAKTOR4` and other opaque/private payloads |
| Track/disc numbers, dates, genre, composer, lyricist and supported sort fields | Cue points, grids, loops, ratings, play counts and timed annotation frames |
| Publisher/label, copyright, lyrics, catalog number and barcode | BPM, musical key, ReplayGain and other gain/analysis fields |
| Common Spotify, MusicBrainz, ISRC and Discogs identifiers | Arbitrary custom tags, comments and unrecognized vendor fields |
| Embedded covers, including their image bytes and descriptions | Download-source tags and embedded chapters from the new source |

The exact allowlist is in `savify/library_tags.py`. It deliberately does not copy
all custom fields because DJ analysis can be stored there. Less common custom
identifier fields may need an explicit entry in that allowlist. The CSV identifies
which fields were discarded for each completed replacement. Original backups retain
**all** original bytes and tags, including Traktor data.

Handled extensions: MP3, FLAC, M4A/MP4 audio, Ogg Vorbis, Opus, WAV, AIF/AIFF, and
AAC. AAC requires readable ID3 song tags. WAV supports ID3 and standard RIFF INFO
text tags; RIFF text is represented as ID3 in the replacement. Missing or unreadable
artist/title tags are reported as an error for review; filenames are not guessed.
Symlink directories are not followed and symlink audio files are not replaced.

## Traktor Pro 3 after repair

Close Traktor during the repair so it cannot write old analysis back while files
are being replaced. Only repaired files lose Traktor metadata; correctly identified
tracks keep their cues and grids.

Traktor also maintains its own collection database. Clearing a file's embedded
analysis does **not** clear the corresponding entry in `collection.nml`. Back up
your Traktor collection, then use the report to remove the repaired entries from
the collection **without deleting the audio**, re-import those files, and analyze
the new recordings. Where Traktor offers a choice between file tags and collection
tags, choose **File** for the repaired tracks. Do not apply old collection metadata
to the new files. Review the resulting entries before DJ use.

The tool does not edit `collection.nml`, playlists, or unrelated sidecar files.
Removing/re-importing collection entries may affect playlist membership, so retain
your collection backup while updating Traktor. The report gives the exact repaired
paths for that step.

References: [Native Instruments collection/tag guidance](https://support.native-instruments.com/support/solutions/articles/69000879494-how-to-restore-the-traktor-track-collection-from-a-backup),
[Traktor embedded metadata](https://github.com/willwillems/traktor-metadata),
[TRAKTOR4 field structure](https://hellricer.github.io/2021/05/05/decoding-traktor4-field.html).

## Reports, backups and recovery

By default, everything is written under `Music/_savify_audit/`, excluded from
scanning. Choose a separate location with `--state-dir PATH`; reuse that location
when resuming. Do not share a state directory between different music roots.

| File/directory | Purpose |
| --- | --- |
| `report.csv` | Latest result for each encountered path: status, intended song, original/expected/current durations, repair trigger, verification method, reason, backup and replacement URL |
| `events.jsonl` | Append-only results, including detailed fingerprint recording evidence |
| `audit.sqlite3` | Checkpoints, lookup cache and replacement journal |
| `backups/` | Byte-for-byte originals, grouped by hash and their relative paths |
| `youtube-matching.jsonl` | Candidate searches and acceptance/rejection details |

CSV is refreshed every 25 files and at completion or normal interruption. JSONL
and SQLite are updated after every processed file. Historical paths remain in the
report if you later move/remove files. Backups are never automatically deleted.
Plan space for the originals plus one temporary replacement at a time.

To undo a repair, close Traktor and copy the report's `backup` file back to its
corresponding `path`, overwriting the repaired file. This also restores the original
Traktor data. The next audit detects the changed checksum and checks the file again.

The original is checked for concurrent modification before backup and immediately
before replacement. A failed download, identity check, tag/artwork check, backup,
or atomic replacement leaves the original in place. A prepared replacement is
journaled before the atomic swap so a process interruption immediately afterward
can be recovered on the next run. One process at a time may use a state directory.
Keep editors closed; modification checks cannot coordinate with an application
that writes to the file at precisely the moment of replacement.

Statuses: `verified`, `mismatch`, `corrupt`, `unknown`, `repaired`, `repair_failed`,
or `error`. Exit codes: 0 = completed with no unresolved encountered files;
1 = unresolved/review cases remain; 2 = setup/fatal error; 130 = interrupted.

## Validation

The combined suite contains 170 tests. It uses synthetic audio and real FFmpeg,
Chromaprint, Mutagen, and yt-dlp conversion where applicable. AcoustID responses
and repair downloads are simulated to test decision-making and failure handling
without relying on an external catalog or modifying a real music collection.
Duration tests cover clips, album-length files, small timing differences, skipped
original fingerprints/decoding, unrecognized replacements, conflicting identities,
weak source metadata, service failures, and recovery of duration-based repairs.

Live AcoustID lookup and live YouTube repair have not been validated with your
application key or collection. Windows locking code is included; automated checks
ran on Linux with Python 3.12. Start with `--limit 25`, inspect the report and repaired
files, then run the full collection.

```bash
python -m pip install -r requirements-dev.txt
python -m pytest -q tests
```
