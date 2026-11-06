"""Copy basic music metadata by allowlist, never opaque DJ/analysis payloads."""
import copy
from dataclasses import dataclass
import hashlib
import json
from pathlib import Path
import re
import struct

import mutagen
from mutagen.flac import FLAC
from mutagen.id3 import Frames, ID3, ID3NoHeaderError
from mutagen.mp4 import MP4

from .track import Track


SUPPORTED_EXTENSIONS = {'.mp3', '.flac', '.m4a', '.mp4', '.ogg', '.opus', '.wav', '.aif', '.aiff', '.aac'}
ID3_BASIC = {
    'TIT2', 'TIT3', 'TIT1', 'TPE1', 'TPE2', 'TPE3', 'TPE4', 'TALB', 'TRCK', 'TPOS',
    'TDRC', 'TDOR', 'TYER', 'TORY', 'TDAT', 'TCON', 'TCOM', 'TEXT', 'TPUB', 'TCOP',
    'TSRC', 'TSOA', 'TSOP', 'TSOT', 'TSO2', 'TSOC', 'APIC', 'USLT', 'WOAR', 'WPUB', 'WCOP',
}
MP4_BASIC = {
    '\xa9nam', '\xa9ART', 'aART', '\xa9alb', 'trkn', 'disk', '\xa9day', '\xa9gen', 'gnre',
    '\xa9wrt', 'cprt', 'covr', '\xa9grp', '\xa9lyr', 'soal', 'soar', 'soaa', 'sonm', 'soco',
}


def tag_key(value):
    return re.sub(r'[^a-z0-9]', '', str(value).casefold())


CUSTOM_BASIC = {tag_key(key) for key in (
    'ISRC', 'LABEL', 'PUBLISHER', 'CATALOGNUMBER', 'BARCODE', 'UPC', 'EAN',
    'SPOTIFY_ID', 'SPOTIFY_TRACK_ID', 'SPOTIFY_TRACK_URL', 'SPOTIFY_URL', 'SPOTIFY_URI',
    'SPOTIFY_ALBUM_ID', 'SPOTIFY_ARTIST_ID', 'TRACK_ID',
    'MusicBrainz Track Id', 'MusicBrainz Recording Id', 'MusicBrainz Release Track Id',
    'MusicBrainz Album Id', 'MusicBrainz Release Id', 'MusicBrainz Artist Id',
    'MusicBrainz Album Artist Id', 'MusicBrainz Release Group Id',
    'MusicBrainz Album Type', 'MusicBrainz Album Status', 'MusicBrainz Album Release Country',
    'DISCOGS_RELEASE_ID', 'DISCOGS_ARTIST_ID',
)}
VORBIS_BASIC = {tag_key(key) for key in (
    'TITLE', 'SUBTITLE', 'ARTIST', 'ARTISTS', 'ALBUM', 'ALBUMARTIST', 'ALBUM_ARTIST',
    'TRACKNUMBER', 'TRACKTOTAL', 'TOTALTRACKS', 'DISCNUMBER', 'DISCTOTAL', 'TOTALDISCS',
    'DATE', 'YEAR', 'ORIGINALDATE', 'ORIGINALYEAR', 'GENRE', 'COMPOSER', 'CONDUCTOR',
    'LYRICIST', 'REMIXER', 'COPYRIGHT', 'GROUPING', 'LYRICS',
    'ARTISTSORT', 'ALBUMARTISTSORT', 'ALBUMSORT', 'TITLESORT', 'COMPOSERSORT',
    'METADATA_BLOCK_PICTURE', 'COVERART', 'COVERARTMIME',
)} | CUSTOM_BASIC
UFID_OWNERS = {'http://musicbrainz.org', 'https://musicbrainz.org', 'spotify.com',
               'https://spotify.com', 'http://spotify.com'}


def allowed_id3(frame):
    if frame.FrameID in ID3_BASIC:
        return True
    if frame.FrameID in ('TXXX', 'WXXX'):
        return tag_key(getattr(frame, 'desc', '')) in CUSTOM_BASIC
    if frame.FrameID == 'UFID':
        return frame.owner.rstrip('/').casefold() in UFID_OWNERS
    return False


def allowed_mp4(key):
    if key in MP4_BASIC:
        return True
    if key.startswith('----:'):
        parts = key.split(':', 2)
        return len(parts) == 3 and parts[1] == 'com.apple.iTunes' and tag_key(parts[2]) in CUSTOM_BASIC
    return False


class TagError(Exception):
    pass


