#!/usr/bin/env python3
"""Reset only successfully repaired Savify tracks in a Traktor collection.

Standalone, standard-library-only Python 3.11+. Preview by default; --apply
backs up and atomically edits the explicitly selected collection.nml.
"""
from __future__ import annotations

import argparse
from contextlib import contextmanager
import csv
from datetime import datetime, timezone
import hashlib
import io
import json
import ntpath
import os
from pathlib import Path
import posixpath
import re
import stat
import subprocess
import sys
import tempfile
from uuid import uuid4
from xml.dom import Node, minidom
from xml.parsers import expat


class CleanupError(Exception):
    pass


def sha256(data):
    return hashlib.sha256(data).hexdigest()


def file_token(path):
    info = path.lstat()
    if not stat.S_ISREG(info.st_mode):
        raise CleanupError(f'Not a regular file (symlinks are not accepted): {path}')
    return (info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns, info.st_ctime_ns)


def read_stable(path):
    before = file_token(path)
    data = path.read_bytes()
    if file_token(path) != before:
        raise CleanupError(f'File changed while reading it; wait for the writer to stop and retry: {path}')
    return data, before


def canonical_path(value):
    """Compare absolute paths without resolving them on the machine running us."""
    if not isinstance(value, str) or not value or any(c in value for c in '\x00\r\n'):
        raise CleanupError('Missing or invalid absolute audio path.')
    value = value.replace('\\', '/')
    if value.startswith('//?/UNC/'):
        value = '//' + value[8:]
    elif value.startswith('//?/'):
        value = value[4:]
    if re.match(r'^[A-Za-z]:/', value):
        return 'win:' + ntpath.normpath(value).replace('\\', '/').lower()
    if value.startswith('//') and len(value.strip('/').split('/')) >= 3:
        return 'win:' + ntpath.normpath(value).replace('\\', '/').lower()
    if value.startswith('/') and not re.match(r'^/[A-Za-z]:', value):
        return 'posix:' + posixpath.normpath(value)
    raise CleanupError(f'Expected an absolute path, not a filename or relative path: {value!r}')


def decode_log(data, encoding=None):
    if encoding:
        return data.decode(encoding)
    if data.startswith((b'\xff\xfe', b'\xfe\xff')):
        return data.decode('utf-16')
    return data.decode('utf-8-sig')


def repaired_paths(path, encoding=None):
    """Accept completed status=repaired records; never infer from attempts."""
    data, _ = read_stable(path)
    try:
        text = decode_log(data, encoding)
    except (UnicodeError, LookupError) as exc:
        raise CleanupError(f'Cannot decode {path}; use --log-encoding for a non-UTF-8 console log: {exc}') from exc
    found = {}
    warnings = []

    def add(value, line):
        try:
            key = canonical_path(value)
        except CleanupError as exc:
            raise CleanupError(f'{path}, record {line}: {exc}') from exc
        found.setdefault(key, value)

    if path.suffix.lower() == '.jsonl' or text.lstrip().startswith('{'):
        lines = text.splitlines()
        for number, line in enumerate(lines, 1):
            if not line.strip():
                continue
            try:
                item = json.loads(line)
            except ValueError as exc:
                if number == len(lines) and not text.endswith(('\n', '\r')):
                    warnings.append(f'{path}: ignored incomplete final JSONL record.')
                    break
                raise CleanupError(f'{path}: invalid JSONL on line {number}.') from exc
            if not isinstance(item, dict):
                raise CleanupError(f'{path}: JSONL record {number} is not an object.')
            if item.get('event') == 'file_result' and item.get('status') == 'repaired':
                add(item.get('path'), number)
    elif path.suffix.lower() == '.csv' or text.startswith(('path,', '"path",')):
        reader = csv.DictReader(io.StringIO(text, newline=''))
        if not reader.fieldnames or not {'path', 'status'} <= set(reader.fieldnames):
            raise CleanupError(f'{path}: CSV must contain path and status columns.')
        for number, item in enumerate(reader, 2):
            if item.get('status') == 'repaired':
                add(item.get('path'), number)
        warnings.append(f'{path}: CSV contains latest results only; events.jsonl retains earlier repairs after later scans.')
    else:
        pending = None
        saw_check = False
        pattern = re.compile(r'^(?:\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}[,.]\d+\s+)?INFO:\s+(.*)$')
        final_status = re.compile(r'^(repaired|repair_failed|duration_match|verified|unknown|mismatch|corrupt|error):\s*(.*)$')
        for number, line in enumerate(text.splitlines(), 1):
            line = re.sub(r'\x1b\[[0-?]*[ -/]*[@-~]', '', line)
            match = pattern.match(line)
            if not match:
                continue
            message = match.group(1)
            checking = re.match(r'^\[\d+\] Checking (.+)$', message)
            if checking:
                pending = checking.group(1)
                saw_check = True
                canonical_path(pending)
                continue
            status = final_status.match(message)
            if status:
                if status.group(1) == 'repaired':
                    if pending is None or not status.group(2):
                        raise CleanupError(f'{path}:{number}: repaired result has no unambiguous preceding Checking path.')
                    add(pending, number)
                pending = None
            elif message.startswith(('Repair enabled.', 'Audit mode:', 'Results:')):
                pending = None
        if not saw_check and text.strip():
            raise CleanupError(f'{path}: no Savify Checking records found; use audit.log, events.jsonl or report.csv.')
    return found, warnings, {'path': str(path.resolve()), 'sha256': sha256(data)}


