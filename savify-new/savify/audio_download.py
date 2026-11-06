"""Download one already-selected video to an isolated temporary directory."""
from pathlib import Path
from yt_dlp import YoutubeDL
from .types import Format
from .media_validation import AudioValidationError


def download_candidate(match, folder, download_format, quality, ffmpeg_location,
                       options, logger, ydl_factory=YoutubeDL):
    completed_paths = []

    def postprocessor_hook(data):
        if data.get('status') == 'finished':
            path = (data.get('info_dict') or {}).get('filepath')
            if path:
                completed_paths.append(Path(path))

    options = dict(options)
    extra_processors = [p for p in options.get('postprocessors', []) if p.get('key') != 'FFmpegExtractAudio']
    hooks = list(options.get('postprocessor_hooks') or []) + [postprocessor_hook]
    options.update({
        'format': options.get('format', 'bestaudio/best'),
        'outtmpl': str(folder / '%(id)s.%(ext)s'),
        'paths': {},
        'restrictfilenames': True,
        'ignoreerrors': False,
        'overwrites': True,
        'nooverwrites': False,
        'noplaylist': True,
        'extract_flat': False,
        'simulate': False,
        'skip_download': False,
        'logger': logger,
        'ffmpeg_location': ffmpeg_location,
        'postprocessors': [{'key': 'FFmpegExtractAudio', 'preferredcodec': download_format,
                            'preferredquality': quality}] + extra_processors,
        'postprocessor_hooks': hooks,
    })
    if ffmpeg_location == 'ffmpeg':
        options.pop('ffmpeg_location', None)
    with ydl_factory(options) as ydl:
        info = ydl.extract_info(match.url, download=True)
    if not info or info.get('_type') in ('playlist', 'multi_video'):
        raise AudioValidationError('No completed single-video download.')
    if match.source != 'direct' and info.get('id') != match.video_id:
        raise AudioValidationError('Downloader returned a different video ID.')
    for key in ('filepath', '_filename'):
        if info.get(key):
            completed_paths.append(Path(info[key]))
    extension = 'ogg' if download_format == Format.VORBIS else download_format
    accepted_extensions = {'.' + extension}
    if download_format == Format.AAC:
        # yt-dlp may emit AAC in an M4A container; publish_audio remuxes to
        # the requested .aac output without re-encoding it.
        accepted_extensions.add('.m4a')
    # This exact expected converted filename is also valid; never glob *.part/json.
    completed_paths.append(folder / '{}.{}'.format(info.get('id'), extension))
    for path in reversed(completed_paths):
        if path.suffix.lower() in accepted_extensions and path.is_file() and path.stat().st_size > 0:
            if path.resolve().parent == folder.resolve():
                return path
    raise AudioValidationError('yt-dlp did not produce the requested completed audio format.')
