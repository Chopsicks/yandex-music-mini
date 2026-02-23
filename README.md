<div align="center">
  <img src="icon.png" alt="Yandex Music Mini Logo" width="120" height="120">
  <h1>🎵 Яндекс.Музыка мини</h1>
  <p><strong>Неофициальный компактный десктоп-клиент для Яндекс.Музыки</strong></p>
  <p>
    <a href="https://github.com/frizzylow/yandex-music-mini/releases">
      <img src="https://img.shields.io/github/v/release/frizzylow/yandex-music-mini?style=flat-square&color=fed42a" alt="Latest Release">
    </a>
    <a href="https://github.com/frizzylow/yandex-music-mini/blob/main/LICENSE">
      <img src="https://img.shields.io/github/license/frizzylow/yandex-music-mini?style=flat-square&color=fed42a" alt="License">
    </a>
    <a href="https://github.com/frizzylow/yandex-music-mini/issues">
      <img src="https://img.shields.io/github/issues/frizzylow/yandex-music-mini?style=flat-square&color=fed42a" alt="Issues">
    </a>
  </p>
  <p>
    <a href="#-особенности">Особенности</a> •
    <a href="#-скриншоты">Скриншоты</a> •
    <a href="#-установка">Установка</a> •
    <a href="#-настройка">Настройка</a> •
    <a href="#-сайт-проекта">Сайт проекта</a> •
    <a href="#-лицензия">Лицензия</a>
  </p>
  <br>
  <img src="screenshot.png" alt="Yandex Music Mini Screenshot" width="700">
</div>

---

**Яндекс.Музыка мини** — это лёгкий и стильный плеер для Яндекс.Музыки, который всегда под рукой. Он занимает минимум места на экране, но даёт полный контроль над воспроизведением: глобальные горячие клавиши, эквалайзер, персональные станции и управление плейлистами. Никаких лишних вкладок браузера — только музыка и удобство.

---

## ✨ Особенности

- **🪟 Компактное окно** — всегда поверх других окон, можно перетаскивать за заголовок. Идеально для работы или игр.
- **⌨️ Глобальные хоткеи** — управляйте плеером, даже когда окно свёрнуто. Все комбинации настраиваются.
- **🎚 Встроенный эквалайзер** — 8 пресетов: рок, поп, классика, джаз, усиление басов и высоких, а также комбинированный режим.
- **🌊 Моя волна** — умная персонализированная станция с тонкой настройкой настроения (бодрое, весёлое, спокойное, грустное), языка (русский, иностранный, без слов) и разнообразия (любимое, популярное, незнакомое).
- **❤️ Интеграция с библиотекой** — лайкайте, дизлайкайте, добавляйте треки в плейлисты прямо из приложения.
- **📋 Плейлисты и поиск** — просматривайте свои плейлисты, треки из раздела «Мне нравится» и ищите новые композиции.
- **🎨 Тёмная и светлая темы** — автоматически подстраиваются под системные настройки или переключаются вручную.
- **🔧 Гибкие настройки** — качество звука (192/320 kbps), автозапуск с Windows, автовоспроизведение, настраиваемый внешний вид.
- **🚀 Минималистичный интерфейс** — ничего лишнего, только основная информация о треке и элементы управления.

---

## 📸 Скриншоты

<div align="center">
  <img src="screenshot-dark.png" alt="Тёмная тема" width="400">
  <img src="screenshot-light.png" alt="Светлая тема" width="400">
  <br>
  <em>Тёмная и светлая темы плеера</em>
</div>

---

## ⚙️ Установка

### Для пользователей Windows

1. Скачайте последнюю версию установщика с [официального сайта](https://frizzylow.github.io/yandex-music-mini/) (раздел **Скачать**) или напрямую по ссылке:  
   [**YandexMusicMini_Setup.exe**](https://frizzylow.github.io/yandex-music-mini/download/YandexMusicMini_Setup.exe)
2. Запустите скачанный файл и следуйте инструкциям установщика.
3. После установки запустите ярлык на рабочем столе или в меню «Пуск».

> **Системные требования:**  
> - Windows 10 или 11 (64-bit)  
> - 2 ГБ оперативной памяти  
> - Установленный [VLC media player](https://www.videolan.org/vlc/) (требуется для воспроизведения)

### Сборка из исходного кода (для разработчиков и Linux)

```bash
# Клонируйте репозиторий
git clone https://github.com/frizzylow/yandex-music-mini.git
cd yandex-music-mini

# Рекомендуется создать виртуальное окружение
python -m venv venv
source venv/bin/activate  # для Linux/macOS
venv\Scripts\activate     # для Windows

# Установите зависимости
pip install -r requirements.txt

# Запустите приложение
python main.py
