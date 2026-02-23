import sys
import os
import json
import threading
import time
import logging
from pathlib import Path
import re
import vlc
from flask import Flask, request, jsonify
import webview
from yandex_music import Client
import keyboard

try:
    import winreg
except ImportError:
    winreg = None
    logging.warning("winreg не доступен (не Windows), автозагрузка будет недоступна")

# Автоматическая авторизация
try:
    from selenium import webdriver
    from selenium.webdriver.chrome.service import Service
    from selenium.webdriver.chrome.options import Options
    from webdriver_manager.chrome import ChromeDriverManager

    SELENIUM_AVAILABLE = True
except ImportError:
    SELENIUM_AVAILABLE = False
    logging.warning("Selenium не установлен — автоматическая авторизация недоступна")

# -------------------- Настройка логирования --------------------
logging.basicConfig(level=logging.DEBUG, format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

APP_NAME = "Яндекс.Музыка мини"
CONFIG_FILE = Path.home() / ".yandex_music_widget.json"
WINDOW_WIDTH = 420
WINDOW_HEIGHT = 260
main_window = None
window_visible = True
cancel_auth = False


# ============================= Настройки пользователя =============================
class Settings:
    def __init__(self):
        self.volume = 80
        self.bitrate = 192
        self.equalizer_preset = "off"
        self.equalizer_enabled = False
        self.auto_start = False
        self.auto_play = True
        self.dark_mode = False
        # Настройки волны по умолчанию: None означает "любое" (сервер сам выберет)
        self.wave_mood = None
        self.wave_diversity = None
        self.wave_language = None
        self.key_bindings = {
            'play_pause': 'ctrl+shift+space',
            'next': 'ctrl+shift+right',
            'prev': 'ctrl+shift+left',
            'like': 'ctrl+shift+l',
            'dislike': 'ctrl+shift+d',
            'minimize': 'ctrl+shift+m',
            'volume_up': 'ctrl+shift+up',
            'volume_down': 'ctrl+shift+down'
        }
        self.load()

    def load(self):
        if CONFIG_FILE.exists():
            try:
                with open(CONFIG_FILE, 'r') as f:
                    data = json.load(f)
                    self.volume = data.get('volume', 80)
                    self.bitrate = data.get('bitrate', 192)
                    self.equalizer_preset = data.get('equalizer_preset', 'off')
                    self.equalizer_enabled = data.get('equalizer_enabled', False)
                    self.auto_start = data.get('auto_start', False)
                    self.auto_play = data.get('auto_play', True)
                    self.dark_mode = data.get('dark_mode', False)
                    # Настройки волны загружаем, но потом принудительно сбросим в None
                    # (чтобы при каждом запуске была стандартная волна)
                    saved_mood = data.get('wave_mood')
                    saved_diversity = data.get('wave_diversity')
                    saved_language = data.get('wave_language')
                    saved_bindings = data.get('key_bindings', {})
                    for key, default in self.key_bindings.items():
                        if key in saved_bindings:
                            self.key_bindings[key] = saved_bindings[key]
            except Exception as e:
                logger.error(f"Ошибка загрузки настроек: {e}")
        # Принудительно сбрасываем настройки волны в None при каждом запуске
        self.wave_mood = None
        self.wave_diversity = None
        self.wave_language = None

    def save(self):
        try:
            data = {}
            if CONFIG_FILE.exists():
                with open(CONFIG_FILE, 'r') as f:
                    data = json.load(f)
            data.update({
                'volume': self.volume,
                'bitrate': self.bitrate,
                'equalizer_preset': self.equalizer_preset,
                'equalizer_enabled': self.equalizer_enabled,
                'auto_start': self.auto_start,
                'auto_play': self.auto_play,
                'dark_mode': self.dark_mode,
                'wave_mood': self.wave_mood,
                'wave_diversity': self.wave_diversity,
                'wave_language': self.wave_language,
                'key_bindings': self.key_bindings
            })
            with open(CONFIG_FILE, 'w') as f:
                json.dump(data, f, indent=2)
        except Exception as e:
            logger.error(f"Ошибка сохранения настроек: {e}")


settings = Settings()


def get_yandex_music_token():
    global cancel_auth
    """Запускает браузер для получения токена, увеличивает счётчик активных попыток."""
    with player.auth_lock:
        player.auth_threads += 1

    logger.info("Открываю браузер для авторизации...")
    options = Options()
    options.add_argument("--no-sandbox")
    options.add_argument("--disable-dev-shm-usage")
    options.add_argument("--window-size=900,600")
    driver = None
    token = None
    try:
        driver = webdriver.Chrome(service=Service(ChromeDriverManager().install()), options=options)
        url = "https://oauth.yandex.ru/authorize?response_type=token&client_id=23cabbbdc6cd418abb4b39c32c41195d"
        driver.get(url)
        print("\n" + "=" * 70)
        print("          АВТОРИЗАЦИЯ В ЯНДЕКС.МУЗЫКЕ")
        print("=" * 70)
        print("1. Войди в свой аккаунт Яндекс (если попросит)")
        print("2. Нажми большую кнопку «Разрешить»")
        print("3. Браузер закроется сам, когда токен будет получен")
        print("Ждём до 3 минут...")
        print("=" * 70)
        start = time.time()
        while time.time() - start < 180:
            if cancel_auth:
                logger.info("Авторизация отменена пользователем")
                break
            try:
                current_url = driver.current_url
                if "#" in current_url and "access_token=" in current_url:
                    fragment = current_url.split("#")[1]
                    match = re.search(r"access_token=([^&]+)", fragment)
                    if match:
                        token = match.group(1)
                        logger.info("✅ Токен успешно получен!")
                        try:
                            with open(CONFIG_FILE, 'w') as f:
                                json.dump({'token': token}, f, indent=2)
                            logger.info("Токен сохранён в конфиг")
                        except Exception as e:
                            logger.error(f"Ошибка сохранения токена: {e}")
                        break
            except Exception as e:
                logger.debug(f"Проверка URL: {e}")
            time.sleep(1.2)
    except Exception as e:
        logger.error(f"Ошибка при открытии браузера: {e}")
    finally:
        if driver:
            try:
                driver.quit()
            except Exception as quit_e:
                logger.warning(f"Ошибка при закрытии драйвера: {quit_e}. Игнорируем, токен уже сохранён.")
        with player.auth_lock:
            player.auth_threads -= 1
            if player.auth_threads < 0:
                player.auth_threads = 0

    # Если токен получен, сразу применяем его
    if token and not cancel_auth:
        try:
            player.set_token(token)
        except Exception as e:
            logger.error(f"Ошибка при установке токена после браузерной авторизации: {e}")

    return token


def get_vlc_instance():
    # Минимальная буферизация для быстрого старта и отклика
    cache_ms = 300  # было 2000, уменьшено для моментального плей/пауза
    if getattr(sys, 'frozen', False):
        base_path = sys._MEIPASS
        vlc_path = os.path.join(base_path, 'vlc')
        plugins_path = os.path.join(vlc_path, 'plugins')
        if os.path.exists(plugins_path):
            os.environ['VLC_PLUGIN_PATH'] = plugins_path
            instance = vlc.Instance(f'--network-caching={cache_ms}', f'--file-caching={cache_ms}',
                                     f'--live-caching={cache_ms}',
                                     '--clock-synchro=0', '--no-audio-time-stretch', '--avcodec-hw=none',
                                     '--plugin-path=' + plugins_path)
        else:
            logger.error(f"VLC plugins not found at {plugins_path}")
            instance = vlc.Instance(f'--network-caching={cache_ms}', f'--file-caching={cache_ms}',
                                     f'--live-caching={cache_ms}',
                                     '--clock-synchro=0', '--no-audio-time-stretch', '--avcodec-hw=none')
    else:
        instance = vlc.Instance(f'--network-caching={cache_ms}', f'--file-caching={cache_ms}',
                                 f'--live-caching={cache_ms}',
                                 '--clock-synchro=0', '--no-audio-time-stretch', '--avcodec-hw=none')
    return instance


# ============================= Эквалайзер =============================
class EqualizerManager:
    PRESETS = {
        'off': None,
        'rock': [6.0, 5.0, 4.0, 3.0, 2.0, 1.0, 0.0, -1.0, -2.0, -3.0],
        'pop': [2.0, 3.0, 4.0, 5.0, 5.0, 4.0, 3.0, 2.0, 1.0, 0.0],
        'classical': [-1.0, 0.0, 1.0, 2.0, 3.0, 3.0, 2.0, 1.0, 0.0, -1.0],
        'jazz': [4.0, 3.0, 2.0, 1.0, 0.0, -1.0, -2.0, -3.0, -4.0, -5.0],
        'electronic': [5.0, 4.0, 3.0, 2.0, 1.0, 0.0, -1.0, -2.0, -3.0, -4.0],
        'bass_boost': [8.0, 7.0, 6.0, 5.0, 4.0, 3.0, 2.0, 1.0, 0.0, -1.0],
        'treble_boost': [-2.0, -1.0, 0.0, 1.0, 2.0, 3.0, 4.0, 5.0, 6.0, 7.0],
        'bass_treble': [6.0, 5.0, 4.0, 3.0, 2.0, 2.0, 3.0, 4.0, 5.0, 6.0]
    }

    @staticmethod
    def create(preset_name):
        if preset_name == 'off' or preset_name not in EqualizerManager.PRESETS:
            return None
        bands = EqualizerManager.PRESETS[preset_name]
        eq = vlc.AudioEqualizer()
        for i, gain in enumerate(bands):
            eq.set_amp_at_index(gain, i)
        return eq


# ============================= Плеер =============================
class Player:
    def __init__(self):
        self.lock = threading.Lock()
        self.client = None
        self.token = None
        self.current_track = None
        self.queue = []
        self.queue_index = 0
        self.playing = False
        self.current_playlist_id = None
        self.instance = get_vlc_instance()
        self.player = self.instance.media_player_new()
        self.is_station = False
        self.station_id = None
        self.station_queue = None
        self.loading_queue = False
        self.monitor_thread = None
        self.current_position = 0
        self.current_duration = 0
        self.liked_tracks_cache = set()
        self.liked_cache_time = 0
        self.loading = False
        self.url_cache = {}
        self.auth_threads = 0
        self.auth_lock = threading.Lock()
        self.user_info_loaded = False  # флаг для фронтенда
        self.player.audio_set_volume(settings.volume)
        # Применяем эквалайзер в отдельном потоке, чтобы не блокировать
        self._apply_equalizer_async()
        self._start_monitor()

    @property
    def browser_auth_in_progress(self):
        with self.auth_lock:
            return self.auth_threads > 0

    def _apply_equalizer_async(self):
        """Запускает применение эквалайзера в отдельном потоке с таймаутом"""
        def apply():
            try:
                if settings.equalizer_enabled and settings.equalizer_preset != 'off':
                    eq = EqualizerManager.create(settings.equalizer_preset)
                    if eq:
                        eq_apply_event = threading.Event()
                        result = [None]

                        def do_apply():
                            try:
                                self.player.set_equalizer(eq)
                                result[0] = True
                            except Exception as e:
                                logger.error(f"Ошибка в потоке применения эквалайзера: {e}")
                                result[0] = False
                            finally:
                                eq_apply_event.set()

                        t = threading.Thread(target=do_apply)
                        t.daemon = True
                        t.start()
                        eq_apply_event.wait(timeout=2.0)  # ждём не больше 2 секунд
                        if not eq_apply_event.is_set():
                            logger.error("Применение эквалайзера зависло, пропускаем")
                        elif result[0]:
                            logger.debug(f"Эквалайзер применён: {settings.equalizer_preset}")
                        else:
                            logger.error("Не удалось применить эквалайзер")
                    else:
                        self.player.set_equalizer(None)
                else:
                    self.player.set_equalizer(None)
            except Exception as e:
                logger.error(f"Ошибка применения эквалайзера: {e}")

        threading.Thread(target=apply, daemon=True).start()

    def _start_monitor(self):
        def monitor():
            while True:
                with self.lock:
                    if self.current_track:
                        state = self.player.get_state()
                        length = self.player.get_length()
                        pos = self.player.get_time()
                        self.current_position = pos
                        self.current_duration = length

                        if state == vlc.State.Ended and length > 0:
                            if settings.auto_play:
                                threading.Thread(target=self.next, daemon=True).start()
                            else:
                                self.player.stop()
                                self.playing = False

                        if self.is_station and len(self.queue) - self.queue_index < 3:
                            self._preload_more_station_tracks()
                time.sleep(0.2)

        self.monitor_thread = threading.Thread(target=monitor, daemon=True)
        self.monitor_thread.start()

    def set_token(self, token):
        self.token = token
        try:
            self.client = Client(token).init()
            logger.info("Авторизация успешна")
            with open(CONFIG_FILE, 'w') as f:
                json.dump({
                    'token': token,
                    'volume': settings.volume,
                    'bitrate': settings.bitrate,
                    'equalizer_preset': settings.equalizer_preset,
                    'equalizer_enabled': settings.equalizer_enabled,
                    'auto_start': settings.auto_start,
                    'auto_play': settings.auto_play,
                    'dark_mode': settings.dark_mode,
                    'wave_mood': settings.wave_mood,
                    'wave_diversity': settings.wave_diversity,
                    'wave_language': settings.wave_language,
                    'key_bindings': settings.key_bindings
                }, f, indent=2)
            self._update_liked_cache()

            # Принудительно сбрасываем настройки волны на сервере (передаём значения по умолчанию)
            self.set_wave_settings(None, None, None)

            self.preload_wave()
            self.user_info_loaded = False  # сбрасываем флаг, чтобы фронтенд загрузил инфо
            return True
        except Exception as e:
            logger.error(f"Ошибка авторизации: {e}")
            return False

    def load_token(self):
        self.loading = True
        try:
            token = None
            if CONFIG_FILE.exists():
                try:
                    with open(CONFIG_FILE, 'r') as f:
                        data = json.load(f)
                        token = data.get('token')
                except:
                    pass
            if token:
                if self.set_token(token):
                    return True
            if SELENIUM_AVAILABLE:
                token = get_yandex_music_token()
                if token:
                    return self.set_token(token)
            return False
        finally:
            self.loading = False

    def get_user_info(self):
        if not self.client:
            return {}
        try:
            me = getattr(self.client, 'me', None)
            if me is None:
                return {}
            account = getattr(me, 'account', None)
            name = ''
            if account:
                name = getattr(account, 'display_name', '') or getattr(account, 'login', '')
            avatar_url = None
            if account and hasattr(account, 'default_avatar_id') and account.default_avatar_id:
                avatar_url = f"https://avatars.yandex.net/get-yapic/{account.default_avatar_id}/islands-200"
            if not name:
                name = getattr(me, 'display_name', '') or getattr(me, 'login', '')
            self.user_info_loaded = True
            return {'name': name, 'avatar': avatar_url}
        except Exception as e:
            logger.error(f"Ошибка получения информации о пользователе: {e}")
            return {}

    def _update_liked_cache(self):
        if not self.client:
            return
        try:
            liked = self.client.users_likes_tracks()
            self.liked_tracks_cache = set(track_short.id for track_short in liked.tracks)
            self.liked_cache_time = time.time()
        except Exception as e:
            logger.error(f"Ошибка обновления кэша лайков: {e}")

    def is_track_liked(self, track_id):
        if time.time() - self.liked_cache_time > 60:
            self._update_liked_cache()
        return track_id in self.liked_tracks_cache

    def like_track(self, track_id):
        if not self.client:
            return False
        try:
            self.client.users_likes_tracks_add(track_id)
            self.liked_tracks_cache.add(track_id)
            return True
        except Exception as e:
            logger.error(f"Ошибка лайка трека {track_id}: {e}")
            return False

    def unlike_track(self, track_id):
        if not self.client:
            return False
        try:
            self.client.users_likes_tracks_remove(track_id)
            self.liked_tracks_cache.discard(track_id)
            return True
        except Exception as e:
            logger.error(f"Ошибка удаления лайка {track_id}: {e}")
            return False

    def dislike_track(self, track_id):
        if not self.client:
            return False
        try:
            self.client.users_dislikes_tracks_add(track_id)
            return True
        except Exception as e:
            logger.error(f"Ошибка дизлайка трека {track_id}: {e}")
            return False

    def add_track_to_playlist(self, track_id, playlist_kind):
        if not self.client:
            return False
        try:
            track = self.client.tracks(track_id)[0]
            if not track.albums:
                return False
            album_id = track.albums[0].id
            playlist = self.client.users_playlists(playlist_kind)
            self.client.users_playlists_insert_track(playlist_kind, track_id, album_id, at=0,
                                                     revision=playlist.revision)
            return True
        except Exception as e:
            logger.error(f"Ошибка добавления трека в плейлист: {e}")
            return False

    def preload_wave(self):
        self.set_station('user:wave')

    def set_station(self, station_id):
        self.is_station = True
        self.station_id = station_id
        self.current_playlist_id = station_id
        try:
            station_tracks = self.client.rotor_station_tracks(station_id)
            self.station_queue = station_tracks
            self.queue = [seq.track for seq in station_tracks.sequence if seq.track]
            if self.queue:
                self.queue_index = 0
                self.play_track_by_object(self.queue[0], auto_play=settings.auto_play)
        except Exception as e:
            logger.error(f"Ошибка загрузки станции: {e}")

    def _preload_more_station_tracks(self):
        if not self.is_station or self.loading_queue:
            return
        self.loading_queue = True
        try:
            queue_ids = [track.id for track in self.queue]
            new_batch = self.client.rotor_station_tracks(self.station_id, queue=queue_ids)
            self.station_queue = new_batch
            self.queue.extend(seq.track for seq in new_batch.sequence if seq.track)
        except Exception as e:
            logger.error(f"Ошибка предзагрузки треков: {e}")
        finally:
            self.loading_queue = False

    def set_playlist(self, playlist_id):
        self.is_station = False
        self.current_playlist_id = playlist_id
        self.player.stop()
        self.playing = False
        self.queue = []
        self.queue_index = 0

        def load_queue():
            if playlist_id == 3:
                liked = self.client.users_likes_tracks()
                track_ids = [t.id for t in liked.tracks]
            else:
                playlist = self.client.users_playlists(playlist_id)
                track_ids = [t.track_id for t in playlist.tracks]
            if track_ids:
                self.queue = self.client.tracks(track_ids)
                if self.queue:
                    self.current_track = self.queue[0]
                    self.play_track_by_object(self.queue[0], auto_play=settings.auto_play)

        threading.Thread(target=load_queue, daemon=True).start()

    def get_track_url(self, track, preferred_bitrate=None):
        cache_key = f"{track.id}_{preferred_bitrate}"
        if cache_key in self.url_cache:
            url, ts = self.url_cache[cache_key]
            if time.time() - ts < 7200:
                return url
        try:
            download_info = track.get_download_info()
            if not download_info:
                return None
            sorted_info = sorted(download_info, key=lambda x: x.bitrate_in_kbps, reverse=True)
            if preferred_bitrate:
                for info in sorted_info:
                    if info.bitrate_in_kbps == preferred_bitrate:
                        direct = info.get_direct_link()
                        if direct:
                            self.url_cache[cache_key] = (direct, time.time())
                            return direct
            for info in sorted_info:
                direct = info.get_direct_link()
                if direct:
                    self.url_cache[cache_key] = (direct, time.time())
                    return direct
            return None
        except Exception as e:
            logger.error(f"Ошибка получения ссылки: {e}")
            return None

    def play_track_by_object(self, track, auto_play=True):
        artists = ', '.join(a.name for a in track.artists) if track.artists else ''
        logger.info(f"Загрузка: {track.title} - {artists}")
        self.current_track = track
        url = self.get_track_url(track, preferred_bitrate=settings.bitrate)
        if not url:
            logger.error("Не удалось получить ссылку, пропускаем трек")
            self.current_track = None
            return False
        media = self.instance.media_new(url)
        self.player.set_media(media)
        # Применяем эквалайзер асинхронно
        self._apply_equalizer_async()
        if auto_play:
            self.player.play()
            self.playing = True
        else:
            self.playing = False
        self.current_position = 0
        self.current_duration = 0
        self._preload_next_track()
        return True

    def _preload_next_track(self):
        for idx in (self.queue_index + 1, self.queue_index + 2):
            if idx < len(self.queue):
                threading.Thread(target=self._preload_track_url, args=(self.queue[idx],), daemon=True).start()

    def _preload_track_url(self, track):
        try:
            self.get_track_url(track, preferred_bitrate=settings.bitrate)
        except:
            pass

    def play_specific_track(self, track_id, playlist_id=None):
        if not self.client:
            return False

        def _play():
            try:
                if playlist_id is None or playlist_id == 0:
                    track = self.client.tracks(track_id)[0]
                    self.queue = [track]
                    self.queue_index = 0
                    self.current_playlist_id = None
                    self.is_station = False
                    self.play_track_by_object(track, auto_play=settings.auto_play)
                    return
                if self.current_playlist_id != playlist_id:
                    if playlist_id == 3:
                        liked = self.client.users_likes_tracks()
                        track_ids = [t.id for t in liked.tracks]
                    else:
                        playlist = self.client.users_playlists(playlist_id)
                        track_ids = [t.track_id for t in playlist.tracks]
                    if not track_ids:
                        return
                    self.queue = self.client.tracks(track_ids)
                    self.current_playlist_id = playlist_id
                    self.is_station = False
                index = next((i for i, t in enumerate(self.queue) if t.id == track_id), -1)
                if index == -1:
                    return
                self.queue_index = index
                self.current_track = self.queue[index]
                self.play_track_by_object(self.queue[index], auto_play=settings.auto_play)
            except Exception as e:
                logger.error(f"Ошибка воспроизведения трека {track_id}: {e}")

        threading.Thread(target=_play, daemon=True).start()
        return True

    def seek(self, position_ms):
        with self.lock:
            if self.player and self.playing:
                self.player.set_time(position_ms)
                self.current_position = position_ms

    def next(self):
        with self.lock:
            if self.queue and self.queue_index + 1 < len(self.queue):
                self.queue_index += 1
                next_track = self.queue[self.queue_index]
            elif self.is_station:
                self._preload_more_station_tracks()
                if self.queue and self.queue_index + 1 < len(self.queue):
                    self.queue_index += 1
                    next_track = self.queue[self.queue_index]
                else:
                    return
            else:
                return
        self.play_track_by_object(next_track, auto_play=True)

    def prev(self):
        with self.lock:
            if self.queue and self.queue_index - 1 >= 0:
                self.queue_index -= 1
                prev_track = self.queue[self.queue_index]
            else:
                return
        self.play_track_by_object(prev_track, auto_play=True)

    def pause(self):
        with self.lock:
            if not self.current_track:
                return
            if self.playing:
                self.player.pause()
                self.playing = False
            else:
                self.player.play()
                self.playing = True

    def like_current(self):
        if self.current_track:
            if self.is_track_liked(self.current_track.id):
                self.unlike_track(self.current_track.id)
            else:
                self.like_track(self.current_track.id)

    def dislike_current(self):
        if self.current_track:
            self.dislike_track(self.current_track.id)
            self.next()

    def set_volume(self, volume):
        with self.lock:
            volume = max(0, min(100, volume))
            self.player.audio_set_volume(volume)
            settings.volume = volume
            settings.save()

    def volume_up(self, step=10):
        self.set_volume(min(100, settings.volume + step))

    def volume_down(self, step=10):
        self.set_volume(max(0, settings.volume - step))

    def set_bitrate(self, bitrate):
        settings.bitrate = bitrate
        settings.save()

    def set_equalizer_preset(self, preset, enabled=True):
        with self.lock:
            settings.equalizer_preset = preset
            settings.equalizer_enabled = enabled
            # Применяем асинхронно
            self._apply_equalizer_async()
            settings.save()

    def set_auto_start(self, enabled):
        settings.auto_start = enabled
        settings.save()
        self._apply_auto_start()

    def _apply_auto_start(self):
        if not winreg:
            return
        key_path = r"Software\Microsoft\Windows\CurrentVersion\Run"
        try:
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER, key_path, 0, winreg.KEY_SET_VALUE) as key:
                if settings.auto_start:
                    app_path = sys.executable if getattr(sys, 'frozen',
                                                         False) else f'"{sys.executable}" "{os.path.abspath(__file__)}"'
                    winreg.SetValueEx(key, APP_NAME, 0, winreg.REG_SZ, app_path)
                else:
                    try:
                        winreg.DeleteValue(key, APP_NAME)
                    except FileNotFoundError:
                        pass
        except Exception as e:
            logger.error(f"Ошибка настройки автозагрузки: {e}")

    def set_auto_play(self, enabled):
        settings.auto_play = enabled
        settings.save()

    def set_dark_mode(self, enabled):
        settings.dark_mode = enabled
        settings.save()

    def set_key_binding(self, action, hotkey):
        settings.key_bindings[action] = hotkey
        settings.save()

    def play_radio_from_track(self, track_id):
        if not self.client:
            return False
        try:
            self.is_station = True
            self.station_id = f"track:{track_id}"
            self.current_playlist_id = None
            station_tracks = self.client.rotor_station_tracks(self.station_id)
            self.station_queue = station_tracks
            self.queue = [seq.track for seq in station_tracks.sequence if seq.track]
            if self.queue:
                self.queue_index = 0
                self.play_track_by_object(self.queue[0], auto_play=True)
                return True
            return False
        except Exception as e:
            logger.error(f"Ошибка радио по треку: {e}")
            return False

    def get_status(self):
        state = {
            'playing': self.playing,
            'authenticated': self.client is not None,
            'loading': self.loading,
            'browser_auth_in_progress': self.browser_auth_in_progress,
            'user_info_loaded': self.user_info_loaded,
            'current_track': None,
            'queue_length': len(self.queue),
            'queue_index': self.queue_index,
            'volume': settings.volume,
            'bitrate': settings.bitrate,
            'equalizer_preset': settings.equalizer_preset,
            'equalizer_enabled': settings.equalizer_enabled,
            'auto_start': settings.auto_start,
            'auto_play': settings.auto_play,
            'dark_mode': settings.dark_mode,
            'wave_mood': settings.wave_mood,
            'wave_diversity': settings.wave_diversity,
            'wave_language': settings.wave_language,
            'key_bindings': settings.key_bindings,
            'position': self.current_position,
            'duration': self.current_duration,
            'has_prev': self.queue_index > 0,
            'has_next': self.queue_index < len(self.queue) - 1,
            'is_single_track': len(self.queue) == 1 and self.current_playlist_id is None,
            'is_station': self.is_station
        }
        if self.current_track:
            track = self.current_track
            artists = ', '.join(a.name for a in track.artists) if track.artists else ''
            cover = f"https://{track.cover_uri.replace('%%', '200x200')}" if track.cover_uri else None
            state['current_track'] = {
                'id': track.id,
                'title': track.title,
                'artists': artists,
                'cover': cover,
                'duration': track.duration_ms / 1000 if track.duration_ms else 0,
                'liked': self.is_track_liked(track.id)
            }
        return state

    def set_wave_settings(self, mood_energy=None, diversity=None, language=None):
        if not self.client:
            return False

        # Сохраняем оригинальные значения в settings (могут быть None)
        if mood_energy is not None:
            settings.wave_mood = mood_energy if mood_energy != 'any' else None
        if diversity is not None:
            settings.wave_diversity = diversity if diversity != 'any' else None
        if language is not None:
            settings.wave_language = language if language != 'any' else None
        settings.save()

        # Для отправки на сервер подставляем значения по умолчанию, если параметр None
        # Это гарантирует, что всегда передаются все три аргумента
        send_mood = mood_energy if mood_energy is not None else 'all'
        send_diversity = diversity if diversity is not None else 'default'
        send_language = language if language is not None else 'any'

        # Применяем к Моей волне
        station_id = 'user:wave'
        try:
            logger.debug(f"Отправка настроек волны: mood_energy={send_mood}, diversity={send_diversity}, language={send_language}")
            self.client.rotor_station_settings2(
                station=station_id,
                mood_energy=send_mood,
                diversity=send_diversity,
                language=send_language
            )
            logger.info(f"Настройки волны применены: {send_mood}, {send_diversity}, {send_language}")

            # Если сейчас играет Моя волна, перезапускаем её, чтобы настройки вступили
            if self.station_id == 'user:wave':
                threading.Thread(target=self.set_station, args=(station_id,), daemon=True).start()
            return True
        except Exception as e:
            logger.error(f"Ошибка настройки волны: {e}")
            return False

    def get_playlists(self):
        if not self.client:
            return []
        try:
            playlists = self.client.users_playlists_list()
            result = [{
                'kind': pl.kind,
                'title': pl.title,
                'track_count': pl.track_count,
                'cover': f"https://{pl.cover.uri.replace('%%', '100x100')}" if pl.cover and pl.cover.uri else None
            } for pl in playlists]
            if not any(pl.get('kind') == 3 for pl in result):
                result.append({
                    'kind': 3,
                    'title': 'Мне нравится',
                    'track_count': len(self.client.users_likes_tracks().tracks) if self.client else 0,
                    'cover': None
                })
            return result
        except Exception as e:
            logger.error(f"Ошибка получения плейлистов: {e}")
            return []

    def get_playlist_tracks(self, playlist_id):
        if not self.client:
            return []
        try:
            track_ids = []
            if playlist_id == 3:
                liked = self.client.users_likes_tracks()
                track_ids = [t.id for t in liked.tracks]
            else:
                playlist = self.client.users_playlists(playlist_id)
                track_ids = [t.track_id for t in playlist.tracks]
            if not track_ids:
                return []
            full = self.client.tracks(track_ids)
            return [{
                'id': track.id,
                'title': track.title,
                'artists': ', '.join(a.name for a in track.artists) if track.artists else '',
                'cover': f"https://{track.cover_uri.replace('%%', '100x100')}" if track.cover_uri else None,
                'liked': self.is_track_liked(track.id)
            } for track in full]
        except Exception as e:
            logger.error(f"Ошибка получения треков плейлиста {playlist_id}: {e}")
            return []

    def search_tracks(self, query):
        if not self.client:
            return []
        try:
            search_result = self.client.search(query, type_='track')
            if not search_result.tracks:
                return []
            tracks = search_result.tracks.results
            return [{
                'id': track.id,
                'title': track.title,
                'artists': ', '.join(a.name for a in track.artists) if track.artists else '',
                'cover': f"https://{track.cover_uri.replace('%%', '100x100')}" if track.cover_uri else None,
                'liked': self.is_track_liked(track.id)
            } for track in tracks]
        except Exception as e:
            logger.error(f"Ошибка поиска: {e}")
            return []

    def logout(self):
        self.client = None
        self.token = None
        self.current_track = None
        self.queue = []
        self.queue_index = 0
        self.playing = False
        self.current_playlist_id = None
        self.is_station = False
        self.station_id = None
        self.station_queue = None
        self.url_cache = {}
        self.liked_tracks_cache = set()
        self.user_info_loaded = False
        self.player.stop()
        try:
            with open(CONFIG_FILE, 'r') as f:
                data = json.load(f)
            data.pop('token', None)
            with open(CONFIG_FILE, 'w') as f:
                json.dump(data, f, indent=2)
            return True
        except Exception as e:
            logger.error(f"Ошибка при выходе: {e}")
            return False


