
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



YOUTUBE_API_KEY = os.getenv("YOUTUBE_API_KEY", "").strip()
YOUTUBE_API_BASE = "https://www.googleapis.com/youtube/v3"


def fetch_youtube_codes():
    """搜尋 YouTube 影片標題、說明及公開留言中的候選兌換碼。"""
    if not YOUTUBE_API_KEY:
        print("未設定 YOUTUBE_API_KEY，略過 YouTube。")
        return {}, False

    discovered = {}
    videos = {}
    successful_searches = 0

    queries = [
        "Where Winds Meet codes",
        "燕雲十六聲 兌換碼",
    ]

    # 搜尋兩組關鍵字，各取最新 5 部影片。
    for query in queries:
        try:
            response = requests.get(
                f"{YOUTUBE_API_BASE}/search",
                params={
                    "part": "snippet",
                    "q": query,
                    "type": "video",
                    "order": "date",
                    "maxResults": 5,
                    "key": YOUTUBE_API_KEY,
                },
                timeout=25,
            )
            response.raise_for_status()
            data = response.json()
            successful_searches += 1

            for item in data.get("items", []):
                video_id = item.get("id", {}).get("videoId")
                snippet = item.get("snippet", {})

                if not video_id:
                    continue

                title = snippet.get("title", "YouTube 影片")
                video_url = f"https://www.youtube.com/watch?v={video_id}"
                videos[video_id] = {
                    "title": title,
                    "url": video_url,
                }

                text = (
                    title + "\n"
                    + snippet.get("description", "")
                )

                for code in extract_codes(text):
                    discovered.setdefault(code, []).append({
                        "name": f"YouTube 影片：{title}",
                        "url": video_url,
                    })

        except (requests.RequestException, ValueError) as error:
            print(f"YouTube 搜尋失敗（{query}）：{error}")

    # 讀取每部影片最近最多 100 則公開留言。
    for video_id, video in videos.items():
        try:
            response = requests.get(
                f"{YOUTUBE_API_BASE}/commentThreads",
                params={
                    "part": "snippet",
                    "videoId": video_id,
                    "maxResults": 100,
                    "order": "time",
                    "textFormat": "plainText",
                    "key": YOUTUBE_API_KEY,
                },
                timeout=25,
            )
            response.raise_for_status()
            data = response.json()

            for item in data.get("items", []):
                comment = (
                    item.get("snippet", {})
                    .get("topLevelComment", {})
                    .get("snippet", {})
                    .get("textDisplay", "")
                )

                for code in extract_codes(comment):
                    discovered.setdefault(code, []).append({
                        "name": f"YouTube 留言：{video['title']}",
                        "url": video["url"],
                    })

        except (requests.RequestException, ValueError) as error:
            # 例如影片關閉留言時，略過該影片，不中斷其他來源。
            print(f"YouTube 留言讀取失敗（{video_id}）：{error}")

    print(f"YouTube：搜尋成功 {successful_searches} 組關鍵字，"
          f"找到 {len(discovered)} 個候選碼。")

    return discovered, successful_searches > 0



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
        return True

    chunks = make_message_chunks(
        date_text,
        daily_record["codes"],
    )

    message_ids = daily_record.setdefault("message_ids", [])
    all_success = True

    for index, content in enumerate(chunks):
        if index < len(message_ids):
            message_id = message_ids[index]

            if webhook_edit(message_id, content):
                print(f"已更新 Discord 當日訊息：{date_text}")
            else:
                print(f"更新失敗，保留訊息 ID，稍後重試：{date_text}")
                all_success = False

            continue

        new_id = webhook_post(content)

        if new_id:
            message_ids.append(new_id)
            print(f"已發送 Discord 日期訊息：{date_text}")
        else:
            print(f"發送失敗，稍後重試：{date_text}")
            all_success = False

    return all_success



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


    youtube_codes, youtube_succeeded = fetch_youtube_codes()

    if youtube_succeeded:
        successful_sources += 1

    for code, code_sources in youtube_codes.items():
        discovered.setdefault(code, []).extend(code_sources)



    
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
                "notified": False,
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


    # 先儲存候選碼資料，避免掃描結果因執行中斷而遺失。
    save_json(DATABASE_FILE, database)

    # 只處理尚未成功通知的新碼。
    pending_codes = [
        code
        for code, item in database.items()
        if item.get("notified") is False
    ]

    daily_record = daily_data.setdefault(
        today,
        {"codes": [], "message_ids": []},
    )

    for code in pending_codes:
        if code not in daily_record["codes"]:
            daily_record["codes"].append(code)

    # 發送或更新今天的貼文；成功後才標記為已通知。
    if daily_record["codes"]:
        delivered = update_daily_discord(today, daily_record)

        if delivered:
            for code in pending_codes:
                database[code]["notified"] = True

            save_json(DATABASE_FILE, database)

    save_json(DAILY_FILE, daily_data)


    print(f"本次找到 {len(discovered)} 個候選碼。")
    print(f"本次新增 {len(new_codes)} 個候選碼。")
    print(f"資料庫總數：{len(database)}。")


if __name__ == "__main__":
    main()
