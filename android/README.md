# WeBook — Android-приложение (озвучка книг)

Нативный клиент (Kotlin) для self-hosted WeBook. Ключевая возможность —
**озвучка книг и статей голосовым движком Android** (Text-to-Speech): фоновое
воспроизведение, регулировка скорости, выбор голоса, автолистание абзацев,
сохранение позиции.

## Что делает

1. **Экран входа** — сначала указывается адрес сервера (`http://IP:8000` или домен),
   затем логин/пароль. Авторизация через `POST /api/token` (Bearer-токен).
2. **Библиотека** — вкладки «Книги» (epub/fb2/pdf) и «Статьи»; данные из
   `GET /api/books` и `GET /api/articles`.
3. **Читалка с озвучкой** — текст приходит из `GET /api/books/{id}/text`
   (`/api/articles/{id}/text`) уже разбитым на абзацы; озвучивается на устройстве.

Текст книг извлекается на сервере (`app/services/book_service.extract_book_text`):
EPUB — через ebooklib, FB2 — через конвертер в HTML, PDF — через pypdf.

## Сборка APK

Требуется **JDK 17** и **Android SDK** (platform-tools, platforms;android-34,
build-tools;34.0.0). Установить и собрать:

```powershell
# из каталога android/
# 1. local.properties должен указывать на Android SDK:
#    sdk.dir=C:\\Users\\<you>\\AppData\\Local\\Android\\Sdk
.\gradlew.bat assembleDebug   # debug-APK (подписан debug-ключом, ставится на любой телефон)
# или
.\gradlew.bat assembleRelease
```

Готовый APK: `app/build/outputs/apk/debug/app-debug.apk`.

## Раздача через сервер («скачать как плагин»)

Собранный APK кладётся в `static/webook.apk` корня проекта WeBook. Сервер отдаёт
его на `GET /settings/app/download`; кнопка «Скачать приложение (APK)» — на странице
**Настройки** рядом с расширением для браузера.

## Подключение к серверу

Сервер должен быть доступен по сети с телефона. Самоподписанный HTTPS-сертификат
по IP не пройдёт проверку на телефоне — используйте прямой HTTP-порт
приложения, например `http://192.168.0.165:8000` (приложение разрешает cleartext).
