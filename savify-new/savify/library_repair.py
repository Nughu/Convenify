"""Prepare verified replacements, back up originals, then replace atomically."""
import hashlib
import os
from pathlib import Path
import shutil
import stat
from tempfile import TemporaryDirectory
from uuid import uuid4

from .audio_download import download_candidate
from .audio_identity import IdentityConfigurationError
from .media_validation import _run, ffmpeg_binary, validate_audio
from .youtube_matcher import NoReliableMatch, _clean_title, _versions, duration_seconds, duration_tolerance


class RepairError(Exception):
    pass


def file_digest(path):
    digest = hashlib.sha256()
    with Path(path).open('rb') as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b''):
            digest.update(chunk)
    return digest.hexdigest()


def file_token(path):
    info = Path(path).lstat()
    if not stat.S_ISREG(info.st_mode):
        raise RepairError('Only regular, non-symlink files may be replaced.')
    return (info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns, info.st_ctime_ns)


def assert_unchanged(path, token, digest):
    if file_token(path) != token or file_digest(path) != digest or file_token(path) != token:
        raise RepairError('Original changed during the audit; no replacement was made. Close Traktor and retry.')


def remux_clean(source, output, ffmpeg_location='ffmpeg'):
    """Start with audio only. Do not copy original chunks, tags, chapters or images."""
    codec = 'pcm_s16be' if Path(output).suffix.lower() in ('.aif', '.aiff') else 'copy'
    command = [ffmpeg_binary(ffmpeg_location), '-nostdin', '-v', 'error', '-y',
               '-i', str(source), '-map', '0:a:0', '-map_metadata', '-1',
               '-map_metadata:s:a', '-1', '-map_chapters', '-1', '-c:a', codec]
    if Path(output).suffix.lower() == '.mp3':
        command += ['-write_id3v1', '0']
    _run(command + [str(output)], timeout=600)


def metadata_duration_match(track, match, actual_duration):
    """Stronger core metadata/length evidence when no fingerprint is recognized.

    Album/label/IDs are deliberately absent. Manual selections may bypass the
    normal search gates, so they still require positive fingerprint verification.
    """
    expected = duration_seconds(getattr(track, 'duration', None))
    source_duration = duration_seconds(match.duration)
    if not expected or not source_duration or match.manual:
        return False
    limit = duration_tolerance(expected)
    return (match.title_score >= 0.94 and match.artist_score >= 0.90
            and _versions(track.name) == _versions(_clean_title(match.title, track.artists))
            and abs(actual_duration - expected) <= limit
            and abs(source_duration - expected) <= limit)


class LibraryRepairer:
    def __init__(self, root, state, matcher, identifier, logger, ffmpeg_location='ffmpeg',
                 options=None, max_candidates=5, downloader=download_candidate):
        self.root, self.state, self.matcher = Path(root), state, matcher
        self.identifier, self.logger = identifier, logger
        self.ffmpeg_location, self.options = ffmpeg_location, options or {}
        self.max_candidates, self.downloader = max_candidates, downloader

    def repair(self, path, snapshot, track, token, digest, duration_mismatch=False):
        path = Path(path)
        download_format = {'.ogg': 'vorbis', '.mp4': 'm4a', '.aif': 'wav', '.aiff': 'wav'}.get(
            path.suffix.lower(), path.suffix.lower()[1:])
        errors, attempts = [], 0
        try:
            for match in self.matcher.candidates(track):
                if attempts >= self.max_candidates:
                    break
                attempts += 1
                with TemporaryDirectory(prefix='.savify-repair-', dir=str(path.parent)) as work:
                    work = Path(work)
                    try:
                        source = self.downloader(match, work, download_format, '0', self.ffmpeg_location,
                                                 self.options, self.logger)
                        validate_audio(source, track, match, self.ffmpeg_location)
                        staging = work / ('replacement' + path.suffix)
                        remux_clean(source, staging, self.ffmpeg_location)
                        snapshot.write_to(staging)
                        snapshot.verify_copy(staging)
                        duration = validate_audio(staging, track, match, self.ffmpeg_location, decode=False)
                        identity = self.identifier.identify(staging, track, duration)
                        duration_fallback = (duration_mismatch and identity.status == 'unknown'
                                             and identity.unrecognized
                                             and metadata_duration_match(track, match, duration))
                        if identity.status != 'verified' and not duration_fallback:
                            raise RepairError('Replacement identity is {}: {}'.format(identity.status, identity.reason))
                        verification = 'metadata_duration' if duration_fallback else 'fingerprint'
                    except IdentityConfigurationError:
                        raise
                    except Exception as exc:
                        errors.append(str(exc))
                        self.logger.warning('Rejected replacement for %s: %s', path, exc)
                        continue
                    # Failures from this point abort this file. In particular, do
                    # not retry a download after the original changed or a disk error.
                    assert_unchanged(path, token, digest)
                    backup = self._backup(path, token, digest)
                    os.chmod(staging, stat.S_IMODE(path.stat().st_mode))
                    with staging.open('rb') as handle:
                        os.fsync(handle.fileno())
                    new_digest = file_digest(staging)
                    replacement_id = self.state.prepare_replacement(path, digest, new_digest, backup, match.url,
                                                                    verification=verification)
                    assert_unchanged(path, token, digest)
                    os.replace(staging, path)  # Same-directory filesystem; original never removed first.
                    self.state.commit_replacement(replacement_id)
                    self.matcher.store.remember(track, match, duration)
                    reason = ('Replacement matched artist/title/version and expected duration; fingerprint unrecognized.'
                              if duration_fallback else 'Fingerprint-verified replacement installed; original backed up.')
                    return {'status': 'repaired', 'reason': reason, 'verification': verification,
                            'digest': new_digest, 'duration': duration, 'score': identity.score,
                            'backup': str(backup), 'source_url': match.url,
                            'discarded_tags': snapshot.dropped, 'identity': identity.to_dict()}
        except NoReliableMatch as exc:
            errors.append(str(exc))
        raise RepairError('No verified replacement after {} candidate(s). {}'.format(
            attempts, ' | '.join(errors[-3:]) or 'No acceptable source found.'))

    def _backup(self, path, token, digest):
        backup = self.state.directory / 'backups' / digest[:16] / path.relative_to(self.root)
        backup.parent.mkdir(parents=True, exist_ok=True)
        if backup.exists():
            if file_digest(backup) != digest:
                raise RepairError('Existing backup has unexpected contents; refusing to overwrite it.')
        else:
            temporary = backup.with_name(backup.name + '.' + uuid4().hex + '.partial')
            try:
                shutil.copy2(path, temporary)
                if file_digest(temporary) != digest:
                    raise RepairError('Original changed while making its backup.')
                with temporary.open('rb') as handle:
                    os.fsync(handle.fileno())
                os.replace(temporary, backup)
            finally:
                temporary.unlink(missing_ok=True)
        assert_unchanged(path, token, digest)
        return backup
