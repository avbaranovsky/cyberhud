#!/usr/bin/env bash
set -e

APP_NAME="cyberhud"
INSTALL_DIR="$HOME/.local/share/$APP_NAME"
BIN_DIR="$HOME/bin"
SOURCE_DIR="$(cd "$(dirname "${BASH_SOURCE}")" && pwd)"

echo "=== Деплой $APP_NAME в $INSTALL_DIR ==="

# 1. Создаем нужные пользовательские папки
mkdir -p "$INSTALL_DIR" "$BIN_DIR"

# 2. Копируем рабочие файлы (сохраняя существующий кэш и историю дисков)
echo "-> Копирование файлов..."
rsync -av --delete \
    --exclude='.git/' \
    --exclude='.gitignore' \
    --exclude='deploy.sh' \
    --exclude='venv/' \
    --exclude='env/' \
    --exclude='__pycache__/' \
    --exclude='*.log' \
    --exclude='weather_cache.json' \
    --exclude='smart_history.json' \
    --exclude='desktop_weather.png' \
    "$SOURCE_DIR/" "$INSTALL_DIR/"

# 3. Создаем виртуальное окружение, если его еще нет
if [ ! -d "$INSTALL_DIR/venv" ]; then
    echo "-> Создание виртуального окружения в $INSTALL_DIR/venv..."
    python3 -m venv "$INSTALL_DIR/venv"
fi

# 4. Устанавливаем зависимости
echo "-> Установка зависимостей..."
"$INSTALL_DIR/venv/bin/pip" install --upgrade pip -q
"$INSTALL_DIR/venv/bin/pip" install -r "$INSTALL_DIR/requirements.txt" -q

# 5. Создаем исполняемые обертки в ~/bin
echo "-> Создание команд в $BIN_DIR..."
cat << EOF > "$BIN_DIR/cyberhud-weather"
#!/usr/bin/env bash
exec "$INSTALL_DIR/venv/bin/python" "$INSTALL_DIR/fetch_weather.py" "\$@"
EOF

cat << EOF > "$BIN_DIR/cyberhud-render"
#!/usr/bin/env bash
exec "$INSTALL_DIR/venv/bin/python" "$INSTALL_DIR/render_hud.py" "\$@"
EOF

chmod +x "$BIN_DIR/cyberhud-weather" "$BIN_DIR/cyberhud-render"

echo "=== Готово! ==="
echo "Убедитесь, что $BIN_DIR добавлен в PATH."

# 6. Автоматически обновляем расписание в crontab без создания дубликатов
echo "-> Настройка расписания в crontab..."
CRON_WEATHER="*/30 * * * * $BIN_DIR/cyberhud-weather"
CRON_RENDER="* * * * * $BIN_DIR/cyberhud-render"

# Читаем текущий crontab (2>/dev/null защитит от ошибки, если crontab пока пустой),
# вырезаем старые упоминания cyberhud и дописываем свежие строчки
(
    crontab -l 2>/dev/null | grep -v "cyberhud-weather" | grep -v "cyberhud-render" || true
    echo "$CRON_WEATHER"
    echo "$CRON_RENDER"
) | crontab -

echo "-> Первый запуск для проверки и создания кэша..."
"$BIN_DIR/cyberhud-weather"
"$BIN_DIR/cyberhud-render"

