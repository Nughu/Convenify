"""Optional Spotify duration/ISRC evidence; original file tags remain authoritative."""
import hashlib
import json

from .audio_identity import recording_relation
from .youtube_matcher import duration_seconds, duration_tolerance, normalize, similarity


class CatalogResolver:
    def __init__(self, client, cache, logger):
        self.client, self.cache, self.logger = client, cache, logger

    def enrich(self, track):
        identity = [getattr(track, 'catalog_id', None), track.name, track.artists, track.album_name, track.isrc]
        key = hashlib.sha256(json.dumps(identity, ensure_ascii=False).encode()).hexdigest()
        stored = self.cache.cache_get('spotify', key, 30 * 86400)
        if stored is not None:
            return self._apply(track, stored)
        try:
            if getattr(track, 'catalog_id', None):
                items = [self.client.track(track.catalog_id)]
            else:
                if track.isrc:
                    query = 'isrc:{}'.format(track.isrc)
                else:
                    query = 'track:{} artist:{}'.format(track.name, track.artists[0])
                items = self.client.search(q=query, type='track', limit=10).get('tracks', {}).get('items', [])
            suitable = [item for item in items if item and recording_relation(track, {
                'title': item.get('name'), 'artists': item.get('artists')}) == 'same'
                and duration_seconds(item.get('duration_ms'))]
            durations = [item['duration_ms'] / 1000 for item in suitable]
            if not durations or max(durations) - min(durations) > duration_tolerance(min(durations)):
                data = {}
            else:
                suitable.sort(key=lambda item: similarity(normalize(track.album_name),
                              normalize((item.get('album') or {}).get('name'))), reverse=True)
                chosen = suitable[0]
                data = {'duration': chosen['duration_ms'] / 1000,
                        'isrc': (chosen.get('external_ids') or {}).get('isrc'), 'id': chosen.get('id')}
            self.cache.cache_put('spotify', key, data)
            return self._apply(track, data)
        except Exception:
            self.logger.debug('Optional Spotify evidence unavailable for %s; continuing with fingerprints.', track)
            return track

    @staticmethod
    def _apply(track, data):
        if data:
            track.duration = data['duration']
            track.duration_ms = int(track.duration * 1000)
            track.isrc = track.isrc or data.get('isrc')
        return track
