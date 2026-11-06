"""Recursive duration audit and safe repair: python -m savify.library_audit."""
import argparse
from collections import Counter
import json
import logging
import os
from pathlib import Path
from types import SimpleNamespace

from .audio_identity import (AcoustIDClient, AudioIdentifier, Fingerprinter, IdentityError,
                             IdentityConfigurationError, IdentityResult)
from .library_catalog import CatalogResolver
from .library_repair import LibraryRepairer, RepairError, assert_unchanged, file_digest, file_token
from .library_state import AuditState, audit_lock
from .library_tags import SUPPORTED_EXTENSIONS, TagSnapshot
from .media_validation import AudioValidationError, _run, ffmpeg_binary, probe_audio, validate_audio
from .youtube_matcher import MatchConfig, MatchStore, SearchProvider, YouTubeMatcher, duration_seconds, duration_tolerance


def audio_files(root, state_dir, logger, on_error=None):
    def failed(exc):
        logger.error('Cannot scan directory: %s', exc)
        if on_error:
            on_error(exc)
    for folder, dirs, files in os.walk(root, followlinks=False, onerror=failed):
        dirs[:] = sorted(name for name in dirs if name != '_savify_audit'
                         and not name.startswith('.savify-repair-')
                         and not (Path(folder) / name).is_symlink()
                         and (Path(folder) / name).resolve() != state_dir.resolve())
        for name in sorted(files):
            path = Path(folder) / name
            if path.suffix.lower() in SUPPORTED_EXTENSIONS:
                yield path


