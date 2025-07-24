#!/usr/bin/env python3
# /// script
# requires-python = ">=3.10, <3.11"
# dependencies = [
#   "ggwave==0.4.2",
#   "pydantic==1.10.19",
#   "python-dotenv==1.0.1",
#   "soundcard==0.4.4",
#   "soundfile==0.13.1",
# ]
# ///

import argparse
import base64
import logging
import os
import sys
from pathlib import Path

import ggwave
import soundcard as sc
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
    loop: bool = False
    newline: bool = False
    binary: bool = False

    class Config:
        env_prefix = 'glisten_'
        env_file_encoding = 'utf-8'
        env_file = _find_default_path('.env')
        fields = {
            'debug': {'env': ['glisten_debug', 'debug']},
        }


settings = Settings()
logger = logging.getLogger(__name__)


def _parse_args():
    parser = argparse.ArgumentParser()
    parser.add_argument('file', nargs='?')
    parser.add_argument('-l', '--loop', action='store_true')
    parser.add_argument('-n', '--newline', action='store_true')
    parser.add_argument('-b', '--binary', action='store_true')
    parser.add_argument('-m', '--mic-id', type=int, default=0)
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

    if not settings.debug:
        ggwave.disableLog()
    instance = ggwave.init()

    base64_encoded = ''
    try:
        if args.file:
            audio_bytes = sf.read(args.file, dtype='float32')[0].tobytes()
            for data in [
                audio_bytes[i : i + 4096] for i in range(0, len(audio_bytes), 4096)
            ]:
                res = ggwave.decode(instance, data)
                if res is not None:
                    decoded = res.decode()
                    if args.binary:
                        logger.debug(decoded)
                        base64_encoded += decoded
                    else:
                        print(decoded, end='', flush=True)
                        if args.newline:
                            print(flush=True)

        else:
            mics = sc.all_microphones()
            logger.debug('mics: %s', mics)
            mic = mics[args.mic_id]
            logger.debug('mic: %s', mic)
            with mic.recorder(samplerate=48000, channels=1, blocksize=1024) as recorder:
                while True:
                    nd_data = recorder.record(numframes=1024)
                    data = nd_data[:, 0].tobytes()
                    res = ggwave.decode(instance, data)
                    if res is not None:
                        decoded = res.decode()
                        if args.binary:
                            logger.debug(decoded)
                            base64_encoded += decoded
                        else:
                            print(decoded, end='', flush=True)
                            if args.newline:
                                print(flush=True)
                        if not args.loop and not args.file:
                            break
    except KeyboardInterrupt:
        pass

    if args.binary:
        sys.stdout.buffer.write(base64.b64decode(base64_encoded.encode()))

    ggwave.free(instance)


if __name__ == '__main__':
    main()
