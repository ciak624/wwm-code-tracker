
import json
import os
import re
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import requests
from bs4 import BeautifulSoup

ROOT = Path(__file__).resolve().parent
SOURCES_FILE = ROOT / "sources.json"
DATA_DIR = ROOT / "data"
DATABASE_FILE = DATA_DIR / "codes.json"
DAILY_FILE = DATA_DIR / "daily_messages.json"

WEBHOOK_URL = os.getenv("DISCORD_WEBHOOK_URL", "").strip()
TAIPEI = ZoneInfo("Asia/Taipei")

HEADERS = {
    "User-Agent": (
        "DiscordBot "
        "(https://github.com/ciak624/wwm-code-tracker, 1.0)"
    )
}

CODE_PATTERN = re.compile(
    r"(?<![A-Za-z0-9])[A-Za-z0-9]{6,20}(?![A-Za-z0-9])"
)

STOPWORDS = {
    "REDEEM", "REWARDS", "REWARD", "EXPIRED", "ACTIVE",
    "WORKING", "LATEST", "OFFICIAL", "WHEREWINDSMEET",
    "COPYCODE", "DISCORD", "PASSWORD", "REGISTER",
    "REDEMPTION", "CHARACTER", "AVAILABLE", "RELEASE",
    "REQUIREMENTS", "CONTINUE", "SUBSCRIBE", "FOLLOWING",
    "PLAYERS", "FEATURES", "UPDATE", "VERSION",
}


def now_taipei():
    return datetime.now(TAIPEI)


def load_json(path, default):
    if not path.exists():
        return default

    with path.open("r", encoding="utf-8") as file:
        return json.load(file)


def save_json(path, data):
    DATA_DIR.mkdir(parents=True, exist_ok=True)

    with path.open("w", encoding="utf-8") as file:
        json.dump(data, file, ensure_ascii=False, indent=2)


def extract_codes(html):
    soup = BeautifulSoup(html, "html.parser")

    for element in soup(["script", "style", "noscript", "svg"]):
        element.decompose()

    lines = [
        line.strip()
        for line in soup.get_text("\n").splitlines()
        if line.strip()
    ]

    found = set()

    for line in lines:
        for match in CODE_PATTERN.finditer(line):
            code = match.group(0).upper()

            if code in STOPWORDS:
                continue

            has_letters = any(char.isalpha() for char in code)
            has_digits = any(char.isdigit() for char in code)

            if has_letters and has_digits:
                found.add(code)
            elif has_letters and len(code) >= 8 and line.upper().strip() == code:
                found.add(code)

    return found


def fetch_source(source):
    try:
        response = requests.get(
            source["url"],
            headers=HEADERS,
            timeout=25,
        )
        response.raise_for_status()

        if "html" not in response.headers.get("Content-Type", "").lower():
            print(f"略過非 HTML 網頁：{source['name']}")
            return set(), False

        codes = extract_codes(response.text)
        print(f"{source['name']}：找到 {len(codes)} 個候選碼")
        return codes, True

    except (requests.RequestException, ValueError) as error:
        print(f"來源讀取失敗：{source.get('name', source['url'])}：{error}")
        return set(), False


def webhook_post(content):
    if not WEBHOOK_URL:
        print("錯誤：未設定 DISCORD_WEBHOOK_URL")
        return None

    separator = "&" if "?" in WEBHOOK_URL else "?"
    url = f"{WEBHOOK_URL}{separator}wait=true"

    try:
        response = requests.post(
            url,
            json={"content": content},
            headers=HEADERS,
            timeout=20,
        )
        response.raise_for_status()
        return response.json().get("id")

    except (requests.RequestException, ValueError) as error:
        print(f"Discord 發送失敗：{error}")
        return None


