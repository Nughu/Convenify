"""Compare sampled audio directly when AcoustID has no usable catalog identity.

This confirms agreement with a separately matched source, not an independently
certified recording ID. Intermediate similarities stay unknown.
"""
from dataclasses import asdict, dataclass
import json
import struct
import subprocess

from .audio_identity import IdentityError
from .media_validation import ffmpeg_binary


@dataclass
class ReferenceComparison:
    status: str
    reason: str
    bit_error_rate: float = None
    compared_frames: int = 0
    offset_frames: int = 0

    def to_dict(self):
        return asdict(self)


def raw_fingerprint(path, fingerprinter):
    if fingerprinter.fpcalc:
        args = [fingerprinter.fpcalc, '-json', '-raw', '-length', '120', str(path)]
    else:
        args = [ffmpeg_binary(fingerprinter.ffmpeg_location), '-nostdin', '-v', 'error', '-i', str(path),
                '-map', '0:a:0', '-t', '120', '-ac', '2', '-ar', '11025', '-c:a', 'pcm_s16le',
                '-f', 'chromaprint', '-fp_format', 'raw', 'pipe:1']
    try:
        result = subprocess.run(args, capture_output=True, timeout=180, check=False)
        if result.returncode:
            raise IdentityError('Reference fingerprint failed: ' + result.stderr.decode('utf-8', 'replace')[-1000:])
        if fingerprinter.fpcalc:
            values = json.loads(result.stdout)['fingerprint']
            if not isinstance(values, list) or any(not isinstance(value, int) for value in values):
                raise ValueError('Expected raw fingerprint integer array')
            return [value & 0xffffffff for value in values]
        if len(result.stdout) % 4:
            raise ValueError('Invalid raw fingerprint size')
        # FFmpeg writes native 32-bit words. Both files are computed on this same
        # machine; a consistent byte permutation does not affect XOR bit counts.
        return list(struct.unpack('<{}I'.format(len(result.stdout) // 4), result.stdout))
    except (OSError, subprocess.TimeoutExpired, ValueError, KeyError) as exc:
        raise IdentityError('Cannot compare reference audio: {}'.format(exc)) from exc


def compare_fingerprints(left, right):
    shortest = min(len(left), len(right))
    if shortest < 120:
        return ReferenceComparison('unknown', 'Too little fingerprint data for reliable reference comparison.')
    if any(len(set(values)) < max(12, min(40, len(values) // 10)) for values in (left, right)):
        return ReferenceComparison('unknown', 'Audio fingerprint is too repetitive or silent for reference comparison.')
    overlap_required = max(120, int(shortest * .75))
    max_offset = min(240, int(shortest * .25))
    best = None
    for offset in range(-max_offset, max_offset + 1):
        start_left, start_right = max(offset, 0), max(-offset, 0)
        count = min(len(left) - start_left, len(right) - start_right)
        if count < overlap_required:
            continue
        errors = [(a ^ b).bit_count() for a, b in zip(left[start_left:start_left + count], right[start_right:start_right + count])]
        rate = sum(errors) / (32 * count)
        if best is None or rate < best[0]:
            best = (rate, count, offset, errors)
    if best is None:
        return ReferenceComparison('unknown', 'Not enough overlapping fingerprint data.')
    rate, count, offset, errors = best
    sections = [errors[count * index // 4:count * (index + 1) // 4] for index in range(4)]
    worst = max(sum(section) / (32 * len(section)) for section in sections)
    if rate <= .12 and worst <= .22:
        status, reason = 'same', 'Existing audio agrees with the matched reference fingerprint.'
    elif rate >= .30:
        status, reason = 'different', 'Existing audio differs clearly from the matched reference fingerprint.'
    else:
        status, reason = 'unknown', 'Reference comparison is inconclusive; existing file left untouched.'
    return ReferenceComparison(status, reason, round(rate, 6), count, offset)
