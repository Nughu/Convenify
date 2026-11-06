"""Resolve Spotify tracks to individual YouTube videos before downloading.

Album, label, ISRC and channel verification are NEVER required for a match.
Search result position and popularity are deliberately not confidence signals.
"""
from dataclasses import asdict, dataclass
from difflib import SequenceMatcher
import json
import math
from pathlib import Path
import re
import threading
import time
import unicodedata
from urllib.parse import parse_qs, urlparse
from uuid import uuid4


class NoReliableMatch(Exception):
    pass


@dataclass
class MatchConfig:
    search_results: int = 10
    inspect_results: int = 5
    min_score: float = 0.82
    min_title: float = 0.72
    min_artist: float = 0.75
    use_youtube_music: bool = True
    use_cache: bool = True

    def __post_init__(self):
        if not 1 <= self.search_results <= 50 or not 1 <= self.inspect_results <= 50:
            raise ValueError('Search/inspection counts must be between 1 and 50.')
        for value in (self.min_score, self.min_title, self.min_artist):
            if not 0 <= value <= 1:
                raise ValueError('Matching thresholds must be between 0 and 1.')


@dataclass
class Match:
    url: str
    video_id: str
    title: str
    channel: str
    duration: float
    score: float
    title_score: float
    artist_score: float
    duration_limit: float
    source: str
    manual: bool = False


def normalize(value):
    text = unicodedata.normalize('NFKD', str(value or '')).casefold()
    text = ''.join(c for c in text if not unicodedata.combining(c))
    return re.sub(r'[^\w]+', ' ', text, flags=re.UNICODE).replace('_', ' ').strip()


def duration_seconds(value):
    try:
        if isinstance(value, str) and ':' in value:
            total = 0.0
            for part in value.split(':'):
                total = total * 60 + float(part)
        else:
            total = float(value)
        return total if math.isfinite(total) and total > 0 else None
    except (TypeError, ValueError):
        return None


def duration_tolerance(expected, relaxed=False, override=None):
    """Use an explicit seconds limit, or the ordinary downloader's timing policy."""
    if override is not None:
        try:
            seconds = float(override)
        except (TypeError, ValueError) as exc:
            raise ValueError('Duration tolerance must be a finite, non-negative number of seconds.') from exc
        if not math.isfinite(seconds) or seconds < 0:
            raise ValueError('Duration tolerance must be a finite, non-negative number of seconds.')
        return seconds
    if not expected:
        return None
    if expected < 30:
        return max(1.0, expected * 0.12)
    if relaxed and expected >= 60:
        return max(8.0, min(30.0, expected * 0.12))
    return max(3.0, min(15.0, expected * 0.06))


def youtube_url(value):
    """Canonicalize only individual YouTube videos, never arbitrary URLs/playlists."""
    value = str(value or '').strip()
    if re.fullmatch(r'[A-Za-z0-9_-]{11}', value):
        return 'https://www.youtube.com/watch?v=' + value
    parsed = urlparse(value)
    if parsed.scheme not in ('http', 'https'):
        return None
    host = (parsed.hostname or '').lower()
    video_id = None
    if host in ('youtu.be', 'www.youtu.be'):
        video_id = parsed.path.strip('/').split('/')[0]
    elif host in ('youtube.com', 'www.youtube.com', 'm.youtube.com', 'music.youtube.com'):
        if parsed.path == '/watch':
            video_id = parse_qs(parsed.query).get('v', [None])[0]
        elif parsed.path.startswith(('/shorts/', '/embed/', '/live/')):
            video_id = parsed.path.split('/')[2]
    if video_id and re.fullmatch(r'[A-Za-z0-9_-]{11}', video_id):
        return 'https://www.youtube.com/watch?v=' + video_id
    return None


def _contains(needle, haystack):
    return bool(needle) and (' ' + needle + ' ') in (' ' + haystack + ' ')


_PRESENTATION = re.compile(
    r'\b(?:official (?:music video|audio|video|lyric video|lyrics)|'
    r'lyrics video|lyric video|lyrics|visuali[sz]er|hd|hq|4k|1080p|720p)\b', re.I)


