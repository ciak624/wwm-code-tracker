import json
import os
import re
from datetime import datetime, timezone, timedelta
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
    "User-Agent": "Mozilla/5.0 (compatible; WWMCodeTracker/2.0; +https://github.com/ciak624/wwm-code-tracker)"
}
CODE_PATTERN = re.compile(r"(?<![A-Za-z0-9])[A-Z0-9]{6,20}(?![A-Za-z0-9])")
STOPWORDS = {
    "REDEEM", "REWARDS", "REWARD", "EXPIRED", "ACTIVE", "WORKING",
    "LATEST", "OFFICIAL", "WHEREWINDSMEET", "COPYCODE", "DISCORD",
    "PASSWORD", "REGISTER", "REDEMPTION", "CHARACTER", "AVAILABLE",
    "RELEASE", "REQUIREMENTS", "CONTINUE", "SUBSCRIBE", "FOLLOWING",
    "PLAYERS", "FEATURES", "UPDATE", "VERSION", "OCTOBER", "NOVEMBER",
    "DECEMBER", "JANUARY", "FEBRUARY", "MARCH", "APRIL", "SEPTEMBER",
    "AUGUST", "JULY", "JUNE", "MAY", "WHERE", "WINDS", "MEET",
}
WWMTOOLS_URL = "https://wwmtools.com/zh/codes/"
REDDIT_URL = "https://www.reddit.com/r/wherewindsmeet_/new.json?limit=50"
REDDIT_MAX_AGE_DAYS = 21


def now_taipei():
    return datetime.now(TAIPEI)


def load_json(path, default):
    if not path.exists():
        return default
    try:
        with path.open("r", encoding="utf-8") as file:
            return json.load(file)
    except (json.JSONDecodeError, OSError) as error:
        print(f"JSON 讀取失敗 {path.name}：{error}")
        return default


def save_json(path, data):
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as file:
        json.dump(data, file, ensure_ascii=False, indent=2)


def clean_code_candidates(text):
    found = set()
    for match in CODE_PATTERN.finditer(text.upper()):
        code = match.group(0)
        if code in STOPWORDS:
            continue
        if any(c.isalpha() for c in code) and any(c.isdigit() for c in code):
            found.add(code)
    return found


def fetch_wwmtools():
    """Only treat the explicitly active section as candidates; separately read expired codes."""
    try:
        response = requests.get(WWMTOOLS_URL, headers=HEADERS, timeout=30)
        response.raise_for_status()
        soup = BeautifulSoup(response.text, "html.parser")
        for element in soup(["script", "style", "noscript", "svg"]):
            element.decompose()
        text = soup.get_text("\n", strip=True)

        active_heading = "可用的兌換碼"
        expired_heading = "已過期的兌換碼"
        if active_heading not in text or expired_heading not in text:
            print("WWM Tools：找不到可用／過期區塊標題，為避免誤報，本次不採用。")
            return set(), set(), False

        active_text = text.split(active_heading, 1)[1].split(expired_heading, 1)[0]
        expired_text = text.split(expired_heading, 1)[1]

        active_codes = clean_code_candidates(active_text)
        expired_codes = clean_code_candidates(expired_text)
        # Explicitly remove any code listed as expired, even if page markup duplicates it.
        active_codes -= expired_codes

        print(f"WWM Tools：有效區找到 {len(active_codes)} 個候選碼；過期區辨識 {len(expired_codes)} 個。")
        return active_codes, expired_codes, True
    except (requests.RequestException, ValueError) as error:
        print(f"WWM Tools 讀取失敗：{error}")
        return set(), set(), False


def fetch_recent_reddit_posts():
    """Scan recent subreddit posts only; WWM Tools' expired list is used as a veto."""
    found = {}
    try:
        response = requests.get(
            REDDIT_URL,
            headers={**HEADERS, "Accept": "application/json"},
            timeout=30,
        )
        response.raise_for_status()
        payload = response.json()
        children = payload.get("data", {}).get("children", [])
        cutoff = datetime.now(timezone.utc).timestamp() - REDDIT_MAX_AGE_DAYS * 86400

        for child in children:
            post = child.get("data", {})
            created = float(post.get("created_utc", 0))
            if created < cutoff:
                continue

            title = post.get("title", "")
            body = post.get("selftext", "")
            permalink = "https://www.reddit.com" + post.get("permalink", "")
            candidates = clean_code_candidates(title + "\n" + body)
            for code in candidates:
                found.setdefault(code, []).append({
                    "name": "Reddit recent community post",
                    "url": permalink,
                    "published_at": datetime.fromtimestamp(created, timezone.utc).isoformat(),
                })

        print(f"Reddit：最近 {REDDIT_MAX_AGE_DAYS} 天的貼文找到 {len(found)} 個候選碼。")
        return found, True
    except (requests.RequestException, ValueError, TypeError) as error:
        print(f"Reddit 讀取失敗：{error}")
        return {}, False