def elements(node, name=None):
    return [c for c in node.childNodes if c.nodeType == Node.ELEMENT_NODE and (name is None or c.tagName == name)]


def single(node, name):
    matches = elements(node, name)
    if len(matches) != 1:
        raise CleanupError(f'Expected exactly one {name} below {node.nodeName}; found {len(matches)}.')
    return matches[0]


def parse_nml(data):
    # Refuse DTDs/entities, while retaining ordinary comments, PIs and all
    # unfamiliar Traktor elements in the DOM used for the actual edit.
    parser = expat.ParserCreate()
    def no_doctype(*args):
        raise CleanupError('NML with a DOCTYPE is unsupported; collection was not changed.')
    parser.StartDoctypeDeclHandler = no_doctype
    parser.ExternalEntityRefHandler = lambda *args: 0
    try:
        parser.Parse(data, True)
        doc = minidom.parseString(data)
    except expat.ExpatError as exc:
        raise CleanupError(f'Invalid NML XML: {exc}') from exc
    if doc.documentElement.tagName != 'NML':
        raise CleanupError('Expected a Traktor NML document.')
    single(doc.documentElement, 'COLLECTION')
    return doc


def nml_path(volume, directory, filename, volume_map):
    if not filename or '/' in filename or '\\' in filename:
        raise CleanupError('LOCATION has a missing or invalid FILE attribute.')
    directory = directory.replace('/:', '/')
    if re.match(r'^/[A-Za-z]:/', directory):
        directory = directory[1:]  # Known Traktor 3 Windows drive-in-DIR representation.
    if re.match(r'^[A-Za-z]:/', directory) or directory.startswith('//'):
        if re.fullmatch(r'[A-Za-z]:', volume) and directory[:2].lower() != volume.lower():
            raise CleanupError('Conflicting drive letters in LOCATION VOLUME and DIR.')
        return directory.rstrip('/') + '/' + filename
    mapped = volume_map.get(volume)
    if mapped is not None:
        return mapped.rstrip('/\\') + '/' + directory.strip('/\\') + '/' + filename
    if re.fullmatch(r'[A-Za-z]:', volume) or volume.startswith(('\\\\', '//')):
        return volume.rstrip('/\\') + '/' + directory.strip('/\\') + '/' + filename
    if not volume and directory.startswith('/'):
        return directory.rstrip('/') + '/' + filename
    raise CleanupError(f'Cannot map volume {volume!r}; use --volume-map "{volume}=J:" if appropriate.')


def entry_path(entry, volume_map):
    loc = single(entry, 'LOCATION')
    value = nml_path(loc.getAttribute('VOLUME'), loc.getAttribute('DIR'), loc.getAttribute('FILE'), volume_map)
    return canonical_path(value), value


