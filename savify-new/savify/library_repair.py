"""Prepare verified replacements, back up originals, then replace atomically."""
import hashlib
from itertools import islice
import os
from pathlib import Path
import shutil
import stat
from tempfile import TemporaryDirectory
from uuid import uuid4

from .audio_download import download_candidate
from .audio_identity import IdentityConfigurationError
from .media_validation import _run, ffmpeg_binary, validate_audio
from .traktor_nml import is_stem_path, STEM_REPAIR_REASON
from .youtube_matcher import (NoReliableMatch, _clean_title, _remix_evidence, _versions,
                              duration_range_error, duration_seconds, duration_tolerance)


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


def sync_file(path):
    # Windows FlushFileBuffers/_commit needs a writable handle. fsync(open(...,
    # 'rb')) can raise EBADF on Windows even though it works on Linux.
    with Path(path).open('r+b') as handle:
        handle.flush()
        os.fsync(handle.fileno())


def remux_clean(source, output, ffmpeg_location='ffmpeg'):
    """Start with audio only. Do not copy original chunks, tags, chapters or images."""
    codec = 'pcm_s16be' if Path(output).suffix.lower() in ('.aif', '.aiff') else 'copy'
    command = [ffmpeg_binary(ffmpeg_location), '-nostdin', '-v', 'error', '-y',
               '-i', str(source), '-map', '0:a:0', '-map_metadata', '-1',
               '-map_metadata:s:a', '-1', '-map_chapters', '-1', '-c:a', codec]
    if Path(output).suffix.lower() == '.mp3':
        command += ['-write_id3v1', '0']
    _run(command + [str(output)], timeout=600)


def metadata_duration_match(track, match, actual_duration, allow_source_duration=False):
    """Stronger core metadata/length evidence when no fingerprint is recognized.

    Album/label/IDs are deliberately absent. Manual selections may bypass the
    normal search gates, so they still require positive fingerprint verification.
    Explicit NML/range requests may use the independently inspected source length
    when no catalog duration is available; the broken original is never a target.
    """
    expected = duration_seconds(getattr(track, 'duration', None))
    source_duration = duration_seconds(match.duration)
    if not source_duration or match.manual:
        return False
    if not expected:
        if not allow_source_duration:
            return False
        expected = source_duration
    limit = duration_tolerance(expected, override=getattr(track, 'duration_tolerance', None))
    return (match.title_score >= 0.94 and match.artist_score >= 0.90
            and _versions(track.name) == _versions(_clean_title(match.title, track.artists))
            and not _remix_evidence(track, {'title': match.title})['reason']
            and not duration_range_error(actual_duration, getattr(track, 'min_duration', None), getattr(track, 'max_duration', None))
            and not duration_range_error(source_duration, getattr(track, 'min_duration', None), getattr(track, 'max_duration', None))
            and abs(actual_duration - expected) <= limit
            and abs(source_duration - expected) <= limit)


