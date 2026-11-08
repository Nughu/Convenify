"""Optional Spotify duration/ISRC evidence; original file tags remain authoritative."""
import hashlib
import json

from .audio_identity import recording_relation
from .youtube_matcher import _named_remixes, duration_seconds, duration_tolerance, normalize, similarity


class CatalogResolver:
    def __init__(self, client, cache, logger):
        self.client, self.cache, self.logger = client, cache, logger

    def enrich(self, track):
        identity = [getattr(track, 'catalog_id', None), track.name, track.artists, track.album_name, track.isrc]
        key = hashlib.sha256(json.dumps(identity, ensure_ascii=False).encode()).hexdigest()
        # Older cached durations did not distinguish the names of remixers.
        # Refresh only named-remix catalog lookups, leaving other caches intact.
        cache_kind = 'spotify-named-remix-v1' if _named_remixes(track.name) else 'spotify-v2'
        stored = self.cache.cache_get(cache_kind, key, 30 * 86400)
        if stored is not None:
            return self._apply(track, stored)
        try:
            stages = []
            if getattr(track, 'catalog_id', None):
                stages.append(lambda: [self.client.track(track.catalog_id)])
            if track.isrc:
                stages.append(lambda: self.client.search(q='isrc:{}'.format(track.isrc), type='track', limit=10).get('tracks', {}).get('items', []))
            title = track.name.replace('"', ' ')
            artist = track.artists[0].replace('"', ' ')
            queries = ['track:"{}" artist:"{}"'.format(title, artist), '{} {}'.format(artist, title)]
            for query in queries:
                stages.append(lambda query=query: self.client.search(q=query, type='track', limit=10).get('tracks', {}).get('items', []))
            for fetch in stages:
                items = fetch()
                suitable = [item for item in items if item and recording_relation(track, {
                    'title': item.get('name'), 'artists': item.get('artists')}) == 'same'
                    and duration_seconds(item.get('duration_ms'))]
                durations = [duration_seconds(item['duration_ms']) / 1000 for item in suitable]
                if not durations:
                    continue
                if max(durations) - min(durations) > duration_tolerance(min(durations)):
                    track.catalog_note = 'Spotify returned multiple matching versions with different lengths.'
                    self.logger.info('  %s', track.catalog_note)
                    return track
                suitable.sort(key=lambda item: similarity(normalize(track.album_name),
                              normalize((item.get('album') or {}).get('name'))), reverse=True)
                chosen = suitable[0]
                data = {'duration': duration_seconds(chosen['duration_ms']) / 1000,
                        'isrc': (chosen.get('external_ids') or {}).get('isrc'), 'id': chosen.get('id')}
                self.cache.cache_put(cache_kind, key, data)
                return self._apply(track, data)
            track.catalog_note = 'No reliable Spotify artist/title/version match found.'
        except Exception as exc:
            status = getattr(exc, 'http_status', None)
            track.catalog_note = 'Spotify lookup failed' + (' (HTTP {})'.format(status) if status else '') + '.'
            self.logger.warning('  %s Continuing with audio evidence.', track.catalog_note)
        return track

    @staticmethod
    def _apply(track, data):
        if data:
            track.catalog_note = 'Matched Spotify track {}.'.format(data.get('id') or '(unknown ID)')
            track.duration = data['duration']
            track.duration_ms = int(track.duration * 1000)
            track.isrc = track.isrc or data.get('isrc')
        return track
