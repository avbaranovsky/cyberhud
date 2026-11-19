import json
import os
import unicodedata
from datetime import datetime
import requests

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
CACHE_FILE = os.path.join(BASE_DIR, "weather_cache.json")

# 1. Словарь для перевода кириллицы в транслит
TRANSLIT_MAP = {
    'А': 'A', 'Б': 'B', 'В': 'V', 'Г': 'G', 'Д': 'D', 'Е': 'E', 'Ё': 'YO',
    'Ж': 'ZH', 'З': 'Z', 'И': 'I', 'Й': 'Y', 'К': 'K', 'Л': 'L', 'М': 'M',
    'Н': 'N', 'О': 'O', 'П': 'P', 'Р': 'R', 'С': 'S', 'Т': 'T', 'У': 'U',
    'Ф': 'F', 'Х': 'KH', 'Ц': 'TS', 'Ч': 'CH', 'Ш': 'SH', 'Щ': 'SHCH',
    'Ъ': '', 'Ы': 'Y', 'Ь': '', 'Э': 'E', 'Ю': 'YU', 'Я': 'YA'
}

def to_translit(text):
    # Нормализуем символы, чтобы убрать нестандартные кодировки
    clean_text = unicodedata.normalize("NFC", str(text)).upper()
    result = []
    for char in clean_text:
        result.append(TRANSLIT_MAP.get(char, char))
    return "".join(result)

def update_weather_cache(city_name="Санкт-Петербург"):
    # Запрашиваем погоду в формате JSON (j1) на русском языке (lang=ru)
    url = f"https://wttr.in/санкт-петербург"

    params = {
        "format": "%t|%h|%w|%C",
        "lang": "ru"
    }

    # Маскируемся под обычный браузер в Linux
    headers = {"User-Agent": "curl/7.81.0"}
    try:

        response = requests.get(url, params=params, headers=headers, timeout=10)
        response.encoding = "utf-8"  # Жестко задаем правильную кодировку!
        raw_temp, raw_hum, raw_wind, raw_cond = response.text.strip().split("|")

        temp_str = raw_temp.replace("°C", "")
        humidity = raw_hum.replace("%", "")
        
        wind_raw = "".join(ch for ch in raw_wind if ch.isdigit() or ch == ".")
        wind = round(float(wind_raw) / 3.6, 1) if "km/h" in raw_wind else wind_raw

        payload = {
            "city": to_translit(city_name),
            "temp": temp_str,
            "condition": to_translit(raw_cond),
            "humidity": humidity,
            "wind": wind,
            "updated_at": datetime.now().strftime("%d.%m.%Y %H:%M"),
        }
        
        # Конструкция "with open" безопасно открывает файл на запись ("w") и сама закрывает его
        with open(CACHE_FILE, "w", encoding="utf-8") as f:
            json.dump(payload, f, ensure_ascii=False, indent=2)

        print("Погода успешно сохранена в кэш!")

    except Exception as e:
        # Блок try...except защищает от падения: если пропадет интернет, старый файл останется целым
        print(f"Не удалось обновить погоду: {e}")

if __name__ == "__main__":
    update_weather_cache("Санкт-Петербург")