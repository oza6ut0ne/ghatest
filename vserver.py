#!/usr/bin/env python3
# /// script
# requires-python = ">=3.13"
# dependencies = [
#   "alkana==0.0.3",
#   "fasteners==0.18",
#   "fastapi==0.101.0",
#   "paho-mqtt==2.1.0",
#   "pydantic==1.10.19",
#   "python-dotenv==1.0.1",
#   "uvicorn==0.23.2",
#   "voicevox-core",
# ]
#
# [tool.uv.sources]
# voicevox-core = [
#   { url = "https://github.com/VOICEVOX/voicevox_core/releases/download/0.16.0/voicevox_core-0.16.0-cp310-abi3-manylinux_2_34_x86_64.whl", marker = "platform_machine == 'x86_64'"},
#   { url = "https://github.com/VOICEVOX/voicevox_core/releases/download/0.16.0/voicevox_core-0.16.0-cp310-abi3-manylinux_2_34_aarch64.whl", marker = "platform_machine != 'x86_64'"},
# ]
# ///

import argparse
import json
import logging
import os
import socket
from pathlib import Path
from typing import Any

import paho.mqtt.client as mqtt
import uvicorn
from fastapi import FastAPI
from fastapi.responses import Response
from pydantic import BaseModel, BaseSettings

import vsay

MAIN_DIR = Path(__file__).resolve().parent
APPIMAGE_FILE = os.environ.get('APPIMAGE')
APPIMAGE_DIR = Path(APPIMAGE_FILE).parent if APPIMAGE_FILE else None


class Settings(BaseSettings):
    debug: bool = False
    enable_mqtt: bool = False
    mqtt_host: str = 'localhost'
    mqtt_port: int = 1883
    mqtt_user: str | None = None
    mqtt_password: str | None = None
    mqtt_node_id: str = f'{socket.gethostname().casefold()}'
    mqtt_topics: list[str] = [f'tts/vsay/{mqtt_node_id}/command/#']
    mqtt_availability_topic: str = f'tts/vsay/{mqtt_node_id}/availability'
    mqtt_topic_all: str = 'tts/vsay/all'
    mqtt_qos: int = 0
    serve_http: bool = False
    listen_port: int = 5010
    r: float = 1.0
    fm: float = 0.0
    english_word_min_length: int = 3
    english_to_kana: bool = True
    use_user_dic: bool = True
    shorten_urls: bool = False
    speaker_id: int = 1
    # speaker_id: int = 3
    # speaker_id: int = 7
    # speaker_id: int = 69

    class Config:
        env_prefix = 'vserver_'
        env_file_encoding = 'utf-8'
        env_file = (
            [str(MAIN_DIR / '.env'), str(APPIMAGE_DIR / '.env'), '.env']
            if APPIMAGE_DIR
            else [str(MAIN_DIR / '.env'), '.env']
        )
        fields = {
            'debug': {'env': ['vserver_debug', 'debug']},
        }

        @classmethod
        def parse_env_var(cls, field_name: str, raw_val: str) -> Any:
            if field_name == 'mqtt_topics':
                return raw_val.split(',')
            return cls.json_loads(raw_val)


settings = Settings()
logger_mqtt = logging.getLogger('mqtt')
logger_http = logging.getLogger('http')
logger_uvicorn = logging.getLogger('uvicorn')
for name in [
    'asyncio',
    'tracing.span',
    'voicevox_core.synthesizer',
    'voicevox_core_python_api',
]:
    library_logger = logging.getLogger(name)
    library_logger.setLevel(logging.WARNING)


def on_connect(client, userdata, flags, reason_code, properties):
    logger_mqtt.info('connected')
    client.publish(settings.mqtt_availability_topic, 'online', retain=True, qos=2)
    for topic in userdata:
        client.subscribe(topic, qos=settings.mqtt_qos)


def on_message(client, userdata, message):
    payload = message.payload.decode(errors='ignore')
    logger_mqtt.debug(payload)
    try:
        data = json.loads(payload)
        text = data.get('text')
        if text is None:
            return
        r = data.get('r', settings.r)
        fm = data.get('fm', settings.fm)
        english_word_min_length = data.get(
            'english_word_min_length', settings.english_word_min_length
        )
        english_to_kana = data.get('english_to_kana', settings.english_to_kana)
        use_user_dic = data.get('use_user_dic', settings.use_user_dic)
        shorten_urls = data.get('shorten_urls', settings.shorten_urls)
        speaker_id = data.get('speaker_id', settings.speaker_id)
    except json.JSONDecodeError:
        text = payload
        r = settings.r
        fm = settings.fm
        english_word_min_length = settings.english_word_min_length
        english_to_kana = settings.english_to_kana
        use_user_dic = settings.use_user_dic
        shorten_urls = settings.shorten_urls
        speaker_id = settings.speaker_id

    logger_mqtt.info(text.replace('\n', ' '))
    try:
        vsay.say(
            text,
            r,
            fm,
            english_word_min_length,
            english_to_kana,
            use_user_dic,
            shorten_urls,
            speaker_id,
            is_threaded=True,
        )
    except Exception as e:
        logger_mqtt.error(e)


def on_disconnect(client, userdata, flags, reason_code, properties):
    logger_mqtt.info('disconnected')