player = Player()

# ============================= Flask приложение =============================
app = Flask(__name__)

# ---------- HTML шаблон (исправленный с улучшенным дизайном) ----------
HTML_TEMPLATE = """<!DOCTYPE html>
<html lang="ru">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Yandex Music Widget</title>
    <link rel="stylesheet" href="https://cdnjs.cloudflare.com/ajax/libs/font-awesome/6.0.0-beta3/css/all.min.css">
    <style>
        * { margin: 0; padding: 0; box-sizing: border-box; font-family: 'Segoe UI', Roboto, Arial, sans-serif; }
        body {
            background: #ffffff;
            color: #333333;
            height: 100vh;
            display: flex;
            flex-direction: column;
            overflow: hidden;
            position: relative;
            transition: background-color 0.3s, color 0.3s;
        }
        button, input, select, .track-item, .playlist-item, .extra-btn, .corner-btn, .close-btn, .minimize-btn, .search-input, .context-menu-item, .playlist-context-item, .title-bar button, .control-btn, .play-btn, .track-action-btn, .like-btn, .dislike-btn, input[type=range] {
            -webkit-app-region: no-drag;
        }

        /* Убираем обводки, оставляем стилизованный фокус */
        *:focus-visible {
            outline: 2px solid #fed42a;
            outline-offset: 2px;
        }
        *:focus {
            outline: none;
        }

        /* -------------------- Темная тема -------------------- */
        body.dark-theme {
            background: #1a1a1a;
            color: #ffffff;
        }
        body.dark-theme .title-bar,
        body.dark-theme .title-bar .app-title,
        body.dark-theme .title-bar .app-title i,
        body.dark-theme .title-bar .window-controls button,
        body.dark-theme .title-bar .window-controls button i {
            color: #ffffff;
        }
        body.dark-theme .close-menu,
        body.dark-theme .close-settings,
        body.dark-theme .close-tracks,
        body.dark-theme .close-token-help,
        body.dark-theme .close-search {
            color: #ffffff !important;
        }
        body.dark-theme .title-bar button {
            color: #ffffff;
        }
        body.dark-theme .user-header {
            background: rgba(40,40,40,0.6);
            backdrop-filter: blur(12px);
            color: #ffffff;
            box-shadow: 0 8px 20px rgba(0,0,0,0.2);
            border: 1px solid rgba(255,255,255,0.1);
        }
        body.dark-theme .control-btn {
            background: rgba(255,255,255,0.1);
            color: #ffffff;
            backdrop-filter: blur(8px);
            box-shadow: 0 4px 15px rgba(0,0,0,0.2);
            border: none;
        }
        body.dark-theme .control-btn:hover {
            background: rgba(255,255,255,0.2);
            transform: scale(1.05);
        }
        .control-btn.play-pause {
            background: linear-gradient(145deg, #fed42a, #e0b800) !important;
            border: none;
            color: #fff !important;
        }
        body.dark-theme .control-btn:disabled {
            opacity: 0.3;
        }
        body.dark-theme .extra-btn,
        body.dark-theme .corner-btn {
            background: rgba(255,255,255,0.1);
            color: #ffffff;
            backdrop-filter: blur(8px);
            border: none;
        }
        body.dark-theme .extra-btn:hover,
        body.dark-theme .corner-btn:hover {
            background: rgba(255,255,255,0.2);
        }
        body.dark-theme .cover-placeholder {
            background: rgba(255,255,255,0.1);
            color: #aaa;
        }
        body.dark-theme .menu-overlay,
        body.dark-theme .settings-overlay,
        body.dark-theme .tracks-overlay,
        body.dark-theme .token-help-overlay,
        body.dark-theme .search-overlay,
        body.dark-theme .context-menu,
        body.dark-theme .playlist-context-menu {
            background: rgba(30,30,30,0.8);
            backdrop-filter: blur(20px);
            border: none;
            box-shadow: 0 8px 30px rgba(0,0,0,0.5);
        }
        body.dark-theme .menu-header h3,
        body.dark-theme .settings-header h3,
        body.dark-theme .tracks-header h3,
        body.dark-theme .token-help-header h3,
        body.dark-theme .search-header h3 {
            color: #ffffff;
        }
        body.dark-theme .playlist-item,
        body.dark-theme .track-item,
        body.dark-theme .setting-item,
        body.dark-theme .search-item,
        body.dark-theme .playlist-context-item {
            background: rgba(255,255,255,0.05);
            color: #ffffff;
            border: none;
        }
        body.dark-theme .playlist-item:hover,
        body.dark-theme .track-item:hover,
        body.dark-theme .search-item:hover {
            background: rgba(255,255,255,0.1);
        }
        body.dark-theme .key-badge {
            background: rgba(255,255,255,0.1);
        }
        body.dark-theme .key-block {
            background: rgba(255,255,255,0.2);
            color: #ffffff;
        }
        body.dark-theme .record-btn {
            background: #fed42a;
            color: #1a1a1a;
        }
        body.dark-theme .record-btn.recording {
            background: #ff4d4d;
            color: #fff;
        }
        body.dark-theme .bitrate-select,
        body.dark-theme .equalizer-select,
        body.dark-theme .search-input {
            background: #333;
            color: #fff;
            border: 1px solid #555;
        }
        body.dark-theme select option {
            background: #2d2d2d;
            color: #fff;
        }

        /* Кнопка play-btn (Воспроизвести) в тёмной теме */
        body.dark-theme .play-btn {
            color: #fff;
        }

        /* Слайдеры переключателей в тёмной теме */
        body.dark-theme .slider {
            background-color: #555;
        }

        /* -------------------- Светлая тема (улучшенная) -------------------- */
        body:not(.dark-theme) {
            background: #f8f9fa;
            color: #212529;
        }
        body:not(.dark-theme) .title-bar {
            background: rgba(255,255,255,0.7);
            backdrop-filter: blur(12px);
            border-bottom: 1px solid rgba(0,0,0,0.05);
            box-shadow: 0 2px 10px rgba(0,0,0,0.03);
        }
        body:not(.dark-theme) .title-bar,
        body:not(.dark-theme) .title-bar .app-title,
        body:not(.dark-theme) .title-bar .app-title i,
        body:not(.dark-theme) .title-bar .window-controls button,
        body:not(.dark-theme) .title-bar .window-controls button i {
            color: #212529 !important;
        }
        body:not(.dark-theme) .close-menu,
        body:not(.dark-theme) .close-settings,
        body:not(.dark-theme) .close-tracks,
        body:not(.dark-theme) .close-token-help,
        body:not(.dark-theme) .close-search {
            color: #212529 !important;
        }
        body:not(.dark-theme) .user-header {
            background: rgba(255,255,255,0.7);
            backdrop-filter: blur(12px);
            color: #212529;
            box-shadow: 0 8px 20px rgba(0,0,0,0.05);
            border: 1px solid rgba(255,255,255,0.7);
        }
        body:not(.dark-theme) .control-btn {
            background: rgba(255,255,255,0.7);
            color: #212529;
            backdrop-filter: blur(8px);
            box-shadow: 0 2px 8px rgba(0,0,0,0.05);
            border: 1px solid rgba(0,0,0,0.03);
        }
        body:not(.dark-theme) .control-btn:hover {
            background: rgba(255,255,255,0.9);
            transform: scale(1.05);
            box-shadow: 0 4px 12px rgba(0,0,0,0.08);
        }
        body:not(.dark-theme) .extra-btn,
        body:not(.dark-theme) .corner-btn {
            background: rgba(255,255,255,0.7);
            color: #212529;
            backdrop-filter: blur(8px);
            border: 1px solid rgba(0,0,0,0.03);
        }
        body:not(.dark-theme) .extra-btn:hover,
        body:not(.dark-theme) .corner-btn:hover {
            background: rgba(255,255,255,0.9);
        }
        body:not(.dark-theme) .cover-placeholder {
            background: rgba(0,0,0,0.03);
            color: #adb5bd;
        }
        body:not(.dark-theme) .menu-overlay,
        body:not(.dark-theme) .settings-overlay,
        body:not(.dark-theme) .tracks-overlay,
        body:not(.dark-theme) .token-help-overlay,
        body:not(.dark-theme) .search-overlay,
        body:not(.dark-theme) .context-menu,
        body:not(.dark-theme) .playlist-context-menu {
            background: rgba(255,255,255,0.8);
            backdrop-filter: blur(20px);
            border: 1px solid rgba(0,0,0,0.05);
            box-shadow: 0 8px 30px rgba(0,0,0,0.1);
        }
        body:not(.dark-theme) .menu-header h3,
        body:not(.dark-theme) .settings-header h3,
        body:not(.dark-theme) .tracks-header h3,
        body:not(.dark-theme) .token-help-header h3,
        body:not(.dark-theme) .search-header h3 {
            color: #212529;
        }
        body:not(.dark-theme) .playlist-item,
        body:not(.dark-theme) .track-item,
        body:not(.dark-theme) .setting-item,
        body:not(.dark-theme) .search-item,
        body:not(.dark-theme) .playlist-context-item {
            background: rgba(255,255,255,0.5);
            border: 1px solid rgba(0,0,0,0.03);
            color: #212529;
            box-shadow: 0 2px 6px rgba(0,0,0,0.02);
        }
        body:not(.dark-theme) .playlist-item:hover,
        body:not(.dark-theme) .track-item:hover,
        body:not(.dark-theme) .search-item:hover {
            background: rgba(255,255,255,0.8);
        }
        body:not(.dark-theme) .key-badge {
            background: rgba(0,0,0,0.03);
        }
        body:not(.dark-theme) .key-block {
            background: #ffffff;
            color: #212529;
            box-shadow: 0 2px 4px rgba(0,0,0,0.05);
        }
        body:not(.dark-theme) .record-btn {
            background: #fed42a;
            color: #212529;
        }
        body:not(.dark-theme) .record-btn.recording {
            background: #ff4d4d;
            color: #fff;
        }
        body:not(.dark-theme) .bitrate-select,
        body:not(.dark-theme) .equalizer-select,
        body:not(.dark-theme) .search-input {
            background: rgba(255,255,255,0.8);
            border: 1px solid rgba(0,0,0,0.1);
            color: #212529;
        }

        /* -------------------- Общие стили -------------------- */
        .playlist-context-menu, .playlist-context-menu * { -webkit-app-region: no-drag; }
        .context-menu, .playlist-context-menu, #profileContextMenu {
            position: fixed;
            border-radius: 12px;
            box-shadow: 0 8px 30px rgba(0,0,0,0.2);
            z-index: 3000;
            flex-direction: column;
            min-width: 180px;
            max-width: 90vw;
            max-height: 90vh;
            overflow-y: auto;
            border: none;
            backdrop-filter: blur(20px);
            padding: 6px 0;
            opacity: 0;
            transform: scale(0.95);
            pointer-events: none;
            transition: opacity 0.2s ease, transform 0.2s ease;
        }
        .context-menu.active, .playlist-context-menu.active, #profileContextMenu.active {
            opacity: 1;
            transform: scale(1);
            pointer-events: auto;
        }

        /* Окно входа */
        #loginOverlay .login-box, #browserAuthOverlay .login-box {
            background: rgba(255,255,255,0.9);
            backdrop-filter: blur(20px);
            border: none;
            box-shadow: 0 8px 30px rgba(0,0,0,0.1);
            color: #212529;
            transition: background 0.3s, color 0.3s;
            border-radius: 24px;
        }
        body.dark-theme #loginOverlay .login-box,
        body.dark-theme #browserAuthOverlay .login-box {
            background: rgba(30,30,30,0.8);
            color: #fff;
        }
        #loginOverlay .login-box input, #browserAuthOverlay .login-box input {
            background: #f0f0f0;
            border-color: #ccc;
            color: #333;
        }
        body.dark-theme #loginOverlay .login-box input,
        body.dark-theme #browserAuthOverlay .login-box input {
            background: #4a4a4a;
            border-color: #555;
            color: #fff;
        }
        #loginOverlay .login-box input:focus, #browserAuthOverlay .login-box input:focus {
            border-color: #fed42a;
            box-shadow: 0 0 0 2px rgba(254,212,42,0.3);
        }
        #loginOverlay .login-box h3, #browserAuthOverlay .login-box h3,
        #loginOverlay .login-box a, #browserAuthOverlay .login-box a {
            color: #333;
        }
        body.dark-theme #loginOverlay .login-box h3,
        body.dark-theme #browserAuthOverlay .login-box h3,
        body.dark-theme #loginOverlay .login-box a,
        body.dark-theme #browserAuthOverlay .login-box a {
            color: #fff;
        }
        #loginOverlay .login-box a:hover, #browserAuthOverlay .login-box a:hover {
            color: #fed42a;
        }

        /* Toast уведомления */
        .toast {
            position: fixed;
            top: 20px;
            left: 50%;
            transform: translateX(-50%) translateY(-20px);
            background: rgba(0,0,0,0.85);
            color: #fff;
            padding: 10px 20px;
            border-radius: 40px;
            font-size: 14px;
            z-index: 20000 !important;
            opacity: 0;
            transition: opacity 0.3s ease, transform 0.3s ease;
            pointer-events: none;
            box-shadow: 0 4px 15px rgba(0,0,0,0.2);
            max-width: 80%;
            text-align: center;
            backdrop-filter: blur(5px);
            border: none;
        }
        .toast.show {
            opacity: 1;
            transform: translateX(-50%) translateY(0);
        }

        /* Прогресс бар */
        body.dark-theme .progress-slider { background: rgba(255,255,255,0.2); }
        body.dark-theme .progress-slider::-webkit-slider-thumb { background: #fed42a; }
        body.dark-theme .time-label { color: #aaa; }
        body:not(.dark-theme) .progress-slider { background: rgba(0,0,0,0.1); }
        body:not(.dark-theme) .progress-slider::-webkit-slider-thumb { background: #fed42a; }
        body:not(.dark-theme) .time-label { color: #6c757d; }

        /* Title bar */
        .title-bar {
            position: fixed;
            top: 0;
            left: 0;
            width: 100%;
            height: 32px;
            display: flex;
            align-items: center;
            justify-content: space-between;
            padding: 0 8px;
            z-index: 1000;
            -webkit-app-region: no-drag;
            transition: background 0.3s, border-color 0.3s;
            border: none;
            user-select: none;
        }
        .title-bar.dragging { -webkit-app-region: drag; }
        .title-bar .app-title {
            font-size: 14px;
            font-weight: 500;
            display: flex;
            align-items: center;
            gap: 6px;
        }
        .title-bar .app-title i {
            color: #fed42a;
            font-size: 16px;
        }
        .title-bar .window-controls {
            display: flex;
            gap: 4px;
            -webkit-app-region: no-drag;
        }
        .title-bar .window-controls button {
            background: transparent;
            border: none;
            color: inherit;
            width: 28px;
            height: 28px;
            border-radius: 6px;
            display: flex;
            align-items: center;
            justify-content: center;
            cursor: pointer;
            transition: background 0.2s, transform 0.1s;
        }
        .title-bar .window-controls button:hover {
            background: rgba(0,0,0,0.05);
            transform: scale(1.05);
        }
        .title-bar .window-controls .close-btn:hover {
            background: #e81123;
            color: white;
        }

        .main-content-wrapper { margin-top: 32px; flex: 1; display: flex; flex-direction: column; overflow: hidden; }
        .user-header {
            position: absolute;
            top: 40px;
            left: 8px;
            display: flex;
            align-items: center;
            gap: 6px;
            padding: 4px 10px 4px 6px;
            border-radius: 30px;
            font-size: 12px;
            z-index: 100;
            transition: background 0.3s, box-shadow 0.3s;
            border: none;
            cursor: pointer;
        }
        .user-avatar {
            width: 24px;
            height: 24px;
            border-radius: 50%;
            object-fit: cover;
            border: 1px solid #fed42a;
        }
        .user-name {
            font-weight: 500;
            max-width: 120px;
            white-space: nowrap;
            overflow: hidden;
            text-overflow: ellipsis;
        }
        .main-content {
            display: flex;
            flex: 1;
            padding: 12px;
            gap: 12px;
            min-height: 0;
            margin-top: 40px;
        }
        .left-column {
            flex: 1;
            display: flex;
            flex-direction: column;
            justify-content: center;
            gap: 8px;
        }
        .track-info-compact { margin-bottom: 4px; }
        .track-title-compact {
            font-size: 14px;
            font-weight: 600;
            white-space: nowrap;
            overflow: hidden;
            text-overflow: ellipsis;
            cursor: context-menu;
        }
        .track-artist-compact {
            font-size: 11px;
            color: #777;
            white-space: nowrap;
            overflow: hidden;
            text-overflow: ellipsis;
            cursor: context-menu;
        }
        .control-buttons {
            display: flex;
            gap: 8px;
            align-items: center;
        }
        .control-btn {
            width: 36px;
            height: 36px;
            border-radius: 50%;
            font-size: 14px;
            cursor: pointer;
            display: flex;
            align-items: center;
            justify-content: center;
            transition: transform 0.15s ease, background 0.2s, box-shadow 0.2s;
            border: none;
        }
        .control-btn:hover:not(:disabled) { transform: scale(1.05); }
        .control-btn:disabled { opacity: 0.3; cursor: default; }
        .control-btn.play-pause {
            background: linear-gradient(145deg, #fed42a, #e0b800);
            color: #fff;
            width: 44px;
            height: 44px;
            font-size: 18px;
            box-shadow: 0 4px 15px rgba(254,212,42,0.3);
            border: none;
        }
        .bottom-row {
            display: flex;
            align-items: center;
            gap: 8px;
            width: 100%;
        }
        .extra-buttons {
            display: flex;
            gap: 8px;
            flex-shrink: 0;
        }
        .extra-btn {
            padding: 4px 8px;
            border-radius: 16px;
            font-size: 11px;
            cursor: pointer;
            display: flex;
            align-items: center;
            gap: 4px;
            transition: 0.2s;
            border: none;
        }
        .extra-btn i { color: #fed42a; }
        .extra-btn.liked i { color: #fed42a; }
        .progress-area {
            display: flex;
            align-items: center;
            gap: 4px;
            flex: 1;
        }
        .time-label {
            font-size: 10px;
            min-width: 35px;
            text-align: center;
        }
        .progress-slider {
            flex: 1;
            height: 4px;
            -webkit-appearance: none;
            border-radius: 2px;
            outline: none;
            border: none;
        }
        .progress-slider::-webkit-slider-thumb {
            -webkit-appearance: none;
            width: 12px;
            height: 12px;
            border-radius: 50%;
            background: #fed42a;
            cursor: pointer;
            border: none;
            transition: transform 0.1s;
        }
        .progress-slider::-webkit-slider-thumb:hover { transform: scale(1.2); }
        .progress-slider::-moz-range-thumb {
            width: 12px;
            height: 12px;
            border-radius: 50%;
            background: #fed42a;
            cursor: pointer;
            border: none;
        }
        .right-column {
            width: 90px;
            display: flex;
            flex-direction: column;
            align-items: center;
            gap: 5px;
        }
        .cover {
            width: 90px;
            height: 90px;
            border-radius: 12px;
            overflow: hidden;
            box-shadow: 0 4px 15px rgba(0,0,0,0.1);
            transition: transform 0.2s, box-shadow 0.2s;
            margin-top: 15px;
            border: none;
            cursor: context-menu;
        }
        .cover:hover {
            transform: scale(1.05);
            box-shadow: 0 8px 25px rgba(0,0,0,0.15);
        }
        .cover img { width: 100%; height: 100%; object-fit: cover; }
        .cover-placeholder {
            width: 100%;
            height: 100%;
            display: flex;
            align-items: center;
            justify-content: center;
            font-size: 24px;
            border: none;
        }
        .corner-buttons {
            position: absolute;
            top: 40px;
            right: 10px;
            display: flex;
            gap: 6px;
            z-index: 100;
        }
        .corner-btn {
            width: 34px;
            height: 34px;
            border-radius: 50%;
            font-size: 14px;
            cursor: pointer;
            display: flex;
            align-items: center;
            justify-content: center;
            transition: 0.2s;
            border: none;
        }
        .corner-btn:hover { transform: scale(1.05); }
        .corner-btn i { color: #fed42a; }

        /* Оверлеи */
        .menu-overlay, .settings-overlay, .tracks-overlay, .token-help-overlay, .search-overlay {
            position: fixed;
            top: 0;
            left: 0;
            width: 100%;
            height: 100%;
            z-index: 2000;
            display: none;
            flex-direction: column;
            padding: 16px;
            overflow-y: auto;
            scrollbar-width: none;
            -ms-overflow-style: none;
            opacity: 0;
            transform: scale(0.95) translateY(10px);
            transition: opacity 0.3s cubic-bezier(0.4,0.0,0.2,1), transform 0.3s cubic-bezier(0.4,0.0,0.2,1);
            border: none;
        }
        .menu-overlay.active, .settings-overlay.active, .tracks-overlay.active, .token-help-overlay.active, .search-overlay.active {
            display: flex;
            opacity: 1;
            transform: scale(1) translateY(0);
        }
        .menu-overlay::-webkit-scrollbar, .settings-overlay::-webkit-scrollbar, .tracks-overlay::-webkit-scrollbar, .token-help-overlay::-webkit-scrollbar, .search-overlay::-webkit-scrollbar { display: none; }
        .menu-header, .settings-header, .tracks-header, .token-help-header, .search-header {
            display: flex;
            justify-content: space-between;
            align-items: center;
            margin-bottom: 12px;
            padding-bottom: 6px;
        }
        .search-header {
            display: flex;
            align-items: center;
            gap: 8px;
        }
        .search-header h3 { flex-shrink: 0; margin: 0; }
        .menu-overlay.active { display: flex !important; opacity: 1 !important; transform: scale(1) translateY(0) !important; }
        .search-header .search-input {
            flex: 1;
            margin: 0;
            padding: 6px 12px;
            border-radius: 20px;
            font-size: 14px;
            outline: none;
            transition: border-color 0.2s, background 0.2s;
            border: none;
        }
        .search-header .search-input:focus { outline: 2px solid #fed42a; }
        .menu-header h3, .settings-header h3, .tracks-header h3, .token-help-header h3, .search-header h3 { font-size: 16px; }
        .close-menu, .close-settings, .close-tracks, .close-token-help, .close-search {
            background: none;
            border: none;
            color: #666;
            font-size: 18px;
            cursor: pointer;
            transition: transform 0.2s;
        }
        .close-menu:hover, .close-settings:hover, .close-tracks:hover, .close-token-help:hover, .close-search:hover { transform: scale(1.1); }
        .login-overlay { opacity: 0; transition: opacity 0.5s ease-in-out; visibility: hidden; }
        .login-overlay:not(.hidden) { opacity: 1; visibility: visible; }
        .login-overlay.hidden { opacity: 0; visibility: hidden; }

        /* Плейлисты */
        .playlist-list { flex: 1; }
        .playlist-item {
            padding: 10px;
            border-radius: 12px;
            margin-bottom: 8px;
            cursor: pointer;
            display: flex;
            align-items: center;
            gap: 12px;
            transition: background 0.2s, transform 0.1s, box-shadow 0.2s;
            border: none;
        }
        .playlist-item:hover { transform: translateX(2px); box-shadow: 0 4px 10px rgba(0,0,0,0.05); }
        .playlist-cover {
            width: 40px;
            height: 40px;
            border-radius: 8px;
            object-fit: cover;
            background: #ddd;
        }
        .playlist-cover-placeholder {
            width: 40px;
            height: 40px;
            border-radius: 8px;
            background: #ddd;
            display: flex;
            align-items: center;
            justify-content: center;
            color: #aaa;
            font-size: 16px;
            border: none;
        }
        .playlist-info { flex: 1; }
        .playlist-title { font-size: 14px; font-weight: 500; }
        .playlist-count { font-size: 11px; color: #888; }
        .play-btn {
            background: #fed42a;
            color: #1a1a1a;
            border: none;
            padding: 4px 10px;
            border-radius: 20px;
            font-size: 11px;
            cursor: pointer;
            display: flex;
            align-items: center;
            gap: 4px;
            margin-left: auto;
            font-weight: 500;
            transition: transform 0.1s, box-shadow 0.2s;
        }
        .play-btn:hover { transform: scale(1.02); box-shadow: 0 4px 10px rgba(254,212,42,0.3); }

        /* Треки */
        .tracks-list { flex: 1; }
        .track-item {
            padding: 8px;
            border-radius: 12px;
            margin-bottom: 6px;
            cursor: pointer;
            display: flex;
            align-items: center;
            gap: 10px;
            transition: background 0.2s, transform 0.1s, box-shadow 0.2s;
            position: relative;
            border: none;
        }
        .track-item:hover { transform: scale(1.01); box-shadow: 0 4px 10px rgba(0,0,0,0.05); }
        .track-item-cover {
            width: 30px;
            height: 30px;
            border-radius: 6px;
            object-fit: cover;
        }
        .track-item-info { flex: 1; }
        .track-item-title { font-size: 13px; font-weight: 500; }
        .track-item-artist { font-size: 10px; color: #777; }
        .track-actions {
            display: flex;
            gap: 6px;
            margin-left: auto;
        }
        .track-action-btn {
            background: transparent;
            border: none;
            color: #aaa;
            font-size: 14px;
            cursor: pointer;
            transition: color 0.2s, transform 0.1s;
            padding: 4px;
        }
        .track-action-btn:hover { color: #fed42a; transform: scale(1.1); }
        .track-action-btn.liked i { color: #aaa; }

        /* Настройки */
        .setting-item {
            margin-bottom: 16px;
            background: rgba(0,0,0,0.02);
            padding: 12px;
            border-radius: 16px;
            border: none;
            transition: background 0.2s;
        }
        .setting-item:hover {
            background: rgba(0,0,0,0.04);
        }
        .setting-label {
            font-size: 14px;
            font-weight: 500;
            margin-bottom: 8px;
            display: flex;
            align-items: center;
            gap: 6px;
        }
        .setting-label i { color: #fed42a; }
        .volume-slider {
            width: 100%;
            accent-color: #fed42a;
            border: none;
        }
        .bitrate-select, .equalizer-select {
            width: 100%;
            padding: 8px;
            border-radius: 12px;
            font-size: 13px;
            border: none;
            outline: none;
        }
        .equalizer-toggle {
            display: flex;
            align-items: center;
            gap: 8px;
            margin-top: 8px;
        }
        .equalizer-toggle input { accent-color: #fed42a; }
        .switch {
            position: relative;
            display: inline-block;
            width: 40px;
            height: 20px;
            margin-left: 8px;
        }
        .switch input { opacity: 0; width: 0; height: 0; }
        .slider {
            position: absolute;
            cursor: pointer;
            top: 0;
            left: 0;
            right: 0;
            bottom: 0;
            background-color: #ccc;
            transition: .2s;
            border-radius: 20px;
            border: none;
        }
        .slider:before {
            position: absolute;
            content: "";
            height: 16px;
            width: 16px;
            left: 2px;
            bottom: 2px;
            background-color: #fff;
            transition: .2s;
            border-radius: 50%;
        }
        input:checked + .slider { background-color: #fed42a; }
        input:checked + .slider:before { transform: translateX(20px); }

        /* Горячие клавиши */
        .key-bindings { display: flex; flex-direction: column; gap: 8px; }
        .key-row {
            display: flex;
            align-items: center;
            gap: 8px;
        }
        .key-row label {
            width: 100px;
            font-size: 13px;
        }
        .key-badge {
            display: flex;
            flex-wrap: wrap;
            gap: 4px;
            flex: 1;
            min-height: 32px;
            border-radius: 16px;
            padding: 4px 8px;
            align-items: center;
            border: none;
        }
        .key-block {
            border-radius: 6px;
            padding: 4px 8px;
            font-size: 12px;
            font-weight: 500;
            display: inline-flex;
            align-items: center;
            gap: 2px;
            border: none;
        }
        .record-btn {
            background: #fed42a;
            color: #1a1a1a;
            border: none;
            border-radius: 16px;
            padding: 6px 12px;
            font-size: 12px;
            cursor: pointer;
            transition: background 0.2s, transform 0.1s;
            display: flex;
            align-items: center;
            gap: 4px;
            font-weight: 500;
        }
        .record-btn.recording {
            background: #ff4d4d;
            color: #fff;
            animation: pulse 1s infinite;
        }
        @keyframes pulse { 0% { opacity: 1; } 50% { opacity: 0.7; } 100% { opacity: 1; } }

        /* Лоадер */
        .loader {
            display: inline-block;
            width: 24px;
            height: 24px;
            border: 2px solid rgba(0,0,0,0.1);
            border-top: 2px solid #fed42a;
            border-radius: 50%;
            animation: spin 0.8s linear infinite;
            margin: 20px auto;
        }
        @keyframes spin { 0% { transform: rotate(0deg); } 100% { transform: rotate(360deg); } }
        .loader-container { display: flex; justify-content: center; align-items: center; width: 100%; padding: 20px; }

        /* Окно входа */
        .login-box {
            width: 100%;
            max-width: 360px;
            padding: 30px 30px;
            text-align: center;
            border-radius: 28px;
        }
        .login-box h3 {
            font-size: 24px;
            margin-bottom: 20px;
            font-weight: 500;
            display: flex;
            align-items: center;
            justify-content: center;
            gap: 8px;
        }
        .login-box h3 i { color: #fed42a; }
        .login-row {
            display: flex;
            gap: 10px;
            align-items: center;
            justify-content: center;
            margin-bottom: 15px;
        }
        .login-row input {
            flex: 1;
            padding: 12px 16px;
            border: 1px solid #555;
            border-radius: 30px;
            font-size: 14px;
            outline: none;
            transition: border-color 0.2s;
        }
        .login-row input:focus { border-color: #fed42a; }
        .login-row button {
            background: #fed42a;
            color: #1a1a1a;
            border: none;
            width: 46px;
            height: 46px;
            border-radius: 50%;
            cursor: pointer;
            font-size: 20px;
            display: flex;
            align-items: center;
            justify-content: center;
            transition: background 0.2s, transform 0.1s;
        }
        .login-row button:hover { background: #e0b800; transform: scale(1.05); }
        .login-box a {
            display: inline-block;
            margin-top: 12px;
            color: #aaa;
            text-decoration: none;
            font-size: 13px;
            cursor: pointer;
            transition: color 0.2s;
        }
        .login-box a:hover { color: #fed42a; }
        .hidden { display: none !important; }

        /* Информация */
        .info-content {
            text-align: center;
            padding: 20px;
            background: rgba(0,0,0,0.02);
            border-radius: 16px;
            margin: 20px;
        }
        body.dark-theme .info-content { background: rgba(255,255,255,0.05); }
        .info-content i { font-size: 48px; color: #fed42a; margin-bottom: 16px; }
        .info-content h2 { font-size: 20px; margin-bottom: 8px; }
        .info-content p { color: #666; margin-bottom: 4px; }
        body.dark-theme .info-content p { color: #fff; }
        .info-user {
            display: flex;
            align-items: center;
            justify-content: center;
            gap: 6px;
            margin: 15px 0;
            font-size: 14px;
        }
        .info-user-avatar {
            width: 1em;
            height: 1em;
            border-radius: 50%;
            object-fit: cover;
            border: 1px solid #fed42a;
        }
        .developer { margin-top: 20px; font-size: 14px; color: #fed42a; font-weight: 500; }

        /* Помощь по токену */
        .token-help-content {
            padding: 20px;
            background: rgba(0,0,0,0.02);
            border-radius: 16px;
            margin: 10px;
        }
        body.dark-theme .token-help-content { background: rgba(255,255,255,0.05); color: #fff; }
        .token-help-content ol { margin-left: 20px; color: #333; }
        body.dark-theme .token-help-content ol { color: #fff; }
        .token-help-content li { margin-bottom: 10px; font-size: 14px; }
        .token-help-content a { color: #fed42a; text-decoration: none; }

        /* Контекстные меню */
        .context-menu-item, .playlist-context-item {
            padding: 10px 15px;
            font-size: 13px;
            cursor: pointer;
            display: flex;
            align-items: center;
            gap: 8px;
            transition: background 0.2s;
            border: none;
            border-bottom: 1px solid rgba(0,0,0,0.05);
        }
        .context-menu-item:last-child, .playlist-context-item:last-child { border-bottom: none; }
        .context-menu-item:hover, .playlist-context-item:hover { background: rgba(0,0,0,0.05); }
        .context-menu-item i, .playlist-context-item i { color: #fed42a; width: 16px; }
        body.dark-theme .context-menu-item, body.dark-theme .playlist-context-item {
            color: #fff;
            border-bottom-color: rgba(255,255,255,0.1);
        }
        body.dark-theme .context-menu-item:hover, body.dark-theme .playlist-context-item:hover { background: rgba(255,255,255,0.15); }
        .playlist-context-header {
            padding: 8px 15px;
            border-bottom: 1px solid rgba(0,0,0,0.1);
            font-weight: bold;
        }
        body.dark-theme .playlist-context-header { border-bottom-color: rgba(255,255,255,0.1); }

        /* Окно настроек волны */
        #waveSettingsOverlay .login-box {
            background: rgba(255,255,255,0.9) !important;
            backdrop-filter: blur(20px);
            border: none;
            box-shadow: 0 8px 30px rgba(0,0,0,0.1);
            color: #212529;
        }
        body.dark-theme #waveSettingsOverlay .login-box {
            background: rgba(30,30,30,0.8) !important;
            color: #fff;
        }
        #waveSettingsOverlay .setting-group { margin-bottom: 20px; }
        #waveSettingsOverlay .setting-group label.setting-label {
            display: flex;
            align-items: center;
            gap: 6px;
            margin-bottom: 10px;
            font-weight: 500;
            font-size: 13px;
        }
        .diversity-options {
            display: flex;
            justify-content: space-around;
            gap: 6px;
        }
        .diversity-options .option {
            flex: 1;
            padding: 8px 2px;
            text-align: center;
            cursor: pointer;
            border-radius: 12px;
            background: rgba(0,0,0,0.03);
            backdrop-filter: blur(5px);
            transition: all 0.2s ease;
            border: 1px solid transparent;
            font-size: 11px;
        }
        .diversity-options .option:hover { background: rgba(0,0,0,0.08); }
        .diversity-options .option.selected {
            border: 1px solid #fed42a;
            background: rgba(255,255,255,0.8);
        }
        body.dark-theme .diversity-options .option {
            background: rgba(255,255,255,0.1);
        }
        body.dark-theme .diversity-options .option:hover { background: rgba(255,255,255,0.2); }
        body.dark-theme .diversity-options .option.selected {
            background: rgba(255,255,255,0.15);
        }
        .diversity-options .icon { display: block; font-size: 22px; margin-bottom: 4px; }
        .mood-options {
            display: flex;
            justify-content: space-around;
            gap: 6px;
        }
        .mood-options .mood {
            flex: 1;
            text-align: center;
            cursor: pointer;
            padding: 6px 2px;
            border-radius: 12px;
            transition: all 0.2s ease;
            background: transparent;
            border: 1px solid transparent;
        }
        .mood-options .mood:hover { transform: scale(1.05); }
        .mood-options .mood.selected { border: 1px solid #fed42a; }
        .mood-options .circle {
            width: 32px;
            height: 32px;
            border-radius: 50%;
            margin: 0 auto 6px;
        }
        .mood-options .energetic { background: radial-gradient(circle at 30% 30%, #ff9966, #ff5e62); }
        .mood-options .fun { background: radial-gradient(circle at 30% 30%, #9ACD32, #32CD32); }
        .mood-options .calm { background: radial-gradient(circle at 30% 30%, #00BFFF, #1E90FF); }
        .mood-options .sad { background: radial-gradient(circle at 30% 30%, #9370DB, #6A5ACD); }
        .mood-options span { font-size: 11px; font-weight: 500; }
        .language-options {
            display: flex;
            flex-wrap: wrap;
            gap: 6px;
            justify-content: center;
        }
        .language-options .lang-btn {
            padding: 6px 12px;
            border: 1px solid transparent;
            border-radius: 20px;
            background: rgba(0,0,0,0.03);
            backdrop-filter: blur(5px);
            cursor: pointer;
            font-size: 12px;
            font-weight: 500;
            transition: all 0.2s ease;
            flex: 1 0 auto;
            min-width: 70px;
        }
        .language-options .lang-btn:hover { background: rgba(0,0,0,0.08); }
        .language-options .lang-btn.selected {
            border: 1px solid #fed42a;
            background: rgba(255,255,255,0.8);
        }
        body.dark-theme .language-options .lang-btn {
            background: rgba(255,255,255,0.1);
        }
        body.dark-theme .language-options .lang-btn:hover { background: rgba(255,255,255,0.2); }
        body.dark-theme .language-options .lang-btn.selected {
            background: rgba(255,255,255,0.15);
        }

        /* Кнопка сброса настроек волны — белая в обеих темах, активная/неактивная */
        #resetWaveSettings {
            background: none !important;
            border: none;
            font-size: 18px;
            width: 28px;
            height: 28px;
            padding: 0;
            display: flex;
            align-items: center;
            justify-content: center;
            cursor: pointer;
            transition: opacity 0.2s, transform 0.2s;
            opacity: 0.6;
            color: inherit !important;
        }
        #resetWaveSettings i {
            color: inherit !important; /* наследуем цвет текста (белый в тёмной, чёрный в светлой) */
        }
        #resetWaveSettings.active {
            opacity: 1;
        }
        #resetWaveSettings.active:hover {
            opacity: 1;
            transform: scale(1.1);
        }
        #resetWaveSettings:not(.active) {
            opacity: 0.3;
            pointer-events: none;
            cursor: default;
        }

        /* Окно браузерной авторизации */
        #browserAuthOverlay .login-box {
            background: rgba(30,30,30,0.8) !important;
            backdrop-filter: blur(20px);
            border: none;
            box-shadow: 0 8px 30px rgba(0,0,0,0.5);
        }
        .auth-message { margin: 20px 0; font-size: 14px; line-height: 1.5; }
        .auth-buttons { display: flex; flex-direction: column; gap: 15px; margin-top: 20px; }
        .auth-buttons button {
            background: #fed42a;
            color: #1a1a1a;
            border: none;
            border-radius: 30px;
            padding: 12px 20px;
            font-size: 14px;
            font-weight: 500;
            cursor: pointer;
            transition: background 0.2s, transform 0.1s;
            display: flex;
            align-items: center;
            justify-content: center;
            gap: 8px;
        }
        .auth-buttons button:hover { background: #e0b800; transform: scale(1.02); }
        .auth-buttons .secondary-btn {
            background: transparent;
            border: 1px solid #fed42a;
            color: inherit;
        }
        .auth-buttons .secondary-btn:hover { background: rgba(254,212,42,0.2); }

        #retryBrowserAuthBtn {
            background: #fed42a;
            color: #fff;
            border: none;
            padding: 8px 16px;
            border-radius: 20px;
            font-size: 13px;
            cursor: pointer;
            transition: background 0.2s;
            margin: 5px 0;
        }
        #retryBrowserAuthBtn:hover { background: #e0b800; }
        #switchToTokenAuth {
            color: #aaa;
            text-decoration: none;
            background: none;
            border: none;
            cursor: pointer;
            font-size: 12px;
            transition: color 0.2s;
            padding: 5px;
        }
        #switchToTokenAuth:hover { color: #fed42a; }

        #browserAuthOverlay, #loginOverlay {
            position: fixed;
            top: 0;
            left: 0;
            width: 100%;
            height: 100%;
            display: flex;
            align-items: center;
            justify-content: center;
            background: rgba(0,0,0,0.5);
            backdrop-filter: blur(5px);
            z-index: 10000;
            opacity: 0;
            transform: scale(0.95);
            transition: opacity 0.4s ease, transform 0.4s ease;
            padding: 20px;
        }
        #browserAuthOverlay.active, #loginOverlay.active {
            opacity: 1;
            transform: scale(1);
        }
        #browserAuthOverlay.hidden, #loginOverlay.hidden {
            opacity: 0;
            transform: scale(0.95);
            pointer-events: none;
        }

        #tokenHelpOverlay { z-index: 15000; }
        #profileContextMenu { min-width: 150px; }
        #profileContextMenu .context-menu-item { padding: 8px 12px; font-size: 13px; }
        #profileContextMenu .context-menu-item i { width: 18px; text-align: center; }

        /* Глобальный лоадер — фон зависит от темы */
        #globalLoader {
            position: fixed !important;
            top: 0 !important;
            left: 0 !important;
            width: 100% !important;
            height: 100% !important;
            background: rgba(255,255,255,0.95) !important;
            z-index: 2147483647 !important;
            backdrop-filter: blur(12px);
            transition: opacity 0.4s ease-out;
            display: flex !important;
            align-items: center !important;
            justify-content: center !important;
            border-radius: 0 !important;
            pointer-events: none;
        }
        body.dark-theme #globalLoader {
            background: rgba(0,0,0,0.95) !important;
        }
        #globalLoader .loader {
            width: 32px;
            height: 32px;
            border-width: 2px;
        }
    </style>
</head>
<body>
<div id="globalLoader" style="display: flex !important; align-items: center; justify-content: center; position: fixed !important; top: 0 !important; left: 0 !important; width: 100% !important; height: 100% !important; z-index: 2147483647 !important; backdrop-filter: blur(12px); transition: opacity 0.4s ease-out; border-radius: 0 !important; pointer-events: none;">
    <div class="loader" style="width:32px; height:32px; border:2px solid #ddd; border-top:2px solid #fed42a;"></div>
</div>
    <div class="title-bar" id="titleBar">
        <div class="app-title"><span>Яндекс.Музыка мини</span></div>
        <div class="window-controls">
            <button class="minimize-btn" id="minimizeWindow"><i class="fas fa-minus"></i></button>
            <button class="close-btn" id="closeWindow"><i class="fas fa-times"></i></button>
        </div>
    </div>
    <div id="loginOverlay" class="login-overlay active">
        <div class="login-box">
            <h3><i class="fas fa-music"></i> Вход</h3>
            <div class="login-row"><input type="text" id="tokenInput" placeholder="Токен"><button class="login-icon-btn" id="loginBtn"><i class="fas fa-sign-in-alt"></i></button></div>
            <a href="#" id="helpTokenLink">Как получить токен?</a>
        </div>
    </div>
    <div id="browserAuthOverlay" class="login-overlay hidden">
        <div class="login-box">
            <h3>Авторизация в браузере</h3>
            <p>Пожалуйста, авторизируйтесь в открывшемся окне браузера.</p>
            <button id="retryBrowserAuthBtn">Повторить</button>
            <button id="switchToTokenAuth">Ввести токен вручную</button>
        </div>
    </div>
    <div id="tokenHelpOverlay" class="token-help-overlay">
        <div class="token-help-header"><h3><i class="fas fa-question-circle" style="color:#fed42a;"></i> Как получить токен</h3><button class="close-token-help" id="closeTokenHelpBtn"><i class="fas fa-times"></i></button></div>
        <div class="token-help-content">
            <ol><li>Перейдите на <a href="https://chromewebstore.google.com/detail/yandex-music-token/lcbjeookjibfhjjopieifgjnhlegmkib" target="_blank">расширение для получение токена</a></li><li>Загрузите расширение для браузера</li><li>Откройте расширение</li><li>Нажмите кнопку "Авторизоваться"</li><li>Привяжите свой яндекс ID</li><li>Снова откройте расширение и внизу нажмите "Скопировать токен"</li><li>Вставте токен в приложение и пользуйтесь!</li></ol>
            <p style="color:#fed42a; margin-top:15px;">Токен сохраняется локально и никуда не передаётся.</p>
        </div>
    </div>
    <div id="mainInterface" class="hidden main-content-wrapper" style="display: flex; flex-direction: column; height: 100%;">
        <div class="user-header" id="userHeader">
            <img src="" alt="avatar" class="user-avatar" id="userAvatar" onerror="this.onerror=null; this.src='https://avatars.yandex.net/get-yapic/0/0-200';">
            <span class="user-name" id="userName"></span>
        </div>
        <div class="main-content">
            <div class="left-column">
                <div class="track-info-compact"><div class="track-title-compact" id="trackTitle">Моя волна</div><div class="track-artist-compact" id="trackArtist">Яндекс.Музыка</div></div>
                <div class="control-buttons"><button class="control-btn" id="prevBtn" disabled><i class="fas fa-backward"></i></button><button class="control-btn play-pause" id="playPauseBtn"><i class="fas fa-play"></i></button><button class="control-btn" id="nextBtn" disabled><i class="fas fa-forward"></i></button></div>
                <div class="bottom-row">
                    <div class="extra-buttons"><button class="extra-btn" id="likeBtn"><i class="far fa-heart"></i></button><button class="extra-btn" id="dislikeBtn"><i class="fas fa-thumbs-down"></i></button></div>
                    <div class="progress-area"><span class="time-label" id="currentTime">0:00</span><input type="range" id="progressSlider" class="progress-slider" value="0" min="0" max="100" step="0.1"><span class="time-label" id="totalTime">0:00</span></div>
                </div>
            </div>
            <div class="right-column">
                <div class="cover" id="cover"><div class="cover-placeholder" id="coverPlaceholder"><i class="fas fa-music"></i></div><img src="" alt="cover" id="coverImg" style="display: none;"></div>
            </div>
        </div>
        <div class="corner-buttons"><button class="corner-btn" id="menuBtn"><i class="fas fa-ellipsis-v"></i></button><button class="corner-btn" id="settingsBtn"><i class="fas fa-sliders-h"></i></button><button class="corner-btn" id="searchBtn"><i class="fas fa-search"></i></button></div>
    </div>
    <div id="menuOverlay" class="menu-overlay">
        <div class="menu-header"><h3><i class="fas fa-list" style="color:#fed42a;"></i> Плейлисты</h3><button class="close-menu" id="closeMenuBtn"><i class="fas fa-times"></i></button></div>
        <div class="loader-container" id="playlistLoader" style="display: none;"><div class="loader"></div></div>
        <div class="playlist-list" id="playlistContainer"></div>
        <div style="margin-top:12px;"><div class="playlist-item" id="waveMenuItem"><div class="playlist-cover-placeholder"><i class="fas fa-wave-square" style="color:#fed42a;"></i></div><div class="playlist-info"><div class="playlist-title">Моя волна</div><div class="playlist-count">Персональная станция</div></div></div></div>
    </div>
    <div id="tracksOverlay" class="tracks-overlay">
        <div class="tracks-header"><h3 id="tracksPlaylistTitle"><i class="fas fa-music"></i> <span></span></h3><button class="close-tracks" id="closeTracksBtn"><i class="fas fa-times"></i></button></div>
        <div class="loader-container" id="tracksLoader" style="display: none;"><div class="loader"></div></div>
        <div class="tracks-list" id="tracksContainer"></div>
    </div>
    <div id="searchOverlay" class="search-overlay">
        <div class="search-header"><h3><i class="fas fa-search" style="color:#fed42a;"></i> Поиск</h3><input type="text" id="searchInput" class="search-input" placeholder="Введите название трека..." autocomplete="off"><button class="close-search" id="closeSearchBtn"><i class="fas fa-times"></i></button></div>
        <div class="loader-container" id="searchLoader" style="display: none;"><div class="loader"></div></div>
        <div class="tracks-list" id="searchResults"></div>
    </div>
    <div id="settingsOverlay" class="settings-overlay">
        <div class="settings-header"><h3><i class="fas fa-sliders-h" style="color:#fed42a;"></i> Настройки</h3><button class="close-settings" id="closeSettingsBtn"><i class="fas fa-times"></i></button></div>
        <div class="setting-item"><div class="setting-label"><i class="fas fa-volume-up"></i> Громкость</div><input type="range" id="volumeSlider" class="volume-slider" min="0" max="100" value="80"></div>
        <div class="setting-item"><div class="setting-label"><i class="fas fa-microchip"></i> Качество звука</div><select id="bitrateSelect" class="bitrate-select"><option value="192">192 kbps (стандарт)</option><option value="320">320 kbps (высокое)</option></select></div>
        <div class="setting-item"><div class="setting-label"><i class="fas fa-chart-line"></i> Эквалайзер</div><select id="equalizerSelect" class="equalizer-select"><option value="off">Выключен</option><option value="rock">Рок</option><option value="pop">Поп</option><option value="classical">Классика</option><option value="jazz">Джаз</option><option value="electronic">Электроника</option><option value="bass_boost">Усиление басов</option><option value="treble_boost">Усиление высоких</option><option value="bass_treble">Басы + Высокие</option></select><div class="equalizer-toggle"><input type="checkbox" id="equalizerEnable" checked> <label for="equalizerEnable">Включить эквалайзер</label></div></div>
        <div class="setting-item"><div class="setting-label"><i class="fas fa-power-off"></i> Автозагрузка при старте Windows</div><label class="switch"><input type="checkbox" id="autoStartCheck"><span class="slider"></span></label></div>
        <div class="setting-item"><div class="setting-label"><i class="fas fa-play-circle"></i> Автовоспроизведение трека</div><label class="switch"><input type="checkbox" id="autoPlayCheck" checked><span class="slider"></span></label></div>
        <div class="setting-item"><div class="setting-label"><i class="fas fa-moon"></i> Тёмная тема</div><label class="switch"><input type="checkbox" id="darkModeCheck"><span class="slider"></span></label></div>
        <div class="setting-item"><div class="setting-label"><i class="fas fa-keyboard"></i> Горячие клавиши</div><div class="key-bindings" id="keyBindings"></div></div>
        <div class="setting-item"><div class="setting-label"><i class="fas fa-info-circle"></i> О программе</div><div style="text-align:center; padding:10px;"><i class="fas fa-music" style="font-size:48px; color:#fed42a; margin-bottom:10px;"></i><h2>Яндекс.Музыка мини</h2><p>Версия 2.8</p><p>Компактный виджет для Яндекс.Музыки</p><div class="info-user" id="infoUserSettings"></div><div class="developer">Разработчик: @frizzylow</div></div></div>
    </div>
    <div id="contextMenu" class="context-menu"><div class="context-menu-item" id="contextLike"><i class="fas fa-heart"></i> <span>Лайкнуть</span></div><div class="context-menu-item" id="contextDislike"><i class="fas fa-thumbs-down"></i> Дизлайкнуть</div><div class="context-menu-item" id="contextRadio"><i class="fas fa-wave-square"></i> Радио по треку</div><div class="context-menu-item" id="contextAddToPlaylist"><i class="fas fa-plus-circle"></i> Добавить в плейлист</div></div>
    <div id="playlistContextMenu" class="playlist-context-menu"><div id="playlistContextList" style="max-height:300px; overflow-y:auto;"></div></div>
    <div id="toast" class="toast"></div>
    <div id="waveSettingsOverlay" class="settings-overlay">
    <div class="login-box" style="max-width:420px; padding:20px; position:relative;">
        <div class="settings-header" style="display: flex; align-items: center; justify-content: space-between; margin-bottom: 20px; padding-bottom: 0; border-bottom: none;">
            <h3 style="font-size: 16px; font-weight: 500; margin: 0; line-height: 1.2;">Настройка Моей волны</h3>
            <div style="display: flex; align-items: center; gap: 8px;">
                <button id="resetWaveSettings" class="corner-btn" style="width: 28px; height: 28px; font-size: 14px; background: none; border: none; display: flex; align-items: center; justify-content: center; cursor: pointer;" title="Сбросить настройки"><i class="fas fa-undo-alt"></i></button>
                <button class="close-settings" id="closeWaveSettings" style="background: none; border: none; color: inherit; font-size: 18px; cursor: pointer; display: flex; align-items: center; justify-content: center;"><i class="fas fa-times"></i></button>
            </div>
        </div>
        <div class="setting-group">
            <label class="setting-label"></i> Разнообразие</label>
            <div class="diversity-options">
                <div data-value="favorite" class="option" title="Любимые треки">
                    <span class="icon">❤️</span>
                    <span>Любимое</span>
                </div>
                <div data-value="popular" class="option" title="Популярное">
                    <span class="icon">⭐</span>
                    <span>Популярное</span>
                </div>
                <div data-value="discover" class="option" title="Незнакомое">
                    <span class="icon">✨</span>
                    <span>Незнакомое</span>
                </div>
            </div>
        </div>
        <div class="setting-group">
            <label class="setting-label"></i> Настроение и энергия</label>
            <div class="mood-options">
                <div data-value="active" class="mood" title="Энергичное">
                    <div class="circle energetic"></div>
                    <span>Бодрое</span>
                </div>
                <div data-value="happy" class="mood" title="Весёлое">
                    <div class="circle fun"></div>
                    <span>Весёлое</span>
                </div>
                <div data-value="calm" class="mood" title="Спокойное">
                    <div class="circle calm"></div>
                    <span>Спокойное</span>
                </div>
                <div data-value="sad" class="mood" title="Грустное">
                    <div class="circle sad"></div>
                    <span>Грустное</span>
                </div>
            </div>
        </div>
        <div class="setting-group">
            <label class="setting-label"></i> Язык</label>
            <div class="language-options">
                <button data-value="russian" class="lang-btn">Русский</button>
                <button data-value="not-russian" class="lang-btn">Иностранный</button>
                <button data-value="without-words" class="lang-btn">Без слов</button>
            </div>
        </div>
    </div>
</div>
<div id="profileContextMenu" class="context-menu">
    <div class="context-menu-item" id="profileLogout"><i class="fas fa-sign-out-alt"></i> Выйти</div>
    <div class="context-menu-item" id="profileYandexId"><i class="fas fa-id-card"></i> Яндекс ID</div>
</div>
    <script>
        let currentState = {}, userName = '', userAvatar = '', playlists = [], seeking = false, recordingAction = null, tempKeys = new Set(), contextTrackId = null, contextPlaylistId = null, statusInterval = null;
        let volumeChanging = false;
        let likeUpdating = false;
        let wasAuthenticated = false; // для отслеживания изменений статуса
        const DEFAULT_AVATAR = 'https://avatars.yandex.net/get-yapic/0/0-200';
        const globalLoader = document.getElementById('globalLoader');
        const profileContextMenu = document.getElementById('profileContextMenu');

        // Функция обновления состояния кнопки сброса
        function updateResetButtonState() {
            const resetBtn = document.getElementById('resetWaveSettings');
            if (!resetBtn) return;
            const anySelected = document.querySelector('.diversity-options .option.selected') !== null ||
                                document.querySelector('.mood-options .mood.selected') !== null ||
                                document.querySelector('.language-options .lang-btn.selected') !== null;
            if (anySelected) {
                resetBtn.classList.add('active');
            } else {
                resetBtn.classList.remove('active');
            }
        }

        (function() {
            const titleBar = document.getElementById('titleBar');
            let dragTimer = null;
            let isDragging = false;
            let startX, startY;
            const DRAG_DELAY = 350;

            titleBar.addEventListener('mousedown', function(e) {
                if (e.target.closest('.window-controls')) return;
                startX = e.clientX;
                startY = e.clientY;
                dragTimer = setTimeout(() => {
                    titleBar.classList.add('dragging');
                    isDragging = true;
                }, DRAG_DELAY);
            });

            titleBar.addEventListener('mousemove', function(e) {
                if (!dragTimer) return;
                if (Math.abs(e.clientX - startX) > 5 || Math.abs(e.clientY - startY) > 5) {
                    clearTimeout(dragTimer);
                    dragTimer = null;
                }
            });

            titleBar.addEventListener('mouseup', function() {
                if (dragTimer) { clearTimeout(dragTimer); dragTimer = null; }
                if (isDragging) { titleBar.classList.remove('dragging'); isDragging = false; }
            });

            titleBar.addEventListener('mouseleave', function() {
                if (dragTimer) { clearTimeout(dragTimer); dragTimer = null; }
                if (isDragging) { titleBar.classList.remove('dragging'); isDragging = false; }
            });
        })();

        function applyTheme(darkMode) { darkMode ? document.body.classList.add('dark-theme') : document.body.classList.remove('dark-theme'); }
        function getRandomGreeting() { const now=new Date(), hour=now.getHours(), day=now.getDay(); let timePeriod=hour>=5&&hour<12?'morning':hour>=12&&hour<18?'day':hour>=18&&hour<23?'evening':'night'; const isWeekend=day===0||day===6; const greetings={morning:['Доброе утро, {name}! ☀️','Утро начинается с музыки, {name}! 🎵','Хорошего дня, {name}!','Просыпайся, {name}, музыка уже ждёт!'],day:['Добрый день, {name}! 🌞','Отличного дня, {name}! 🎶','Музыка скрасит твой день, {name}!','Работай и слушай, {name}!'],evening:['Добрый вечер, {name}! 🌆','Вечер в компании музыки, {name}!','Расслабься, {name}, музыка ждёт!','Прекрасного вечера, {name}!'],night:['Доброй ночи, {name}! 🌙','Музыка под звёздами, {name}!','Спокойной ночи, {name}!','Ночная музыка для тебя, {name}!']}; let pool=greetings[timePeriod]||greetings.day; if(isWeekend) pool=pool.concat(['Отличных выходных, {name}! 🎉','Наслаждайся выходными, {name}!','Выходные с музыкой — лучше, {name}!']); return pool[Math.floor(Math.random()*pool.length)].replace('{name}',userName); }
        function showWelcomeToast() { if(userName) showToast(getRandomGreeting(),5000); }
        function formatTime(ms) { if(!ms||ms<0) return "0:00"; let totalSeconds=Math.floor(ms/1000), minutes=Math.floor(totalSeconds/60), seconds=totalSeconds%60; return minutes+":"+(seconds<10?"0"+seconds:seconds); }
        function formatHotkey(hotkey) { return hotkey?hotkey.split('+').map(p=>p.trim()):[]; }
        function renderKeyBindings() { const container=document.getElementById('keyBindings'), bindings=currentState.key_bindings||{}; const actions=[{id:'play_pause',label:'Play/Pause'},{id:'next',label:'Следующий'},{id:'prev',label:'Предыдущий'},{id:'like',label:'Лайк'},{id:'dislike',label:'Дизлайк'},{id:'minimize',label:'Скрыть/Показать'},{id:'volume_up',label:'Громкость +10'},{id:'volume_down',label:'Громкость -10'}]; let html=''; actions.forEach(action=>{ const hotkey=bindings[action.id]||'', parts=formatHotkey(hotkey), blocksHtml=parts.map(p=>`<span class="key-block">${p}</span>`).join(''), isRecording=recordingAction===action.id, btnText=isRecording?'Нажмите...':'Записать', btnClass=isRecording?'record-btn recording':'record-btn'; html+=`<div class="key-row"><label>${action.label}</label><div class="key-badge" id="key-badge-${action.id}">${blocksHtml}</div><button class="${btnClass}" data-action="${action.id}" id="record-${action.id}">${btnText}</button></div>`; }); container.innerHTML=html; actions.forEach(action=>{ document.getElementById(`record-${action.id}`).addEventListener('click',function(){ if(recordingAction===action.id){ recordingAction=null; this.classList.remove('recording'); this.innerText='Записать'; tempKeys.clear(); }else{ if(recordingAction){ document.getElementById(`record-${recordingAction}`).classList.remove('recording'); document.getElementById(`record-${recordingAction}`).innerText='Записать'; } recordingAction=action.id; this.classList.add('recording'); this.innerText='Нажмите...'; tempKeys.clear(); } }); }); }
        document.addEventListener('keydown',function(e){ if(!recordingAction) return; e.preventDefault(); e.stopPropagation(); const key=e.key.toLowerCase(); if(['control','shift','alt','meta'].includes(key)) tempKeys.add(key); else{ let mappedKey=key; if(key===' ') mappedKey='space'; else if(key==='arrowleft') mappedKey='left'; else if(key==='arrowright') mappedKey='right'; else if(key==='arrowup') mappedKey='up'; else if(key==='arrowdown') mappedKey='down'; tempKeys.add(mappedKey); } });
        document.addEventListener('keyup',function(e){ if(!recordingAction) return; e.preventDefault(); e.stopPropagation(); const key=e.key.toLowerCase(); const hasNonModifier=[...tempKeys].some(k=>!['control','shift','alt','meta'].includes(k)); if(hasNonModifier||!['control','shift','alt','meta'].includes(key)){ const modifiers=[]; if(tempKeys.has('control')) modifiers.push('ctrl'); if(tempKeys.has('shift')) modifiers.push('shift'); if(tempKeys.has('alt')) modifiers.push('alt'); if(tempKeys.has('meta')||tempKeys.has('win')) modifiers.push('win'); const others=[...tempKeys].filter(k=>!['control','shift','alt','meta','win','ctrl'].includes(k)); const combo=[...modifiers,...others].join('+'); document.getElementById(`key-badge-${recordingAction}`).innerHTML=combo.split('+').map(p=>`<span class="key-block">${p}</span>`).join(''); const data={}; data[recordingAction]=combo; fetch('/api/set_key_bindings',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(data)}); const btn=document.getElementById(`record-${recordingAction}`); btn.classList.remove('recording'); btn.innerText='Записать'; recordingAction=null; tempKeys.clear(); }else{ if(key==='control') tempKeys.delete('control'); else if(key==='shift') tempKeys.delete('shift'); else if(key==='alt') tempKeys.delete('alt'); else if(key==='meta') tempKeys.delete('meta'); } });
        function showToast(message, duration=4000) { const toast=document.getElementById('toast'); toast.textContent=message; toast.classList.add('show'); setTimeout(()=>toast.classList.remove('show'),duration); }

        function updateUI() {
            const track=currentState.current_track;
            if(track){
                document.getElementById('trackTitle').innerText=track.title||'Без названия';
                document.getElementById('trackArtist').innerText=track.artists||'Неизвестный';
                const coverImg=document.getElementById('coverImg'), coverPlaceholder=document.getElementById('coverPlaceholder');
                if(track.cover){ coverImg.src=track.cover; coverImg.style.display='block'; coverPlaceholder.style.display='none'; }else{ coverImg.style.display='none'; coverPlaceholder.style.display='flex'; }
                if (!likeUpdating) {
                    document.getElementById('likeBtn').innerHTML=track.liked?'<i class="fas fa-heart" style="color:#fed42a;"></i>':'<i class="far fa-heart"></i>';
                }
            }else{
                document.getElementById('trackTitle').innerText='Моя волна';
                document.getElementById('trackArtist').innerText='Яндекс.Музыка';
                document.getElementById('coverImg').style.display='none';
                document.getElementById('coverPlaceholder').style.display='flex';
            }
            document.getElementById('playPauseBtn').innerHTML=currentState.playing?'<i class="fas fa-pause"></i>':'<i class="fas fa-play"></i>';
            if(!seeking){
                const pos=currentState.position||0, dur=currentState.duration||0;
                document.getElementById('currentTime').innerText=formatTime(pos);
                document.getElementById('totalTime').innerText=formatTime(dur);
                if(dur>0) document.getElementById('progressSlider').value=(pos/dur)*100;
                else document.getElementById('progressSlider').value=0;
            }
            document.getElementById('prevBtn').disabled=!(currentState.has_prev&&!currentState.is_single_track);
            document.getElementById('nextBtn').disabled=!(currentState.has_next&&!currentState.is_single_track);
            if (!volumeChanging) {
                document.getElementById('volumeSlider').value=currentState.volume;
            }
            document.getElementById('bitrateSelect').value=currentState.bitrate;
            document.getElementById('equalizerSelect').value=currentState.equalizer_preset;
            document.getElementById('equalizerEnable').checked=currentState.equalizer_enabled;
            document.getElementById('autoStartCheck').checked=currentState.auto_start;
            document.getElementById('autoPlayCheck').checked=currentState.auto_play;
            document.getElementById('darkModeCheck').checked=currentState.dark_mode;
            applyTheme(currentState.dark_mode);
            renderKeyBindings();

            const browserOverlay = document.getElementById('browserAuthOverlay');
            const loginOverlay = document.getElementById('loginOverlay');
            const loginRow = document.querySelector('.login-row');
            const helpLink = document.getElementById('helpTokenLink');

            if (currentState.browser_auth_in_progress) {
                browserOverlay.classList.add('active');
                browserOverlay.classList.remove('hidden');
                loginOverlay.classList.add('hidden');
                loginRow.style.display = 'none';
                helpLink.style.display = 'none';
                globalLoader.style.opacity = '0';
            } else if (!currentState.authenticated) {
                browserOverlay.classList.remove('active');
                browserOverlay.classList.add('hidden');
                loginOverlay.classList.add('active');
                loginOverlay.classList.remove('hidden');
                loginRow.style.display = 'flex';
                helpLink.style.display = 'block';
                globalLoader.style.opacity = '0';
            } else {
                browserOverlay.classList.remove('active');
                browserOverlay.classList.add('hidden');
                loginOverlay.classList.add('hidden');
            }

            if (currentState.authenticated) {
                document.getElementById('mainInterface').classList.remove('hidden');
                // Если только что авторизовались, показываем лоадер до загрузки первого трека
                if (!wasAuthenticated && currentState.authenticated) {
                    globalLoader.style.opacity = '1';
                    // Загружаем информацию о пользователе и плейлисты, но лоадер остаётся
                    loadUserInfo().then(() => {
                        // После загрузки информации о пользователе загружаем плейлисты
                        loadPlaylists();
                        // Не скрываем лоадер здесь — он скроется, когда появится трек
                    }).catch(() => {});
                }

                // Скрываем лоадер, когда появился первый трек
                if (currentState.current_track) {
                    globalLoader.style.opacity = '0';
                }
            }
            wasAuthenticated = currentState.authenticated;
        }

        async function fetchStatus() { try { const res=await fetch('/api/status'); currentState=await res.json(); updateUI(); } catch(e){ console.error('Ошибка получения статуса:',e); } }
        function action(endpoint, method='POST', body=null) { 
            const options={method}; 
            if(body){ 
                options.headers={'Content-Type':'application/json'}; 
                options.body=JSON.stringify(body); 
            } 
            fetch(endpoint, options).catch(e=>console.error('Ошибка действия:',e)); 
        }

        document.getElementById('playPauseBtn').onclick=()=>{
            currentState.playing = !currentState.playing;
            document.getElementById('playPauseBtn').innerHTML = currentState.playing ? '<i class="fas fa-pause"></i>' : '<i class="fas fa-play"></i>';
            action('/api/toggle');
        };

        document.getElementById('likeBtn').onclick=()=>{
            if(currentState.current_track) {
                likeUpdating = true;
                const newLiked = !currentState.current_track.liked;
                currentState.current_track.liked = newLiked;
                document.getElementById('likeBtn').innerHTML = newLiked ? '<i class="fas fa-heart" style="color:#fed42a;"></i>' : '<i class="far fa-heart"></i>';
                action('/api/like');
                showToast(newLiked ? 'Трек добавлен в избранное' : 'Трек удалён из избранного');
                setTimeout(() => { likeUpdating = false; }, 1000);
            }
        };

        document.getElementById('dislikeBtn').onclick=()=>{
            action('/api/dislike');
            showToast('Трек больше не будет появляться в рекомендациях');
        };

        document.getElementById('prevBtn').onclick=()=>action('/api/prev');
        document.getElementById('nextBtn').onclick=()=>action('/api/next');

        const progressSlider=document.getElementById('progressSlider'); 
        progressSlider.addEventListener('mousedown',()=>{seeking=true;}); 
        progressSlider.addEventListener('mouseup',()=>{ seeking=false; if(currentState.duration>0){ const pos=(progressSlider.value/100)*currentState.duration; action('/api/seek','POST',{position:Math.round(pos)}); } }); 
        progressSlider.addEventListener('touchstart',()=>{seeking=true;}); 
        progressSlider.addEventListener('touchend',()=>{ seeking=false; if(currentState.duration>0){ const pos=(progressSlider.value/100)*currentState.duration; action('/api/seek','POST',{position:Math.round(pos)}); } });

        async function loadUserInfo() {
            try {
                const res=await fetch('/api/user'), data=await res.json();
                userName=data.name||'Пользователь';
                userAvatar=data.avatar||DEFAULT_AVATAR;
                document.getElementById('userName').innerText=userName;
                document.getElementById('userAvatar').src=userAvatar;
                document.getElementById('infoUserSettings').innerHTML=`<img src="${userAvatar}" class="info-user-avatar" alt="avatar"> <span>${userName}</span>`;
                showWelcomeToast();
                return true;
            } catch(e){
                console.error('Ошибка загрузки информации о пользователе:',e);
                return false;
            }
        }
        async function loadPlaylists() { const loader=document.getElementById('playlistLoader'), container=document.getElementById('playlistContainer'); loader.style.display='flex'; container.innerHTML=''; try { const res=await fetch('/api/playlists'); playlists=await res.json(); playlists.forEach(pl=>{ const div=document.createElement('div'); div.className='playlist-item'; let coverHtml=pl.cover?`<img src="${pl.cover}" class="playlist-cover">`:`<div class="playlist-cover-placeholder"><i class="fas fa-music"></i></div>`; div.innerHTML=`${coverHtml}<div class="playlist-info"><div class="playlist-title">${pl.title}</div><div class="playlist-count">${pl.track_count||0} треков</div></div><button class="play-btn" data-kind="${pl.kind}"><i class="fas fa-play"></i> Воспроизвести</button>`; div.addEventListener('click',(e)=>{ if(e.target.classList.contains('play-btn')) return; openTracksView(pl.kind,pl.title); }); div.querySelector('.play-btn').addEventListener('click',(e)=>{ e.stopPropagation(); action('/api/play_playlist/'+pl.kind); document.getElementById('menuOverlay').classList.remove('active'); }); container.appendChild(div); }); } catch(e){ console.error('Ошибка загрузки плейлистов:',e); } finally { loader.style.display='none'; } }
        async function openTracksView(kind, title) { const loader=document.getElementById('tracksLoader'), container=document.getElementById('tracksContainer'); loader.style.display='flex'; container.innerHTML=''; document.querySelector('#tracksPlaylistTitle span').innerText=title; document.getElementById('tracksOverlay').classList.add('active'); try { const res=await fetch(`/api/playlist_tracks/${kind}`), tracks=await res.json(); tracks.forEach(track=>{ const div=document.createElement('div'); div.className='track-item'; div.setAttribute('data-track-id',track.id); div.setAttribute('data-playlist-id',kind); let coverHtml=track.cover?`<img src="${track.cover}" class="track-item-cover">`:`<div class="track-item-cover" style="background:#ddd; display:flex; align-items:center; justify-content:center;"><i class="fas fa-music" style="color:#aaa;"></i></div>`; div.innerHTML=`${coverHtml}<div class="track-item-info"><div class="track-item-title">${track.title}</div><div class="track-item-artist">${track.artists}</div></div><div class="track-actions"><button class="track-action-btn like-btn" data-track-id="${track.id}" data-liked="${track.liked}"><i class="fa${track.liked?'s':'r'} fa-heart" style="color:${track.liked?'#aaa':'#aaa'};"></i></button><button class="track-action-btn dislike-btn" data-track-id="${track.id}"><i class="fas fa-thumbs-down"></i></button></div>`; div.addEventListener('click',(e)=>{ if(e.target.closest('.track-action-btn')) return; fetch('/api/play_specific_track',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({id:track.id,playlist_id:kind})}).then(()=>{ document.getElementById('tracksOverlay').classList.remove('active'); fetchStatus(); }); }); const likeBtn=div.querySelector('.like-btn'); likeBtn.addEventListener('click',(e)=>{ e.stopPropagation(); const newLiked=!track.liked; fetch('/api/track_action',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({action:newLiked?'like':'unlike',track_id:track.id})}).then(res=>res.json()).then(data=>{ if(data.success){ track.liked=newLiked; likeBtn.innerHTML=`<i class="fa${newLiked?'s':'r'} fa-heart" style="color:${newLiked?'#aaa':'#aaa'};"></i>`; likeBtn.setAttribute('data-liked',newLiked); showToast(newLiked?'Трек добавлен в избранное':'Трек удалён из избранного'); } }); }); const dislikeBtn=div.querySelector('.dislike-btn'); dislikeBtn.addEventListener('click',(e)=>{ e.stopPropagation(); fetch('/api/track_action',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({action:'dislike',track_id:track.id})}).then(res=>res.json()).then(data=>{ if(data.success){ showToast('Трек больше не будет появляться в рекомендациях'); if(kind==3) div.remove(); } }); }); div.addEventListener('contextmenu',(e)=>{ e.preventDefault(); contextTrackId=track.id; contextPlaylistId=kind; showContextMenu(e,contextTrackId); }); container.appendChild(div); }); } catch(e){ console.error('Ошибка загрузки треков:',e); } finally { loader.style.display='none'; } }

        function showContextMenu(e, trackId) {
            e.preventDefault(); e.stopPropagation();
            contextTrackId = trackId;
            const menu = document.getElementById('contextMenu');
            let left = e.pageX, top = e.pageY;
            const menuWidth = 200, menuHeight = menu.scrollHeight || 200;
            const winWidth = window.innerWidth, winHeight = window.innerHeight;
            if (left + menuWidth > winWidth) left = winWidth - menuWidth - 5;
            if (top + menuHeight > winHeight) top = winHeight - menuHeight - 5;
            left = Math.max(5, left);
            top = Math.max(5, top);
            menu.style.left = left + 'px';
            menu.style.top = top + 'px';
            menu.classList.add('active');
        }

        document.getElementById('contextLike').addEventListener('click',(e)=>{ e.stopPropagation(); if(!contextTrackId) return; fetch('/api/track_action',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({action:'like',track_id:contextTrackId})}).then(res=>res.json()).then(data=>{ if(data.success){ showToast('Трек добавлен в избранное'); document.getElementById('contextMenu').classList.remove('active'); fetchStatus(); } }); });
        document.getElementById('contextDislike').addEventListener('click',()=>{ if(!contextTrackId) return; fetch('/api/track_action',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({action:'dislike',track_id:contextTrackId})}).then(res=>res.json()).then(data=>{ if(data.success){ showToast('Трек больше не будет появляться в рекомендациях'); document.getElementById('contextMenu').classList.remove('active'); fetchStatus(); } }); });
        document.getElementById('contextAddToPlaylist').addEventListener('click',(e)=>{ e.preventDefault(); e.stopPropagation(); if(!contextTrackId) return; showPlaylistContextMenu(e, contextTrackId); });
        function showPlaylistContextMenu(event, trackId) { event.preventDefault(); event.stopPropagation(); if(!trackId) return; const menu=document.getElementById('playlistContextMenu'), list=document.getElementById('playlistContextList'); list.innerHTML='<div class="loader-container" style="padding:20px;"><div class="loader"></div></div>'; let left=event.pageX, top=event.pageY; const winWidth=window.innerWidth, winHeight=window.innerHeight; if(left+220>winWidth) left=winWidth-220-5; if(top+300>winHeight) top=winHeight-300-5; left=Math.max(5,left); top=Math.max(5,top); menu.style.left=left+'px'; menu.style.top=top+'px'; menu.classList.add('active'); fetch('/api/playlists').then(res=>res.json()).then(playlists=>{ list.innerHTML=''; const header=document.createElement('div'); header.className='playlist-context-header'; header.innerText='Выберите плейлист'; list.appendChild(header); playlists.forEach(pl=>{ if(pl.kind==3) return; const item=document.createElement('div'); item.className='playlist-context-item'; item.innerHTML=`<i class="fas fa-list"></i> ${pl.title}`; item.addEventListener('click',(e)=>{ e.preventDefault(); e.stopPropagation(); showToast('Добавляем в плейлист...'); fetch('/api/add_to_playlist',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({track_id:trackId, playlist_kind:pl.kind})}).then(res=>res.json()).then(data=>{ if(data.success) showToast('Трек добавлен в плейлист'); else showToast('Ошибка добавления'); }).catch(err=>{ console.error('Error adding to playlist:',err); showToast('Ошибка соединения'); }).finally(()=>{ menu.classList.remove('active'); document.getElementById('contextMenu').classList.remove('active'); }); }); list.appendChild(item); }); }).catch(err=>{ list.innerHTML='<div style="padding:20px; text-align:center;">Ошибка загрузки плейлистов</div>'; console.error('Error loading playlists:',err); menu.classList.remove('active'); }); }
        function closeMenus(e) { const contextMenu=document.getElementById('contextMenu'), playlistContextMenu=document.getElementById('playlistContextMenu'); if(contextMenu.classList.contains('active')&&!contextMenu.contains(e.target)){ contextMenu.classList.remove('active'); } if(playlistContextMenu.classList.contains('active')&&!playlistContextMenu.contains(e.target)){ playlistContextMenu.classList.remove('active'); } if(profileContextMenu.classList.contains('active')&&!profileContextMenu.contains(e.target)&&!userHeader.contains(e.target)){ profileContextMenu.classList.remove('active'); } }
        document.addEventListener('mousedown', closeMenus); document.addEventListener('click', closeMenus); document.addEventListener('contextmenu',(e)=>{ closeMenus(e); if(!e.target.closest('#contextMenu')&&!e.target.closest('#playlistContextMenu')) e.preventDefault(); });
        document.addEventListener('keydown',(e)=>{ if(e.key==='Escape'){ document.getElementById('contextMenu').classList.remove('active'); document.getElementById('playlistContextMenu').classList.remove('active'); profileContextMenu.classList.remove('active'); } });

        const searchInput=document.getElementById('searchInput'); let searchTimeout=null; searchInput.addEventListener('input',()=>{ clearTimeout(searchTimeout); const query=searchInput.value.trim(); if(query.length<2) return; searchTimeout=setTimeout(()=>performSearch(query),500); });
        async function performSearch(query) { const loader=document.getElementById('searchLoader'), container=document.getElementById('searchResults'); loader.style.display='flex'; container.innerHTML=''; try { const res=await fetch('/api/search?q='+encodeURIComponent(query)), tracks=await res.json(); tracks.forEach(track=>{ const div=document.createElement('div'); div.className='search-item track-item'; div.setAttribute('data-track-id',track.id); let coverHtml=track.cover?`<img src="${track.cover}" class="track-item-cover">`:`<div class="track-item-cover" style="background:#ddd; display:flex; align-items:center; justify-content:center;"><i class="fas fa-music" style="color:#aaa;"></i></div>`; div.innerHTML=`${coverHtml}<div class="track-item-info"><div class="track-item-title">${track.title}</div><div class="track-item-artist">${track.artists}</div></div><div class="track-actions"><button class="track-action-btn like-btn" data-track-id="${track.id}" data-liked="${track.liked}"><i class="fa${track.liked?'s':'r'} fa-heart" style="color:${track.liked?'#aaa':'#aaa'};"></i></button></div>`; div.addEventListener('click',(e)=>{ if(e.target.closest('.track-action-btn')) return; fetch('/api/play_specific_track',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({id:track.id,playlist_id:0})}).then(()=>{ document.getElementById('searchOverlay').classList.remove('active'); fetchStatus(); }); }); const likeBtn=div.querySelector('.like-btn'); likeBtn.addEventListener('click',(e)=>{ e.stopPropagation(); const newLiked=!track.liked; fetch('/api/track_action',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({action:newLiked?'like':'unlike',track_id:track.id})}).then(res=>res.json()).then(data=>{ if(data.success){ track.liked=newLiked; likeBtn.innerHTML=`<i class="fa${newLiked?'s':'r'} fa-heart" style="color:${newLiked?'#aaa':'#aaa'};"></i>`; likeBtn.setAttribute('data-liked',newLiked); showToast(newLiked?'Трек добавлен в избранное':'Трек удалён из избранного'); } }); }); div.addEventListener('contextmenu',(e)=>{ e.preventDefault(); e.stopPropagation(); contextTrackId=track.id; contextPlaylistId=0; showContextMenu(e,contextTrackId); }); container.appendChild(div); }); } catch(e){ console.error('Ошибка поиска:',e); } finally { loader.style.display='none'; } }
        document.getElementById('minimizeWindow').addEventListener('click',()=>{ if(window.pywebview&&window.pywebview.api&&window.pywebview.api.minimize) window.pywebview.api.minimize(); else console.log('Minimize not available'); });
        document.getElementById('closeWindow').addEventListener('click',()=>{ if(window.pywebview&&window.pywebview.api&&window.pywebview.api.close) window.pywebview.api.close(); else console.log('Close not available'); });
        document.getElementById('menuBtn').onclick=()=>document.getElementById('menuOverlay').classList.add('active');
        document.getElementById('closeMenuBtn').onclick=()=>document.getElementById('menuOverlay').classList.remove('active');
        const waveItem = document.getElementById('waveMenuItem');
        waveItem.style.display = 'flex';
        waveItem.style.alignItems = 'center';
        waveItem.style.gap = '8px';
        waveItem.innerHTML += `<button class="play-btn" id="waveSettingsBtn" style="font-size:10px; padding:3px 8px;">Настроить</button>`;

        waveItem.addEventListener('click', function(e) {
            if (e.target.closest('#waveSettingsBtn')) return;
            fetch('/api/play_wave', {method:'POST'});
            document.getElementById('menuOverlay').classList.remove('active');
        });

        // При открытии окна настроек волны загружаем сохранённые значения
        document.getElementById('waveSettingsBtn').addEventListener('click', function(e) {
            e.stopImmediatePropagation();
            e.preventDefault();

            // Устанавливаем выбранные опции из currentState
            document.querySelectorAll('.diversity-options .option, .mood-options .mood, .language-options .lang-btn').forEach(el => {
                el.classList.remove('selected');
            });

            if (currentState.wave_diversity) {
                const divOpt = document.querySelector(`.diversity-options .option[data-value="${currentState.wave_diversity}"]`);
                if (divOpt) divOpt.classList.add('selected');
            }
            if (currentState.wave_mood) {
                const moodOpt = document.querySelector(`.mood-options .mood[data-value="${currentState.wave_mood}"]`);
                if (moodOpt) moodOpt.classList.add('selected');
            }
            if (currentState.wave_language) {
                const langBtn = document.querySelector(`.language-options .lang-btn[data-value="${currentState.wave_language}"]`);
                if (langBtn) langBtn.classList.add('selected');
            }

            updateResetButtonState();
            document.getElementById('waveSettingsOverlay').classList.add('active');
        });

        document.getElementById('closeWaveSettings').addEventListener('click', function() {
            document.getElementById('waveSettingsOverlay').classList.remove('active');
        });

        function resetWaveSettingsToDefault() {
            document.querySelectorAll('.diversity-options .option, .mood-options .mood, .language-options .lang-btn').forEach(el => {
                el.classList.remove('selected');
            });
            // Ничего не выбираем — все настройки сбрасываются (сервер сам будет выбирать)
            updateResetButtonState();
        }

        document.getElementById('resetWaveSettings').addEventListener('click', function() {
            resetWaveSettingsToDefault();
            saveWaveSettings();
        });

        const diversityOptions = document.querySelectorAll('.diversity-options .option');
        diversityOptions.forEach(opt => {
            opt.addEventListener('click', () => {
                diversityOptions.forEach(o => o.classList.remove('selected'));
                opt.classList.add('selected');
                updateResetButtonState();
                saveWaveSettings();
            });
        });

        const moodOptions = document.querySelectorAll('.mood-options .mood');
        moodOptions.forEach(m => {
            m.addEventListener('click', () => {
                moodOptions.forEach(o => o.classList.remove('selected'));
                m.classList.add('selected');
                updateResetButtonState();
                saveWaveSettings();
            });
        });

        const languageButtons = document.querySelectorAll('.language-options .lang-btn');
        languageButtons.forEach(btn => {
            btn.addEventListener('click', () => {
                languageButtons.forEach(b => b.classList.remove('selected'));
                btn.classList.add('selected');
                updateResetButtonState();
                saveWaveSettings();
            });
        });

        function saveWaveSettings() {
            const moodEnergy = document.querySelector('.mood-options .mood.selected')?.dataset.value || null;
            const diversity = document.querySelector('.diversity-options .option.selected')?.dataset.value || null;
            const language = document.querySelector('.language-options .lang-btn.selected')?.dataset.value || null;

            const settings = {};
            if (moodEnergy) settings.mood_energy = moodEnergy;
            if (diversity) settings.diversity = diversity;
            if (language) settings.language = language;

            fetch('/api/set_wave_settings', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify(settings)
            })
            .then(res => res.json())
            .then(data => {
                if (data.success) {
                } else {
                    showToast('❌ Ошибка сохранения', 2000);
                }
                fetchStatus();
            })
            .catch(() => showToast('❌ Ошибка соединения', 2000));
        }
        document.getElementById('contextRadio').addEventListener('click', (e) => { e.stopImmediatePropagation(); if(!contextTrackId) return; document.getElementById('contextMenu').classList.remove('active'); document.getElementById('contextMenu').style.display='none'; document.getElementById('tracksOverlay').classList.remove('active'); document.getElementById('searchOverlay').classList.remove('active'); document.getElementById('menuOverlay').classList.remove('active'); document.getElementById('waveSettingsOverlay').style.display='none'; showToast('🎵 Запускаем радио по треку...',2500); fetch('/api/play_radio_from_track',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({track_id:contextTrackId})}).then(()=>fetchStatus()); });
        document.getElementById('closeTracksBtn').onclick=()=>document.getElementById('tracksOverlay').classList.remove('active');
        document.getElementById('searchBtn').onclick=()=>{ document.getElementById('searchOverlay').classList.add('active'); document.getElementById('searchInput').focus(); };
        document.getElementById('closeSearchBtn').onclick=()=>document.getElementById('searchOverlay').classList.remove('active');
        document.getElementById('settingsBtn').onclick=()=>document.getElementById('settingsOverlay').classList.add('active');
        document.getElementById('closeSettingsBtn').onclick=()=>document.getElementById('settingsOverlay').classList.remove('active');
        document.getElementById('helpTokenLink').onclick=(e)=>{ 
            e.preventDefault(); 
            document.getElementById('loginOverlay').classList.add('hidden');
            document.getElementById('tokenHelpOverlay').classList.add('active'); 
        };
        document.getElementById('closeTokenHelpBtn').onclick=()=>{
            document.getElementById('tokenHelpOverlay').classList.remove('active');
            document.getElementById('loginOverlay').classList.remove('hidden');
        };
        let volumeTimeout;
        document.getElementById('volumeSlider').addEventListener('input',(e)=>{
            const vol = parseInt(e.target.value);
            volumeChanging = true;
            document.getElementById('volumeSlider').value = vol;
            if (volumeTimeout) clearTimeout(volumeTimeout);
            volumeTimeout = setTimeout(() => {
                action('/api/set_volume','POST',{volume:vol});
                setTimeout(() => { volumeChanging = false; }, 200);
            }, 20);
        });
        document.getElementById('bitrateSelect').addEventListener('change',(e)=>action('/api/set_bitrate','POST',{bitrate:parseInt(e.target.value)}));
        document.getElementById('equalizerSelect').addEventListener('change',(e)=>{ const preset=e.target.value, enabled=document.getElementById('equalizerEnable').checked; action('/api/set_equalizer','POST',{preset,enabled}); });
        document.getElementById('equalizerEnable').addEventListener('change',(e)=>{ const enabled=e.target.checked, preset=document.getElementById('equalizerSelect').value; action('/api/set_equalizer','POST',{preset,enabled}); });
        document.getElementById('autoStartCheck').addEventListener('change',(e)=>action('/api/set_auto_start','POST',{enabled:e.target.checked}));
        document.getElementById('autoPlayCheck').addEventListener('change',(e)=>action('/api/set_auto_play','POST',{enabled:e.target.checked}));
        document.getElementById('darkModeCheck').addEventListener('change',(e)=>{ const enabled=e.target.checked; action('/api/set_dark_mode','POST',{enabled}); applyTheme(enabled); });

        // Добавляем обработчик контекстного меню на обложку и область трека в главном окне
        document.getElementById('cover').addEventListener('contextmenu', function(e) {
            e.preventDefault();
            e.stopPropagation();
            if (currentState.current_track) {
                contextTrackId = currentState.current_track.id;
                contextPlaylistId = currentState.current_playlist_id;
                showContextMenu(e, contextTrackId);
            }
        });

        document.getElementById('trackTitle').addEventListener('contextmenu', function(e) {
            e.preventDefault();
            e.stopPropagation();
            if (currentState.current_track) {
                contextTrackId = currentState.current_track.id;
                contextPlaylistId = currentState.current_playlist_id;
                showContextMenu(e, contextTrackId);
            }
        });

        document.getElementById('trackArtist').addEventListener('contextmenu', function(e) {
            e.preventDefault();
            e.stopPropagation();
            if (currentState.current_track) {
                contextTrackId = currentState.current_track.id;
                contextPlaylistId = currentState.current_playlist_id;
                showContextMenu(e, contextTrackId);
            }
        });

        document.getElementById('loginBtn').onclick=async ()=>{
            const token=document.getElementById('tokenInput').value.trim();
            if(!token) return;

            globalLoader.style.opacity = '1';  // только один глобальный лоадер

            try {
                const res=await fetch('/api/login',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({token})});
                if(res.ok){
                    document.getElementById('loginOverlay').classList.add('hidden');
                    // Лоадер остаётся до появления трека
                } else {
                    showToast('Ошибка входа: неверный токен', 3000);
                    globalLoader.style.opacity = '0';
                }
            } catch(e){
                showToast('Ошибка соединения', 3000);
                globalLoader.style.opacity = '0';
            }
        };

        document.getElementById('retryBrowserAuthBtn').addEventListener('click', () => {
            fetch('/api/retry_browser_auth', { method: 'POST' })
                .then(() => showToast('Повторная попытка открыть браузер...', 2000))
                .catch(() => showToast('Ошибка', 2000));
        });

        document.getElementById('switchToTokenAuth').addEventListener('click', () => {
            fetch('/api/cancel_browser_auth', { method: 'POST' })
                .then(() => {
                    document.getElementById('browserAuthOverlay').classList.remove('active');
                    document.getElementById('browserAuthOverlay').classList.add('hidden');
                    document.getElementById('loginOverlay').classList.add('active');
                    document.getElementById('loginOverlay').classList.remove('hidden');
                    showToast('Переход к ручному вводу токена', 2000);
                })
                .catch(() => showToast('Ошибка отмены авторизации', 2000));
        });

        statusInterval = setInterval(fetchStatus, 500);

        (async () => {
            try {
                const res = await fetch('/api/status');
                currentState = await res.json();
                updateUI();
                if (currentState.authenticated) {
                    document.getElementById('mainInterface').classList.remove('hidden');
                    // Если уже есть трек, скрываем лоадер, иначе он останется до появления трека
                    if (currentState.current_track) {
                        globalLoader.style.opacity = '0';
                    } else {
                        globalLoader.style.opacity = '1';
                        loadUserInfo().then(() => loadPlaylists());
                    }
                } else {
                    globalLoader.style.opacity = '0';
                }
            } catch (e) {
                console.error('Ошибка при начальной проверке статуса:', e);
                globalLoader.style.opacity = '0';
            }
        })();

        const userHeader = document.getElementById('userHeader');
        const profileLogout = document.getElementById('profileLogout');
        const profileYandexId = document.getElementById('profileYandexId');

        function showProfileMenu(event) {
            document.getElementById('contextMenu').classList.remove('active');
            document.getElementById('playlistContextMenu').classList.remove('active');

            const rect = userHeader.getBoundingClientRect();
            let left = rect.left;
            let top = rect.bottom + 5;

            const menuWidth = 150;
            const menuHeight = profileContextMenu.scrollHeight || 80;

            if (left + menuWidth > window.innerWidth) {
                left = window.innerWidth - menuWidth - 5;
            }
            if (top + menuHeight > window.innerHeight) {
                top = rect.top - menuHeight - 5;
            }

            profileContextMenu.style.left = left + 'px';
            profileContextMenu.style.top = top + 'px';
            profileContextMenu.classList.add('active');
        }

        function hideProfileMenu() {
            profileContextMenu.classList.remove('active');
        }

        userHeader.addEventListener('click', function(e) {
            e.stopPropagation();
            if (profileContextMenu.classList.contains('active')) {
                hideProfileMenu();
            } else {
                showProfileMenu(e);
            }
        });

        profileLogout.addEventListener('click', function() {
            fetch('/api/logout', { method: 'POST' })
                .then(res => res.json())
                .then(data => {
                    if (data.success) {
                        showToast('Вы вышли из аккаунта', 2000);
                        hideProfileMenu();

                        document.getElementById('mainInterface').classList.add('hidden');
                        document.getElementById('browserAuthOverlay').classList.remove('hidden');
                        document.getElementById('browserAuthOverlay').classList.add('active');
                        document.getElementById('loginOverlay').classList.add('hidden');

                        fetch('/api/retry_browser_auth', { method: 'POST' })
                            .catch(() => showToast('Ошибка запуска браузера', 2000));

                        fetchStatus();
                    } else {
                        showToast('Ошибка при выходе', 2000);
                    }
                })
                .catch(() => showToast('Ошибка соединения', 2000));
        });

        profileYandexId.addEventListener('click', function() {
            window.open('https://id.yandex.ru/', '_blank');
            hideProfileMenu();
        });
    </script>
</body>
</html>
"""