def raw_entry_key(entry):
    loc = single(entry, 'LOCATION')
    return loc.getAttribute('VOLUME') + loc.getAttribute('DIR') + loc.getAttribute('FILE')


def primary_path(key, volume_map):
    if '/:' in key:
        volume, tail = key.split('/:', 1)
        pieces = tail.split('/:')
        directory = '/:' + '/:'.join(pieces[:-1]) + ('/:' if len(pieces) > 1 else '')
        return canonical_path(nml_path(volume, directory, pieces[-1], volume_map))
    return canonical_path(key)


def playlist_entries(playlist):
    return elements(playlist, 'ENTRY')


def playlist_token(entry, volume_map):
    keys = elements(entry, 'PRIMARYKEY')
    if len(keys) == 1 and keys[0].getAttribute('TYPE') == 'TRACK':
        value = keys[0].getAttribute('KEY')
        try:
            return primary_path(value, volume_map)
        except CleanupError:
            return 'unresolved:' + value
    return 'other:' + entry.toxml()


def playlists(doc):
    for section in elements(doc.documentElement, 'PLAYLISTS'):
        yield from section.getElementsByTagName('PLAYLIST')


def playlist_descriptor(playlist):
    names = []
    node = playlist.parentNode
    while node and node.nodeType == Node.ELEMENT_NODE:
        if node.tagName == 'NODE':
            names.append(node.getAttribute('NAME'))
        node = node.parentNode
    return {'uuid': playlist.getAttribute('UUID'), 'names': list(reversed(names))}


def serialize(doc):
    result = doc.toxml(encoding='UTF-8')
    parse_nml(result).unlink()
    return result


def clean_collection(data, repaired, volume_map):
    doc = parse_nml(data)
    collection = single(doc.documentElement, 'COLLECTION')
    removed = []
    removed_nodes = []
    unsupported = []
    for entry in elements(collection, 'ENTRY'):
        try:
            key, path = entry_path(entry, volume_map)
        except CleanupError as exc:
            unsupported.append({'title': entry.getAttribute('TITLE'), 'reason': str(exc)})
            continue
        if key in repaired:
            removed.append({'path': repaired[key], 'canonical_path': key,
                            'title': entry.getAttribute('TITLE'), 'artist': entry.getAttribute('ARTIST')})
            removed_nodes.append(entry)
            collection.removeChild(entry)
    targets = {item['canonical_path'] for item in removed}
    changes = []
    for playlist in playlists(doc):
        entries = playlist_entries(playlist)
        before = [playlist_token(e, volume_map) for e in entries]
        removed_refs = []
        for index, entry in enumerate(entries):
            if before[index] in targets:
                removed_refs.append({'index': index, 'canonical_path': before[index]})
                playlist.removeChild(entry)
        if removed_refs:
            playlist.setAttribute('ENTRIES', str(len(playlist_entries(playlist))))
            changes.append(dict(playlist_descriptor(playlist), before=before,
                                after=[playlist_token(e, volume_map) for e in playlist_entries(playlist)],
                                removed=removed_refs))
    if removed:
        collection.setAttribute('ENTRIES', str(len(elements(collection, 'ENTRY'))))
    report = {'removed': removed, 'removed_entry_count': len(removed),
              'removed_path_count': len(targets), 'playlist_changes': changes,
              'removed_playlist_references': sum(len(c['removed']) for c in changes),
              'unmatched_repaired_paths': [v for k, v in repaired.items() if k not in targets],
              'unmapped_collection_entries': unsupported}
    return serialize(doc) if removed else data, report, clean_reimport(doc, removed_nodes, volume_map)


