"""Identify the audio, independently of its tags, with Chromaprint/AcoustID.

No match, missing recording metadata, and conflicting database links are UNKNOWN.
They are never treated as proof that a file is correct or needs replacement.
"""
from dataclasses import asdict, dataclass, field
import hashlib
import json
import math
import re
import subprocess
import time

import requests

from .media_validation import AudioValidationError, _run, ffmpeg_binary, probe_audio
from .youtube_matcher import (_artist_score, _clean_title, _versions, duration_seconds,
                              duration_tolerance, similarity)


class IdentityError(Exception):
    """Temporary identification failure; leave the original alone."""


class IdentityConfigurationError(IdentityError):
    """Stop the batch rather than repeat a broken setup for thousands of files."""


@dataclass
class IdentityResult:
    status: str
    reason: str
    score: float = 0.0
    recordings: list = field(default_factory=list)
    duration: float = None
    # An actual lookup without usable identification, not a network failure or
    # conflicting recording links. Only this can permit a duration-led fallback.
    unrecognized: bool = False
    incomplete: bool = False

    def to_dict(self):
        return asdict(self)


class Fingerprinter:
    def __init__(self, ffmpeg_location='ffmpeg', fpcalc=None):
        self.ffmpeg_location = ffmpeg_location
        self.fpcalc = fpcalc

    def check(self):
        if self.fpcalc:
            try:
                result = subprocess.run([self.fpcalc, '-version'], capture_output=True,
                                        text=True, timeout=15)
            except (OSError, subprocess.TimeoutExpired) as exc:
                raise IdentityConfigurationError('Cannot run fpcalc: {}'.format(exc)) from exc
            if result.returncode:
                raise IdentityConfigurationError('fpcalc is not available at the supplied path.')
        else:
            try:
                result = _run([ffmpeg_binary(self.ffmpeg_location), '-hide_banner',
                               '-h', 'muxer=chromaprint'], timeout=15)
            except AudioValidationError as exc:
                raise IdentityConfigurationError(str(exc)) from exc
            if 'fp_format' not in result.stdout + result.stderr:
                raise IdentityConfigurationError(
                    'This FFmpeg lacks Chromaprint. Install fpcalc and pass --fpcalc PATH, '
                    'or use an FFmpeg build with the chromaprint muxer.')

    def fingerprint(self, path, duration=None):
        duration = duration or probe_audio(path, self.ffmpeg_location)
        try:
            if self.fpcalc:
                result = _run([self.fpcalc, '-json', '-length', '120', str(path)], timeout=180)
                fingerprint = json.loads(result.stdout)['fingerprint']
            else:
                result = _run([ffmpeg_binary(self.ffmpeg_location), '-nostdin', '-v', 'error',
                               '-i', str(path), '-map', '0:a:0', '-t', '120', '-ac', '2',
                               '-ar', '11025', '-c:a', 'pcm_s16le', '-f', 'chromaprint',
                               '-fp_format', 'base64', 'pipe:1'], timeout=180)
                fingerprint = result.stdout.strip()
        except (AudioValidationError, ValueError, KeyError) as exc:
            raise IdentityError('Cannot fingerprint audio: {}'.format(exc)) from exc
        if not isinstance(fingerprint, str) or len(fingerprint) < 12:
            raise IdentityError('Audio is too short or unsuitable for a useful fingerprint.')
        # The lookup duration is the WHOLE file, not just the first 120 seconds.
        return duration, fingerprint


