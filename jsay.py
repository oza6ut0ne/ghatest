#!/usr/bin/env python3
# /// script
# dependencies = [
#   "alkana==0.0.3",
#   "fasteners==0.18",
#   "pydantic==1.10.19",
#   "python-dotenv==1.0.1",
# ]
# ///

import argparse
import csv
import io
import logging
import os
import queue
import re
import subprocess
import sys
import threading
import traceback
import wave
from pathlib import Path

import alkana
import fasteners
from pydantic import BaseSettings

# JDIC = '/var/lib/mecab/dic/open-jtalk/naist-jdic/'
MAIN_DIR = Path(__file__).resolve().parent
APPIMAGE_FILE = os.environ.get('APPIMAGE')
APPIMAGE_DIR = Path(APPIMAGE_FILE).parent if APPIMAGE_FILE else None

URL_REPLACE_TEXT = 'URL'
URL_REGEX = re.compile(r'(https?|ftp)(:\/\/[-_.!~*\'()a-zA-Z0-9;\/?:\@&=+\$,%#]+)')
SPLIT_TEXT_REGEX = re.compile(r'(?<=[\n　。、！？!?」』)）】》])|(?<=\.\s)')


def _create_default_path(rel_path):
    if (Path('.').resolve() / rel_path).exists():
        return Path('.').resolve() / rel_path
    elif APPIMAGE_DIR and (APPIMAGE_DIR / rel_path).exists():
        return APPIMAGE_DIR / rel_path
    return MAIN_DIR / rel_path


DEFAULT_ALKANA_EXTRA_DATA = _create_default_path('alkana_extra_data.csv')
DEFAULT_USER_DIC = _create_default_path('user_dic.csv')


class Settings(BaseSettings):
    debug: bool = False
    htsvoice: str = str(MAIN_DIR / 'hts-voice/tohoku-f01-angry.htsvoice')
    open_jtalk_dic: str = str(MAIN_DIR / 'open_jtalk_dic_utf_8-1.11')
    alkana_extra_data: str = str(DEFAULT_ALKANA_EXTRA_DATA)
    user_dic: str = str(DEFAULT_USER_DIC)
    play_command: str = 'aplay'
    lock_file: str = '/tmp/lockfiles/jsay.lock'
    batch_num_lines: int = 10
    batch_max_bytes: int = 1024
    r: float = 1.0
    fm: float = 3.0
    english_word_min_length: int = 3
    english_to_kana: bool = True
    use_user_dic: bool = True
    shorten_urls: bool = False

    class Config:
        env_prefix = 'jsay_'
        env_file_encoding = 'utf-8'
        env_file = (
            [str(MAIN_DIR / '.env'), str(APPIMAGE_DIR / '.env'), '.env']
            if APPIMAGE_DIR
            else [str(MAIN_DIR / '.env'), '.env']
        )
        fields = {
            'debug': {'env': ['jsay_debug', 'debug']},
            'htsvoice': {'env': ['jsay_htsvoice', 'htsvoice']},
            'open_jtalk_dic': {'env': ['jsay_open_jtalk_dic', 'open_jtalk_dic']},
            'alkana_extra_data': {
                'env': ['jsay_alkana_extra_data', 'alkana_extra_data']
            },
            'user_dic': {'env': ['jsay_user_dic', 'user_dic']},
            'play_command': {'env': ['jsay_play_command', 'play_command']},
            'lock_file': {'env': ['jsay_lock_file', 'lock_file']},
        }


settings = Settings()
if Path(settings.alkana_extra_data).is_file():
    alkana.add_external_data(settings.alkana_extra_data)

if Path(settings.user_dic).is_file():
    with open(settings.user_dic) as f:
        reader = csv.reader(f)
        USER_DIC = {r[0].lower(): r[1] for r in reader}
        USER_DATA_REGEX = re.compile(
            '|'.join(re.escape(k) for k in USER_DIC.keys()), re.IGNORECASE
        )
        del reader
else:
    USER_DIC = {}
    USER_DATA_REGEX = None

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
    speed=settings.r,
    fm=settings.fm,
    english_word_min_length=settings.english_word_min_length,
    english_to_kana=settings.english_to_kana,
    use_user_dic=settings.use_user_dic,
    shorten_urls=settings.shorten_urls,
):
    audio_bytes = generate_audio_bytes(
        script,
        speed,
        fm,
        english_word_min_length,
        english_to_kana,
        use_user_dic,
        shorten_urls,
    )
    play_sound(audio_bytes)


