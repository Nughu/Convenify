"""Library audit or direct NML replacements: python -m savify.library_audit."""
import argparse
from collections import Counter
import json
import logging
import os
from pathlib import Path
import re
from types import SimpleNamespace

from .audio_identity import (AcoustIDClient, AudioIdentifier, Fingerprinter, IdentityError,
                             IdentityConfigurationError, IdentityResult)
from .library_catalog import CatalogResolver
from .library_repair import LibraryRepairer, RepairError, assert_unchanged, file_digest, file_token
from .library_state import AuditState, audit_lock, repair_run_log
from .library_tags import SUPPORTED_EXTENSIONS, TagSnapshot
from .media_validation import AudioValidationError, _run, ffmpeg_binary, probe_audio, validate_audio
from .traktor_nml import selected_paths
from .youtube_matcher import (MatchConfig, MatchStore, SearchProvider, YouTubeMatcher,
                              duration_range_error, duration_seconds, duration_tolerance)


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
                 full_verification=False, duration_tolerance_seconds=10.0,
                 min_duration=None, max_duration=None):
        self.root, self.state, self.identifier, self.logger = Path(root), state, identifier, logger
        self.repairer, self.catalog = repairer, catalog
        self.ffmpeg_location, self.rescan, self.repair_unknown = ffmpeg_location, rescan, repair_unknown
        self.reference_check = reference_check
        self.full_verification = full_verification
        self.duration_tolerance = duration_tolerance(None, override=duration_tolerance_seconds)
        bounds = (min_duration, max_duration)
        for value in bounds:
            if value is not None and duration_seconds(value) is None:
                raise ValueError('Duration bounds must be finite, positive seconds.')
        self.min_duration, self.max_duration = [duration_seconds(value) for value in bounds]
        if self.min_duration is not None and self.max_duration is not None and self.min_duration > self.max_duration:
            raise ValueError('--min-duration must not exceed --max-duration.')

    def process(self, path):
        path = Path(path)
        result = {'path': str(path), 'status': 'error', 'reason': '',
                  'min_duration': self.min_duration, 'max_duration': self.max_duration,
                  'selection_source': 'library_scan'}
        digest = ''
        try:
            token = file_token(path)
            # Read the entire file only for an existing content checkpoint or an
            # actual verification/repair. Most files need only tags and a probe.
            if self.full_verification or self.state.has_content_checkpoint(path):
                digest = file_digest(path)
                assert_unchanged(path, token, digest)
                previous = self.state.cached_file(path, digest) if not self.rescan else None
                if (previous and previous.get('duration_tolerance') == self.duration_tolerance
                        and previous.get('min_duration') == self.min_duration
                        and previous.get('max_duration') == self.max_duration):
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
            track.min_duration, track.max_duration = self.min_duration, self.max_duration
            duration_mismatch = False
            range_mismatch = False
            catalog_note = getattr(track, 'catalog_note', 'No independent catalog duration available.' if not expected else 'Independent catalog match.')
            result.update(expected_duration=expected, duration_tolerance=tolerance, catalog_note=catalog_note)
            try:
                # Probe first: decoding/fingerprinting an entire wrong album is
                # unnecessary once an independent track length disproves it.
                duration = probe_audio(path, self.ffmpeg_location)
                self.logger.info('  Existing file duration: %.1fs; expected: %s', duration,
                                 '{:.1f}s'.format(expected) if expected else 'unavailable — ' + catalog_note)
                range_reason = duration_range_error(duration, self.min_duration, self.max_duration)
                range_mismatch = bool(range_reason)
                duration_mismatch = bool(expected and abs(duration - expected) > tolerance)
                # Never infer the desired length from the potentially wrong file.
                if range_mismatch:
                    self.logger.info('  Existing file has %s; requesting a replacement.', range_reason)
                    identity = IdentityResult('outside_duration_range', range_reason, duration=duration)
                elif duration_mismatch:
                    self.logger.info('  Existing file has a clear duration mismatch; skipping its fingerprint.')
                    identity = IdentityResult('mismatch',
                        'Duration {:.1f}s differs from independently matched {:.1f}s by more than {:.1f}s.'.format(
                            duration, expected, tolerance), duration=duration)
                elif recovered and not self.rescan and (expected or self.full_verification
                                                       or self.min_duration is not None or self.max_duration is not None):
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
                          repair_trigger=('duration_range' if range_mismatch
                                          else 'duration_mismatch' if duration_mismatch else identity.status),
                          identified_as='; '.join('{} — {}'.format('/'.join(r.get('artists') or []), r.get('title') or '?')
                                                  for r in identity.recordings))
            if file_token(path) != token:
                raise RepairError('Original changed during the audit; no replacement was made. Close Traktor and retry.')
            verify_reference = (not range_mismatch and self.full_verification
                                and identity.status == 'unknown' and self.reference_check and self.repairer is not None
                                and expected is not None and (identity.unrecognized or identity.incomplete or identity.recordings))
            should_repair = range_mismatch or duration_mismatch or (self.full_verification and (
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
                                                    duration_mismatch=duration_mismatch, verify_existing=verify_reference,
                                                    force_redownload=range_mismatch)
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
        files = audio_files(self.root, self.state.directory, self.logger, scan_errors.append)
        for index, path in enumerate(files, 1):
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


class NMLRepairRunner:
    """Replace known-broken targets without auditing their existing audio.

    Metadata identifies the requested song. Content hashes protect the original
    against concurrent writes and verify backups; they never decide whether a
    target needs repair. Only newly downloaded audio goes through validation.
    """
    def __init__(self, paths, state, repairer, logger, catalog=None,
                 duration_tolerance_seconds=10.0, min_duration=None, max_duration=None):
        self.paths = tuple(paths)
        if not self.paths:
            raise ValueError('NML selection is empty; no files were selected.')
        if repairer is None:
            raise ValueError('NML mode requires a replacement downloader.')
        self.state, self.repairer, self.logger, self.catalog = state, repairer, logger, catalog
        self.duration_tolerance = duration_tolerance(None, override=duration_tolerance_seconds)
        self.min_duration, self.max_duration = min_duration, max_duration

    def process(self, path):
        path = Path(path)
        result = {'path': str(path), 'status': 'error', 'reason': '',
                  'selection_source': 'nml', 'repair_trigger': 'nml_selection',
                  'original_verification': 'skipped_nml', 'original_duration': None,
                  'min_duration': self.min_duration, 'max_duration': self.max_duration,
                  'duration_tolerance': self.duration_tolerance}
        digest = ''
        try:
            token = file_token(path)
            snapshot = TagSnapshot.read(path)
            track = snapshot.intended_track()
            result.update(artist=' / '.join(track.artists), title=track.name, track_id=str(track.id))
            if self.catalog:
                self.catalog.enrich(track)
            track.duration_tolerance = self.duration_tolerance
            track.min_duration, track.max_duration = self.min_duration, self.max_duration
            result.update(expected_duration=duration_seconds(track.duration),
                          catalog_note=getattr(track, 'catalog_note', ''))
            # No duration probe, audio decoding, fingerprint, reference comparison
            # or successful-checkpoint shortcut for the known-broken original.
            digest = file_digest(path)
            assert_unchanged(path, token, digest)
            try:
                repaired = self.repairer.repair(path, snapshot, track, token, digest,
                                                force_redownload=True, verify_existing=False)
                digest = repaired.pop('digest')
                result.update(repaired)
            except IdentityConfigurationError:
                raise
            except Exception as exc:
                self.logger.debug('NML replacement failure details for %s', path, exc_info=True)
                # A reporting failure after the atomic swap must not hide success.
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
            self.logger.debug('NML target failure details for %s', path, exc_info=True)
            result.update(status='error', reason=str(exc))
        self.state.record(path, digest, result)
        return result

    def run(self, limit=None):
        counts = Counter()
        for index, path in enumerate(self.paths, 1):
            if limit is not None and index > limit:
                break
            self.logger.info('[%s] Re-downloading NML target %s (existing audio verification skipped)', index, path)
            result = self.process(path)
            counts[result['status']] += 1
            self.logger.info('  %s: %s', result['status'], result['reason'])
            if index % 25 == 0:
                self.state.export_report()
        return counts


def parser():
    result = argparse.ArgumentParser(description=(
        'Recursively check music durations. Matching lengths skip audio verification and downloads. '
        'Alternatively, --nml FILE directly re-downloads every listed track without a library directory or existing-audio checks. '
        'Directory mode defaults to audit only; --repair installs replacements.'))
    result.add_argument('directory', type=Path, nargs='?', help='Music directory for audit mode; cannot be combined with --nml.')
    result.add_argument('--repair', action='store_true', help='Install validated replacements at the original paths.')
    result.add_argument('--min-duration', type=duration_bound_argument, metavar='SECONDS', help=(
        'Also replace files shorter than this duration with --repair, even without a catalog length. '
        'Seconds or MM:SS / HH:MM:SS. Lower bound also applies to replacements.'))
    result.add_argument('--max-duration', type=duration_bound_argument, metavar='SECONDS', help=(
        'Also replace files longer than this duration with --repair, even without a catalog length. '
        'Seconds or MM:SS / HH:MM:SS. Upper bound also applies to replacements.'))
    result.add_argument('--nml', type=Path, metavar='FILE', help=(
        'Direct replacement mode: re-download every explicitly listed track at its original path. '
        'Implies --repair; no directory, existing-audio verification or successful-checkpoint skips.'))
    result.add_argument('--duration-tolerance', type=duration_tolerance_argument, default=10.0, metavar='SECONDS', help=(
        'Maximum absolute difference from expected duration for existing files and replacements '
        '(default: 10 seconds, independent of track length). Accepts non-negative decimals; 0 requires an exact match.'))
    result.add_argument('--full-verification', action='store_true', help=(
        'Opt into fingerprint checks even when duration matches or is unavailable; '
        'with --repair, unknown files may trigger temporary reference downloads.'))
    result.add_argument('--repair-unknown', action='store_true', help=(
        'With --repair --full-verification, also attempt unrecognized/ambiguous files. '
        'Replacement still requires positive identity verification.'))
    result.add_argument('--state-dir', type=Path, help=(
        'Reports and backups (default: DIRECTORY/_savify_audit in audit mode; '
        '_savify_audit/nml-PLAYLIST beside the NML file in direct replacement mode).'))
    result.add_argument('--run-log', type=Path, metavar='FILE', help=(
        'Create a new JSONL log of replacements installed during this invocation for Traktor cleanup. '
        'Excludes cached results and older repairs; the file must not already exist.'))
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


def duration_bound_argument(value):
    value = str(value).strip()
    if ':' in value and not re.fullmatch(r'\d+:[0-5]\d(?::[0-5]\d)?(?:\.\d+)?', value):
        raise argparse.ArgumentTypeError('Use a valid duration: seconds, MM:SS or HH:MM:SS.')
    seconds = duration_seconds(value)
    if seconds is None:
        raise argparse.ArgumentTypeError('Duration must be finite and positive: seconds, MM:SS or HH:MM:SS.')
    return seconds


def main(argv=None):
    command = parser()
    args = command.parse_args(argv)
    if args.nml is not None:
        if args.directory is not None:
            command.error('--nml is a separate replacement mode; do not supply a library directory')
        if args.full_verification or args.repair_unknown:
            command.error('--nml replaces known-broken tracks directly; do not use --full-verification or --repair-unknown')
        args.nml = args.nml.expanduser().resolve()
        args.repair = True
        root = None
        state_source = args.nml
        default_state_dir = args.nml.parent / '_savify_audit' / ('nml-' + args.nml.stem)
    else:
        if args.directory is None:
            command.error('supply a music directory for audit mode, or --nml FILE for direct replacements')
        root = args.directory.expanduser().resolve()
        if not root.is_dir():
            command.error('directory must be an existing directory')
        state_source = root
        default_state_dir = root / '_savify_audit'
    if args.min_duration is not None and args.max_duration is not None and args.min_duration > args.max_duration:
        command.error('--min-duration must not exceed --max-duration')
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
    state_dir = args.state_dir.expanduser().resolve() if args.state_dir else default_state_dir
    if root is not None and (state_dir == root or state_dir in root.parents):
        command.error('--state-dir must be a separate directory, not the music root or its parent')
    nml_paths = None
    if args.nml:
        try:
            nml_paths = selected_paths(args.nml, state_dir, SUPPORTED_EXTENSIONS)
        except ValueError as exc:
            command.error(str(exc))
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
        logger.info('Mode: %s.', 'direct NML replacement; existing audio is not verified' if nml_paths is not None else
                    'full audio verification' if args.full_verification else
                    'duration only; matching lengths skip audio verification and downloads unless outside configured bounds')
        logger.info('Duration tolerance: +/- %gs (fixed, independent of track length).', args.duration_tolerance)
        if args.min_duration is not None or args.max_duration is not None:
            logger.info('Allowed duration range: %s to %s seconds (inclusive).',
                        args.min_duration if args.min_duration is not None else 'no minimum',
                        args.max_duration if args.max_duration is not None else 'no maximum')
        if nml_paths is not None:
            logger.info('NML selection: %s unique track(s) from %s; every listed track is requested for a fresh download.',
                        len(nml_paths), args.nml)
        if args.repair:
            logger.info('Repair enabled. Close Traktor/tag editors. Originals will be backed up before replacement.')
            if nml_paths is None:
                logger.info('Unknown-file reference comparison: %s.',
                            'enabled when an independent duration is known' if args.full_verification and not args.no_reference_check
                            else 'disabled')
        logger.info('Spotify expected-duration lookup: %s.', 'enabled' if use_spotify else 'disabled; set SPOTIPY_CLIENT_ID/SECRET to enable')
        with audit_lock(state_dir), repair_run_log(args.run_log) as run_log:
            state = AuditState(state_dir, state_source, run_log=run_log)
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
                if nml_paths is not None:
                    runner = NMLRepairRunner(nml_paths, state, repairer, logger, catalog,
                                              duration_tolerance_seconds=args.duration_tolerance,
                                              min_duration=args.min_duration, max_duration=args.max_duration)
                else:
                    runner = LibraryAuditor(root, state, identifier, logger, repairer, catalog, args.ffmpeg_location,
                                             args.rescan, args.repair_unknown,
                                             reference_check=args.full_verification and not args.no_reference_check,
                                             full_verification=args.full_verification,
                                             duration_tolerance_seconds=args.duration_tolerance,
                                             min_duration=args.min_duration, max_duration=args.max_duration)
                counts = runner.run(args.limit)
                logger.info('Results: %s', json.dumps(dict(counts), sort_keys=True))
                logger.info('Report: %s', state_dir / 'report.csv')
                if state.run_log_error is not None:
                    logger.error('This run\'s replacement log could not be completed; automatic Traktor cleanup must be skipped: %s',
                                 state.run_log_error)
                    return 2
                return 1 if any(counts[status] for status in (
                    'unknown', 'mismatch', 'corrupt', 'repair_failed', 'error', 'outside_duration_range')) else 0
            finally:
                try:
                    state.export_report()
                finally:
                    state.close()
                    logger.removeHandler(file_log)
                    file_log.close()
    except KeyboardInterrupt:
        if nml_paths is not None:
            logger.warning('Interrupted. Completed replacements are recorded; rerunning this NML re-downloads every listed track again.')
        else:
            logger.warning('Interrupted. Completed files are checkpointed; rerun the same command to resume.')
        return 130
    except (IdentityConfigurationError, AudioValidationError, OSError, ValueError, RuntimeError) as exc:
        logger.error('%s', exc)
        return 2


if __name__ == '__main__':
    raise SystemExit(main())