class AcoustIDClient:
    endpoint = 'https://api.acoustid.org/v2/lookup'

    def __init__(self, api_key, cache=None, session=None, clock=time.monotonic, sleep=time.sleep):
        if not api_key:
            raise IdentityConfigurationError('Set ACOUSTID_API_KEY to an AcoustID application key.')
        self.api_key, self.cache = api_key, cache
        self.session = session or requests.Session()
        self.clock, self.sleep, self.last_request = clock, sleep, None

    def lookup(self, duration, fingerprint):
        seconds = max(1, int(round(duration)))
        key = hashlib.sha256(('v1:{}:{}'.format(seconds, fingerprint)).encode()).hexdigest()
        if self.cache:
            cached = self.cache.cache_get('acoustid', key, max_age=7 * 86400)
            if cached is not None:
                return cached
        payload = {'client': self.api_key, 'format': 'json', 'duration': seconds,
                   'fingerprint': fingerprint, 'meta': 'recordings recordingids isrcs'}
        for attempt in range(3):
            if self.last_request is not None:
                self.sleep(max(0, 0.4 - (self.clock() - self.last_request)))
            self.last_request = self.clock()
            try:
                response = self.session.post(self.endpoint, data=payload, timeout=(10, 30))
            except requests.RequestException as exc:
                if attempt < 2:
                    self.sleep(2 ** attempt)
                    continue
                # Do not include the request body/application key in exception logs.
                raise IdentityError('AcoustID network request failed.') from exc
            if response.status_code in (401, 403):
                raise IdentityConfigurationError('AcoustID rejected the application key/access.')
            if response.status_code == 429 or response.status_code >= 500:
                if attempt < 2:
                    try:
                        delay = float(response.headers.get('Retry-After', 2 ** (attempt + 1)))
                    except ValueError:
                        delay = 2 ** (attempt + 1)
                    self.sleep(max(0.4, min(delay, 30)))
                    continue
                raise IdentityError('AcoustID is unavailable or rate limited; retry this scan later.')
            if response.status_code != 200:
                raise IdentityError('AcoustID returned HTTP {}.'.format(response.status_code))
            try:
                body = response.json()
            except ValueError as exc:
                raise IdentityError('AcoustID returned invalid JSON.') from exc
            if not isinstance(body, dict):
                raise IdentityError('AcoustID returned an invalid response.')
            if body.get('status') != 'ok':
                code = (body.get('error') or {}).get('code')
                if code in (2, 4, 5):  # invalid API key / API version / format
                    raise IdentityConfigurationError('AcoustID rejected the application configuration (code {}).'.format(code))
                raise IdentityError('AcoustID lookup failed (code {}).'.format(code))
            if not isinstance(body.get('results'), list):
                raise IdentityError('AcoustID response has no results list.')
            if self.cache:
                self.cache.cache_put('acoustid', key, body)
            return body
        raise IdentityError('AcoustID lookup failed.')


def _base_recording_title(value):
    # Catalogs often put remix/version names in a suffix, while fingerprint
    # databases omit that suffix. Its absence is uncertainty, not a different song.
    base = re.sub(r'(?:\s+[-–—]\s+|\s*\()[^()]*\b(?:remix|mix|edit|version|acoustic|instrumental|live)\b[^()]*\)?$',
                  '', str(value or ''), flags=re.I)
    return _clean_title(base)


def _missing_version(track, title):
    wanted, found = _clean_title(track.name), _clean_title(title)
    return (wanted != _base_recording_title(track.name)
            and found == _base_recording_title(title)
            and similarity(_base_recording_title(track.name), found) >= .93)


def recording_relation(track, recording):
    """Return same/different/uncertain; album, label and IDs are not requirements."""
    title = recording.get('title') or ''
    artists = [artist.get('name', '').strip() for artist in recording.get('artists') or []
               if isinstance(artist, dict) and isinstance(artist.get('name'), str) and artist.get('name', '').strip()]
    if not title or not artists:
        return 'uncertain'
    title_score = similarity(_clean_title(track.name), _clean_title(title))
    # Older Savify tags joined artists with '/', including names such as AC/DC.
    # Compare complete credits too; never split every slash into a new artist.
    artist_score = _artist_score(track, {'artists': artists + [' / '.join(artists)]})
    # Mild spelling/credit differences need review, not automatic replacement.
    if artist_score < 0.50:
        return 'different'
    if _missing_version(track, title):
        return 'uncertain'
    if title_score < 0.60:
        return 'different'
    if (title_score >= 0.93 and artist_score >= 0.90
            and _versions(track.name) == _versions(title)):
        return 'same'
    return 'uncertain'


