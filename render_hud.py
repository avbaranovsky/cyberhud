from datetime import datetime
import psutil
from PIL import Image, ImageDraw, ImageFilter, ImageFont, ImageOps
import json
import os
import subprocess

now_str = datetime.now().strftime("%d.%m.%Y %H:%M")
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
CACHE_FILE = os.path.join(BASE_DIR, "weather_cache.json")
HISTORY_FILE = os.path.join(BASE_DIR, "smart_history.json")
BG_IMAGE = os.path.join(BASE_DIR, "bg.png")
CUSTOM_FONT = os.path.join(BASE_DIR, "font.ttf")
SYSTEM_FONT = "/usr/share/fonts/truetype/dejavu/DejaVuSansMono-Bold.ttf"
OUTPUT_IMAGE = os.path.join(BASE_DIR, "desktop_weather.png")

def load_weather_from_cache():
    # Проверяем, существует ли файл на диске
    if os.path.exists(CACHE_FILE):
        try:
            with open(CACHE_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            pass

    # Запасной словарь-заглушка на случай, если файл кэша случайно удалили
    return {
        "city": "SANKT-PETERBURG",
        "temp": "N/A",
        "condition": "NET DANNYKH",
        "humidity": "--",
        "wind": "--",
        "updated_at": "OZHIDANIE...",
    }

# 3. Функция опроса датчиков компьютера
def get_hardware_stats():
    temps = psutil.sensors_temperatures()
    
    # 1. Процессор (средняя температура по всем ядрам из 'coretemp')
    cpu_temp = "N/A"
    if "coretemp" in temps:
        cores = [t.current for t in temps["coretemp"] if "core" in t.label.lower()]
        if cores:
            cpu_temp = f"{round(sum(cores) / len(cores))} \u00b0C"
            
    # 2. Материнская плата и корпус (два датчика из 'acpitz')
    mb_temp = f"{round(temps['acpitz'][0].current)} \u00b0C" if "acpitz" in temps and len(temps["acpitz"]) > 0 else "N/A"
    case_temp = f"{round(temps['acpitz'][1].current)} \u00b0C" if "acpitz" in temps and len(temps["acpitz"]) > 1 else "N/A"
    
    # 3. Видеокарта ('radeon') и накопитель ('nvme')
    gpu_temp = f"{round(temps['radeon'][0].current)} \u00b0C" if "radeon" in temps and temps["radeon"] else "N/A"
    ssd_temp = f"{round(temps['nvme'][0].current)} \u00b0C" if "nvme" in temps and temps["nvme"] else "N/A"
    
    return cpu_temp, mb_temp, case_temp, gpu_temp, ssd_temp

def get_disk_usage_percent(dev_dict):
    total_bytes = 0
    used_bytes = 0
    
    # Собираем сам диск и все его вложенные разделы (sda1, sda2, nvme0n1p1...)
    nodes = [dev_dict] + dev_dict.get("children", [])
    
    for node in nodes:
        mp = node.get("mountpoint")
        # Пропускаем несмонтированные разделы и файл подкачки [SWAP]
        if mp and mp != "[SWAP]" and os.path.exists(mp):
            try:
                usage = psutil.disk_usage(mp)
                total_bytes += usage.total
                used_bytes += usage.used
            except Exception:
                pass
                
    if total_bytes > 0:
        return round((used_bytes / total_bytes) * 100)
    return None  # Если ни один раздел диска сейчас не примонтирован

def get_disks_smart_status(history_file="smart_history.json"):
    # 1. Загружаем историю прошлых проверок, чтобы отследить ухудшения (желтый статус)
    history = {}
    if os.path.exists(history_file):
        try:
            with open(history_file, "r", encoding="utf-8") as f:
                history = json.load(f)
        except Exception:
            history = {}

    # 2. Получаем список физических дисков через lsblk (только с моделью и серийником)
    lsblk_cmd = ["lsblk", "-J", "-o", "NAME,MODEL,SERIAL,MOUNTPOINT"]
    lsblk_out = subprocess.check_output(lsblk_cmd, text=True)
    devices = json.loads(lsblk_out).get("blockdevices", [])

    disks_result = []
    new_history = {}

    # Критические атрибуты SATA (по ID) и NVMe, рост которых означает деградацию диска
    WATCH_ATTRS = {5, 187, 188, 197, 198}

    for dev in devices:
        name = dev.get("name")
        model = (dev.get("model") or "").strip()
        serial = (dev.get("serial") or "").strip()

        # Пропускаем виртуальные диски (loop, zram, ram и т.д.), у которых нет модели или серийника
        if not model or not serial:
            continue

        dev_path = f"/dev/{name}"
        status = "GREEN"  # По умолчанию считаем, что все отлично
        metrics = {}

        try:
            # Запускаем smartctl в режиме вывода JSON (-j)
            proc = subprocess.run(
                ["sudo", "smartctl", "-a", "-j", dev_path],
                capture_output=True,
                text=True,
                timeout=5,
            )
            smart_data = json.loads(proc.stdout)

            # Проверяем общий вердикт SMART (PASSED / FAILED)
            passed = smart_data.get("smart_status", {}).get("passed", True)
            if not passed:
                status = "RED"

            # Собираем счетчики ошибок для SATA дисков
            ata_attrs = smart_data.get("ata_smart_attributes", {}).get("table", [])
            for attr in ata_attrs:
                attr_id = attr.get("id")
                if attr_id in WATCH_ATTRS:
                    metrics[str(attr_id)] = attr.get("raw", {}).get("value", 0)

            # Собираем счетчики ошибок для NVMe накопителей
            nvme_log = smart_data.get("nvme_smart_health_information_log", {})
            if nvme_log:
                metrics["media_errors"] = nvme_log.get("media_errors", 0)
                metrics["critical_warning"] = nvme_log.get("critical_warning", 0)
                metrics["percentage_used"] = nvme_log.get("percentage_used", 0)
                if metrics["critical_warning"] > 0:
                    status = "RED"

            # Сравниваем с прошлым опросом по серийному номеру
            prev_metrics = history.get(serial, {})
            if status != "RED" and prev_metrics:
                for key, val in metrics.items():
                    # Если любой счетчик битых секторов или ошибок вырос — зажигаем желтый!
                    if val > prev_metrics.get(key, 0):
                        status = "YELLOW"
                        break

        except Exception:
            # Если smartctl не смог прочитать диск
            status = "YELLOW"

        new_history[serial] = metrics
        usage_pct = get_disk_usage_percent(dev)

        disks_result.append({
            "dev": name.upper(),
            "status": status,
            "usage": usage_pct  # Число от 0 до 100 или None
        })

    # Сохраняем свежие показатели в файл для следующего сравнения
    with open(history_file, "w", encoding="utf-8") as f:
        json.dump(new_history, f, indent=2)

    return disks_result

def get_fans_stats():
    fans = psutil.sensors_fans()
    active_fans = []
    
    if fans:
        for chip_name, entries in fans.items():
            for idx, entry in enumerate(entries, start=1):
                # Берем только те разъемы, к которым реально подключены кулеры (обороты > 0)
                if entry.current > 0:
                    # Если драйвер не дал имя (пустой label), называем FAN1, FAN2...
                    label = entry.label.upper() if entry.label else f"FAN{idx}"
                    # Сокращаем длинные названия для компактности
                    label = label.replace(" ", "")[:8]
                    active_fans.append(f"{label}: {round(entry.current)} RPM")
                    
    return active_fans

# 4. Функция отрисовки обоих блоков поверх фоновой картинки
def render_full_hud(bg_path, weather_data, hw_data, fans_data, disks_data, output_path="desktop_weather.png"):
    city = weather_data["city"]
    temp = weather_data["temp"]
    condition = weather_data["condition"]
    humidity = weather_data["humidity"]
    wind = weather_data["wind"]
    weather_time = weather_data["updated_at"]

    cpu_t, mb_t, case_t, gpu_t, ssd_t = hw_data

    # 1. Подготавливаем фон 1024x768 и слои
    raw_img = Image.open(bg_path).convert("RGBA")
    base_img = ImageOps.fit(raw_img, (1024, 768), method=Image.Resampling.NEAREST)

    overlay = Image.new("RGBA", base_img.size, (0, 0, 0, 0))
    glow_layer = Image.new("RGBA", base_img.size, (0, 0, 0, 0))

    draw_overlay = ImageDraw.Draw(overlay)
    draw_glow = ImageDraw.Draw(glow_layer)

    # Проверяем, лежит ли рядом со скриптом пользовательский font.ttf
    if os.path.exists(CUSTOM_FONT):
        font_path = CUSTOM_FONT
    elif os.path.exists(SYSTEM_FONT):
        font_path = SYSTEM_FONT
    else:
        font_path = None  # Запасной вариант, если скрипт запустят не на Ubuntu/Debian

    # Загружаем шрифты нужных размеров (или встроенный стандартный, если ничего не нашлось)
    if font_path:
        font_header = ImageFont.truetype(font_path, 16)
        font_temp = ImageFont.truetype(font_path, 62)
        font_main = ImageFont.truetype(font_path, 19)
    else:
        font_header = ImageFont.load_default(size=16)
        font_temp = ImageFont.load_default(size=62)
        font_main = ImageFont.load_default(size=19)

    neon_green = (65, 255, 30, 255)
    glow_green = (0, 220, 40, 110)
    now_str = datetime.now().strftime("%d.%m.%Y %H:%M")

    # 3. Рисуем квадратные плашки дисков по нижнему краю на слое overlay
    DISK_COLORS = {
        "GREEN":  ((0, 255, 70, 230),  (0, 25, 8, 210),   (65, 255, 30, 255)),
        "YELLOW": ((255, 200, 0, 240), (35, 25, 0, 215),  (255, 220, 40, 255)),
        "RED":    ((255, 40, 40, 250), (45, 0, 0, 225),   (255, 70, 70, 255)),
    }

    start_x = 30
    card_w = 96
    card_h = 52
    gap = 12
    disk_lines = []

    for idx, disk in enumerate(disks_data):
        x1 = start_x + idx * (card_w + gap)
        y1 = 690
        x2 = x1 + card_w
        y2 = y1 + card_h

        if x2 > 995:
            row = idx // 9
            col = idx % 9
            x1 = start_x + col * (card_w + gap)
            y1 = 690 - row * (card_h + gap)
            x2 = x1 + card_w
            y2 = y1 + card_h

        outline_col, fill_col, text_col = DISK_COLORS.get(disk["status"], DISK_COLORS["GREEN"])

        # 1. Рисуем основную квадратную рамку плашки
        draw_overlay.rectangle(
            (x1, y1, x2, y2),
            fill=fill_col,
            outline=outline_col,
            width=2,
        )

        # 2. Рисуем вертикальный "градусник" заполненности у правого края плашки
        usage = disk.get("usage")
        bar_w = 8  # Ширина столбика градусника в пикселях
        bar_x2 = x2 - 6
        bar_x1 = bar_x2 - bar_w
        bar_y1 = y1 + 6
        bar_y2 = y2 - 6

        # Тонкая рамка-колба для градусника
        draw_overlay.rectangle((bar_x1, bar_y1, bar_x2, bar_y2), outline=outline_col, width=1)

        if usage is not None and usage > 0:
            # Вычисляем высоту заполненной части снизу вверх
            max_h = bar_y2 - bar_y1 - 2
            fill_h = round((usage / 100) * max_h)
            
            # Если диск забит больше чем на 90%, столбик становится красным, иначе в цвет плашки
            bar_fill = (255, 50, 50, 240) if usage >= 90 else outline_col
            
            if fill_h > 0:
                draw_overlay.rectangle(
                    (bar_x1 + 1, bar_y2 - 1 - fill_h, bar_x2 - 1, bar_y2 - 1),
                    fill=bar_fill
                )

        # 3. Центрируем имя диска в оставшейся левой части плашки (шириной card_w - 16)
        label = disk["dev"]
        left, top, right, bottom = draw_overlay.textbbox((0, 0), label, font=font_header)
        text_w = right - left
        text_h = bottom - top

        text_area_w = card_w - 14
        text_x = x1 + (text_area_w - text_w) // 2
        text_y = y1 + (card_h - text_h) // 2 - 2
        disk_lines.append((text_x, text_y, label, font_header, text_col))

    # 4. Готовим текстовые строки для верхних двух блоков
    max_len = 28
    short_cond = condition if len(condition) <= max_len else condition[:max_len - 2] + ".."

    weather_lines = [
        (45, 36, f"> SYS.METEO // {city}", font_header),
        (45, 62, f"{temp}\u00b0C", font_temp),
        (45, 138, f"STATUS: {short_cond}", font_main),
        (45, 166, f"VLAZH.: {humidity}% | VETER: {wind} M/S", font_main),
        (45, 202, f"> PROGNOZ POLUCHEN {weather_time}", font_header),
    ]

    hw_lines = [
        (45, 268, "> SYS.MONITOR // TEMPERATURA", font_header),
        (45, 298, f"CPU: {cpu_t}  |  GPU: {gpu_t}", font_main),
        (45, 325, f"MB:  {mb_t}  |  KORPUS: {case_t}", font_main),
        (45, 352, f"SSD: {ssd_t}", font_main),
        (45, 388, f"> DANNYE POLUCHENY {now_str} _", font_header),
    ]

    # 3. Строки третьего блока (Вентиляторы по 2 штуки в строку)
    fans_lines = [
        (45, 452, "> SYS.COOLING // VENTILYATORY", font_header),
    ]

    if fans_data:
        # Группируем список кулеров парами: [0, 1] в первую строку, [2, 3] во вторую
        row1 = "  |  ".join(fans_data[:2])
        fans_lines.append((45, 482, row1, font_main))
        
        if len(fans_data) > 2:
            row2 = "  |  ".join(fans_data[2:4])
            fans_lines.append((45, 510, row2, font_main))
    else:
        fans_lines.append((45, 482, "KULERY: NET DANNYKH", font_main))

    fans_lines.append((45, 552, f"> OBOROTY SNYATY {now_str} _", font_header))

    all_lines = weather_lines + hw_lines + fans_lines

    # Создаем временный объект для замера ширины текста в пикселях
    max_text_w = 0
    for x, y, text, font in all_lines:
        left, top, right, bottom = draw_overlay.textbbox((0, 0), text, font=font)
        text_w = right - left
        if text_w > max_text_w:
            max_text_w = text_w

    # Вычисляем правую границу рамок: левый отступ текста (45) + самая длинная строка + отступ справа (20)
    # Ставим минимальный порог 340px, чтобы плашки не схлопнулись, если данных мало
    panel_x2 = max(340, 45 + max_text_w + 20)

    # Используем вычисленный panel_x2 вместо жесткого числа 480
    weather_box = (30, 25, panel_x2, 240)
    hw_box = (30, 255, panel_x2, 425)
    fans_box = (30, 440, panel_x2, 590)
    
    for box in [weather_box, hw_box, fans_box]:
        draw_overlay.rectangle(
            box,
            fill=(0, 18, 5, 130),
            outline=(0, 255, 70, 230),
            width=2,
        )

    # Накладываем все полупрозрачные рамки на фон
    combined = Image.alpha_composite(base_img, overlay)

    # 5. ЭТАП СВЕЧЕНИЯ: рисуем ореол для всех текстов
    for x, y, text, font in all_lines:
        draw_glow.text(
            (x, y), text, font=font, fill=glow_green,
            stroke_width=1, stroke_fill=glow_green
        )

    # <--- ВОТ ПЕРВОЕ МЕСТО ВСТАВКИ (свечение для дисков) --->
    for x, y, text, font, color in disk_lines:
        r, g, b, _ = color          # <-- ЗАМЕНИТЕ СТАРУЮ СТРОКУ НА ЭТИ ДВЕ
        glow_col = (r, g, b, 120)   # <--
        draw_glow.text((x, y), text, font=font, fill=glow_col)

    # Размываем весь слой свечения разом и накладываем на картинку
    glow_layer = glow_layer.filter(ImageFilter.GaussianBlur(radius=2))
    combined = Image.alpha_composite(combined, glow_layer)

    # 6. ЭТАП ЧЕТКОГО ТЕКСТА: рисуем резкие буквы поверх свечения
    draw_final = ImageDraw.Draw(combined)
    draw_final.fontmode = "1"

    for x, y, text, font in all_lines:
        thickness = 1 if font == font_temp else 0
        draw_final.text(
            (x, y), text, font=font, fill=neon_green,
            stroke_width=thickness, stroke_fill=neon_green
        )

    # <--- ВОТ ВТОРОЕ МЕСТО ВСТАВКИ (четкий текст для дисков) --->
    for x, y, text, font, color in disk_lines:
        draw_final.text((x, y), text, font=font, fill=color)

    # Сохраняем итоговый результат
    combined.convert("RGB").save(output_path)
    print(f"Готово (1024x768): {output_path}")

def set_wallpaper_feh(image_path):
    env = os.environ.copy()
    env["DISPLAY"] = ":0.1"
    env["XAUTHORITY"] = os.path.expanduser("~/.Xauthority")
    subprocess.run(["feh", "--no-fehbg", "--bg-fill", image_path], env=env, check=False)

# 5. Главный блок запуска (связываем все функции вместе)
if __name__ == "__main__":
    weather_info = load_weather_from_cache()
    hardware_info = get_hardware_stats()
    fans_info = get_fans_stats()
    disks_info = get_disks_smart_status()
    render_full_hud(BG_IMAGE, weather_info, hardware_info, fans_info, disks_info, OUTPUT_IMAGE)
    set_wallpaper_feh(OUTPUT_IMAGE)