# ---------- Flask endpoints ----------
@app.route('/')
def index():
    return HTML_TEMPLATE


@app.route('/api/login', methods=['POST'])
def login():
    data = request.get_json()
    return jsonify({'success': player.set_token(data.get('token'))})


@app.route('/api/cancel_browser_auth', methods=['POST'])
def cancel_browser_auth():
    global cancel_auth
    cancel_auth = True
    with player.auth_lock:
        player.auth_threads = 0
    return jsonify({'success': True})


@app.route('/api/set_wave_settings', methods=['POST'])
def set_wave_settings():
    if not player.client:
        return jsonify({'success': False}), 401
    data = request.get_json() or {}
    # Если значение не передано, будет None
    success = player.set_wave_settings(data.get('mood_energy'), data.get('diversity'), data.get('language'))
    return jsonify({'success': success})


@app.route('/api/status')
def status():
    return jsonify(player.get_status())


@app.route('/api/user')
def user():
    logger.debug("GET /api/user called")
    return jsonify(player.get_user_info())


@app.route('/api/toggle', methods=['POST'])
def toggle():
    player.pause()
    return jsonify({'success': True})


@app.route('/api/next', methods=['POST'])
def next_track():
    player.next()
    return jsonify({'success': True})


@app.route('/api/prev', methods=['POST'])
def prev_track():
    player.prev()
    return jsonify({'success': True})