def _clean_title(value, artists=()):
    text = normalize(value)
    for artist in sorted(artists, key=lambda x: len(str(x)), reverse=True):
        name = normalize(artist)
        # Remove artist prefixes/suffixes, not words occurring inside song names.
        if name and text.startswith(name + ' '):
            text = text[len(name):].strip()
        if name and text.endswith(' ' + name):
            text = text[:-len(name)].strip()
    original = text
    text = _PRESENTATION.sub(' ', text)
    # A real track may itself be named "Lyrics" or "HD".
    if not text.strip():
        text = original
    text = re.sub(r'\b(?:feat|featuring|ft)\b.*$', '', text)
    # Remaster years are presentation differences, not different compositions.
    text = re.sub(r'\b(?:\d{4} )?remaster(?:ed)?(?: \d{4})?\b', '', text)
    text = re.sub(r'\boriginal (?:mix|version)\b', '', text)
    return ' '.join(text.split())


def similarity(left, right):
    if not left or not right:
        return 0.0
    if left == right:
        return 1.0
    ratio = SequenceMatcher(None, left, right).ratio()
    a, b = set(left.split()), set(right.split())
    overlap = 2 * len(a & b) / (len(a) + len(b))
    # Do not let a shared short word turn an unrelated title into a match.
    if len(a) == 1 or len(b) == 1:
        return ratio
    return ratio if ratio >= 0.96 else 0.55 * ratio + 0.45 * overlap


def _versions(value):
    text = normalize(value)
    flags = set()
    for name, pattern in {
        'remix': r'\bremix\b', 'acoustic': r'\bacoustic\b',
        'instrumental': r'\binstrumental\b', 'karaoke': r'\bkaraoke\b',
        'nightcore': r'\bnightcore\b', 'sped up': r'\bsped up\b',
        'slowed': r'\bslowed\b', 'demo': r'\bdemo\b',
        'radio edit': r'\bradio (?:edit|version)\b',
        'extended': r'\bextended (?:mix|version|edit)\b',
        'live': r'\blive (?:at|in|from|version|session|performance)\b|\blive$',
        'cover': r'\bcover(?: by\b| version\b|$)',
    }.items():
        if re.search(pattern, text):
            flags.add(name)
    return flags


def _artist_score(track, candidate):
    primary = normalize(track.artists[0]) if track.artists else ''
    if primary in ('', 'unknown artist', 'unknown publisher'):
        return 0.0
    structured = candidate.get('artists') or []
    if isinstance(structured, str):
        structured = [structured]
    names = [a.get('name', '') if isinstance(a, dict) else a for a in structured]
    names += [candidate.get('artist') or '', candidate.get('creator') or '']
    # Short artist names must match whole tokens (Air is not Hair).
    def name_similarity(name):
        text = normalize(name)
        if len(primary) <= 4 and not _contains(primary, text):
            return 0.0
        return similarity(primary, text)
    scores = [name_similarity(n) for n in names if n]
    scores.extend(1.0 for name in names if _contains(primary, normalize(name)))
    for value in [candidate.get('title'), candidate.get('channel'), candidate.get('uploader')]:
        text = normalize(value)
        if _contains(primary, text):
            scores.append(1.0)
        else:
            text = re.sub(r'\b(?:topic|official|vevo)\b', '', text).strip()
            if len(text.split()) <= len(primary.split()) + 2:
                scores.append(name_similarity(text))
    # Reupload descriptions may contain credits, but arbitrary keyword lists do not
    # supply artist evidence. Require a credit line or title + artist on one line.
    for line in str(candidate.get('description') or '')[:1500].splitlines():
        text = normalize(line)
        if _contains(primary, text) and (
            text.startswith(('artist ', 'performed by ', 'music by '))
            or _contains(_clean_title(track.name), text)
        ):
            scores.append(0.78)
    return max(scores, default=0.0)