class LibraryAuditor:
    def __init__(self, root, state, identifier, logger, repairer=None, catalog=None,
                 ffmpeg_location='ffmpeg', rescan=False, repair_unknown=False, reference_check=False,
                 full_verification=False, duration_tolerance_seconds=10.0):
        self.root, self.state, self.identifier, self.logger = Path(root), state, identifier, logger
        self.repairer, self.catalog = repairer, catalog
        self.ffmpeg_location, self.rescan, self.repair_unknown = ffmpeg_location, rescan, repair_unknown
        self.reference_check = reference_check
        self.full_verification = full_verification
        self.duration_tolerance = duration_tolerance(None, override=duration_tolerance_seconds)

    def process(self, path):
        path = Path(path)
        result = {'path': str(path), 'status': 'error', 'reason': ''}
        digest = ''
        try:
            token = file_token(path)
            # Read the entire file only for an existing content checkpoint or an
            # actual verification/repair. Most files need only tags and a probe.
            if self.full_verification or self.state.has_content_checkpoint(path):
                digest = file_digest(path)
                assert_unchanged(path, token, digest)
                previous = self.state.cached_file(path, digest) if not self.rescan else None
                if previous and previous.get('duration_tolerance') == self.duration_tolerance:
                    return dict(previous, cached=True)
            snapshot = TagSnapshot.read(path)
            track = snapshot.intended_track()
            result.update(artist=' / '.join(track.artists), title=track.name, track_id=str(track.id))
            # Recover a crash after os.replace but before the file checkpoint.
            recovered = self.state.replacement_for(path, digest) if digest else None
            if recovered:
                result.update(recovered)  # Keep the backup/source even when the tolerance changed.
            if self.catalog:
                self.catalog.enrich(track)
            expected = duration_seconds(track.duration)
            tolerance = self.duration_tolerance
            # The same fixed limit follows replacement searches and validation.
            track.duration_tolerance = tolerance
            duration_mismatch = False
            catalog_note = getattr(track, 'catalog_note', 'No independent catalog duration available.' if not expected else 'Independent catalog match.')
            result.update(expected_duration=expected, duration_tolerance=tolerance, catalog_note=catalog_note)
            try:
                # Probe first: decoding/fingerprinting an entire wrong album is
                # unnecessary once an independent track length disproves it.
                duration = probe_audio(path, self.ffmpeg_location)
                self.logger.info('  Existing file duration: %.1fs; expected: %s', duration,
                                 '{:.1f}s'.format(expected) if expected else 'unavailable — ' + catalog_note)
                # Never infer the desired length from the potentially wrong file.
                if expected and abs(duration - expected) > tolerance:
                    duration_mismatch = True
                    self.logger.info('  Existing file has a clear duration mismatch; skipping its fingerprint.')
                    identity = IdentityResult('mismatch',
                        'Duration {:.1f}s differs from independently matched {:.1f}s by more than {:.1f}s.'.format(
                            duration, expected, tolerance), duration=duration)
                elif recovered and not self.rescan and (expected or self.full_verification):
                    # A journal proves a prior installation, not compliance with
                    # today's tolerance. Recover only after checking its length.
                    assert_unchanged(path, token, digest)
                    result.update(status='repaired', duration=duration,
                                  reason='Recovered previously validated replacement from the journal; current duration tolerance satisfied.'
                                  if expected else 'Recovered previously validated replacement from the journal.')
                    self.state.record(path, digest, result)
                    return result
                elif not self.full_verification:
                    if expected:
                        identity = IdentityResult('duration_match',
                            'Duration matches within {:.1f}s; presumed correct. Skipping decoding, '
                            'fingerprinting and reference downloads; original and Traktor tags retained.'.format(tolerance),
                            duration=duration)
                        result['verification'] = 'duration_only'
                    else:
                        identity = IdentityResult('unknown',
                            'No reliable expected duration; verification and repair skipped. ' + catalog_note,
                            duration=duration)
                        result['verification'] = 'not_checked'
                else:
                    self.logger.info('  Decoding and identifying existing file audio.')
                    validate_audio(path, SimpleNamespace(duration=None), ffmpeg_location=self.ffmpeg_location)
                    try:
                        identity = self.identifier.identify(path, track, duration)
                    except IdentityConfigurationError:
                        raise
                    except IdentityError as exc:
                        identity = IdentityResult('unknown', str(exc), duration=duration)
            except AudioValidationError as exc:
                if self.full_verification:
                    identity = IdentityResult('corrupt', 'Audio decode/probe failed: {}'.format(exc))
                else:
                    identity = IdentityResult('unknown',
                        'Cannot measure duration; verification and repair skipped: {}'.format(exc))
                    result['verification'] = 'not_checked'
            result.update(status=identity.status, reason=identity.reason, score=identity.score,
                          duration=identity.duration, identity=identity.to_dict(),
                          original_duration=identity.duration, original_identity=identity.to_dict(),
                          repair_trigger='duration_mismatch' if duration_mismatch else identity.status,
                          identified_as='; '.join('{} — {}'.format('/'.join(r.get('artists') or []), r.get('title') or '?')
                                                  for r in identity.recordings))
            if file_token(path) != token:
                raise RepairError('Original changed during the audit; no replacement was made. Close Traktor and retry.')
            verify_reference = (self.full_verification and identity.status == 'unknown' and self.reference_check and self.repairer is not None
                                and expected is not None and (identity.unrecognized or identity.incomplete or identity.recordings))
            should_repair = duration_mismatch or (self.full_verification and (
                identity.status in ('mismatch', 'corrupt') or (
                    self.repair_unknown and identity.status == 'unknown') or verify_reference))
            if should_repair and self.repairer and not digest:
                digest = file_digest(path)
            if digest:
                assert_unchanged(path, token, digest)
            if should_repair and self.repairer:
                if verify_reference:
                    self.logger.info('  Database identification inconclusive; searching for a reference to compare with the existing file.')
                try:
                    repaired = self.repairer.repair(path, snapshot, track, token, digest,
                                                    duration_mismatch=duration_mismatch, verify_existing=verify_reference)
                    digest = repaired.pop('digest')
                    result.update(repaired)
                except IdentityConfigurationError:
                    raise
                except Exception as exc:
                    self.logger.debug('Repair failure details for %s', path, exc_info=True)
                    # If commit/reporting failed AFTER a successful atomic swap,
                    # report the journaled replacement rather than a false failure.
                    current_digest = file_digest(path)
                    recovered = self.state.replacement_for(path, current_digest)
                    if current_digest != digest and recovered:
                        digest = current_digest
                        result.update(recovered, status='repaired', reason='Validated replacement installed; recovered from journal.')
                    else:
                        result.update(status='repair_failed', reason=str(exc))
        except IdentityConfigurationError:
            raise
        except Exception as exc:
            self.logger.debug('Audit failure details for %s', path, exc_info=True)
            result.update(status='error', reason=str(exc))
        self.state.record(path, digest, result)
        return result

    def run(self, limit=None):
        counts = Counter()
        scan_errors = []
        for index, path in enumerate(audio_files(self.root, self.state.directory, self.logger, scan_errors.append), 1):
            if limit is not None and index > limit:
                break
            self.logger.info('[%s] Checking %s', index, path)
            result = self.process(path)
            counts[result['status']] += 1
            if result.get('cached'):
                counts['cached'] += 1
            self.logger.info('  %s: %s', result['status'], result['reason'])
            if index % 25 == 0:
                self.state.export_report()
        for exc in scan_errors:
            path = str(exc.filename or self.root)
            self.state.record(path, '', {'path': path, 'status': 'error', 'reason': 'Cannot scan directory: ' + str(exc)})
            counts['error'] += 1
        return counts