@app.route('/api/like', methods=['POST'])
def like():
    player.like_current()
    return jsonify({'success': True})


@app.route('/api/dislike', methods=['POST'])
def dislike():
    player.dislike_current()
    return jsonify({'success': True})


@app.route('/api/seek', methods=['POST'])
def seek():
    data = request.get_json()
    if data and 'position' in data:
        player.seek(data['position'])
        return jsonify({'success': True})
    return jsonify({'success': False}), 400


@app.route('/api/playlists')
def get_playlists():
    return jsonify(player.get_playlists())


@app.route('/api/play_playlist/<int:playlist_id>', methods=['POST'])
def play_playlist(playlist_id):
    if not player.client:
        return jsonify({'success': False}), 401
    player.set_playlist(playlist_id)
    return jsonify({'success': True})


@app.route('/api/play_wave', methods=['POST'])
def play_wave():
    if not player.client:
        return jsonify({'success': False}), 401
    threading.Thread(target=player.set_station, args=('user:wave',), daemon=True).start()
    return jsonify({'success': True})


@app.route('/api/play_radio_from_track', methods=['POST'])
def play_radio_from_track():
    data = request.get_json()
    if not data or 'track_id' not in data:
        return jsonify({'success': False}), 400
    threading.Thread(target=player.play_radio_from_track, args=(data['track_id'],), daemon=True).start()
    return jsonify({'success': True})


