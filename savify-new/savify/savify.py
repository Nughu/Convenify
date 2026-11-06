"""Main module for Savify."""
__all__ = ['Savify']

import time
import traceback
from multiprocessing import cpu_count
from multiprocessing.dummy import Pool as ThreadPool
from pathlib import Path
import json
import os
import threading
from tempfile import TemporaryDirectory
from dataclasses import asdict
from uuid import uuid4
from urllib.error import URLError
from urllib.parse import urlparse

import validators
import requests
from yt_dlp import YoutubeDL
from yt_dlp.utils import DownloadError

from .utils import PathHolder, safe_path_string, check_env, check_ffmpeg, check_file, create_dir
from .types import *
from .spotify import Spotify
from .track import Track
from .youtube_matcher import Match, MatchConfig, MatchStore, SearchProvider, YouTubeMatcher, NoReliableMatch, normalize
from .media_validation import AudioValidationError, validate_audio, publish_audio
from .ydl_options import normalize_options
from .logger import Logger
from .exceptions import FFmpegNotInstalledError, SpotifyApiCredentialsNotSetError, UrlNotSupportedError, \
    YoutubeDlExtractionError, InternetConnectionError


def _sort_dir(track: Track, group: str) -> str:
    if not group:
        return str()

    group = group.replace('%artist%', safe_path_string(track.artists[0]))
    group = group.replace('%album%', safe_path_string(track.album_name))
    group = group.replace('%playlist%', safe_path_string(track.playlist or ''))

    return str(group)


def _progress(data) -> None:
    if data['status'] == 'downloading':
        pass
    elif data['status'] == 'finished':
        pass
    elif data['status'] == 'error':
        raise YoutubeDlExtractionError


