#!/usr/bin/env python3
# /// script
# dependencies = [
#   "alkana==0.0.3",
#   "fasteners==0.18",
#   "pydantic==1.10.19",
#   "python-dotenv==1.0.1",
#   "voicevox-core",
# ]
#
# [tool.uv.sources]
# voicevox-core = [
#   { url = "https://github.com/VOICEVOX/voicevox_core/releases/download/0.16.0/voicevox_core-0.16.0-cp310-abi3-manylinux_2_34_x86_64.whl", marker = "platform_machine == 'x86_64'"},
#   { url = "https://github.com/VOICEVOX/voicevox_core/releases/download/0.16.0/voicevox_core-0.16.0-cp310-abi3-manylinux_2_34_aarch64.whl", marker = "platform_machine == 'aarch64'"},
# ]
# ///

import argparse
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
from voicevox_core import AccelerationMode
from voicevox_core.blocking import Onnxruntime, OpenJtalk, Synthesizer, VoiceModelFile

MAIN_DIR = Path(__file__).resolve().parent
APPIMAGE_FILE = os.environ.get('APPIMAGE')
APPIMAGE_DIR = Path(APPIMAGE_FILE).parent if APPIMAGE_FILE else None

DEFAULT_ALKANA_EXTRA_DATA_NAME = 'alkana_extra_data.csv'
if (Path('.').resolve() / DEFAULT_ALKANA_EXTRA_DATA_NAME).exists():
    DEFAULT_ALKANA_EXTRA_DATA = Path('.').resolve() / DEFAULT_ALKANA_EXTRA_DATA_NAME
elif APPIMAGE_DIR and (APPIMAGE_DIR / DEFAULT_ALKANA_EXTRA_DATA_NAME).exists():
    DEFAULT_ALKANA_EXTRA_DATA = APPIMAGE_DIR / DEFAULT_ALKANA_EXTRA_DATA_NAME
else:
    DEFAULT_ALKANA_EXTRA_DATA = MAIN_DIR / DEFAULT_ALKANA_EXTRA_DATA_NAME

URL_REPLACE_TEXT = 'URL'
URL_REGEX = re.compile(r'(https?|ftp)(:\/\/[-_.!~*\'()a-zA-Z0-9;\/?:\@&=+\$,%#]+)')
SPLIT_TEXT_REGEX = re.compile(r'(?<=[\n　。、！？!?」』)）】》])|(?<=\.\s)')

# https://github.com/VOICEVOX/voicevox_vvm/blob/0.1.0/README.md
VVM_TO_STYLE_IDS_MAP = {
    '0.vvm': [0, 1, 2, 3, 4, 5, 6, 7, 8, 10],
    '1.vvm': [14],
    '2.vvm': [15, 16, 17, 18],
    '3.vvm': [9, 61, 62, 63, 64, 65],
    '4.vvm': [11, 21],
    '5.vvm': [19, 22, 36, 37, 38],
    '6.vvm': [29, 30, 31],
    '7.vvm': [27, 28],
    '8.vvm': [23, 24, 25, 26],
    '9.vvm': [12, 32, 33, 34, 35],
    '10.vvm': [39, 40, 41, 42],
    '11.vvm': [43, 44, 45, 47, 48, 49, 50],
    '12.vvm': [51, 52, 53],
    '13.vvm': [54, 55, 56, 57, 58, 59, 60],
    '14.vvm': [67, 68, 69, 70, 71, 72, 73, 74],
    '15.vvm': [13, 20, 46, 66, 75, 76, 77, 78, 79, 80, 81, 82, 83, 84, 85, 86],
    '16.vvm': [87, 88],
    '17.vvm': [89],
    '18.vvm': [90, 91, 92, 93, 94, 95, 96, 97, 98],
}
STYLE_ID_TO_VVM_MAP = {
    _id: vvm for vvm, ids in VVM_TO_STYLE_IDS_MAP.items() for _id in ids
}


class Settings(BaseSettings):
    onnxruntime: str = str(MAIN_DIR / 'libvoicevox_onnxruntime.so')
    voicevox_models: str = str(MAIN_DIR / 'vvms')
    open_jtalk_dic: str = str(MAIN_DIR / 'open_jtalk_dic_utf_8-1.11')
    alkana_extra_data: str = str(DEFAULT_ALKANA_EXTRA_DATA)
    play_command: str = 'aplay'
    lock_file: str = '/tmp/lockfiles/vsay.lock'
    batch_num_lines: int = 10
    batch_max_bytes: int = 1024
    r: float = 1.0
    fm: float = 0.0
    english_word_min_length: int = 3
    english_to_kana: bool = True
    shorten_urls: bool = False
    cpu_num_threads: int = 0
    acceleration_mode: AccelerationMode = 'AUTO'
    speaker_id: int = 3

    class Config:
        env_prefix = 'vsay_'
        env_file_encoding = 'utf-8'
        env_file = (
            [str(MAIN_DIR / '.env'), str(APPIMAGE_DIR / '.env'), '.env']
            if APPIMAGE_DIR
            else [str(MAIN_DIR / '.env'), '.env']
        )
        fields = {
            'onnxruntime': {'env': ['vsay_onnxruntime', 'onnxruntime']},
            'voicevox_models': {'env': ['vsay_voicevox_models', 'voicevox_models']},
            'open_jtalk_dic': {'env': ['vsay_open_jtalk_dic', 'open_jtalk_dic']},
            'alkana_extra_data': {
                'env': ['vsay_alkana_extra_data', 'alkana_extra_data']
            },
            'play_command': {'env': ['vsay_play_command', 'play_command']},
            'lock_file': {'env': ['vsay_lock_file', 'lock_file']},
        }