@app.route('/api/playlist_tracks/<int:playlist_id>')
def playlist_tracks(playlist_id):
    if not player.client:
        return jsonify([])
    return jsonify(player.get_playlist_tracks(playlist_id))


@app.route('/api/play_specific_track', methods=['POST'])
def play_specific_track():
    data = request.get_json()
    if not data or 'id' not in data:
        return jsonify({'success': False}), 400
    player.play_specific_track(data['id'], data.get('playlist_id'))
    return jsonify({'success': True})


@app.route('/api/track_action', methods=['POST'])
def track_action():
    data = request.get_json()
    if not data or 'action' not in data or 'track_id' not in data:
        return jsonify({'success': False}), 400
    actions = {'like': player.like_track, 'unlike': player.unlike_track, 'dislike': player.dislike_track}
    if data['action'] in actions:
        return jsonify({'success': actions[data['action']](data['track_id'])})
    return jsonify({'success': False}), 400


@app.route('/api/add_to_playlist', methods=['POST'])
def add_to_playlist():
    data = request.get_json()
    if not data or 'track_id' not in data or 'playlist_kind' not in data:
        return jsonify({'success': False}), 400
    return jsonify({'success': player.add_track_to_playlist(data['track_id'], data['playlist_kind'])})


@app.route('/api/set_volume', methods=['POST'])
def set_volume():
    data = request.get_json()
    if data and 'volume' in data:
        player.set_volume(data['volume'])
        return jsonify({'success': True})
    return jsonify({'success': False}), 400