def webhook_edit(message_id, content):
    url = f"{WEBHOOK_URL.rstrip('/')}/messages/{message_id}"

    try:
        response = requests.patch(
            url,
            json={"content": content},
            headers=HEADERS,
            timeout=20,
        )
        response.raise_for_status()
        return True

    except requests.RequestException as error:
        print(f"Discord 訊息更新失敗：{error}")
        return False


def make_message_chunks(date_text, codes):
    heading = f"👻 {date_text}\n"
    chunks = []
    current = heading

    for code in codes:
        item = f"`{code}` "
        if len(current) + len(item) > 1850:
            chunks.append(current.rstrip())
            current = heading + item
        else:
            current += item

    if current != heading:
        chunks.append(current.rstrip())

    return chunks


def update_daily_discord(date_text, daily_record):
    if not daily_record["codes"]:
        return

    chunks = make_message_chunks(
        date_text,
        daily_record["codes"],
    )

    message_ids = daily_record.setdefault("message_ids", [])

    for index, content in enumerate(chunks):
        if index < len(message_ids):
            message_id = message_ids[index]

            if webhook_edit(message_id, content):
                print(f"已更新 Discord 當日訊息：{date_text}")
                continue

        # 沒有舊訊息，或舊訊息已無法更新時，建立新訊息。
        new_id = webhook_post(content)

        if new_id:
            if index < len(message_ids):
                message_ids[index] = new_id
            else:
                message_ids.append(new_id)

            print(f"已發送 Discord 日期訊息：{date_text}")


def main():
    if not WEBHOOK_URL:
        print("錯誤：請先設定 DISCORD_WEBHOOK_URL")
        return

    sources_data = load_json(SOURCES_FILE, {"sources": []})
    sources = sources_data.get("sources", [])

    if not sources:
        print("sources.json 沒有設定任何來源。")
        return

    database = load_json(DATABASE_FILE, {})
    daily_data = load_json(DAILY_FILE, {})

    today = now_taipei().date().isoformat()
    first_daily_run = not DAILY_FILE.exists()

    discovered = {}
    successful_sources = 0

    for source in sources:
        codes, succeeded = fetch_source(source)

        if succeeded:
            successful_sources += 1

        for code in codes:
            discovered.setdefault(code, []).append({
                "name": source.get("name", source["url"]),
                "url": source["url"],
            })

    if successful_sources == 0:
        print("所有來源都無法讀取；保留原有紀錄，不修改狀態。")
        return

    new_codes = []

    for code, code_sources in discovered.items():
        if code not in database:
            database[code] = {
                "first_seen": now_taipei().isoformat(timespec="seconds"),
                "first_seen_date": today,
                "last_seen": now_taipei().isoformat(timespec="seconds"),
                "sources": code_sources,
            }
            new_codes.append(code)
        else:
            database[code]["last_seen"] = now_taipei().isoformat(
                timespec="seconds"
            )

            existing_urls = {
                item.get("url")
                for item in database[code].get("sources", [])
            }

            for item in code_sources:
                if item["url"] not in existing_urls:
                    database[code].setdefault("sources", []).append(item)
                    existing_urls.add(item["url"])

    save_json(DATABASE_FILE, database)

    # 第一次切換到日期格式時，補上先前首次掃描通知中的前 12 個候選碼。
    if first_daily_run and database:
        daily_data[today] = {
            "codes": sorted(database.keys())[:12],
            "message_ids": [],
        }

    daily_record = daily_data.setdefault(
        today,
        {"codes": [], "message_ids": []},
    )

    for code in new_codes:
        if code not in daily_record["codes"]:
            daily_record["codes"].append(code)

    # 當天所有掃描共用日期；已存在的訊息會被更新，不會每次另發。
    if daily_record["codes"]:
        update_daily_discord(today, daily_record)

    save_json(DAILY_FILE, daily_data)

    print(f"本次找到 {len(discovered)} 個候選碼。")
    print(f"本次新增 {len(new_codes)} 個候選碼。")
    print(f"資料庫總數：{len(database)}。")


if __name__ == "__main__":
    main()