def say(
    script,
    speed=settings.r,
    fm=settings.fm,
    english_word_min_length=settings.english_word_min_length,
    english_to_kana=settings.english_to_kana,
    use_user_dic=settings.use_user_dic,
    shorten_urls=settings.shorten_urls,
    is_threaded=False,
):
    if not isinstance(speed, (float, int)) or speed <= 0:
        raise ValueError('speed must be positive')

    if not isinstance(english_word_min_length, int) or english_word_min_length < 1:
        raise ValueError('english_word_min_length must be positive integer')

    if is_threaded:
        __ensure_worker()
        __queue.put(
            (
                script,
                speed,
                fm,
                english_word_min_length,
                english_to_kana,
                use_user_dic,
                shorten_urls,
            )
        )
    else:
        __say(
            script,
            speed,
            fm,
            english_word_min_length,
            english_to_kana,
            use_user_dic,
            shorten_urls,
        )


def generate_audio_bytes(
    script,
    speed=settings.r,
    fm=settings.fm,
    english_word_min_length=settings.english_word_min_length,
    english_to_kana=settings.english_to_kana,
    use_user_dic=settings.use_user_dic,
    shorten_urls=settings.shorten_urls,
):
    logger.debug(script)
    all_lines = [l for l in script.splitlines() if len(l.strip()) > 0]
    batch_lines = [
        '\n'.join(all_lines[i : i + settings.batch_num_lines])
        for i in range(0, len(all_lines), settings.batch_num_lines)
    ]

    results = []
    for batch_text in batch_lines:
        logger.debug(batch_text)
        text = remove_bad_characters(batch_text)
        if shorten_urls:
            text = replace_urls(text)
        if use_user_dic:
            text = apply_user_dic(text)
        if english_to_kana:
            text = convert_english_to_kana(text, english_word_min_length)

        texts = split_text_by_max_bytes(text)
        for text in texts:
            if len(text.strip()) == 0:
                continue

            cmd_echo = ['echo', text]
            cmd_jtalk = [
                'open_jtalk',
                '-x',
                settings.open_jtalk_dic,
                '-m',
                settings.htsvoice,
                '-ow',
                '/dev/stdout',
                '-r',
                '{:f}'.format(speed),
                '-fm',
                '{:f}'.format(fm),
            ]

            p_echo = subprocess.Popen(cmd_echo, shell=False, stdout=subprocess.PIPE)
            p_jtalk = subprocess.Popen(
                cmd_jtalk,
                shell=False,
                stdin=p_echo.stdout,
                stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL,
            )

            try:
                audio_bytes, _ = p_jtalk.communicate(timeout=60)
                if len(audio_bytes) > 0:
                    results.append(audio_bytes)
            except subprocess.TimeoutExpired as e:
                logger.error(e)

    return join_audio_bytes_list(results)


@fasteners.interprocess_locked(settings.lock_file)
def play_sound(audio_bytes):
    p_aplay = subprocess.Popen(
        settings.play_command,
        shell=False,
        stdin=subprocess.PIPE,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )

    try:
        p_aplay.communicate(input=audio_bytes, timeout=120)
    except subprocess.TimeoutExpired as e:
        p_aplay.terminate()
        logger.error(e)


def remove_bad_characters(text):
    text = text.replace('\n', '　')
    text = text.replace('\0', '')
    logger.debug(text)
    return text


def replace_urls(text):
    result = URL_REGEX.sub(URL_REPLACE_TEXT, text)
    logger.debug(result)
    return result


def apply_user_dic(text):
    if len(USER_DIC) == 0 or USER_DATA_REGEX is None:
        return text

    def replacer(match):
        matched_text = match.group(0)
        return USER_DIC[matched_text.lower()]

    result = USER_DATA_REGEX.sub(replacer, text)
    logger.debug(result)
    return result


def convert_english_to_kana(
    text, english_word_min_length=settings.english_word_min_length
):
    if not isinstance(english_word_min_length, int) or english_word_min_length < 1:
        raise ValueError('english_word_min_length must be positive integer')

    # https://mackro.blog.jp/archives/8479732.html
    output = ''
    while word := re.search(r'[a-zA-Z]{' f'{english_word_min_length}' r',} ?', text):
        output += text[: word.start()] + word_to_kana(
            word.group().rstrip(), english_word_min_length
        )
        text = text[word.end() :]

    result = output + text
    logger.debug(result)
    return result