def parser():
    result = argparse.ArgumentParser(description=(
        'Recursively check music durations. Matching lengths skip audio verification and downloads. '
        'Default: audit only. --repair backs up and replaces clear duration mismatches.'))
    result.add_argument('directory', type=Path, help='Music directory; includes all subdirectories.')
    result.add_argument('--repair', action='store_true', help='Install validated replacements at the original paths.')
    result.add_argument('--duration-tolerance', type=duration_tolerance_argument, default=10.0, metavar='SECONDS', help=(
        'Maximum absolute difference from expected duration for existing files and replacements '
        '(default: 10 seconds, independent of track length). Accepts non-negative decimals; 0 requires an exact match.'))
    result.add_argument('--full-verification', action='store_true', help=(
        'Opt into fingerprint checks even when duration matches or is unavailable; '
        'with --repair, unknown files may trigger temporary reference downloads.'))
    result.add_argument('--repair-unknown', action='store_true', help=(
        'With --repair --full-verification, also attempt unrecognized/ambiguous files. '
        'Replacement still requires positive identity verification.'))
    result.add_argument('--state-dir', type=Path, help='Reports, checkpoints and backups (default: DIRECTORY/_savify_audit).')
    result.add_argument('--rescan', action='store_true', help=(
        'Recheck unchanged verified/repaired files using the selected mode; also refresh fingerprint lookups. '
        'Does not enable full verification.'))
    result.add_argument('--limit', type=int, help='Process at most N files for an initial trial.')
    result.add_argument('--ffmpeg-location', default='ffmpeg', help='FFmpeg executable or its directory.')
    result.add_argument('--fpcalc', help='Optional Chromaprint fpcalc executable, for FFmpeg builds without Chromaprint.')
    catalog = result.add_mutually_exclusive_group()
    catalog.add_argument('--spotify', dest='spotify', action='store_true', help=(
        'Use SPOTIPY_CLIENT_ID/SECRET for expected duration (also enabled automatically when both credentials are set).'))
    catalog.add_argument('--no-spotify', dest='spotify', action='store_false', help='Disable optional Spotify catalog lookups.')
    result.set_defaults(spotify=None)
    result.add_argument('--no-reference-check', action='store_true', help=(
        'With --full-verification, leave unidentified files for review instead of downloading references.'))
    result.add_argument('--match-overrides', type=Path, help='JSON mapping report track_id values to individual YouTube URLs.')
    result.add_argument('--max-candidates', type=int, default=5, help='Maximum replacement downloads per file (default: 5).')
    result.add_argument('--cookies', type=Path, help='Optional yt-dlp cookies.txt file.')
    result.add_argument('--verbose', action='store_true')
    return result


def duration_tolerance_argument(value):
    try:
        return duration_tolerance(None, override=value)
    except (TypeError, ValueError) as exc:
        raise argparse.ArgumentTypeError(str(exc)) from exc