def _riff_info(path):
    """Read standard WAV INFO text when no ID3 block exists (old FFmpeg output)."""
    mapping = {b'INAM': 'TIT2', b'IART': 'TPE1', b'IPRD': 'TALB', b'ITRK': 'TRCK',
               b'ICRD': 'TDRC', b'IGNR': 'TCON', b'ICOP': 'TCOP', b'IMUS': 'TCOM',
               b'ISRC': 'TSRC'}
    tags, dropped = ID3(), []
    with Path(path).open('rb') as source:
        header = source.read(12)
        if header[:4] != b'RIFF' or header[8:] != b'WAVE':
            return None, []
        end = Path(path).stat().st_size
        while source.tell() + 8 <= end:
            name, size = struct.unpack('<4sI', source.read(8))
            position = source.tell()
            if position + size > end:
                break
            if name == b'LIST' and 4 <= size <= 16 * 1024 * 1024 and source.read(4) == b'INFO':
                while source.tell() + 8 <= position + size:
                    key, count = struct.unpack('<4sI', source.read(8))
                    if source.tell() + count > position + size:
                        break
                    data = source.read(count).rstrip(b'\x00')
                    if count % 2:
                        source.read(1)
                    if key in mapping:
                        try:
                            text = data.decode('utf-8')
                        except UnicodeDecodeError:
                            text = data.decode('cp1252', 'replace')
                        tags.add(Frames[mapping[key]](encoding=3, text=[text]))
                    else:
                        dropped.append('RIFF:' + key.decode('ascii', 'replace'))
            source.seek(position + size + size % 2)
    return tags or None, dropped


def _strings(value):
    if value is None:
        return []
    if hasattr(value, 'text'):
        value = value.text
    if not isinstance(value, (list, tuple)):
        value = [value]
    return [v.decode('utf-8', 'replace') if isinstance(v, bytes) else str(v) for v in value]


