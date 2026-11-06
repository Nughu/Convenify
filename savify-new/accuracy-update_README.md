# Convenify / Savify accuracy update

This package updates the supplied modified Savify scripts. It selects and validates
an individual recording before downloading it. A fan reupload needs no album,
label, ISRC, track number, verified channel, or matching cross-platform track ID.

## Installation

1. Back up your existing modified Savify package.
2. Merge the included `savify/` folder into that package. Replace the five changed
   existing scripts and add **all three new modules** listed below. Other supplied
   scripts are included unchanged for convenience. Preserve any additional files
   from your existing installation.
3. In the Python environment that runs Savify, install `ytmusicapi` and keep
   `yt-dlp` current. For the exact tested dependency set, run:

   ```bash
   python -m pip install -r requirements.txt
   ```

   This pinned dependency set requires **Python 3.11 or newer**; validation ran on
   Python 3.12. The archive is a package overlay, not a pip-installable project.
4. Keep FFmpeg installed or your existing bundled FFmpeg executable. FFprobe is
   used when available; audio validation also supports a standalone FFmpeg binary.
5. Keep your existing Spotify credentials and invocation. To run directly from the
   extracted folder, use `python -m savify`.

If `ytmusicapi` is absent, the resolver warns once and uses validated regular
YouTube searches. No YouTube Music login is required for the public searches.

## Matching behavior

1. An explicit manual mapping, if present, takes priority.
2. A previously successful cached URL is re-inspected and re-scored.
3. YouTube Music song searches use the artist/title and available ISRC.
4. Album-track lists and additional artist/album queries provide fallback
   candidates. Album tracks still pass the same title, artist, and duration checks.
5. Regular YouTube searches retrieve up to ten candidates per query. The best five
   plausible candidates per stage receive full metadata inspection.
6. Only one accepted URL is downloaded at a time. A failed or invalid download
   advances to the next accepted candidate. Exhaustion reports a failure rather
   than selecting an unrelated result.

The default score combines title (55%), artist (30%), and duration (15%). The
minimum score is 0.82, with separate title and artist gates. A matching album gives
a small bonus; a missing or different album gives **no penalty**. Label, release
IDs, ISRC equality, track number, verification badges, and view counts are not
acceptance requirements. ISRC is a search hint and never automatic proof of identity.

Artist evidence can come from structured metadata, the video title, channel, or
recognizable description credits. All credited artists are available for title
cleanup and fallback queries; evidence for the primary artist is still needed.
There is no automatic requirement that the uploader be the artist.

Unicode and accents are normalized. Presentation suffixes, featured-artist text,
remaster years, and “Original Mix” are handled during comparison. Explicitly
different live/remix/acoustic/instrumental/cover/sped-up versions are rejected.
Meaningful requested version qualifiers must be confirmed. Metadata matching
cannot identify deliberately mislabeled audio with certainty.

Ordinary duration tolerance is 6% of the expected length, clamped to 3–15 seconds.
For tracks under 30 seconds it is 12%, with a one-second minimum. Very strong title
and artist matches may allow a video intro/outro up to 12%, capped at 30 seconds,
for tracks at least a minute long. There is no global minimum song length.
Unknown search-result duration requires stronger identity evidence and is resolved
from full metadata or the actual audio before acceptance.

Album/ISRC lookup failures do not block regular matching. If a simplified Spotify
album response lacks ISRC, full track details are requested only as a late fallback.

## Commands

Use real Spotify URLs in place of the placeholders:

```bash
python -m savify "SPOTIFY_ALBUM_URL"
python -m savify --search-results 15 "SPOTIFY_ALBUM_URL"
python -m savify --no-youtube-music "SPOTIFY_TRACK_URL"
python -m savify --verify-existing "SPOTIFY_ALBUM_URL"
python -m savify --redownload --no-match-cache "SPOTIFY_ALBUM_URL"
python -m savify --match-overrides youtube-overrides.json "SPOTIFY_TRACK_URL"
```

| Option | Effect |
| --- | --- |
| `--search-results 10` | Candidate search count, from 1 to 50. |
| `--match-threshold 0.82` | Overall score threshold; independent title/artist/duration gates remain active. |
| `--no-youtube-music` | Use regular YouTube matching only. |
| `--no-match-cache` | Do not read or update successful URL mappings. Manual mappings still apply. |
| `--match-overrides FILE` | Read explicit Spotify-to-YouTube selections from JSON. |
| `--verify-existing` | Check existing audio duration/decoding and its saved title/artist source record. |
| `--redownload` | Attempt a fresh validated replacement, including for existing files. |
| `--workers 4` | Concurrent track limit, from 1 to 16 in the CLI. |

