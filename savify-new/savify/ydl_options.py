"""Translate CLI options with yt-dlp's own parser instead of guessing API keys."""


def normalize_options(options):
    result = dict(options)
    if 'sleep-requests' in result:
        result.setdefault('sleep_interval_requests', float(result.pop('sleep-requests')))
    if 'retry-sleep' in result:
        delay = float(result.pop('retry-sleep'))
        result.setdefault('retry_sleep_functions', {'http': lambda attempt: delay})
    if result.pop('force_ipv4', False):
        result.setdefault('source_address', '0.0.0.0')
    return result


def parse_extra_options(arguments):
    if not arguments:
        return {}
    import yt_dlp
    # Avoid loading global yt-dlp config into an embedding application implicitly.
    baseline = yt_dlp.parse_options(['--ignore-config']).ydl_opts
    parsed = yt_dlp.parse_options(['--ignore-config'] + list(arguments))
    if parsed.urls:
        raise ValueError('Unexpected extra URL/argument: {}'.format(parsed.urls[0]))

    def equivalent(a, b):
        if a is None or b is None:
            return a is b
        if callable(a) and callable(b):
            # yt-dlp constructs fresh range-filter callbacks even with no flags.
            return getattr(a, '__dict__', None) == getattr(b, '__dict__', None)
        try:
            return a == b
        except Exception:
            return False
    return {key: value for key, value in parsed.ydl_opts.items()
            if key not in baseline or not equivalent(value, baseline[key])}
