#!/usr/bin/env python3
# /// script
# requires-python = ">=3.10, <3.11"
# dependencies = [
#   "fasteners==0.18",
#   "ggwave==0.4.2",
#   "numpy==2.2.6",
#   "pydantic==1.10.19",
#   "python-dotenv==1.0.1",
#   "soundfile==0.13.1",
# ]
# ///

import argparse
import base64
import io
import logging
import os
import queue
import subprocess
import sys
import tempfile
import threading
import traceback
from pathlib import Path

import fasteners
import ggwave
import numpy as np
import soundfile as sf
from pydantic import BaseSettings

MAIN_DIR = Path(__file__).resolve().parent
APPIMAGE_FILE = os.environ.get('APPIMAGE')
APPIMAGE_DIR = Path(APPIMAGE_FILE).parent if APPIMAGE_FILE else None


def _find_config_dir_path():
    xdg_config_home = os.environ.get('XDG_CONFIG_HOME')
    if xdg_config_home and (Path(xdg_config_home) / 'ggwave-server').exists():
        return Path(xdg_config_home) / 'ggwave-server'
    return Path.home() / '.config' / 'ggwave-server'


CONFIG_DIR = _find_config_dir_path()


def _find_default_path(rel_path):
    if (Path('.').resolve() / rel_path).exists():
        return Path('.').resolve() / rel_path
    elif APPIMAGE_DIR and (APPIMAGE_DIR / rel_path).exists():
        return APPIMAGE_DIR / rel_path
    elif (MAIN_DIR / rel_path).exists():
        return MAIN_DIR / rel_path
    return CONFIG_DIR / rel_path


class Settings(BaseSettings):
    debug: bool = False
    play_command: str = 'paplay'
    lock_file: str = str(Path(tempfile.gettempdir()) / 'lockfiles/gsay.lock')
    play_timeout: int|None = None
    batch_max_bytes: int = 140
    protocol_id: int = 2
    volume: int = 50
    binary: bool = False

    class Config:
        env_prefix = 'gsay_'
        env_file_encoding = 'utf-8'
        env_file = _find_default_path('.env')
        fields = {
            'debug': {'env': ['gsay_debug', 'debug']},
            'play_command': {'env': ['gsay_play_command', 'play_command']},
            'lock_file': {'env': ['gsay_lock_file', 'lock_file']},
            'protocol_id': {'env': ['gsay_protocol_id', 'protocol_id']},
            'volume': {'env': ['gsay_volume', 'volume']},
        }


settings = Settings()
logger = logging.getLogger(__name__)

__queue: queue.Queue | None = None
__thread: threading.Thread | None = None


def __ensure_worker():
    global __queue
    global __thread
    if __queue is None:
        __queue = queue.Queue()
    if __thread is None:
        __thread = threading.Thread(target=__worker, args=(__queue,), daemon=True)
    if not __thread.is_alive():
        try:
            __thread.start()
        except RuntimeError:
            __thread = threading.Thread(target=__worker, args=(__queue,), daemon=True)
            __thread.start()


def __worker(q):
    while True:
        try:
            item = q.get()
            logger.debug(item)
            __say(*item)
        except Exception:
            logger.error(traceback.format_exc())


def __say(
    script,
    protocol_id=settings.protocol_id,
    volume=settings.volume,
    binary=settings.binary,
):
    audio_bytes = generate_audio_bytes(
        script,
        protocol_id,
        volume,
        binary,
    )
    play_sound(audio_bytes)


def say(
    script,
    protocol_id=settings.protocol_id,
    volume=settings.volume,
    binary=settings.binary,
    is_threaded=False,
):
    if not isinstance(volume, int) or volume < 0:
        raise ValueError('volume must not be positive')

    if is_threaded:
        __ensure_worker()
        __queue.put(
            (
                script,
                protocol_id,
                volume,
                binary,
            )
        )
    else:
        __say(
            script,
            protocol_id,
            volume,
            binary,
        )


def generate_audio_bytes(
    script,
    protocol_id=settings.protocol_id,
    volume=settings.volume,
    binary=settings.binary,
):
    logger.debug(script)

    max_batch_chars = settings.batch_max_bytes // 4
    if binary:
        # script = base64.b64encode(script.encode()).decode()
        max_batch_chars = settings.batch_max_bytes

    texts = [
        script[i : i + max_batch_chars] for i in range(0, len(script), max_batch_chars)
    ]

    raw_bytes_list = []
    for text in texts:
        logger.debug(text)
        if len(text.strip()) == 0:
            continue
        raw_bytes = ggwave.encode(text, protocolId=protocol_id, volume=volume)
        if len(raw_bytes) > 0:
            raw_bytes_list.append(raw_bytes)

    joined_raw_bytes = b''.join(raw_bytes_list)
    nd_bytes = np.frombuffer(joined_raw_bytes, dtype=np.float32)
    result_bytes = io.BytesIO()
    sf.write(result_bytes, nd_bytes, 48000, subtype='FLOAT', format='WAV')
    result_bytes.seek(0)
    return result_bytes.read()


@fasteners.interprocess_locked(settings.lock_file)
def play_sound(audio_bytes):
    p_play = subprocess.Popen(
        settings.play_command,
        shell=False,
        stdin=subprocess.PIPE,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )

    try:
        p_play.communicate(input=audio_bytes, timeout=settings.play_timeout)
    except subprocess.TimeoutExpired as e:
        p_play.terminate()
        logger.error(e)


def _parse_args():
    parser = argparse.ArgumentParser()
    parser.add_argument('script', nargs='?', default=sys.stdin)
    parser.add_argument('-i', '--protocol-id', type=int, default=settings.protocol_id)
    parser.add_argument('-v', '--volume', type=int, default=settings.volume)
    parser.add_argument('-b', '--binary', action='store_true')
    parser.add_argument('-p', '--print-bytes', action='store_true')
    return parser.parse_args()


def main():
    args = _parse_args()
    log_format = '%(asctime)s %(levelname)s:%(name)s: %(message)s'
    log_format_debug = (
        '%(asctime)s %(levelname)s:%(name)s:%(funcName)s:%(lineno)d: %(message)s'
    )
    if settings.debug:
        logging.basicConfig(level=logging.DEBUG, format=log_format_debug)
    else:
        logging.basicConfig(level=logging.INFO, format=log_format)

    logger.debug(settings.dict())
    logger.debug(args)

    if args.script is sys.stdin:
        if args.script.isatty():
            return
        else:
            if args.binary:
                args.script = base64.b64encode(args.script.buffer.read()).decode()
            else:
                args.script = ''.join(args.script.readlines())
    else:
        if args.binary:
            args.script = base64.b64encode(args.script.encode()).decode()

    if args.print_bytes:
        audio_bytes = generate_audio_bytes(
            args.script,
            args.protocol_id,
            args.volume,
            args.binary,
        )
        sys.stdout.buffer.write(audio_bytes)
        sys.stdout.buffer.flush()
    else:
        say(
            args.script,
            args.protocol_id,
            args.volume,
            args.binary,
        )


if __name__ == '__main__':
    main()
