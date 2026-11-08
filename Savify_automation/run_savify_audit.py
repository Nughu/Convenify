"""Launch Convenify's sibling savify-new checkout with the bundled Python."""
from pathlib import Path
import runpy
import sys


def main():
    source_root = Path(__file__).resolve().parent.parent / 'savify-new'
    entrypoint = source_root / 'savify' / 'library_audit.py'
    if not entrypoint.is_file():
        print('ERROR: Savify source was not found at: {}'.format(entrypoint), file=sys.stderr)
        return 2
    # Explicitly prefer this checkout, including with embedded/isolated Python
    # configurations that ignore the working directory or PYTHONPATH.
    sys.path.insert(0, str(source_root))
    runpy.run_module('savify.library_audit', run_name='__main__', alter_sys=True)
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