class LibraryRepairer:
    def __init__(self, root, state, matcher, identifier, logger, ffmpeg_location='ffmpeg',
                 options=None, max_candidates=5, downloader=download_candidate):
        self.root = Path(root) if root is not None else None
        self.state, self.matcher = state, matcher
        self.identifier, self.logger = identifier, logger
        self.ffmpeg_location, self.options = ffmpeg_location, options or {}
        self.max_candidates, self.downloader = max_candidates, downloader

    def repair(self, path, snapshot, track, token, digest, duration_mismatch=False, verify_existing=False,
               force_redownload=False):
        path = Path(path)
        if is_stem_path(path):
            raise RepairError(STEM_REPAIR_REASON)
        if force_redownload:
            verify_existing = False
        download_format = {'.ogg': 'vorbis', '.mp4': 'm4a', '.aif': 'wav', '.aiff': 'wav'}.get(
            path.suffix.lower(), path.suffix.lower()[1:])
        errors, attempts = [], 0
        try:
            for match in islice(self.matcher.candidates(track), self.max_candidates):
                attempts += 1
                self.logger.info('  Downloading temporary %s candidate %s/%s: %s [%s]',
                                 'reference' if verify_existing else 'replacement', attempts, self.max_candidates,
                                 match.title, match.url)
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
                        self.state.log_event('candidate_identity', path=str(path), source_url=match.url,
                                             title=match.title, identity=identity.to_dict())
                        duration_fallback = ((duration_mismatch or verify_existing or force_redownload) and identity.status == 'unknown'
                                             and (identity.unrecognized or identity.incomplete)
                                             and metadata_duration_match(track, match, duration,
                                                                         allow_source_duration=force_redownload))
                        if identity.status != 'verified' and not duration_fallback:
                            raise RepairError('Replacement identity is {}: {}'.format(identity.status, identity.reason))
                        verification = 'metadata_duration' if duration_fallback else 'fingerprint'
                        if duration_fallback and not duration_seconds(getattr(track, 'duration', None)):
                            verification = 'metadata_source_duration'
                        comparison = None
                        if verify_existing:
                            comparison = self.identifier.compare(path, staging)
                            self.state.log_event('reference_comparison', path=str(path), source_url=match.url,
                                                 comparison=comparison.to_dict())
                            self.logger.info('  Reference comparison: %s — %s', comparison.status, comparison.reason)
                            if comparison.status == 'same':
                                assert_unchanged(path, token, digest)
                                return {'status': 'verified', 'reason': 'Existing audio matches the downloaded reference; original and Traktor tags retained.',
                                        'digest': digest, 'reference_duration': duration, 'verification': 'reference_fingerprint',
                                        'reference_comparison': comparison.to_dict(), 'source_url': match.url,
                                        'reference_identity': identity.to_dict()}
                            if comparison.status != 'different':
                                raise RepairError(comparison.reason)
                            if duration_fallback:
                                verification = 'reference_metadata_duration'
                    except IdentityConfigurationError:
                        raise
                    except Exception as exc:
                        errors.append(str(exc))
                        self.logger.warning('Rejected replacement for %s: %s', path, exc)
                        self.logger.debug('Candidate failure details for %s', path, exc_info=True)
                        continue
                    # Failures from this point abort this file. In particular, do
                    # not retry a download after the original changed or a disk error.
                    assert_unchanged(path, token, digest)
                    self.logger.info('  Backing up original before replacement: %s', path)
                    backup = self._backup(path, token, digest)
                    sync_file(staging)
                    os.chmod(staging, stat.S_IMODE(path.stat().st_mode))
                    new_digest = file_digest(staging)
                    replacement_id = self.state.prepare_replacement(path, digest, new_digest, backup, match.url,
                                                                    verification=verification)
                    assert_unchanged(path, token, digest)
                    os.replace(staging, path)  # Same-directory filesystem; original never removed first.
                    self.state.record_installed_replacement(path, backup, match.url, verification)
                    self.state.commit_replacement(replacement_id)
                    self.matcher.store.remember(track, match, duration)
                    reason = ('Replacement matched artist/title/version and expected duration; database identity incomplete or unrecognized.'
                              if duration_fallback else 'Fingerprint-verified replacement installed; original backed up.')
                    if verification == 'metadata_source_duration':
                        reason = ('Requested replacement matched artist/title/version, inspected source duration and configured bounds; '
                                  'no independent expected duration was available; original backed up.')
                    return {'status': 'repaired', 'reason': reason, 'verification': verification,
                            'digest': new_digest, 'duration': duration, 'score': identity.score,
                            'backup': str(backup), 'source_url': match.url,
                            'discarded_tags': snapshot.dropped, 'identity': identity.to_dict(),
                            'reference_comparison': comparison.to_dict() if comparison else None}
        except NoReliableMatch as exc:
            errors.append(str(exc))
        reason = 'No conclusive {} after {} candidate(s). {}'.format(
            'reference comparison' if verify_existing else 'replacement', attempts,
            ' | '.join(errors[-3:]) or 'No acceptable source found.')
        if verify_existing:
            return {'status': 'unknown', 'reason': reason, 'digest': digest}
        raise RepairError(reason)

    def _backup(self, path, token, digest):
        if self.root is None:
            # NML targets can span unrelated directories, drives and UNC shares.
            # Namespace by full path so equal filenames/content never share a backup.
            path_id = hashlib.sha256(os.fsencode(str(path.resolve()))).hexdigest()[:16]
            relative = Path(path_id) / path.name
        else:
            relative = path.relative_to(self.root)
        backup = self.state.directory / 'backups' / digest[:16] / relative
        backup.parent.mkdir(parents=True, exist_ok=True)
        if backup.exists():
            if file_digest(backup) != digest:
                raise RepairError('Existing backup has unexpected contents; refusing to overwrite it.')
        else:
            temporary = backup.with_name(backup.name + '.' + uuid4().hex + '.partial')
            try:
                shutil.copyfile(path, temporary)
                if file_digest(temporary) != digest:
                    raise RepairError('Original changed while making its backup.')
                sync_file(temporary)
                shutil.copystat(path, temporary)
                os.replace(temporary, backup)
            finally:
                temporary.unlink(missing_ok=True)
        assert_unchanged(path, token, digest)
        return backup