def clean_reimport(original_doc, removed, volume_map):
    """New import entries carry only location and basic title/artist hints."""
    doc = minidom.Document()
    def child(parent, tag, **attrs):
        element = doc.createElement(tag)
        for key, value in attrs.items():
            element.setAttribute(key, str(value))
        parent.appendChild(element)
        return element
    root = child(doc, 'NML', VERSION=original_doc.documentElement.getAttribute('VERSION') or '19')
    child(root, 'HEAD', COMPANY='www.native-instruments.com', PROGRAM='Traktor')
    child(root, 'MUSICFOLDERS')
    collection = child(root, 'COLLECTION', ENTRIES=0)
    section = child(root, 'PLAYLISTS')
    folder = child(section, 'NODE', TYPE='FOLDER', NAME='$ROOT')
    subnodes = child(folder, 'SUBNODES', COUNT=1)
    node = child(subnodes, 'NODE', TYPE='PLAYLIST', NAME='Savify repaired tracks')
    playlist = child(node, 'PLAYLIST', TYPE='LIST', UUID=uuid4().hex, ENTRIES=0)
    seen = set()
    for old in removed:
        key = raw_entry_key(old)
        canonical, _ = entry_path(old, volume_map)
        if canonical in seen:
            continue
        seen.add(canonical)
        entry = child(collection, 'ENTRY')
        for name in ('TITLE', 'ARTIST'):
            if old.hasAttribute(name):
                entry.setAttribute(name, old.getAttribute(name))
        old_location = single(old, 'LOCATION')
        child(entry, 'LOCATION', **{name: old_location.getAttribute(name)
                                   for name in ('DIR', 'FILE', 'VOLUME', 'VOLUMEID')
                                   if old_location.hasAttribute(name)})
        reference = child(playlist, 'ENTRY')
        child(reference, 'PRIMARYKEY', TYPE='TRACK', KEY=key)
    collection.setAttribute('ENTRIES', str(len(seen)))
    playlist.setAttribute('ENTRIES', str(len(seen)))
    return serialize(doc)


def restore_playlists(data, manifest, volume_map):
    if manifest.get('schema') != 1 or manifest.get('mode') != 'cleanup' or not manifest.get('applied'):
        raise CleanupError('Playlist restoration needs manifest.json from an applied cleanup, not a preview.')
    doc = parse_nml(data)
    collection = single(doc.documentElement, 'COLLECTION')
    available = {}
    for entry in elements(collection, 'ENTRY'):
        try:
            key, _ = entry_path(entry, volume_map)
            available.setdefault(key, raw_entry_key(entry))
        except CleanupError:
            pass
    plans = []
    for saved in manifest.get('playlist_changes', []):
        candidates = []
        for playlist in playlists(doc):
            descriptor = playlist_descriptor(playlist)
            if (saved['uuid'] and descriptor['uuid'] == saved['uuid']) or (
                    not saved['uuid'] and descriptor['names'] == saved['names']):
                candidates.append(playlist)
        name = ' / '.join(saved['names'])
        if len(candidates) != 1:
            raise CleanupError(f'Cannot uniquely find playlist {name!r}; no changes applied.')
        playlist = candidates[0]
        current = [playlist_token(e, volume_map) for e in playlist_entries(playlist)]
        if current == saved['before']:
            continue  # Already restored.
        if current != saved['after']:
            raise CleanupError(f'Playlist {name!r} changed since cleanup; refusing to overwrite those edits.')
        for removal in saved['removed']:
            if removal['canonical_path'] not in available:
                raise CleanupError(f'Re-import repaired tracks before restoring playlist {name!r}.')
        plans.append((playlist, saved))
    restored = 0
    for playlist, saved in plans:
        for removal in sorted(saved['removed'], key=lambda r: r['index']):
            entry = doc.createElement('ENTRY')
            primary = doc.createElement('PRIMARYKEY')
            primary.setAttribute('TYPE', 'TRACK')
            primary.setAttribute('KEY', available[removal['canonical_path']])
            entry.appendChild(primary)
            entries = playlist_entries(playlist)
            if removal['index'] < len(entries):
                playlist.insertBefore(entry, entries[removal['index']])
            else:
                playlist.appendChild(entry)
            restored += 1
        playlist.setAttribute('ENTRIES', str(len(playlist_entries(playlist))))
    return serialize(doc) if restored else data, {'restored_playlist_references': restored}


