#!/usr/bin/env python3
"""Extract an artist's Spotify discography links using the official Spotify Web API.

Rules:
- A release with exactly 1 track -> output the track URL.
- A release with 2+ tracks       -> output the album/release URL.

The module can be used from the command line or imported by another Python script.
It uses only the Python standard library.

Credentials are read from either:
1. Environment variables SPOTIPY_CLIENT_ID and SPOTIPY_CLIENT_SECRET, or
2. spotify_credentials.json next to this script:
   {"client_id": "...", "client_secret": "..."}
"""

from __future__ import annotations

import argparse
import base64
import json
import os
import re
import ssl
import subprocess
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

try:
    import certifi
except ImportError:  # pragma: no cover
    certifi = None

API_BASE = "https://api.spotify.com/v1"
TOKEN_URL = "https://accounts.spotify.com/api/token"
DEFAULT_MARKET = "DE"
MAX_RETRIES = 6


class SpotifyError(RuntimeError):
    pass


def _safe_filename(name: str) -> str:
    name = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "", name)
    name = re.sub(r"\s+", " ", name).strip()
    return name or "Spotify Discography"


def build_queue_block(artist_name: str, links: list[str]) -> str:
    """Return a queue section with an empty line, a hashtagged artist header and URLs."""
    artist_label = str(artist_name).strip() or "Unknown Artist"
    cleaned_links = [str(link).strip() for link in links if str(link).strip()]

    block = ["", f"# {artist_label}"]
    block.extend(cleaned_links)
    return "\n".join(block) + ("\n" if cleaned_links else "\n")


def append_to_queue_file(queue_file: str | Path, artist_name: str, links: list[str]) -> Path:
    """Append a formatted artist block to the shared download queue file."""
    queue_path = Path(queue_file).expanduser().resolve()
    queue_path.parent.mkdir(parents=True, exist_ok=True)

    existing = ""
    if queue_path.exists():
        existing = queue_path.read_text(encoding="utf-8")

    if existing and not existing.endswith("\n"):
        existing += "\n"

    queue_path.write_text(existing + build_queue_block(artist_name, links), encoding="utf-8")
    return queue_path


def parse_artist_id(value: str) -> str:
    """Accept a Spotify artist ID, URI, artist URL, or discography URL."""
    value = value.strip()

    m = re.search(r"spotify:artist:([A-Za-z0-9]+)", value)
    if m:
        return m.group(1)

    m = re.search(r"/artist/([A-Za-z0-9]+)", value)
    if m:
        return m.group(1)

    if re.fullmatch(r"[A-Za-z0-9]+", value):
        return value

    raise ValueError(
        "Could not determine the Spotify artist ID. Pass an artist URL, "
        "discography URL, Spotify URI, or raw artist ID."
    )


def _load_credentials(credentials_file: str | Path | None = None) -> tuple[str, str]:
    client_id = os.environ.get("SPOTIPY_CLIENT_ID", "").strip()
    client_secret = os.environ.get("SPOTIPY_CLIENT_SECRET", "").strip()

    if client_id and client_secret:
        return client_id, client_secret

    if credentials_file is None:
        credentials_file = Path(__file__).resolve().with_name("spotify_credentials.json")
    else:
        credentials_file = Path(credentials_file)

    if credentials_file.exists():
        try:
            data = json.loads(credentials_file.read_text(encoding="utf-8"))
            client_id = str(data.get("client_id", "")).strip()
            client_secret = str(data.get("client_secret", "")).strip()
        except (OSError, json.JSONDecodeError) as exc:
            raise SpotifyError(f"Could not read {credentials_file}: {exc}") from exc

    if not client_id or not client_secret:
        raise SpotifyError(
            "Spotify API credentials were not found. Set SPOTIPY_CLIENT_ID and "
            "SPOTIPY_CLIENT_SECRET, or create spotify_credentials.json next to "
            "this script."
        )

    return client_id, client_secret


