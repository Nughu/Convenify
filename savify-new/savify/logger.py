import logging
import os
import sys
import traceback
from .utils import create_dir
from datetime import datetime
from pathlib import Path


class Logger:
    def __init__(self, log_location: str = '', log_level=logging.INFO) -> None:
        self.logger = logging.getLogger('savify')
        self.logger.setLevel(logging.DEBUG)

        gui = bool(os.environ.get('CONVENIFY_GUI'))
        formatter = logging.Formatter('[%(levelname)s]\t%(message)s')

        # The desktop filters visibility itself. Deliver every record so toggling
        # Show DEBUG also reveals messages generated before the toggle.
        if gui or log_level is not None:
            stream = getattr(sys, 'stderr', None)
            if stream is not None and hasattr(stream, 'reconfigure'):
                try:
                    stream.reconfigure(encoding='utf-8', errors='backslashreplace')
                except (AttributeError, OSError, ValueError):
                    pass
            stream_handler = logging.StreamHandler()
            stream_handler.setLevel(logging.DEBUG if gui else log_level)
            stream_handler.setFormatter(formatter)
            self.logger.addHandler(stream_handler)

        if not gui or os.environ.get('CONVENIFY_SAVE_RUN_FILES') == '1':
            time = (str(datetime.now()).replace(" ", "_")).replace(":", "_")
            if gui:
                log_location = os.environ.get('CONVENIFY_RUN_DIR', log_location)
            log_file = Path(f'{log_location}/logs/{time}_savify.log')
            create_dir(log_file.parent)
            file_handler = logging.FileHandler(log_file, encoding='utf-8')
            file_handler.setLevel(logging.DEBUG)
            file_handler.setFormatter(formatter)
            self.logger.addHandler(file_handler)

    def log_traceback(self) -> None:
        self.logger.error('An error occurred!')
        self.logger.error(traceback.format_exc())

    def debug(self, message):
        self.logger.debug(message)

    def warning(self, message):
        self.logger.warning(message)

    def error(self, message):
        self.logger.error(message)

    def info(self, message):
        self.logger.info(message)