@app.route('/api/volume_up', methods=['POST'])
def volume_up():
    player.volume_up()
    return jsonify({'success': True, 'volume': settings.volume})


@app.route('/api/volume_down', methods=['POST'])
def volume_down():
    player.volume_down()
    return jsonify({'success': True, 'volume': settings.volume})


@app.route('/api/set_bitrate', methods=['POST'])
def set_bitrate():
    data = request.get_json()
    if data and 'bitrate' in data:
        player.set_bitrate(data['bitrate'])
        return jsonify({'success': True})
    return jsonify({'success': False}), 400


@app.route('/api/set_equalizer', methods=['POST'])
def set_equalizer():
    data = request.get_json()
    if data and 'preset' in data and 'enabled' in data:
        player.set_equalizer_preset(data['preset'], data['enabled'])
        return jsonify({'success': True})
    return jsonify({'success': False}), 400


@app.route('/api/set_auto_start', methods=['POST'])
def set_auto_start():
    data = request.get_json()
    if data and 'enabled' in data:
        player.set_auto_start(data['enabled'])
        return jsonify({'success': True})
    return jsonify({'success': False}), 400


@app.route('/api/set_auto_play', methods=['POST'])
def set_auto_play():
    data = request.get_json()
    if data and 'enabled' in data:
        player.set_auto_play(data['enabled'])
        return jsonify({'success': True})
    return jsonify({'success': False}), 400