def webhook_post(content):
    if not WEBHOOK_URL:
        print("錯誤：未設定 DISCORD_WEBHOOK_URL")
        return None
    separator = "&" if "?" in WEBHOOK_URL else "?"
    url = f"{WEBHOOK_URL}{separator}wait=true"
    try:
        response = requests.post(
            url, json={"content": content}, headers=HEADERS, timeout=20
        )
        response.raise_for_status()
        return response.json().get("id")
    except (requests.RequestException, ValueError) as error:
        print(f"Discord 發送失敗：{error}")
        return None


def webhook_edit(message_id, content):
    if not WEBHOOK_URL:
        return False
    url = f"{WEBHOOK_URL.rstrip('/')}/messages/{message_id}"
    try:
        response = requests.patch(
            url, json={"content": content}, headers=HEADERS, timeout=20
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
    if not daily_record.get("codes"):
        return
    chunks = make_message_chunks(date_text, daily_record["codes"])
    message_ids = daily_record.setdefault("message_ids", [])
    for index, content in enumerate(chunks):
        if index < len(message_ids):
            if webhook_edit(message_ids[index], content):
                print(f"已更新 Discord 當日訊息：{date_text}")
                continue
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

    database = load_json(DATABASE_FILE, {})
    daily_data = load_json(DAILY_FILE, {})
    today = now_taipei().date().isoformat()
    timestamp = now_taipei().isoformat(timespec="seconds")

    active_codes, expired_codes, wwm_ok = fetch_wwmtools()
    reddit_found, reddit_ok = fetch_recent_reddit_posts()

    # Do not proceed if both independent sources failed; preserve all files unchanged.
    if not wwm_ok and not reddit_ok:
        print("有效碼來源與 Reddit 都無法讀取；不修改資料庫，也不發通知。")
        return

    discovered = {}
    for code in active_codes:
        discovered.setdefault(code, []).append({
            "name": "WWM Tools active-code list",
            "url": WWMTOOLS_URL,
        })

    for code, sources in reddit_found.items():
        if code in expired_codes:
            print(f"排除已被 WWM Tools 標示過期的碼：{code}")
            continue
        discovered.setdefault(code, []).extend(sources)

    # If WWM Tools was reachable, its expired list is authoritative enough to veto codes.
    if wwm_ok:
        for code in list(discovered):
            if code in expired_codes:
                discovered.pop(code, None)

    new_codes = []
    for code, code_sources in discovered.items():
        if code not in database:
            database[code] = {
                "first_seen": timestamp,
                "first_seen_date": today,
                "last_seen": timestamp,
                "sources": code_sources,
                "status": "candidate",
            }
            new_codes.append(code)
        else:
            database[code]["last_seen"] = timestamp
            existing_urls = {
                item.get("url") for item in database[code].get("sources", [])
            }
            for item in code_sources:
                if item.get("url") not in existing_urls:
                    database[code].setdefault("sources", []).append(item)
                    existing_urls.add(item.get("url"))

    # Never bulk-send the existing database on first run. Only genuinely new codes
    # discovered by this run are eligible for a Discord message.
    if new_codes:
        # Keep new codes first in the order encountered, without duplicates.
        daily_record = daily_data.setdefault(today, {"codes": [], "message_ids": []})
        for code in new_codes:
            if code not in daily_record["codes"]:
                daily_record["codes"].append(code)
        update_daily_discord(today, daily_record)

    save_json(DATABASE_FILE, database)
    save_json(DAILY_FILE, daily_data)

    print(f"本次有效來源：WWM Tools={'成功' if wwm_ok else '失敗'}；Reddit={'成功' if reddit_ok else '失敗'}")
    print(f"本次找到 {len(discovered)} 個候選碼；本次新增 {len(new_codes)} 個。")
    print(f"資料庫總數：{len(database)}。")


if __name__ == "__main__":
    main()