def assert_traktor_closed():
    if sys.platform == 'win32':
        result = subprocess.run(['tasklist', '/FO', 'CSV', '/NH'], capture_output=True, timeout=20, check=False)
        if result.returncode:
            raise CleanupError('Could not check running applications; close Traktor and retry.')
        rows = csv.reader(io.StringIO(result.stdout.decode(errors='replace')))
        if any(row and row[0].lower().startswith('traktor') and row[0].lower().endswith('.exe') for row in rows):
            raise CleanupError('Traktor is running. Close it before applying collection changes.')
    elif sys.platform == 'darwin':
        result = subprocess.run(['pgrep', '-x', 'Traktor'], capture_output=True, timeout=20, check=False)
        if result.returncode == 0:
            raise CleanupError('Traktor is running. Close it before applying collection changes.')
        if result.returncode != 1:
            raise CleanupError('Could not check running applications; close Traktor and retry.')


@contextmanager
def collection_lock(path):
    # OS locks release on a crash. This coordinates copies of this script, not Traktor.
    lock_path = path.with_name(path.name + '.savify-reset.lock')
    with lock_path.open('a+b') as handle:
        handle.seek(0)
        if not handle.read(1):
            handle.write(b'0')
            handle.flush()
        handle.seek(0)
        try:
            if os.name == 'nt':
                import msvcrt
                msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as exc:
            raise CleanupError('Another cleanup is using this collection.') from exc
        try:
            yield
        finally:
            if os.name == 'nt':
                handle.seek(0)
                msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


def write_new(path, data):
    with path.open('xb') as out:
        out.write(data)
        out.flush()
        os.fsync(out.fileno())


def unchanged(path, original, token):
    if file_token(path) != token or path.read_bytes() != original or file_token(path) != token:
        raise CleanupError('collection.nml changed during this operation; it was not replaced. Close Traktor and retry.')


def install(path, original, token, updated, report_dir):
    assert_traktor_closed()
    unchanged(path, original, token)
    backup = report_dir / 'collection.original.nml'
    write_new(backup, original)
    if backup.read_bytes() != original:
        raise CleanupError('Backup verification failed; collection was not changed.')
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(prefix='.savify-nml-', suffix='.tmp', dir=path.parent, delete=False) as out:
            temporary = Path(out.name)
            out.write(updated)
            out.flush()
            os.fsync(out.fileno())
        os.chmod(temporary, stat.S_IMODE(path.stat().st_mode))
        assert_traktor_closed()
        unchanged(path, original, token)
        os.replace(temporary, path)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)
    return backup


def volume_maps(values):
    result = {}
    for value in values:
        if '=' not in value:
            raise CleanupError('--volume-map requires NAME=ROOT, for example DJ Disk=J:')
        name, root = value.split('=', 1)
        canonical_path(root.rstrip('/\\') + '/test.mp3')
        if not name or name in result:
            raise CleanupError('Volume map names must be non-empty and unique.')
        result[name] = root
    return result


def build_parser():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('collection', type=Path, help='The active Traktor collection.nml, not an exported playlist.')
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument('--repair-log', type=Path, action='append', help='events.jsonl (recommended), audit.log, console log or report.csv. Repeat for multiple logs.')
    source.add_argument('--restore-playlists', type=Path, metavar='MANIFEST', help='After re-import, restore playlist positions from an applied cleanup manifest.')
    parser.add_argument('--apply', action='store_true', help='Back up and atomically update collection.nml. Default: preview only.')
    parser.add_argument('--report-dir', type=Path, help='New, not-yet-existing directory for the preview, manifest, re-import file and backup.')
    parser.add_argument('--log-encoding', help='For a legacy console log only, e.g. cp1252. UTF-8/UTF-16 BOMs are automatic.')
    parser.add_argument('--volume-map', action='append', default=[], metavar='NAME=ROOT', help='Explicit mapping for NML volume labels, e.g. "DJ Disk=J:". Drive-letter volumes need no mapping.')
    return parser