def evaluate(track, candidate, config=None, manual=False):
    """Return (accepted match, reason); extended metadata supplies bonuses only."""
    config = config or MatchConfig()
    url = youtube_url(candidate.get('webpage_url') or candidate.get('url') or candidate.get('id'))
    if not url:
        return None, 'not an individual YouTube video'
    if candidate.get('is_live') or candidate.get('live_status') in ('is_live', 'is_upcoming'):
        return None, 'live or upcoming stream'
    if candidate.get('isAvailable') is False:
        return None, 'unavailable recording'
    title = str(candidate.get('title') or candidate.get('track') or '')
    expected_title = _clean_title(track.name)
    candidate_titles = [title, candidate.get('track') or '']
    title_score = max(similarity(expected_title, _clean_title(t, track.artists)) for t in candidate_titles)
    artist_score = _artist_score(track, candidate)
    if not manual:
        if title_score < config.min_title:
            return None, 'title mismatch ({:.2f})'.format(title_score)
        if artist_score < config.min_artist:
            return None, 'insufficient artist evidence ({:.2f})'.format(artist_score)
        desired_versions = _versions(track.name)
        found_versions = _versions(_clean_title(title, track.artists)) | _versions(candidate.get('track'))
        # A word within the actual song name (e.g. "Live Forever") is not a tag.
        remainder = normalize(title).replace(normalize(track.name), '')
        extra_versions = _versions(remainder) - desired_versions
        if extra_versions or desired_versions - found_versions:
            return None, 'different or unconfirmed recording version'
        if re.search(r'\b(?:full album|full concert|album stream|complete album|playlist|compilation)\b', remainder):
            return None, 'multi-track upload'

    expected = duration_seconds(getattr(track, 'duration', None))
    actual = duration_seconds(candidate.get('duration_seconds')) or duration_seconds(candidate.get('duration'))
    override = getattr(track, 'duration_tolerance', None)
    limit = duration_tolerance(expected, override=override)
    duration_score = 0.65
    if expected and actual:
        delta = abs(expected - actual)
        if delta > limit:
            loose_limit = duration_tolerance(expected, relaxed=True, override=override)
            if (manual or (title_score >= 0.94 and artist_score >= 0.9)) and delta <= loose_limit:
                limit = loose_limit
            else:
                return None, 'duration mismatch ({:.1f}s vs {:.1f}s)'.format(actual, expected)
        duration_score = 1.0 - 0.4 * min(1.0, delta / limit) if limit else 1.0
    elif not manual and (title_score < 0.9 or artist_score < 0.78):
        return None, 'missing duration requires stronger title/artist evidence'
    score = 0.55 * title_score + 0.30 * artist_score + 0.15 * duration_score
    album = candidate.get('album') or ''
    if isinstance(album, dict):
        album = album.get('name') or ''
    if album and similarity(normalize(track.album_name), normalize(album)) >= 0.9:
        score += 0.02
    if candidate.get('_source', '').startswith('youtube-music'):
        score += 0.01
    if not manual and score < config.min_score:
        return None, 'low confidence ({:.2f})'.format(score)
    return Match(url, url.split('v=')[1], title,
                 str(candidate.get('channel') or candidate.get('uploader') or ''),
                 actual, min(1.0, score), title_score, artist_score, limit,
                 candidate.get('_source', 'youtube'), manual), 'accepted'


class MatchStore:
    """Thread-safe cache and JSONL audit log; only successful audio is cached."""
    def __init__(self, directory, logger, overrides_path=None):
        self.directory = Path(directory)
        self.directory.mkdir(parents=True, exist_ok=True)
        self.logger = logger
        self.lock = threading.RLock()
        self.cache_path = self.directory / 'youtube-matches.json'
        self.audit_path = self.directory / 'youtube-matching.jsonl'
        self.overrides_path = Path(overrides_path) if overrides_path else self.directory / 'youtube-overrides.json'
        if overrides_path and not self.overrides_path.is_file():
            raise ValueError('Override file does not exist: {}'.format(self.overrides_path))
        self.cache = self._read(self.cache_path, strict=False)
        self.overrides = self._read(self.overrides_path, strict=True)
        if not isinstance(self.overrides, dict) or any(not youtube_url(v) for v in self.overrides.values()):
            raise ValueError('YouTube overrides must map Spotify IDs/URIs to individual YouTube URLs or video IDs.')

    def _read(self, path, strict):
        try:
            value = json.loads(path.read_text(encoding='utf-8'))
            if not isinstance(value, dict):
                raise ValueError('expected a JSON object')
            return value
        except FileNotFoundError:
            return {}
        except (OSError, ValueError) as exc:
            if strict:
                raise ValueError('Cannot read override file {}: {}'.format(path, exc)) from exc
            self.logger.warning('Ignoring unreadable matching cache: {}'.format(exc))
            return {}

    @staticmethod
    def signature(track):
        return [str(track.id), track.name, list(track.artists), getattr(track, 'duration', None), 1]

    def cached(self, track):
        with self.lock:
            entry = self.cache.get(str(track.id), {})
            if isinstance(entry, dict) and entry.get('signature') == self.signature(track):
                return youtube_url(entry.get('url'))
        return None

    def override(self, track):
        return self.overrides.get(str(track.id)) or self.overrides.get(getattr(track, 'uri', ''))

    def record(self, track, event, **details):
        with self.lock:
            try:
                with self.audit_path.open('a', encoding='utf-8') as output:
                    output.write(json.dumps(dict(time=time.time(), spotify_id=str(track.id),
                                                 track=str(track), event=event, **details), ensure_ascii=False) + '\n')
            except OSError as exc:
                self.logger.warning('Could not write matching log: {}'.format(exc))

    def remember(self, track, match, actual_duration):
        with self.lock:
            self.cache[str(track.id)] = dict(signature=self.signature(track), url=match.url,
                                             match=asdict(match), actual_duration=actual_duration)
            temp = self.cache_path.with_name(self.cache_path.name + '.' + uuid4().hex + '.tmp')
            try:
                temp.write_text(json.dumps(self.cache, ensure_ascii=False, indent=2), encoding='utf-8')
                temp.replace(self.cache_path)
            except OSError as exc:
                self.logger.warning('Could not save matching cache: {}'.format(exc))
            finally:
                temp.unlink(missing_ok=True)