settings = Settings()
if Path(settings.alkana_extra_data).exists():
    alkana.add_external_data(settings.alkana_extra_data)

for name in ['ort']:
    library_logger = logging.getLogger(name)
    library_logger.setLevel(logging.ERROR)


__queue: queue.Queue | None = None
__thread: threading.Thread | None = None

__core: Synthesizer | None = None
__onnxruntime: Onnxruntime | None = None
__open_jtalk: OpenJtalk | None = None


def __ensure_core(speaker_id=None):
    global __core
    global __onnxruntime
    global __open_jtalk

    if __core is None:
        if __onnxruntime is None:
            __onnxruntime = Onnxruntime.load_once(filename=settings.onnxruntime)
        if __open_jtalk is None:
            __open_jtalk = OpenJtalk(settings.open_jtalk_dic)
        __core = Synthesizer(
            onnxruntime=__onnxruntime,
            open_jtalk=__open_jtalk,
            acceleration_mode=settings.acceleration_mode,
            cpu_num_threads=settings.cpu_num_threads,
        )

    if speaker_id is not None:
        vvm = STYLE_ID_TO_VVM_MAP.get(speaker_id)
        if vvm is None:
            raise ValueError(f'Invalid speaker_id: {speaker_id}')
        with VoiceModelFile.open(Path(settings.voicevox_models) / vvm) as model:
            if not __core.is_loaded_voice_model(model.id):
                __core.load_voice_model(model)


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
            __say(*q.get())
        except Exception:
            print(traceback.format_exc())


def __say(
    script,
    speed=settings.r,
    fm=settings.fm,
    english_word_min_length=settings.english_word_min_length,
    english_to_kana=settings.english_to_kana,
    shorten_urls=settings.shorten_urls,
    speaker_id=settings.speaker_id,
):
    audio_bytes = generate_audio_bytes(
        script,
        speed,
        fm,
        english_word_min_length,
        english_to_kana,
        shorten_urls,
        speaker_id,
    )
    play_sound(audio_bytes)


def say(
    script,
    speed=settings.r,
    fm=settings.fm,
    english_word_min_length=settings.english_word_min_length,
    english_to_kana=settings.english_to_kana,
    shorten_urls=settings.shorten_urls,
    speaker_id=settings.speaker_id,
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
                shorten_urls,
                speaker_id,
            )
        )
    else:
        __say(
            script,
            speed,
            fm,
            english_word_min_length,
            english_to_kana,
            shorten_urls,
            speaker_id,
        )


def generate_audio_bytes(
    script,
    speed=settings.r,
    fm=settings.fm,
    english_word_min_length=settings.english_word_min_length,
    english_to_kana=settings.english_to_kana,
    shorten_urls=settings.shorten_urls,
    speaker_id=settings.speaker_id,
):
    global __core
    all_lines = [l for l in script.splitlines() if len(l.strip()) > 0]
    batch_lines = [
        '\n'.join(all_lines[i : i + settings.batch_num_lines])
        for i in range(0, len(all_lines), settings.batch_num_lines)
    ]

    results = []
    for batch_text in batch_lines:
        text = remove_bad_characters(batch_text)
        if shorten_urls:
            text = replace_urls(text)
        if english_to_kana:
            text = convert_english_to_kana(text, english_word_min_length)

        texts = split_text_by_max_bytes(text)
        for text in texts:
            if len(text.strip()) == 0:
                continue

            __ensure_core(speaker_id)
            audio_query = __core.create_audio_query(text, speaker_id)
            audio_query.speed_scale = speed
            audio_query.pitch_scale = fm
            audio_query.volume_scale = 2.0
            audio_bytes = __core.synthesis(audio_query, speaker_id)
            if len(audio_bytes) > 0:
                results.append(audio_bytes)

            del __core
            __core = None

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
        print(e)


def remove_bad_characters(text):
    text = text.replace('\n', '　')
    text = text.replace('\0', '')
    return text


def replace_urls(text):
    return URL_REGEX.sub(URL_REPLACE_TEXT, text)


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

    return output + text


def word_to_kana(word, english_word_min_length=settings.english_word_min_length):
    if not isinstance(english_word_min_length, int) or english_word_min_length < 1:
        raise ValueError('english_word_min_length must be positive integer')

    if kana := alkana.get_kana(word.lower()):
        return kana
    else:
        if re.fullmatch(
            # r'(?:[A-Z][a-z]{' f'{english_word_min_length - 1}' r',}){2,}',
            r'(?:[A-Za-z][a-z]+)(?:[A-Z][a-z]+)+',
            word,
        ):
            # m = re.match(r'[A-Z][a-z]{' f'{english_word_min_length - 1}' r',}', word)
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
                    print('WARN: batch_max_bytes is too small', file=sys.stderr)
                texts.append(buf_text)
            buf_text = split_text

    if len(buf_text) > 0:
        if len(buf_text) > max_bytes_len:
            print('WARN: batch_max_bytes is too small', file=sys.stderr)
        texts.append(buf_text)

    return texts


def _parse_args():
    parser = argparse.ArgumentParser(description='talk with voicevox')
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
    parser.add_argument('-u', '--shorten-urls', action='store_true')
    parser.add_argument('-i', '--speaker-id', type=int, default=settings.speaker_id)
    parser.add_argument('-p', '--print-bytes', action='store_true')
    return parser.parse_args()


def main():
    args = _parse_args()
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
            args.shorten_urls,
            args.speaker_id,
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
            args.shorten_urls,
            args.speaker_id,
        )


if __name__ == '__main__':
    main()