def run(args):
    path = args.collection.expanduser().absolute()
    if path.suffix.lower() != '.nml':
        raise CleanupError('Collection input must be an .nml file.')
    mapping = volume_maps(args.volume_map)
    original, token = read_stable(path)
    manifest = {'schema': 1, 'mode': 'cleanup', 'collection': str(path), 'original_sha256': sha256(original),
                'applied': False, 'volume_map': mapping, 'warnings': []}
    reimport = None
    if args.repair_log:
        repaired = {}
        sources = []
        for source in args.repair_log:
            found, warnings, source_info = repaired_paths(source, args.log_encoding)
            repaired.update(found)
            manifest['warnings'].extend(warnings)
            sources.append(source_info)
        updated, plan, reimport = clean_collection(original, repaired, mapping)
        manifest.update(plan, repair_sources=sources, successful_repair_paths=len(repaired))
        count = plan['removed_entry_count']
        print(f'{len(repaired)} successfully repaired paths; {count} collection entries matched; '
              f'{plan["removed_playlist_references"]} playlist references matched.')
        for item in plan['removed'][:10]:
            print('  Remove: ' + item['path'])
        if count > 10:
            print(f'  ... and {count - 10} more; all are listed in manifest.json.')
        if plan['unmatched_repaired_paths']:
            print(f'{len(plan["unmatched_repaired_paths"])} repaired paths were not found in this collection; see manifest.json.')
        if plan['unmapped_collection_entries']:
            print(f'{len(plan["unmapped_collection_entries"])} collection entries could not be mapped; they are untouched and listed in manifest.json.')
    else:
        previous = json.loads(args.restore_playlists.read_text(encoding='utf-8-sig'))
        if str(path) != previous.get('collection'):
            raise CleanupError('Manifest belongs to a different collection path.')
        mapping = mapping or previous.get('volume_map', {})
        updated, plan = restore_playlists(original, previous, mapping)
        manifest.update(plan, mode='restore_playlists', source_manifest=str(args.restore_playlists.absolute()))
        count = plan['restored_playlist_references']
        print(f'{count} playlist references can be restored; fresh collection entries remain untouched.')
    manifest['updated_sha256'] = sha256(updated)
    for warning in manifest['warnings']:
        print('NOTE: ' + warning)
    stamp = datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')
    report_dir = (args.report_dir.expanduser().absolute() if args.report_dir else
                  path.with_name(path.name + '.savify-' + stamp + '-' + uuid4().hex[:8]))
    report_dir.mkdir(parents=True, exist_ok=False)
    write_new(report_dir / 'collection.proposed.nml', updated)
    if reimport is not None and count:
        write_new(report_dir / 'reimport-repaired.nml', reimport)
    manifest_path = report_dir / 'manifest.json'
    write_new(manifest_path, (json.dumps(manifest, ensure_ascii=False, indent=2) + '\n').encode('utf-8'))
    print('Report: ' + str(manifest_path))
    if not args.apply:
        print('PREVIEW ONLY: collection.nml is unchanged. Add --apply to create a backup and install the changes.')
        return 0
    if not count:
        print('No changes to apply. Collection was left untouched.')
        return 0
    with collection_lock(path):
        backup = install(path, original, token, updated, report_dir)
        manifest['applied'] = True
        manifest['backup'] = str(backup)
        # A crash after the swap can be checked against updated_sha256 in the
        # prewritten manifest; the original backup already exists at that point.
        temporary = report_dir / 'manifest.applied.json'
        try:
            write_new(temporary, (json.dumps(manifest, ensure_ascii=False, indent=2) + '\n').encode('utf-8'))
            os.replace(temporary, manifest_path)
        except OSError as exc:
            raise CleanupError(f'Collection WAS updated and backed up at {backup}, but manifest finalization failed: {exc}') from exc
    print('APPLIED. Original backup: ' + str(backup))
    if args.repair_log:
        print('Next: start Traktor, import reimport-repaired.nml using File tags, and analyze the repaired tracks.')
        print('Playlist memberships were saved. After re-import and closing Traktor, use --restore-playlists with this manifest.')
    return 0


def main(argv=None):
    args = build_parser().parse_args(argv)
    try:
        return run(args)
    except KeyboardInterrupt:
        print('Interrupted. Inspect the manifest and backup before repeating an apply.', file=sys.stderr)
        return 130
    except (CleanupError, OSError, ValueError, subprocess.SubprocessError) as exc:
        print('ERROR: ' + str(exc), file=sys.stderr)
        return 2


if __name__ == '__main__':
    raise SystemExit(main())