class SearchProvider:
    """Network boundary kept separate so matching can be tested without downloads."""
    def __init__(self, ydl_options, logger, config):
        self.ydl_options = dict(ydl_options)
        self.logger = logger
        self.config = config
        self.local = threading.local()
        self.album_lock = threading.Lock()
        self.album_cache = {}
        self.music_class = None
        if config.use_youtube_music:
            try:
                from ytmusicapi import YTMusic
                self.music_class = YTMusic
            except ImportError:
                logger.warning('ytmusicapi is not installed; using validated YouTube searches.')

    def music(self):
        if not self.music_class:
            return None
        if not hasattr(self.local, 'music'):
            self.local.music = self.music_class()
        return self.local.music

    def songs(self, query):
        client = self.music()
        if not client:
            return []
        return [self._music_entry(r) for r in client.search(
            query, filter='songs', limit=self.config.search_results, ignore_spelling=True)
                if r and r.get('videoId')][:self.config.search_results]

    @staticmethod
    def _music_entry(result):
        return dict(result, id=result.get('videoId'), url=youtube_url(result.get('videoId')),
                    track=result.get('title'), _source='youtube-music')

    def album(self, track):
        client = self.music()
        if not client or not track.album_name or track.album_name.startswith('Unknown '):
            return []
        key = (tuple(track.artists), track.album_name)
        with self.album_lock:
            if key in self.album_cache:
                return self.album_cache[key]
            results = []
            for album in client.search('{} {}'.format(track.artists[0], track.album_name),
                                       filter='albums', limit=3, ignore_spelling=True)[:3]:
                if not album.get('browseId'):
                    continue
                details = client.get_album(album['browseId'])
                for item in details.get('tracks') or []:
                    if item and item.get('videoId'):
                        entry = self._music_entry(item)
                        entry['album'] = entry.get('album') or details.get('title')
                        entry['artists'] = entry.get('artists') or details.get('artists') or []
                        entry['_source'] = 'youtube-music-album'
                        results.append(entry)
            self.album_cache[key] = results
            return results

    def _options(self, flat=False):
        # Transport/auth options survive; download-only callbacks and filters do not.
        allowed = ('proxy', 'cookiefile', 'cookiesfrombrowser', 'http_headers', 'socket_timeout',
                   'source_address', 'extractor_args', 'cachedir', 'js_runtimes', 'remote_components',
                   'sleep_interval_requests', 'extractor_retries', 'retry_sleep_functions')
        result = {k: self.ydl_options[k] for k in allowed if k in self.ydl_options}
        result.update(quiet=True, logger=self.logger, noplaylist=True,
                      extract_flat='in_playlist' if flat else False,
                      ignoreerrors=False, socket_timeout=result.get('socket_timeout', 20))
        return result

    def videos(self, query):
        from yt_dlp import YoutubeDL
        with YoutubeDL(self._options(flat=True)) as ydl:
            result = ydl.extract_info('ytsearch{}:{}'.format(self.config.search_results, query), download=False)
            return list((result or {}).get('entries') or [])[:self.config.search_results]

    def inspect(self, url):
        from yt_dlp import YoutubeDL
        with YoutubeDL(self._options()) as ydl:
            return ydl.extract_info(url, download=False)