def main(argv=None):
    command = parser()
    args = command.parse_args(argv)
    root = args.directory.expanduser().resolve()
    if not root.is_dir():
        command.error('directory must be an existing directory')
    if args.repair_unknown and not args.repair:
        command.error('--repair-unknown requires --repair')
    if args.repair_unknown and not args.full_verification:
        command.error('--repair-unknown requires --full-verification; default mode repairs only duration mismatches')
    if args.max_candidates < 1 or (args.limit is not None and args.limit < 1):
        command.error('--max-candidates and --limit must be positive')
    if args.cookies and not args.cookies.is_file():
        command.error('--cookies must point to an existing file')
    has_spotify_credentials = all(os.environ.get(name) for name in ('SPOTIPY_CLIENT_ID', 'SPOTIPY_CLIENT_SECRET'))
    if args.spotify and not has_spotify_credentials:
        command.error('--spotify requires SPOTIPY_CLIENT_ID and SPOTIPY_CLIENT_SECRET')
    use_spotify = has_spotify_credentials if args.spotify is None else args.spotify
    state_dir = args.state_dir.expanduser().resolve() if args.state_dir else root / '_savify_audit'
    if state_dir == root or state_dir in root.parents:
        command.error('--state-dir must be a separate directory, not the music root or its parent')
    logging.basicConfig(level=logging.INFO, format='%(levelname)s: %(message)s')
    logger = logging.getLogger('savify.audit')
    logger.setLevel(logging.DEBUG)
    for handler in logging.getLogger().handlers:
        if isinstance(handler, logging.StreamHandler) and not isinstance(handler, logging.FileHandler):
            handler.setLevel(logging.DEBUG if args.verbose else logging.INFO)
    try:
        _run([ffmpeg_binary(args.ffmpeg_location), '-version'], timeout=15)
        identifier, client = None, None
        if args.repair or args.full_verification:
            fingerprinter = Fingerprinter(args.ffmpeg_location, args.fpcalc)
            fingerprinter.check()
            client = AcoustIDClient(os.environ.get('ACOUSTID_API_KEY'))
            identifier = AudioIdentifier(fingerprinter, client)
        logger.info('Audit mode: %s.', 'full audio verification' if args.full_verification else
                    'duration only; matching lengths skip audio verification and downloads')
        logger.info('Duration tolerance: +/- %gs (fixed, independent of track length).', args.duration_tolerance)
        if args.repair:
            logger.info('Repair enabled. Close Traktor/tag editors. Originals will be backed up before replacement.')
            logger.info('Unknown-file reference comparison: %s.',
                        'enabled when an independent duration is known' if args.full_verification and not args.no_reference_check
                        else 'disabled')
        logger.info('Spotify expected-duration lookup: %s.', 'enabled' if use_spotify else 'disabled; set SPOTIPY_CLIENT_ID/SECRET to enable')
        with audit_lock(state_dir):
            state = AuditState(state_dir, root)
            file_log = logging.FileHandler(state_dir / 'audit.log', encoding='utf-8')
            file_log.setLevel(logging.DEBUG)
            file_log.setFormatter(logging.Formatter('%(asctime)s %(levelname)s: %(message)s'))
            logger.addHandler(file_log)
            try:
                if args.rescan:
                    with state.db:
                        state.db.execute("DELETE FROM cache WHERE kind='acoustid'")
                if client:
                    client.cache = state
                catalog = None
                if use_spotify:
                    from spotipy import Spotify
                    from spotipy.cache_handler import MemoryCacheHandler
                    from spotipy.oauth2 import SpotifyClientCredentials
                    catalog = CatalogResolver(Spotify(auth_manager=SpotifyClientCredentials(cache_handler=MemoryCacheHandler()),
                                                      requests_timeout=20, retries=2, status_retries=2), state, logger)
                repairer = None
                if args.repair:
                    options = {'quiet': True, 'socket_timeout': 30, 'retries': 3, 'fragment_retries': 3,
                               'sleep_interval': 5, 'max_sleep_interval': 10, 'sleep_interval_requests': 1}
                    if args.cookies:
                        options['cookiefile'] = str(args.cookies)
                    config = MatchConfig()
                    store = MatchStore(state_dir, logger, args.match_overrides)
                    matcher = YouTubeMatcher(SearchProvider(options, logger, config), store, logger, config)
                    repairer = LibraryRepairer(root, state, matcher, identifier, logger, args.ffmpeg_location,
                                               options, args.max_candidates)
                auditor = LibraryAuditor(root, state, identifier, logger, repairer, catalog, args.ffmpeg_location,
                                         args.rescan, args.repair_unknown,
                                         reference_check=args.full_verification and not args.no_reference_check,
                                         full_verification=args.full_verification,
                                         duration_tolerance_seconds=args.duration_tolerance)
                counts = auditor.run(args.limit)
                logger.info('Results: %s', json.dumps(dict(counts), sort_keys=True))
                logger.info('Report: %s', state_dir / 'report.csv')
                return 1 if any(counts[status] for status in ('unknown', 'mismatch', 'corrupt', 'repair_failed', 'error')) else 0
            finally:
                try:
                    state.export_report()
                finally:
                    state.close()
                    logger.removeHandler(file_log)
                    file_log.close()
    except KeyboardInterrupt:
        logger.warning('Interrupted. Completed files are checkpointed; rerun the same command to resume.')
        return 130
    except (IdentityConfigurationError, AudioValidationError, OSError, ValueError, RuntimeError) as exc:
        logger.error('%s', exc)
        return 2


if __name__ == '__main__':
    raise SystemExit(main())