def word_to_kana(word, english_word_min_length=settings.english_word_min_length):
    if not isinstance(english_word_min_length, int) or english_word_min_length < 1:
        raise ValueError('english_word_min_length must be positive integer')

    if kana := alkana.get_kana(word.lower()):
        return kana
    else:
        if re.fullmatch(
            # r'(?:[A-Z][a-z]{' f'{english_word_min_length - 1}' r',}){2,}', word
            # r'(?:[A-Za-z][a-z]{'
            # f'{english_word_min_length - 1}'
            # r',})(?:[A-Z][a-z]{'
            # f'{english_word_min_length - 1}'
            # r',})+',
            r'(?:[A-Za-z][a-z]+)(?:[A-Z][a-z]+)+',
            word,
        ):
            # m = re.match(r'[A-Z][a-z]{' f'{english_word_min_length - 1}' r',}', word)
            # m = re.match(r'[A-Za-z][a-z]{' f'{english_word_min_length - 1}' r',}', word)
            m = re.match(r'[A-Za-z][a-z]+', word)
            first = word_to_kana(m.group())
            second = word_to_kana(word[m.end() :])
            return first + second
        return word


def join_audio_bytes_list(audio_bytes_list):
    if len(audio_bytes_list) == 1:
        return audio_bytes_list[0]

    if sum(len(b) for b in audio_bytes_list) == 0:
        return b''

    result_bytes = io.BytesIO()
    is_properties_set = False
    with wave.open(result_bytes, 'wb') as fw:
        for audio_bytes in audio_bytes_list:
            if len(audio_bytes) == 0:
                continue
            with wave.open(io.BytesIO(audio_bytes), 'rb') as fr:
                if not is_properties_set:
                    fw.setsampwidth(fr.getsampwidth())
                    fw.setnchannels(fr.getnchannels())
                    fw.setframerate(fr.getframerate())
                    is_properties_set = True
                fw.writeframes(fr.readframes(fr.getnframes()))

    result_bytes.seek(0)
    return result_bytes.read()


def split_text_by_max_bytes(text, max_bytes_len=settings.batch_max_bytes):
    if max_bytes_len <= 0 or len(text.encode()) <= max_bytes_len:
        return [text]

    split_texts = SPLIT_TEXT_REGEX.split(text)
    texts = []
    buf_text = ''
    for split_text in split_texts:
        if len((buf_text + split_text).encode()) <= max_bytes_len:
            buf_text += split_text
        else:
            if len(buf_text) > 0:
                if len(buf_text) > max_bytes_len:
                    logger.warning('batch_max_bytes is too small')
                texts.append(buf_text)
            buf_text = split_text

    if len(buf_text) > 0:
        if len(buf_text) > max_bytes_len:
            logger.warning('batch_max_bytes is too small')
        texts.append(buf_text)

    logger.debug(texts)
    return texts


def _parse_args():
    parser = argparse.ArgumentParser(description='talk with open_jtalk')
    parser.add_argument('script', nargs='?', default=sys.stdin)
    parser.add_argument('-r', '--speed', type=float, default=settings.r)
    parser.add_argument('-f', '--fm', type=float, default=settings.fm)
    parser.add_argument(
        '-m',
        '--english-word-min-length',
        type=int,
        default=settings.english_word_min_length,
    )
    parser.add_argument('-e', '--english-to-kana', action='store_true')
    parser.add_argument('-d', '--use-user-dic', action='store_true')
    parser.add_argument('-u', '--shorten-urls', action='store_true')
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
            args.script = ''.join(args.script.readlines())

    if args.print_bytes:
        audio_bytes = generate_audio_bytes(
            args.script,
            args.speed,
            args.fm,
            args.english_word_min_length,
            args.english_to_kana,
            args.use_user_dic,
            args.shorten_urls,
        )
        sys.stdout.buffer.write(audio_bytes)
        sys.stdout.buffer.flush()
    else:
        say(
            args.script,
            args.speed,
            args.fm,
            args.english_word_min_length,
            args.english_to_kana,
            args.use_user_dic,
            args.shorten_urls,
        )


if __name__ == '__main__':
    main()