class YouTubeMatcher:
    def __init__(self, provider, store, logger, config=None, enrich_track=None):
        self.provider = provider
        self.store = store
        self.logger = logger
        self.config = config or MatchConfig()
        self.enrich_track = enrich_track

    def _inspect_stage(self, track, entries, seen, manual=False):
        shortlist = {}
        for item in entries or []:
            if not isinstance(item, dict):
                continue
            url = youtube_url(item.get('webpage_url') or item.get('url') or item.get('id'))
            if not url or url in seen:
                continue
            decision, reason = evaluate(track, item, self.config, manual=manual)
            # Missing artist/duration fields may be recovered from the video page.
            provisional = max(similarity(_clean_title(track.name), _clean_title(t, track.artists))
                              for t in (item.get('title'), item.get('track')))
            if not manual and not decision and (provisional < self.config.min_title or
                    reason.startswith(('duration mismatch', 'live or upcoming', 'multi-track', 'different'))):
                self.store.record(track, 'rejected', url=url, reason=reason)
                continue
            priority = decision.score if decision else provisional * 0.8
            if url not in shortlist or priority > shortlist[url][0]:
                shortlist[url] = (priority, item)
        accepted = []
        for _, item in sorted(shortlist.values(), key=lambda pair: pair[0], reverse=True)[:self.config.inspect_results]:
            url = youtube_url(item.get('webpage_url') or item.get('url') or item.get('id'))
            try:
                detail = self.provider.inspect(url)
                if not detail or detail.get('_type') in ('playlist', 'multi_video'):
                    raise ValueError('no individual video metadata')
                # Preserve optional music metadata when the video extractor lacks it.
                merged = dict(item)
                merged.update({k: v for k, v in detail.items() if v is not None and v != '' and v != []})
                merged['url'] = url
                merged['webpage_url'] = url
                if duration_seconds(detail.get('duration')):
                    merged['duration_seconds'] = detail['duration']
                merged['_source'] = 'manual' if manual else item.get('_source', 'youtube')
                decision, reason = evaluate(track, merged, self.config, manual=manual)
                if decision:
                    accepted.append(decision)
                else:
                    self.store.record(track, 'rejected', url=url, reason=reason)
            except Exception as exc:
                self.logger.debug('Cannot inspect {}: {}'.format(url, exc))
                self.store.record(track, 'inspection_failed', url=url, reason=str(exc))
        for decision in sorted(accepted, key=lambda m: m.score, reverse=True):
            if decision.url not in seen:
                seen.add(decision.url)
                self.store.record(track, 'candidate', **asdict(decision))
                yield decision

    def candidates(self, track):
        """Yield validated alternatives lazily; downloading never reruns a search."""
        seen = set()
        override = self.store.override(track)
        if override:
            yield from self._inspect_stage(track, [{'url': override}], seen, manual=True)
            raise NoReliableMatch('The manual YouTube selection could not be downloaded/validated.')
        cached = self.store.cached(track) if self.config.use_cache else None
        if cached:
            # Cached entries must pass today's metadata checks too.
            yield from self._inspect_stage(track, [{'url': cached, 'title': track.name,
                                                    '_source': 'cache'}], seen)
        query = '{} {}'.format(track.artists[0], track.name)
        queries = list(dict.fromkeys([query, '{} {}'.format(' '.join(track.artists), track.name),
                                      '{} {}'.format(query, track.album_name or '')]))
        stages = []
        if getattr(track, 'isrc', None):
            stages.append(('isrc', lambda: self.provider.songs(track.isrc)))
        stages.extend([('music', lambda: self.provider.songs(query)),
                       ('album', lambda: self.provider.album(track))])
        for extra in queries[1:]:
            stages.append(('music fallback', lambda q=extra: self.provider.songs(q)))
        # Album responses often lack ISRC. Enrich only after ordinary music matching
        # fails, and continue normally if Spotify cannot supply the optional field.
        if self.config.use_youtube_music and self.enrich_track and not getattr(track, 'isrc', None):
            def enriched_search():
                self.enrich_track(track)
                return self.provider.songs(track.isrc) if getattr(track, 'isrc', None) else []
            stages.append(('optional isrc', enriched_search))
        for text_query in queries:
            stages.append(('youtube', lambda q=text_query: self.provider.videos(q)))
        for name, search in stages:
            try:
                entries = search()
            except Exception as exc:
                self.logger.debug('{} search failed for {}: {}'.format(name, track, exc))
                self.store.record(track, 'search_failed', provider=name, reason=str(exc))
                continue
            yield from self._inspect_stage(track, entries, seen)
        raise NoReliableMatch('No reliable, downloadable YouTube match for {}. See youtube-matching.jsonl.'.format(track))