def _decode_error_body(exc: HTTPError) -> tuple[str, dict[str, Any] | None]:
    try:
        raw = exc.read().decode("utf-8", errors="replace")
    except Exception:
        return "", None

    try:
        obj = json.loads(raw)
        return raw, obj if isinstance(obj, dict) else None
    except json.JSONDecodeError:
        return raw, None


def _ssl_context() -> ssl.SSLContext:
    """Use certifi's CA bundle when available so urllib validates Spotify TLS correctly."""
    if certifi is not None:
        try:
            return ssl.create_default_context(cafile=certifi.where())
        except Exception:
            pass
    return ssl.create_default_context()


@dataclass
class SpotifyClient:
    client_id: str
    client_secret: str
    timeout: float = 30.0

    def __post_init__(self) -> None:
        self._token: str | None = None
        self._token_expires_at = 0.0

    def _get_token(self, force: bool = False) -> str:
        if (
            not force
            and self._token
            and time.monotonic() < self._token_expires_at - 60
        ):
            return self._token

        basic = base64.b64encode(
            f"{self.client_id}:{self.client_secret}".encode("utf-8")
        ).decode("ascii")

        body = urlencode({"grant_type": "client_credentials"}).encode("ascii")
        request = Request(
            TOKEN_URL,
            data=body,
            method="POST",
            headers={
                "Authorization": f"Basic {basic}",
                "Content-Type": "application/x-www-form-urlencoded",
                "User-Agent": "spotify-discography-link-extractor/1.0",
            },
        )

        try:
            with urlopen(request, timeout=self.timeout, context=_ssl_context()) as response:
                payload = json.load(response)
        except HTTPError as exc:
            raw, _ = _decode_error_body(exc)
            raise SpotifyError(
                f"Spotify authentication failed (HTTP {exc.code}). {raw}"
            ) from exc
        except URLError as exc:
            raise SpotifyError(f"Could not reach Spotify authentication: {exc}") from exc

        token = payload.get("access_token")
        if not token:
            raise SpotifyError("Spotify did not return an access token.")

        self._token = str(token)
        expires_in = int(payload.get("expires_in", 3600))
        self._token_expires_at = time.monotonic() + expires_in
        return self._token

    def get_json(self, url: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
        if params:
            query = urlencode({k: v for k, v in params.items() if v is not None})
            url = f"{url}{'&' if '?' in url else '?'}{query}"

        refreshed_after_401 = False

        for attempt in range(MAX_RETRIES + 1):
            token = self._get_token(force=False)
            request = Request(
                url,
                headers={
                    "Authorization": f"Bearer {token}",
                    "Accept": "application/json",
                    "User-Agent": "spotify-discography-link-extractor/1.0",
                },
            )

            try:
                with urlopen(request, timeout=self.timeout, context=_ssl_context()) as response:
                    data = json.load(response)
                    if not isinstance(data, dict):
                        raise SpotifyError(f"Unexpected Spotify response from {url}")
                    return data

            except HTTPError as exc:
                raw, body = _decode_error_body(exc)

                if exc.code == 401 and not refreshed_after_401:
                    self._get_token(force=True)
                    refreshed_after_401 = True
                    continue

                if exc.code == 429:
                    reason = None
                    if body:
                        reason = body.get("reason")
                        if reason is None and isinstance(body.get("error"), dict):
                            reason = body["error"].get("reason")

                    if reason == "QUOTA_EXCEEDED":
                        raise SpotifyError(
                            "Spotify API development quota exceeded. Try again after the "
                            "quota resets or use a different eligible developer account."
                        ) from exc

                    retry_after = exc.headers.get("Retry-After")
                    try:
                        delay = max(1.0, float(retry_after)) if retry_after else 2 ** attempt
                    except ValueError:
                        delay = 2 ** attempt

                    if attempt < MAX_RETRIES:
                        print(f"Rate limited by Spotify; retrying in {delay:.0f}s...")
                        time.sleep(delay)
                        continue

                if 500 <= exc.code <= 599 and attempt < MAX_RETRIES:
                    delay = min(30, 2 ** attempt)
                    print(f"Spotify server error {exc.code}; retrying in {delay}s...")
                    time.sleep(delay)
                    continue

                message = raw.strip() or str(exc.reason)
                raise SpotifyError(
                    f"Spotify API request failed (HTTP {exc.code}) for {url}: {message}"
                ) from exc

            except URLError as exc:
                if attempt < MAX_RETRIES:
                    delay = min(30, 2 ** attempt)
                    print(f"Network error; retrying in {delay}s...")
                    time.sleep(delay)
                    continue
                raise SpotifyError(f"Network error while contacting Spotify: {exc}") from exc

        raise SpotifyError(f"Spotify request failed after retries: {url}")


def _get_artist_releases(
    client: SpotifyClient,
    artist_id: str,
    market: str,
) -> list[dict[str, Any]]:
    """Fetch every album/single/compilation page and preserve API order."""
    url = f"{API_BASE}/artists/{artist_id}/albums"
    params: dict[str, Any] | None = {
        "include_groups": "album,single,compilation",
        "market": market,
        "limit": 10,  # Current Development Mode maximum.
        "offset": 0,
    }

    releases: list[dict[str, Any]] = []
    seen_ids: set[str] = set()
    page_number = 0

    while url:
        page_number += 1
        data = client.get_json(url, params=params)
        params = None  # 'next' already contains its own query string.

        items = data.get("items") or []
        if not isinstance(items, list):
            raise SpotifyError("Spotify returned an invalid artist-albums response.")

        for item in items:
            if not isinstance(item, dict):
                continue
            release_id = item.get("id")
            if not release_id or release_id in seen_ids:
                continue
            seen_ids.add(str(release_id))
            releases.append(item)

        print(f"Fetched page {page_number}: {len(releases)} unique releases found...")
        next_url = data.get("next")
        url = str(next_url) if next_url else ""

    return releases


def _get_single_track_url(
    client: SpotifyClient,
    album_id: str,
    market: str,
) -> str:
    data = client.get_json(
        f"{API_BASE}/albums/{album_id}/tracks",
        params={"market": market, "limit": 1, "offset": 0},
    )

    items = data.get("items") or []
    if not items or not isinstance(items[0], dict):
        raise SpotifyError(f"One-track release {album_id} returned no track.")

    track = items[0]
    external_urls = track.get("external_urls") or {}
    track_url = external_urls.get("spotify") if isinstance(external_urls, dict) else None

    if not track_url:
        track_id = track.get("id")
        if track_id:
            track_url = f"https://open.spotify.com/track/{track_id}"

    if not track_url:
        raise SpotifyError(f"Could not determine track URL for one-track release {album_id}.")

    return str(track_url)


def extract_discography(
    artist: str,
    *,
    market: str = DEFAULT_MARKET,
    output_dir: str | Path = ".",
    output_file: str | Path | None = None,
    credentials_file: str | Path | None = None,
    verbose: bool = True,
    append_queue: bool = False,
    queue_file: str | Path = "download-queue.txt",
) -> Path:
    """Extract Spotify release/track URLs and return the written text-file path.

    This is the function to import from another Python script.
    """
    artist_id = parse_artist_id(artist)
    market = market.strip().upper()

    if not re.fullmatch(r"[A-Z]{2}", market):
        raise ValueError("market must be a two-letter country code, e.g. DE or US")

    client_id, client_secret = _load_credentials(credentials_file)
    client = SpotifyClient(client_id, client_secret)

    artist_obj = client.get_json(f"{API_BASE}/artists/{artist_id}")
    artist_name = str(artist_obj.get("name") or artist_id)

    if verbose:
        print(f"Artist: {artist_name}")
        print(f"Artist ID: {artist_id}")
        print(f"Market: {market}")
        print("Fetching releases...")

    releases = _get_artist_releases(client, artist_id, market)

    links: list[str] = []
    one_track_count = 0
    multi_track_count = 0

    for index, release in enumerate(releases, 1):
        release_id = str(release.get("id") or "")
        title = str(release.get("name") or release_id)

        try:
            total_tracks = int(release.get("total_tracks", 0))
        except (TypeError, ValueError):
            total_tracks = 0

        if not release_id:
            raise SpotifyError(f"Release #{index} has no Spotify ID: {title}")

        if total_tracks == 1:
            url = _get_single_track_url(client, release_id, market)
            one_track_count += 1
            kind = "TRACK"
        elif total_tracks >= 2:
            external_urls = release.get("external_urls") or {}
            url = external_urls.get("spotify") if isinstance(external_urls, dict) else None
            if not url:
                url = f"https://open.spotify.com/album/{release_id}"
            url = str(url)
            multi_track_count += 1
            kind = "RELEASE"
        else:
            raise SpotifyError(
                f"Release '{title}' ({release_id}) has invalid total_tracks={total_tracks}."
            )

        links.append(url)
        if verbose:
            print(f"[{index:>3}/{len(releases)}] {kind:7} {title}")

    if append_queue:
        queue_path = append_to_queue_file(queue_file, artist_name, links)
        if verbose:
            print()
            print(f"Appended {len(links)} links to queue: {queue_path}")
            print(f"  One-track releases -> track URLs: {one_track_count}")
            print(f"  Multi-track releases -> release URLs: {multi_track_count}")
        return queue_path

    if output_file is None:
        filename = f"{_safe_filename(artist_name)} - Spotify Discography.txt"
        out_path = Path(output_dir) / filename
    else:
        out_path = Path(output_file)

    out_path = out_path.expanduser().resolve()
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text("\n".join(links) + ("\n" if links else ""), encoding="utf-8")

    if verbose:
        print()
        print(f"Done: {len(links)} links written")
        print(f"  One-track releases -> track URLs: {one_track_count}")
        print(f"  Multi-track releases -> release URLs: {multi_track_count}")
        print(f"Output: {out_path}")

    return out_path


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Extract an artist's Spotify discography URLs. One-track releases become "
            "track URLs; multi-track releases remain album/release URLs."
        )
    )
    parser.add_argument(
        "artist",
        nargs="?",
        help="Spotify artist URL, discography URL, Spotify URI, or artist ID",
    )
    parser.add_argument(
        "--market",
        default=DEFAULT_MARKET,
        help=f"Spotify market/country code (default: {DEFAULT_MARKET})",
    )
    parser.add_argument(
        "--output-dir",
        default=".",
        help="Directory for the artist-named text file (default: current directory)",
    )
    parser.add_argument(
        "--output",
        help="Exact output TXT path. Overrides --output-dir and artist-based filename.",
    )
    parser.add_argument(
        "--credentials",
        help="Path to spotify_credentials.json (otherwise environment variables or file next to script)",
    )
    parser.add_argument(
        "--after",
        help=(
            "Optional Python script to run after extraction. The generated TXT path is "
            "passed to it as the first argument."
        ),
    )
    parser.add_argument(
        "--append-queue",
        action="store_true",
        help="Append the extracted URLs to download-queue.txt as a '# Artist' block instead of writing a text file.",
    )
    parser.add_argument(
        "--queue-file",
        default="download-queue.txt",
        help="Queue file to append to when --append-queue is used (default: download-queue.txt).",
    )
    args = parser.parse_args(argv)

    artist = args.artist
    if not artist:
        artist = input("Spotify artist URL or artist ID: ").strip()

    try:
        output_path = extract_discography(
            artist,
            market=args.market,
            output_dir=args.output_dir,
            output_file=args.output,
            credentials_file=args.credentials,
            verbose=True,
            append_queue=args.append_queue,
            queue_file=args.queue_file,
        )

        if args.after:
            after_script = Path(args.after).expanduser().resolve()
            if not after_script.exists():
                raise SpotifyError(f"Post-processing script not found: {after_script}")

            print()
            print(f"Running post-processing script: {after_script}")
            completed = subprocess.run(
                [sys.executable, str(after_script), str(output_path)],
                check=False,
            )
            if completed.returncode != 0:
                raise SpotifyError(
                    f"Post-processing script exited with code {completed.returncode}."
                )

        return 0

    except (SpotifyError, ValueError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        print("\nCancelled.", file=sys.stderr)
        return 130


if __name__ == "__main__":
    raise SystemExit(main())