@dataclass
class TagSnapshot:
    family: str
    tags: object
    pictures: list
    id3_version: int
    dropped: list

    @classmethod
    def read(cls, path):
        path = Path(path)
        audio = None
        extra_dropped = []
        try:
            audio = mutagen.File(path, easy=False)
        except mutagen.MutagenError:
            # A corrupt MP3 can still have recoverable standard ID3 metadata.
            if path.suffix.lower() not in ('.mp3', '.aac'):
                raise
        tags = audio.tags if audio is not None else None
        if tags is None and path.suffix.lower() in ('.mp3', '.aac'):
            try:
                tags = ID3(path)
            except ID3NoHeaderError:
                tags = None
        if tags is None and path.suffix.lower() == '.wav':
            tags, extra_dropped = _riff_info(path)
        if tags is None:
            raise TagError('No readable basic song tags; automatic filename guessing is disabled.')
        if isinstance(tags, ID3):
            kept = [copy.deepcopy(frame) for frame in tags.values() if allowed_id3(frame)]
            dropped = extra_dropped + [str(key) for key, frame in tags.items() if not allowed_id3(frame)]
            return cls('id3', kept, [], 3 if tags.version[1] == 3 else 4, dropped)
        if isinstance(audio, MP4):
            kept = {key: copy.deepcopy(value) for key, value in tags.items() if allowed_mp4(key)}
            return cls('mp4', kept, [], 4, [key for key in tags if not allowed_mp4(key)])
        if path.suffix.lower() in ('.flac', '.ogg', '.opus'):
            kept = {key: copy.deepcopy(value) for key, value in tags.items() if tag_key(key) in VORBIS_BASIC}
            pictures = [copy.deepcopy(picture) for picture in audio.pictures] if isinstance(audio, FLAC) else []
            return cls('vorbis', kept, pictures, 4, [key for key in tags.keys() if tag_key(key) not in VORBIS_BASIC])
        raise TagError('Unsupported tag container: {}'.format(type(audio).__name__))

    def fields(self):
        """A small normalized view for identifying the intended song, not writing."""
        fields = {}
        if self.family == 'id3':
            mapping = {'TIT2': 'title', 'TPE1': 'artist', 'TPE2': 'albumartist', 'TALB': 'album',
                       'TRCK': 'tracknumber', 'TPOS': 'discnumber', 'TDRC': 'date', 'TSRC': 'isrc'}
            for frame in self.tags:
                key = mapping.get(frame.FrameID)
                if key:
                    fields[key] = _strings(frame)
                elif frame.FrameID == 'TXXX':
                    fields[tag_key(frame.desc)] = _strings(frame)
                elif frame.FrameID == 'UFID' and 'spotify' in frame.owner.casefold():
                    fields['spotifyid'] = _strings(frame.data)
        elif self.family == 'mp4':
            mapping = {'\xa9nam': 'title', '\xa9ART': 'artist', 'aART': 'albumartist',
                       '\xa9alb': 'album', 'trkn': 'tracknumber', 'disk': 'discnumber', '\xa9day': 'date'}
            for key, value in self.tags.items():
                if key in mapping:
                    fields[mapping[key]] = _strings(value)
                elif key.startswith('----:'):
                    fields[tag_key(key.split(':', 2)[2])] = _strings(value)
        else:
            fields = {tag_key(key): _strings(value) for key, value in self.tags.items()}
        return fields

    def intended_track(self):
        fields = self.fields()
        title = (fields.get('title') or [''])[0].strip()
        artists = [value.strip() for value in fields.get('artist', []) if value.strip()]
        if not title or not artists:
            raise TagError('Artist and title tags are required to determine the intended track.')
        spotify_id = None
        for key in ('spotifytrackid', 'spotifyid', 'spotifytrackurl', 'spotifyurl', 'spotifyuri'):
            for value in fields.get(key, []):
                found = re.search(r'(?:spotify:track:|open\.spotify\.com/track/)([A-Za-z0-9]{22})', value)
                if found:
                    spotify_id = found.group(1)
                elif re.fullmatch(r'[A-Za-z0-9]{22}', value):
                    spotify_id = value
        identity = json.dumps([title, artists, fields.get('album')], ensure_ascii=False)
        local_id = 'local-' + hashlib.sha256(identity.encode()).hexdigest()[:24]
        track = Track({'id': spotify_id or local_id, 'name': title,
                       'artists': [{'name': value} for value in artists],
                       'album': {'name': (fields.get('album') or [''])[0], 'images': []},
                       'external_ids': {'isrc': (fields.get('isrc') or [None])[0]},
                       'track_number': (fields.get('tracknumber') or [''])[0],
                       'disc_number': (fields.get('discnumber') or [''])[0]})
        track.catalog_id = spotify_id
        return track

    def write_to(self, path):
        """Only call on freshly remuxed replacement audio, never the user's original."""
        path = Path(path)
        audio = mutagen.File(path, easy=False)
        if self.family == 'id3':
            if path.suffix.lower() == '.aac':
                try:
                    ID3(path).delete(path, delete_v1=True, delete_v2=True)
                except ID3NoHeaderError:
                    pass
                tags = ID3()
                for frame in self.tags:
                    tags.add(copy.deepcopy(frame))
                tags.save(path, v2_version=self.id3_version, v1=0)
                return
            if audio is None:
                raise TagError('Cannot write ID3 tags to replacement container.')
            if audio.tags is None:
                audio.add_tags()
            if not isinstance(audio.tags, ID3):
                raise TagError('Replacement ID3 container differs from the original.')
            audio.tags.clear()
            for frame in self.tags:
                audio.tags.add(copy.deepcopy(frame))
            if path.suffix.lower() == '.mp3':
                audio.save(v2_version=self.id3_version, v1=0)
            else:
                audio.save(v2_version=self.id3_version)
        else:
            if audio is None or (self.family == 'mp4' and not isinstance(audio, MP4)):
                raise TagError('Replacement tag container differs from the original.')
            if audio.tags is None:
                audio.add_tags()
            audio.tags.clear()
            audio.tags.update(copy.deepcopy(self.tags))
            if isinstance(audio, FLAC):
                audio.clear_pictures()
                for picture in self.pictures:
                    audio.add_picture(copy.deepcopy(picture))
            audio.save()

    def signature(self):
        """Semantic metadata checksum, including exact embedded artwork bytes."""
        def stable(value):
            if value is None or isinstance(value, (str, int, float, bool)):
                return value
            if isinstance(value, bytes):
                return {'bytes': value.hex(), 'format': getattr(value, 'imageformat', None),
                        'dataformat': getattr(value, 'dataformat', None)}
            if isinstance(value, dict):
                return {str(k): stable(v) for k, v in value.items()}
            if isinstance(value, (list, tuple)):
                return [stable(v) for v in value]
            if hasattr(value, '__dict__'):
                return {k: stable(v) for k, v in vars(value).items() if k not in ('encoding', 'salt')}
            return str(value)
        if self.family == 'id3':
            tags = sorted([(frame.HashKey, stable(frame)) for frame in self.tags], key=lambda value: value[0])
        else:
            tags = {key.casefold(): stable(value) for key, value in self.tags.items()}
        data = [self.family, tags, [picture.write().hex() for picture in self.pictures]]
        return hashlib.sha256(json.dumps(data, sort_keys=True, ensure_ascii=False).encode()).hexdigest()

    def verify_copy(self, path):
        actual = self.read(path)
        if actual.dropped:
            raise TagError('Replacement still has non-basic metadata: {}'.format(', '.join(actual.dropped)))
        if actual.signature() != self.signature():
            raise TagError('Basic tags/artwork did not survive the replacement exactly.')