class Savify:
    def __init__(self, api_credentials=None, quality=Quality.BEST, download_format=Format.MP3,
                 group=None, path_holder: PathHolder = None, retry: int = 1,
                 ydl_options: dict = None, skip_cover_art: bool = False, logger: Logger = None,
                 ffmpeg_location: str = 'ffmpeg', match_config: MatchConfig = None,
                 match_overrides=None, verify_existing=False, redownload=False,
                 workers=None, audio_verifier=None) -> None:

        self.download_format = download_format
        self.ffmpeg_location = ffmpeg_location
        if ffmpeg_location == 'ffmpeg':
            for name in ('ffmpeg.exe', 'ffmpeg'):
                bundled = Path(__file__).resolve().parent / name
                if bundled.is_file():
                    self.ffmpeg_location = str(bundled)
                    break
        self.verify_existing = verify_existing
        self.redownload = redownload
        self.workers = min(4, cpu_count()) if workers is None else workers
        if self.workers < 1:
            raise ValueError('workers must be a positive integer')
        self.audio_verifier = audio_verifier
        self._state_lock = threading.RLock()
        self._output_locks = {}
        self.skip_cover_art = skip_cover_art
        self.downloaded_cover_art = dict()
        self.quality = quality
        self.queue_size = 0
        self.completed = 0
        if retry < 0:
            raise ValueError('retry cannot be negative')
        self.retry = retry
        self.group = group

        # Config or defaults...
        self.ydl_options = normalize_options(ydl_options or {})
        self.path_holder = path_holder or PathHolder()
        self.logger = logger or Logger(self.path_holder.data_path)

        if api_credentials is None:
            if not check_env():
                raise SpotifyApiCredentialsNotSetError

            self.spotify = Spotify()
        else:
            self.spotify = Spotify(api_credentials=api_credentials)

        if not check_ffmpeg() and self.ffmpeg_location == 'ffmpeg':
            raise FFmpegNotInstalledError

        self.match_config = match_config or MatchConfig()
        self.match_store = MatchStore(self.path_holder.data_path, self.logger, match_overrides)
        self.matcher = YouTubeMatcher(
            SearchProvider(self._transport_options(), self.logger, self.match_config),
            self.match_store, self.logger, self.match_config, self.spotify.enrich_track)
        self.check_for_updates()

    def check_for_updates(self) -> None:
        #self.logger.info('Preparing download...')
        pass

    def _parse_query(self, query, query_type=Type.TRACK, artist_albums: bool = False) -> list:
        result = list()
        if validators.url(query) or query.startswith('spotify:'):
            hostname = urlparse(query).hostname or ''
            if query.startswith('spotify:') or hostname == 'spotify.com' or hostname.endswith('.spotify.com'):
                result = self.spotify.link(query, artist_albums=artist_albums)
            else:
                raise UrlNotSupportedError(query)

        else:
            if query_type == Type.TRACK:
                result = self.spotify.search(query, query_type=Type.TRACK)

            elif query_type == Type.ALBUM:
                result = self.spotify.search(query, query_type=Type.ALBUM)

            elif query_type == Type.PLAYLIST:
                result = self.spotify.search(query, query_type=Type.PLAYLIST)

            elif query_type == Type.ARTIST:
                result = self.spotify.search(query, query_type=Type.ARTIST, artist_albums=artist_albums)

        return result

    def download(self, query, query_type=Type.TRACK, create_m3u=False, artist_albums: bool = False) -> None:
        try:
            queue = self._parse_query(query, query_type=query_type, artist_albums=artist_albums)
            self.queue_size += len(queue)
        except (requests.exceptions.ConnectionError, URLError):
            raise InternetConnectionError

        if not (len(queue) > 0):
            self.logger.info('Nothing found using the given query.')
            return

        self.logger.info(f'Downloading {len(queue)} songs...')
        start_time = time.time()
        with ThreadPool(self.workers) as pool:
            jobs = pool.map(self._download, queue)

        failed_jobs = list()
        successful_jobs = list()
        for job in jobs:
            if job['returncode'] != 0:
                failed_jobs.append(job)
            else:
                successful_jobs.append(job)

        if create_m3u and len(successful_jobs) > 0:
            track = successful_jobs[0]['track']
            playlist = safe_path_string(track.playlist or '')

            if not playlist:
                if query_type in {Type.EPISODE, Type.SHOW, Type.ALBUM}:
                    playlist = track.album_name
                elif query_type == Type.ARTIST:
                    playlist = track.artists[0]
                else:
                    playlist = track.name

            playlist = safe_path_string(playlist)
            m3u = f'#EXTM3U\n#PLAYLIST:{playlist}\n'
            m3u_location = self.path_holder.get_download_dir() / f'{playlist}.m3u'

            for job in successful_jobs:
                track = job['track']
                location = job['location']
                m3u += f'#EXTINF:{int(track.duration or -1)},{str(track)}\n'
                from os.path import relpath
                m3u += f'{relpath(location, m3u_location.parent)}\n'

            self.logger.info('Creating the M3U playlist file..')
            with open(m3u_location, 'w', encoding='utf-8') as m3u_file:
                m3u_file.write(m3u)

        message = f'Download Finished!\n\tCompleted {len(queue) - len(failed_jobs)}/{len(queue)}' \
                  f' songs in {time.time() - start_time:.0f}s\n'

        if len(failed_jobs) > 0:
            message += '\n\tFailed Tracks:\n'
            for failed_job in failed_jobs:
                message += f'\n\tSong:\t{str(failed_job["track"])}' \
                           f'\n\tReason:\t{failed_job["error"]}\n'

        self.logger.info(message)
        self.queue_size -= len(queue)
        self.completed -= len(queue)

    def _transport_options(self):
        return {
            'sleep_interval': 45,
            'max_sleep_interval': 60,
            'sleep_interval_requests': 30,
            'retries': 1,
            'retry_sleep_functions': {'http': lambda attempt: 30},
            'source_address': '0.0.0.0',
            **self.ydl_options,
        }

    def _output_path(self, track):
        extension = 'ogg' if self.download_format == Format.VORBIS else self.download_format
        return self.path_holder.get_download_dir() / _sort_dir(track, self.group) / safe_path_string(
            '{}.{}'.format(track, extension))

    def _download(self, track: Track) -> dict:
        output = self._output_path(track)
        # Repeated tracks in playlists must not race on the same destination.
        with self._state_lock:
            lock = self._output_locks.setdefault(str(output.resolve()), threading.Lock())
        try:
            with lock:
                return self._download_track(track, output)
        except Exception as exc:
            self.logger.error('Failed to download {}: {}'.format(track, exc))
            self.logger.debug(traceback.format_exc())
            self.match_store.record(track, 'failed', reason=str(exc))
            return {'track': track, 'returncode': 1, 'location': output, 'error': str(exc)}
        finally:
            with self._state_lock:
                self.completed += 1

    def _existing_file(self, track, output):
        if self.verify_existing:
            actual = validate_audio(output, track, ffmpeg_location=self.ffmpeg_location)
            sidecar = output.with_suffix(output.suffix + '.source.json')
            try:
                record = json.loads(sidecar.read_text(encoding='utf-8'))
            except (OSError, ValueError) as exc:
                raise AudioValidationError(
                    'Existing audio has no readable source record. Use --redownload to replace it after matching.') from exc
            signature = record.get('signature')
            if (not isinstance(signature, list) or len(signature) < 3
                    or normalize(signature[1]) != normalize(track.name)
                    or not isinstance(signature[2], list) or not signature[2]
                    or normalize(signature[2][0]) != normalize(track.artists[0])):
                raise AudioValidationError('Existing file belongs to a different track/version; use --redownload.')
            self.match_store.record(track, 'existing_verified', actual_duration=actual, location=str(output))
            self.logger.info('{} -> existing duration and source record checked.'.format(track))
        else:
            self.logger.info('{} -> already downloaded. Use --verify-existing to audit, or --redownload to replace.'.format(track))
        return {'track': track, 'returncode': 0, 'location': output}

    def _download_track(self, track, output):
        if check_file(output) and not self.redownload:
            return self._existing_file(track, output)
        create_dir(output.parent)
        if track.platform != Platform.SPOTIFY:
            raise NoReliableMatch('Unsupported track source.')
        if track.track_type == Type.EPISODE:
            candidates = [Match(track.url, str(track.id), track.name, '', track.duration,
                                1.0, 1.0, 1.0, None, 'direct', True)]
        else:
            candidates = self.matcher.candidates(track)
        last_error = None
        try:
            for match in candidates:
                for attempt in range(self.retry + 1):
                    try:
                        with TemporaryDirectory(prefix='savify-', dir=self.path_holder.get_temp_dir()) as folder:
                            source = self._download_candidate(track, match, Path(folder))
                            actual = validate_audio(source, track, match, self.ffmpeg_location)
                            if self.audio_verifier and self.audio_verifier(source, track, match) is not True:
                                raise AudioValidationError('The custom audio identity verifier rejected this candidate.')
                            artwork = None
                            if self.download_format == Format.MP3 and not self.skip_cover_art:
                                try:
                                    # Each job owns its temporary art as well as its audio.
                                    response = requests.get(track.cover_art_url, timeout=20)
                                    response.raise_for_status()
                                    artwork = Path(folder) / 'cover.jpg'
                                    artwork.write_bytes(response.content)
                                except Exception as exc:
                                    self.logger.warning('Cover art unavailable for {}: {}'.format(track, exc))
                                    artwork = None
                            publish_audio(source, output, track, self.ffmpeg_location, artwork)
                        self._record_success(track, match, actual, output)
                        self.logger.info('Downloaded {} -> {} [{:.0%} match]'.format(track, match.url, match.score))
                        return {'track': track, 'returncode': 0, 'location': output, 'source_url': match.url}
                    except AudioValidationError as exc:
                        # Bad content will not become correct by retrying the same URL.
                        last_error = exc
                        self.match_store.record(track, 'audio_rejected', url=match.url, reason=str(exc))
                        break
                    except (DownloadError, YoutubeDlExtractionError, OSError) as exc:
                        last_error = exc
                        self.match_store.record(track, 'download_failed', url=match.url,
                                                attempt=attempt + 1, reason=str(exc))
                        self.logger.debug('Candidate {} failed: {}'.format(match.url, exc))
        except NoReliableMatch as exc:
            if last_error:
                raise NoReliableMatch('{} Last candidate error: {}'.format(exc, last_error)) from last_error
            raise
        raise NoReliableMatch('No downloadable match for {}: {}'.format(track, last_error or 'no candidates'))

    def _download_candidate(self, track, match, folder):
        completed_paths = []

        def postprocessor_hook(data):
            if data.get('status') == 'finished':
                path = (data.get('info_dict') or {}).get('filepath')
                if path:
                    completed_paths.append(Path(path))

        options = self._transport_options()
        extra_processors = [p for p in options.get('postprocessors', []) if p.get('key') != 'FFmpegExtractAudio']
        hooks = list(options.get('postprocessor_hooks') or []) + [postprocessor_hook]
        options.update({
            'format': options.get('format', 'bestaudio/best'),
            'outtmpl': str(folder / '%(id)s.%(ext)s'),
            'paths': {},
            'restrictfilenames': True,
            'ignoreerrors': False,
            'overwrites': True,
            'nooverwrites': False,
            'noplaylist': True,
            'extract_flat': False,
            'simulate': False,
            'skip_download': False,
            'logger': self.logger,
            'ffmpeg_location': self.ffmpeg_location,
            'postprocessors': [{'key': 'FFmpegExtractAudio', 'preferredcodec': self.download_format,
                                'preferredquality': self.quality}] + extra_processors,
            'postprocessor_hooks': hooks,
        })
        if self.ffmpeg_location == 'ffmpeg':
            options.pop('ffmpeg_location', None)
        with YoutubeDL(options) as ydl:
            info = ydl.extract_info(match.url, download=True)
        if not info or info.get('_type') in ('playlist', 'multi_video'):
            raise AudioValidationError('No completed single-video download.')
        if match.source != 'direct' and info.get('id') != match.video_id:
            raise AudioValidationError('Downloader returned a different video ID.')
        for key in ('filepath', '_filename'):
            if info.get(key):
                completed_paths.append(Path(info[key]))
        extension = 'ogg' if self.download_format == Format.VORBIS else self.download_format
        accepted_extensions = {'.' + extension}
        if self.download_format == Format.AAC:
            # yt-dlp may emit AAC in an M4A container; publish_audio remuxes to
            # the requested .aac output without re-encoding it.
            accepted_extensions.add('.m4a')
        # This exact expected converted filename is also valid; never glob *.part/json.
        completed_paths.append(folder / '{}.{}'.format(info.get('id'), extension))
        for path in reversed(completed_paths):
            if path.suffix.lower() in accepted_extensions and path.is_file() and path.stat().st_size > 0:
                if path.resolve().parent == folder.resolve():
                    return path
        raise AudioValidationError('yt-dlp did not produce the requested completed audio format.')

    def _record_success(self, track, match, actual, output):
        if match.source != 'direct' and self.match_config.use_cache:
            self.match_store.remember(track, match, actual)
        record = {'signature': self.match_store.signature(track), 'match': asdict(match),
                  'actual_duration': actual, 'recorded_at': time.time()}
        sidecar = output.with_suffix(output.suffix + '.source.json')
        temp = sidecar.with_name(sidecar.name + '.' + uuid4().hex + '.tmp')
        try:
            temp.write_text(json.dumps(record, ensure_ascii=False, indent=2), encoding='utf-8')
            os.replace(temp, sidecar)
        except OSError as exc:
            self.logger.warning('Could not write source record: {}'.format(exc))
        finally:
            temp.unlink(missing_ok=True)
        self.match_store.record(track, 'downloaded', location=str(output), **record)

