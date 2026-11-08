"""Read explicit track paths from a Traktor NML export without changing it."""
import ntpath
import os
from pathlib import Path
import re
from urllib.parse import unquote, urlsplit
import xml.etree.ElementTree as ET


def decode_path(value):
    """Decode Traktor's /: separators, preserving literal percent signs."""
    text = str(value or '')
    if not text or '\x00' in text:
        raise ValueError('NML contains an empty or invalid track path.')
    if text.lower().startswith('file:'):
        parsed = urlsplit(text)
        text = unquote(parsed.path)
        if parsed.netloc and parsed.netloc.lower() != 'localhost':
            text = '//' + parsed.netloc + text
    else:
        text = text.replace('/:', '/')
    # Some Traktor exports put the drive letter inside DIR instead of VOLUME.
    if re.match(r'^/[A-Za-z]:[/\\]', text):
        text = text[1:]
    if re.match(r'^[A-Za-z]:[/\\]', text) or text.startswith(('\\\\', '//')):
        return ntpath.normpath(text)
    if not text.startswith('/'):
        raise ValueError('NML track path is not absolute: {}. Export paths to the original files.'.format(value))
    return os.path.normpath(text)


def location_path(location):
    directory = location.get('DIR') or ''
    filename = location.get('FILE') or ''
    volume = location.get('VOLUME') or ''
    if not filename or '/' in filename or '\\' in filename or filename in ('.', '..'):
        raise ValueError('NML LOCATION has a missing or invalid FILE attribute.')
    if volume and not re.fullmatch(r'[A-Za-z]:', volume) and not volume.startswith(('\\\\', '//')):
        raise ValueError('NML uses an unsupported named volume: {}. Export absolute local track paths.'.format(volume))
    separator = '' if directory.endswith(('/', '\\', '/:')) else '/'
    return decode_path(volume + directory + separator + filename)


def path_key(value):
    """Windows paths compare without case; POSIX paths retain case."""
    text = str(value)
    if re.match(r'^[A-Za-z]:', text) or text.startswith(('\\\\', '//')):
        return ntpath.normcase(ntpath.normpath(text))
    return os.path.normcase(os.path.normpath(text))


def read_nml_paths(filename):
    """Prefer explicit playlist members over a potentially larger COLLECTION."""
    try:
        document = ET.parse(filename)
    except (ET.ParseError, OSError) as exc:
        raise ValueError('Cannot read NML export {}: {}'.format(filename, exc)) from exc
    root = document.getroot()
    for element in root.iter():
        element.tag = element.tag.rsplit('}', 1)[-1]
    if root.tag != 'NML':
        raise ValueError('Expected a Traktor NML document.')
    playlists = list(root.iter('PLAYLIST'))
    if playlists:
        paths = []
        for playlist in playlists:
            for entry in playlist.findall('ENTRY'):
                key = entry.find('PRIMARYKEY')
                if key is None or (key.get('TYPE') or 'TRACK').upper() != 'TRACK':
                    raise ValueError('NML playlist contains an unsupported entry without a local TRACK key.')
                paths.append(decode_path(key.get('KEY')))
    elif any(node.get('TYPE') == 'SMARTLIST' for node in root.iter('NODE')):
        raise ValueError('NML contains a smart-list rule without exported members. Export the smart list as a track playlist.')
    else:
        paths = []
        for entry in root.findall('./COLLECTION/ENTRY'):
            location = entry.find('LOCATION')
            if location is None:
                raise ValueError('NML collection entry has no LOCATION.')
            paths.append(location_path(location))
    unique = {}
    for path in paths:
        unique.setdefault(path_key(path), path)
    if not unique:
        raise ValueError('NML export contains no explicit track paths; no files were selected.')
    return list(unique.values())


def selected_paths(filename, root, state_dir, extensions):
    """Constrain exact paths to this library. Never guess by filename or title."""
    root, state_dir = Path(root).resolve(), Path(state_dir).resolve()
    selected = {}
    for value in read_nml_paths(filename):
        path = Path(value)
        if not path.is_absolute():
            raise ValueError('NML path cannot be used on this operating system: {}'.format(value))
        if path.is_symlink():
            raise ValueError('NML selects a symbolic link: {}'.format(value))
        resolved = path.resolve()
        try:
            relative = resolved.relative_to(root)
        except ValueError as exc:
            raise ValueError('NML track is outside the library directory: {}'.format(value)) from exc
        if (resolved == state_dir or state_dir in resolved.parents
                or any(part == '_savify_audit' or part.startswith('.savify-repair-') for part in relative.parts)):
            raise ValueError('NML selects an audit backup or temporary file: {}'.format(value))
        if path.suffix.lower() not in extensions:
            raise ValueError('NML selects an unsupported audio format: {}'.format(value))
        # Keep missing paths in the selection: the audit reports each as an error
        # and continues with the remaining listed tracks, never the whole library.
        selected.setdefault(path_key(resolved), resolved)
    return list(selected.values())