def on_connect_fail(client, userdata):
    logger_mqtt.error('connection failed')


class SayParam(BaseModel):
    text: str
    r: float = settings.r
    fm: float = settings.fm
    english_word_min_length: int = settings.english_word_min_length
    english_to_kana: bool = settings.english_to_kana
    use_user_dic: bool = settings.use_user_dic
    shorten_urls: bool = settings.shorten_urls
    speaker_id: int = settings.speaker_id


app = FastAPI()


@app.get('/say')
async def get_say(
    text: str,
    r: float = settings.r,
    fm: float = settings.fm,
    english_word_min_length: int = settings.english_word_min_length,
    english_to_kana: bool = settings.english_to_kana,
    use_user_dic: bool = settings.use_user_dic,
    shorten_urls: bool = settings.shorten_urls,
    speaker_id: int = settings.speaker_id,
):
    logger_http.debug(locals())
    logger_uvicorn.info(text.replace('\n', ' '))
    try:
        vsay.say(
            text,
            r,
            fm,
            english_word_min_length,
            english_to_kana,
            use_user_dic,
            shorten_urls,
            speaker_id,
            is_threaded=True,
        )
    except Exception as e:
        logger_uvicorn.error(e)

    return Response('OK')


@app.post('/say')
async def post_say(param: SayParam):
    logger_http.debug(locals())
    logger_uvicorn.info(param.text)
    try:
        vsay.say(
            param.text,
            param.r,
            param.fm,
            param.english_word_min_length,
            param.english_to_kana,
            param.use_user_dic,
            param.shorten_urls,
            param.speaker_id,
            is_threaded=True,
        )
    except Exception as e:
        logger_uvicorn.error(e)

    return Response('OK')


@app.get('/audio')
async def get_audio(
    text: str,
    r: float = settings.r,
    fm: float = settings.fm,
    english_word_min_length: int = settings.english_word_min_length,
    english_to_kana: bool = settings.english_to_kana,
    use_user_dic: bool = settings.use_user_dic,
    shorten_urls: bool = settings.shorten_urls,
    speaker_id: int = settings.speaker_id,
):
    logger_http.debug(locals())
    logger_uvicorn.info(text.replace('\n', ' '))
    try:
        audio_bytes = vsay.generate_audio_bytes(
            text,
            r,
            fm,
            english_word_min_length,
            english_to_kana,
            use_user_dic,
            shorten_urls,
            speaker_id,
        )
    except Exception as e:
        logger_uvicorn.error(e)
        audio_bytes = b''

    return Response(content=audio_bytes, media_type='audio/wav')


@app.post('/audio')
async def post_audio(param: SayParam):
    logger_http.debug(locals())
    logger_uvicorn.info(param.text)
    try:
        audio_bytes = vsay.generate_audio_bytes(
            param.text,
            param.r,
            param.fm,
            param.english_word_min_length,
            param.english_to_kana,
            param.use_user_dic,
            param.shorten_urls,
            param.speaker_id,
        )
    except Exception as e:
        logger_uvicorn.error(e)
        audio_bytes = b''

    return Response(content=audio_bytes, media_type='audio/wav')


def _parse_args():
    parser = argparse.ArgumentParser()
    parser.add_argument(
        '-s', '--serve-http', action='store_true', default=settings.serve_http
    )
    parser.add_argument('-l', '--listen-port', type=int, default=settings.listen_port)
    parser.add_argument(
        '-m', '--enable-mqtt', action='store_true', default=settings.enable_mqtt
    )
    parser.add_argument('-b', '--mqtt-host', default=settings.mqtt_host)
    parser.add_argument('-p', '--mqtt-port', type=int, default=settings.mqtt_port)
    parser.add_argument('-u', '--mqtt-user', default=settings.mqtt_user)
    parser.add_argument('-c', '--mqtt-password', default=settings.mqtt_password)
    parser.add_argument('-t', '--mqtt-topics', nargs='+', default=settings.mqtt_topics)
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

    logger = logging.getLogger(__name__)
    logger.debug(settings.dict())
    logger.debug(vsay.settings.dict())
    logger.debug(args)

    if not args.enable_mqtt and not args.serve_http:
        raise ValueError('At least one of --enable-mqtt or --serve-http is required.')

    if args.enable_mqtt:
        topics = args.mqtt_topics
        if settings.mqtt_topic_all:
            topics.append(settings.mqtt_topic_all)

        mqttc = mqtt.Client(
            protocol=mqtt.MQTTv5,
            callback_api_version=mqtt.CallbackAPIVersion.VERSION2,
            userdata=topics,
        )
        mqttc.username_pw_set(args.mqtt_user, args.mqtt_password)
        mqttc.on_message = on_message
        mqttc.on_connect = on_connect
        mqttc.on_connect_fail = on_connect_fail
        mqttc.on_disconnect = on_disconnect
        mqttc.will_set(settings.mqtt_availability_topic, 'offline', retain=True, qos=2)

        try:
            mqttc.connect(args.mqtt_host, args.mqtt_port, keepalive=600)
        except OSError:
            pass

        if args.serve_http:
            mqttc.loop_start()

    if args.serve_http:
        uvicorn.run(app, host=['::', '0.0.0.0'], port=args.listen_port)
    else:
        mqttc.loop_forever()


if __name__ == '__main__':
    main()