Your configured 45–60 second pre-download wait and 30 second request/retry waits
are preserved, with corrected Python option names. **The request wait now actually
takes effect**, so multi-candidate searches can take considerably longer.
Pass additional yt-dlp options **after the Spotify query**. They are parsed by
yt-dlp itself, including booleans, numerical values, and retry expressions:

```bash
python -m savify "SPOTIFY_TRACK_URL" --sleep-requests 2 --sleep-interval 5 --max-sleep-interval 10
```

Searches reuse transport/auth settings where applicable. They do not inherit
download filters or postprocessors. Savify owns its temporary output paths,
single-video selection, and audio-conversion postprocessor; flags that would
turn that operation into a playlist, simulation, or skipped download are not used.

## Manual mappings and records

Copy `youtube-overrides.example.json` to `youtube-overrides.json`, replacing the
placeholder keys with Spotify track IDs or URIs and values with actual YouTube
video IDs or individual video URLs. Use `--match-overrides` to select the file,
or place it in Savify's data directory. An explicit mapping bypasses textual
matching, but audio availability, duration, and decoding checks still apply.
An unavailable manual selection is reported without silently replacing that choice.

The data directory contains:

- `youtube-matches.json`: successful Spotify-to-YouTube mappings and their evidence.
- `youtube-matching.jsonl`: candidates, rejection reasons, failures, and accepted sources.
- `youtube-overrides.json`: optional user-maintained manual selections.

Each new audio file also gets a neighboring `FILE.ext.source.json` containing the
source URL/title/channel, scores, expected track details, and measured duration.
These records describe the matching decision; they are not audio fingerprints.

Existing files are skipped by default, preserving the old behavior. An old file
without a source record cannot be verified by `--verify-existing`; it is reported
for review and left intact. Use `--redownload` to attempt a replacement.
Different Spotify release IDs alone do not invalidate an existing source record.

## Audio and file handling

- The completed audio must have a measurable positive duration and decode fully.
- Its duration is compared with Spotify and the inspected video, where available.
- Partial files and metadata sidecars cannot be mistaken for completed audio.
- Each attempt has its own temporary directory. Duplicate destinations are
  serialized within one Savify instance.
- Final files are published atomically after validation. A failed replacement
  leaves an existing audio file intact.
- Metadata is passed to FFmpeg as individual arguments, preserving quotes and
  non-ASCII text. MP3 cover art is retained; unavailable/bad artwork falls back to
  correctly tagged audio without artwork.
- AAC container differences are remuxed into the requested `.aac` output. Vorbis
  uses the standard `.ogg` extension.

## Optional fingerprint verification

No external fingerprint database is enabled automatically: it would require a
reference/database choice, credentials where applicable, and coverage decisions.
The Python API supports an additional verifier:

```python
from savify import Savify

def verify_identity(audio_path, spotify_track, selected_match):
    # Compare with your trusted reference or fingerprint service here.
    # Return exactly True only when your check accepts the recording.
    raise NotImplementedError("Connect your chosen fingerprint verifier")

app = Savify(audio_verifier=verify_identity)
```

The callback runs after duration/decoding validation and before publishing or
caching. `False` rejects the candidate. An exception fails that track rather than
publishing unverified audio.

## Changed files and validation

| File | Change |
| --- | --- |
| `savify.py` | Separate resolution/download; validated alternatives; atomic output; source records; existing-file audit. |
| `track.py` | Preserve optional duration, ISRC, and explicit status. |
| `spotify.py` | Lazy ISRC enrichment, complete album pagination, avoid mutating raw album tracks. |
| `cli.py` | Matching controls and proper yt-dlp argument parsing. |
| `utils.py` | Correct FFmpeg detection. |
| `youtube_matcher.py` | New matching, search-provider, caching, and audit-log module. |
| `media_validation.py` | New audio verification and metadata publication module. |
| `ydl_options.py` | New Python/CLI option translation module. |

Run the regression tests from this folder:

```bash
python -m pip install -r requirements-dev.txt
python -m pytest -q tests
```

Validation completed with **73 passing tests**. Tests exercise sparse reuploads, unrelated popular results, short clips, full
albums, version mismatches, Unicode, unknown duration, fallback candidates,
manual mappings, failed replacements, concurrency, and real yt-dlp/FFmpeg conversion
of synthetic audio in MP3, M4A, AAC, Opus, Vorbis, FLAC, and WAV formats.

A live YouTube Music metadata search succeeded during validation. Live regular
YouTube search was blocked by this execution environment's certificate trust
configuration. No certificate-verification bypass was introduced. Full live
YouTube downloading and your specific problematic albums remain unbenchmarked.