def assess_response(track, body, actual_duration, min_score=0.90):
    candidates = []
    for item in body.get('results') or []:
        if not isinstance(item, dict):
            continue
        try:
            score = float(item.get('score', 0))
        except (TypeError, ValueError):
            continue
        if math.isfinite(score) and 0 <= score <= 1:
            candidates.append((score, item))
    top = max((score for score, _ in candidates), default=0.0)
    if top < min_score:
        return IdentityResult('unknown', 'No high-confidence audio fingerprint match.', top,
                              duration=actual_duration, unrecognized=True)
    # Include plausible alternatives below the acceptance threshold; a competing
    # database mapping must not disappear simply because it scored 0.89 vs 0.91.
    considered = [item for score, item in candidates if score >= max(0.75, top - 0.10)]
    evidence, relations = [], []
    expected_duration = duration_seconds(getattr(track, 'duration', None))
    for item in considered:
        recordings = item.get('recordings') or []
        if not recordings:
            relations.append('uncertain')
        for recording in recordings:
            if not isinstance(recording, dict):
                relations.append('uncertain')
                continue
            relation = recording_relation(track, recording)
            issue = ''
            if relation == 'uncertain':
                if not recording.get('title') or not any(a.get('name') for a in recording.get('artists') or [] if isinstance(a, dict)):
                    issue = 'missing title or artist'
                    if (recording.get('title') and similarity(_base_recording_title(track.name), _base_recording_title(recording['title'])) < .60
                            or recording.get('artists') and any(a.get('name') for a in recording['artists'] if isinstance(a, dict))
                            and _artist_score(track, {'artists': recording['artists']}) < .50):
                        issue = 'conflicting partial metadata'
                elif _missing_version(track, recording.get('title')):
                    issue = 'missing requested version'
                else:
                    issue = 'artist/title/version uncertain'
            recording_duration = duration_seconds(recording.get('duration'))
            reference_duration = expected_duration or recording_duration
            duration_ok = (reference_duration is not None and
                           abs(actual_duration - reference_duration) <= duration_tolerance(reference_duration, relaxed=True))
            if relation == 'same':
                if reference_duration is None:
                    relation = 'uncertain'  # A sampled fingerprint alone cannot rule out a full album/clip.
                    issue = 'missing reference duration'
                elif not duration_ok:
                    relation = 'different_length'
            relations.append(relation)
            evidence.append({'id': recording.get('id'), 'title': recording.get('title'),
                             'artists': [a.get('name') for a in recording.get('artists') or [] if isinstance(a, dict)],
                             'duration': recording_duration, 'score': item.get('score'), 'relation': relation, 'issue': issue})
    # Unlinked IDs/empty records do not contradict a fully identified recording.
    if 'same' in relations and all(r['relation'] == 'same' or (not r['title'] and not any(r['artists'])) for r in evidence):
        return IdentityResult('verified', 'Audio fingerprint, artist/title, version and duration agree.', top, evidence, actual_duration)
    if relations and all(r in ('different', 'different_length') for r in relations):
        return IdentityResult('mismatch', 'High-confidence audio identification contradicts the intended song or length.', top, evidence, actual_duration)
    unrecognized = not any(r.get('title') or any(r.get('artists') or []) for r in evidence)
    incomplete = bool(evidence) and all(r['relation'] == 'same' or r['issue'] in (
        'missing title or artist', 'missing requested version', 'missing reference duration') for r in evidence)
    issues = sorted({r['issue'] or r['relation'] for r in evidence if r['relation'] != 'same'})
    reason = 'Fingerprint lookup could not confirm identity: {}.'.format(', '.join(issues) or 'no linked recording metadata')
    return IdentityResult('unknown', reason, top, evidence, actual_duration,
                          unrecognized=unrecognized, incomplete=incomplete)


class AudioIdentifier:
    def __init__(self, fingerprinter, client):
        self.fingerprinter, self.client = fingerprinter, client

    def identify(self, path, track, duration=None):
        duration, fingerprint = self.fingerprinter.fingerprint(path, duration)
        if duration < 15:
            return IdentityResult('unknown', 'Too short for confident automatic fingerprint identification.', duration=duration)
        return assess_response(track, self.client.lookup(duration, fingerprint), duration)

    def compare(self, original, reference):
        from .reference_audio import raw_fingerprint, compare_fingerprints
        return compare_fingerprints(raw_fingerprint(original, self.fingerprinter),
                                    raw_fingerprint(reference, self.fingerprinter))
