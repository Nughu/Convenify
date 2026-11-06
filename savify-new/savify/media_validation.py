"""Validate completed audio and publish it atomically, using existing FFmpeg."""
import json
import os
from pathlib import Path
import re
import shutil
import subprocess

from .youtube_matcher import duration_seconds, duration_tolerance


class AudioValidationError(Exception):
    pass


def ffmpeg_binary(location='ffmpeg'):
    location = Path(location)
    if location.is_dir():
        return str(location / ('ffmpeg.exe' if os.name == 'nt' else 'ffmpeg'))
    return str(location)


def _run(args, timeout=180):
    try:
        result = subprocess.run(args, capture_output=True, text=True, encoding='utf-8',
                                errors='replace', timeout=timeout, check=False)
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise AudioValidationError(str(exc)) from exc
    if result.returncode:
        raise AudioValidationError(result.stderr[-2000:] or 'Audio validation failed.')
    return result


def probe_audio(path, ffmpeg_location='ffmpeg'):
    path = Path(path)
    if not path.is_file() or path.stat().st_size == 0:
        raise AudioValidationError('No completed audio file was produced.')
    binary = ffmpeg_binary(ffmpeg_location)
    ffmpeg_path = Path(shutil.which(binary) or binary)
    sibling = ffmpeg_path.with_name('ffprobe.exe' if ffmpeg_path.suffix.lower() == '.exe' else 'ffprobe')
    probe = str(sibling) if sibling.is_file() else shutil.which('ffprobe')
    if probe:
        result = _run([probe, '-v', 'error', '-select_streams', 'a:0', '-show_entries',
                       'stream=codec_type,duration:format=duration', '-of', 'json', str(path)])
        try:
            info = json.loads(result.stdout)
            streams = info.get('streams') or []
            if not streams:
                raise AudioValidationError('The file contains no audio stream.')
            duration = duration_seconds(streams[0].get('duration')) or duration_seconds(
                info.get('format', {}).get('duration'))
        except (ValueError, TypeError, AttributeError) as exc:
            raise AudioValidationError('Cannot read the audio duration.') from exc
    else:
        # Support the existing standalone bundled FFmpeg without requiring ffprobe.
        result = _run([binary, '-nostdin', '-hide_banner', '-i', str(path),
                       '-map', '0:a:0', '-t', '0.1', '-f', 'null', '-'])
        match = re.search(r'Duration:\s*(\d+:\d+:\d+(?:\.\d+)?)', result.stderr)
        duration = duration_seconds(match.group(1)) if match else None
    if not duration:
        raise AudioValidationError('Cannot verify a positive audio duration.')
    return duration


def validate_audio(path, track, match=None, ffmpeg_location='ffmpeg', decode=True):
    actual = probe_audio(path, ffmpeg_location)
    expected = duration_seconds(getattr(track, 'duration', None))
    override = getattr(track, 'duration_tolerance', None)
    limit = (duration_tolerance(expected, override=override) if override is not None else
             (match.duration_limit if match and match.duration_limit is not None else duration_tolerance(expected)))
    if expected and abs(actual - expected) > limit:
        raise AudioValidationError('Audio duration {:.1f}s does not match expected {:.1f}s (tolerance {:.1f}s).'.format(
            actual, expected, limit))
    # The actual media must also agree with the inspected source metadata.
    if match and match.duration and abs(actual - match.duration) > max(3, min(15, match.duration * 0.03)):
        raise AudioValidationError('Downloaded audio differs from the inspected video duration.')
    if decode:
        _run([ffmpeg_binary(ffmpeg_location), '-nostdin', '-v', 'error', '-xerror',
              '-i', str(path), '-map', '0:a:0', '-f', 'null', '-'], timeout=max(180, int(actual * 2)))
    return actual


def publish_audio(source, output, track, ffmpeg_location='ffmpeg', cover_art=None):
    """Tag a verified file. Use argument arrays so quotes in metadata stay literal."""
    from uuid import uuid4
    source, output = Path(source), Path(output)
    staging = output.with_name('.' + output.stem + '.' + uuid4().hex + output.suffix)
    metadata = {'title': track.name, 'album': track.album_name,
                'artist': '/'.join(track.artists), 'date': track.release_date,
                'track': '{}/{}'.format(track.track_number, track.album_track_count),
                'disc': track.disc_number}
    command = [ffmpeg_binary(ffmpeg_location), '-nostdin', '-v', 'error', '-y', '-i', str(source)]
    if cover_art:
        command += ['-i', str(cover_art)]
    command += ['-map', '0:a:0', '-c:a', 'copy']
    if cover_art:
        command += ['-map', '1:v:0', '-c:v', 'copy', '-disposition:v', 'attached_pic',
                    '-metadata:s:v', 'title=Album cover', '-metadata:s:v', 'comment=Cover (front)']
    for key, value in metadata.items():
        if value is not None and value != '':
            command += ['-metadata', '{}={}'.format(key, value)]
    if output.suffix.lower() == '.mp3':
        command += ['-id3v2_version', '3', '-write_id3v1', '1']
    command.append(str(staging))
    try:
        try:
            _run(command)
        except AudioValidationError:
            if not cover_art:
                raise
            # Cover artwork must not turn a correct recording into a failed track.
            return publish_audio(source, output, track, ffmpeg_location, cover_art=None)
        probe_audio(staging, ffmpeg_location)
        os.replace(staging, output)
    finally:
        staging.unlink(missing_ok=True)