@app.route('/api/set_dark_mode', methods=['POST'])
def set_dark_mode():
    data = request.get_json()
    if data and 'enabled' in data:
        player.set_dark_mode(data['enabled'])
        return jsonify({'success': True})
    return jsonify({'success': False}), 400


@app.route('/api/set_key_bindings', methods=['POST'])
def set_key_bindings():
    data = request.get_json()
    for action, hotkey in data.items():
        player.set_key_binding(action, hotkey)
    setup_hotkeys()
    return jsonify({'success': True})


@app.route('/api/search')
def search():
    query = request.args.get('q')
    if not query:
        return jsonify([])
    return jsonify(player.search_tracks(query))


@app.route('/api/retry_browser_auth', methods=['POST'])
def retry_browser_auth():
    global cancel_auth
    cancel_auth = False  # Сбрасываем флаг отмены
    threading.Thread(target=get_yandex_music_token, daemon=True).start()
    return jsonify({'success': True})


@app.route('/api/logout', methods=['POST'])
def logout():
    success = player.logout()
    return jsonify({'success': success})


# ============================= Глобальные хоткеи =============================
def setup_hotkeys():
    try:
        keyboard.unhook_all()
    except:
        pass
    keyboard.add_hotkey(settings.key_bindings['play_pause'], lambda: player.pause())
    keyboard.add_hotkey(settings.key_bindings['next'], lambda: player.next())
    keyboard.add_hotkey(settings.key_bindings['prev'], lambda: player.prev())
    keyboard.add_hotkey(settings.key_bindings['like'], lambda: player.like_current())
    keyboard.add_hotkey(settings.key_bindings['dislike'], lambda: player.dislike_current())
    keyboard.add_hotkey(settings.key_bindings['volume_up'], lambda: player.volume_up())
    keyboard.add_hotkey(settings.key_bindings['volume_down'], lambda: player.volume_down())
    if main_window:
        def toggle_visibility():
            global window_visible
            if window_visible:
                main_window.hide()
                window_visible = False
            else:
                main_window.show()
                window_visible = True

        keyboard.add_hotkey(settings.key_bindings['minimize'], toggle_visibility)


# ============================= API для окна =============================
class Api:
    def minimize(self):
        global main_window
        if main_window:
            main_window.minimize()
            logger.info("Window minimized via API")

    def close(self):
        global main_window
        if main_window:
            main_window.destroy()
            logger.info("Window closed via API")


# ============================= Запуск Flask =============================
def run_flask():
    app.run(host='127.0.0.1', port=5000, debug=False, use_reloader=False)


# ============================= Главная функция =============================
def main():
    global main_window, window_visible
    flask_thread = threading.Thread(target=run_flask, daemon=True)
    flask_thread.start()
    time.sleep(1.0)  # Даём Flask время для запуска
    api_instance = Api()
    main_window = webview.create_window(APP_NAME, 'http://127.0.0.1:5000/', width=WINDOW_WIDTH, height=WINDOW_HEIGHT,
                                        resizable=False, fullscreen=False, on_top=True, frameless=True, easy_drag=True,
                                        js_api=api_instance)
    threading.Thread(target=player.load_token, daemon=True).start()
    try:
        setup_hotkeys()
    except Exception as e:
        logger.error(f"Не удалось настроить горячие клавиши: {e}")
    webview.start()


if __name__ == '__main__':
    main()