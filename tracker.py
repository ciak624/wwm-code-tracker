
import json
import os
import re
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import requests
from bs4 import BeautifulSoup


# ============================================================
# 基本設定
# ============================================================

ROOT = Path(__file__).resolve().parent
SOURCES_FILE = ROOT / "sources.json"
DATA_DIR = ROOT / "data"
DATABASE_FILE = DATA_DIR / "codes.json"
DAILY_FILE = DATA_DIR / "daily_messages.json"

WEBHOOK_URL = os.getenv("DISCORD_WEBHOOK_URL", "").strip()
YOUTUBE_API_KEY = os.getenv("YOUTUBE_API_KEY", "").strip()
YOUTUBE_API_BASE = "https://www.googleapis.com/youtube/v3"

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


# ============================================================
# 時間與 JSON
# ============================================================

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


# ============================================================
# 兌換碼辨識
# ============================================================

def extract_codes(text):
    soup = BeautifulSoup(text, "html.parser")

    for element in soup(
        ["script", "style", "noscript", "svg"]
    ):
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

            elif (
                has_letters
                and len(code) >= 8
                and line.upper().strip() == code
            ):
                found.add(code)

    return found


# ============================================================
# 網站掃描
# ============================================================

def fetch_source(source):
    try:
        response = requests.get(
            source["url"],
            headers=HEADERS,
            timeout=25,
        )
        response.raise_for_status()

        content_type = response.headers.get(
            "Content-Type", ""
        ).lower()

        if "html" not in content_type:
            print(f"略過非 HTML 網頁：{source['name']}")
            return set(), False

        codes = extract_codes(response.text)

        print(
            f"{source['name']}："
            f"找到 {len(codes)} 個候選碼"
        )

        return codes, True

    except (requests.RequestException, ValueError) as error:
        print(
            f"來源讀取失敗："
            f"{source.get('name', source['url'])}：{error}"
        )
        return set(), False


# ============================================================
# YouTube 掃描
# 搜尋影片標題、說明欄、公開的頂層留言
# ============================================================

def fetch_youtube_codes():
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
                video_id = item.get(
                    "id", {}
                ).get("videoId")

                snippet = item.get("snippet", {})

                if not video_id:
                    continue

                title = snippet.get(
                    "title", "YouTube 影片"
                )

                video_url = (
                    "https://www.youtube.com/watch?v="
                    f"{video_id}"
                )

                videos[video_id] = {
                    "title": title,
                    "url": video_url,
                }

                text = (
                    title
                    + "\n"
                    + snippet.get("description", "")
                )

                for code in extract_codes(text):
                    discovered.setdefault(
                        code, []
                    ).append({
                        "name": f"YouTube 影片：{title}",
                        "url": video_url,
                    })

        except (requests.RequestException, ValueError) as error:
            print(
                f"YouTube 搜尋失敗（{query}）：{error}"
            )

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
                    discovered.setdefault(
                        code, []
                    ).append({
                        "name": (
                            f"YouTube 留言："
                            f"{video['title']}"
                        ),
                        "url": video["url"],
                    })

        except (requests.RequestException, ValueError) as error:
            print(
                f"YouTube 留言讀取失敗"
                f"（{video_id}）：{error}"
            )

    print(
        f"YouTube：搜尋成功 {successful_searches} 組，"
        f"找到 {len(discovered)} 個候選碼。"
    )

    return discovered, successful_searches > 0


# ============================================================
# Discord Webhook
# ============================================================

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
    url = (
        f"{WEBHOOK_URL.rstrip('/')}"
        f"/messages/{message_id}"
    )

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


# ============================================================
# 整理最近三個有新碼的日期
#
# 只採用本追蹤器已通知或正在等待通知的兌換碼。
# 舊資料沒有 notified 欄位時，不會被重新列入。
# ============================================================

def get_recent_code_groups(database, pending_codes):
    pending_set = set(pending_codes)
    eligible = []

    for code, item in database.items():
        if (
            item.get("notified") is not True
            and code not in pending_set
        ):
            continue

        first_seen_date = item.get("first_seen_date")

        if not first_seen_date:
            continue

        first_seen = item.get("first_seen", "")

        eligible.append({
            "code": code,
            "date": first_seen_date,
            "first_seen": first_seen,
        })

    # 先找出最近三個有兌換碼的日期。
    recent_dates = sorted(
        {item["date"] for item in eligible},
        reverse=True,
    )[:3]

    groups = []

    for date_text in recent_dates:
        # 同一天依首次發現時間倒序排列。
        # 時間較新的碼放前面。
        codes = [
            item["code"]
            for item in sorted(
                eligible,
                key=lambda item: item["first_seen"],
                reverse=True,
            )
            if item["date"] == date_text
        ]

        groups.append((date_text, codes))

    return groups


# ============================================================
# Discord 訊息分段
#
# 每個日期都有標題，最新日期排在前面。
# 保留 Discord 單則訊息長度限制。
# ============================================================

def make_message_chunks(groups):
    sections = []

    for date_text, codes in groups:
        heading = f"👻 {date_text}\n"
        current_codes = []

        for code in codes:
            candidate_codes = current_codes + [code]
            candidate = (
                heading
                + " ".join(
                    f"`{item}`"
                    for item in candidate_codes
                )
            )

            if len(candidate) > 1850 and current_codes:
                sections.append(
                    heading
                    + " ".join(
                        f"`{item}`"
                        for item in current_codes
                    )
                )
                current_codes = [code]

            else:
                current_codes.append(code)

        if current_codes:
            sections.append(
                heading
                + " ".join(
                    f"`{item}`"
                    for item in current_codes
                )
            )

    chunks = []
    current = ""

    for section in sections:
        candidate = (
            f"{current}\n\n{section}"
            if current
            else section
        )

        if len(candidate) > 1850 and current:
            chunks.append(current)
            current = section
        else:
            current = candidate

    if current:
        chunks.append(current)

    return chunks


# ============================================================
# 更新當天 Discord 訊息
#
# 同一天：編輯同一則訊息。
# 隔天：使用新的日期紀錄，建立新訊息。
# 不刪除舊訊息，也不修改其他日期的訊息。
# ============================================================

def update_daily_discord(
    date_text,
    daily_record,
    groups,
):
    if not groups:
        return True

    chunks = make_message_chunks(groups)

    message_ids = daily_record.setdefault(
        "message_ids", []
    )

    all_success = True

    for index, content in enumerate(chunks):
        if index < len(message_ids):
            message_id = message_ids[index]

            if webhook_edit(message_id, content):
                print(
                    f"已更新 Discord 當日訊息："
                    f"{date_text}"
                )
            else:
                print(
                    f"更新失敗，保留訊息 ID："
                    f"{date_text}"
                )
                all_success = False

            continue

        new_id = webhook_post(content)

        if new_id:
            message_ids.append(new_id)

            print(
                f"已發送 Discord 日期訊息："
                f"{date_text}"
            )
        else:
            all_success = False

            print(
                f"發送失敗，稍後重試："
                f"{date_text}"
            )

    return all_success


# ============================================================
# 主程式
# ============================================================

def main():
    if not WEBHOOK_URL:
        print(
            "錯誤：請先設定 DISCORD_WEBHOOK_URL"
        )
        return

    sources_data = load_json(
        SOURCES_FILE,
        {"sources": []},
    )

    sources = sources_data.get("sources", [])

    database = load_json(
        DATABASE_FILE,
        {},
    )

    daily_data = load_json(
        DAILY_FILE,
        {},
    )

    today = now_taipei().date().isoformat()
    discovered = {}
    successful_sources = 0

    # --------------------------------------------------------
    # 1. 掃描網站
    # --------------------------------------------------------

    for source in sources:
        codes, succeeded = fetch_source(source)

        if succeeded:
            successful_sources += 1

        for code in codes:
            discovered.setdefault(
                code, []
            ).append({
                "name": source.get(
                    "name", source["url"]
                ),
                "url": source["url"],
            })

    # --------------------------------------------------------
    # 2. 掃描 YouTube
    # --------------------------------------------------------

    youtube_codes, youtube_succeeded = (
        fetch_youtube_codes()
    )

    if youtube_succeeded:
        successful_sources += 1

    for code, code_sources in youtube_codes.items():
        discovered.setdefault(
            code, []
        ).extend(code_sources)

    # 所有來源都失敗時，不更新資料庫狀態。
    if successful_sources == 0:
        print(
            "所有來源都無法讀取；"
            "保留原有紀錄，不修改狀態。"
        )
        return

    # --------------------------------------------------------
    # 3. 記錄新碼，保留首次發現日期
    # --------------------------------------------------------

    new_codes = []

    for code, code_sources in discovered.items():
        if code not in database:
            database[code] = {
                "first_seen": now_taipei().isoformat(
                    timespec="microseconds"
                ),
                "first_seen_date": today,
                "last_seen": now_taipei().isoformat(
                    timespec="seconds"
                ),
                "sources": code_sources,
                "notified": False,
            }

            new_codes.append(code)

        else:
            database[code]["last_seen"] = (
                now_taipei().isoformat(
                    timespec="seconds"
                )
            )

            existing_urls = {
                item.get("url")
                for item in database[code].get(
                    "sources", []
                )
            }

            for item in code_sources:
                if item["url"] not in existing_urls:
                    database[code].setdefault(
                        "sources", []
                    ).append(item)

                    existing_urls.add(item["url"])

    # 先保存候選碼，即使 Discord 暫時失敗也不丟失資料。
    save_json(DATABASE_FILE, database)

    # --------------------------------------------------------
    # 4. 找出尚未成功通知的碼
    #
    # 舊資料沒有 notified=False，不會被重新通知。
    # --------------------------------------------------------

    pending_codes = [
        code
        for code, item in database.items()
        if item.get("notified") is False
    ]

    # --------------------------------------------------------
    # 5. 有待通知的新碼時，更新今天的訊息
    #
    # 訊息內容包含最近三個有新碼的日期。
    # --------------------------------------------------------

    if pending_codes:
        daily_record = daily_data.setdefault(
            today,
            {
                "codes": [],
                "message_ids": [],
            },
        )

        # 記錄這次要處理的碼，避免重複加入。
        for code in pending_codes:
            if code not in daily_record["codes"]:
                daily_record["codes"].append(code)

        groups = get_recent_code_groups(
            database,
            pending_codes,
        )

        delivered = update_daily_discord(
            today,
            daily_record,
            groups,
        )

        # 所有訊息成功更新後，才標記為已通知。
        if delivered:
            for code in pending_codes:
                database[code]["notified"] = True

            save_json(DATABASE_FILE, database)

    # --------------------------------------------------------
    # 6. 保存 Discord 訊息 ID
    # --------------------------------------------------------

    save_json(DAILY_FILE, daily_data)

    # --------------------------------------------------------
    # 7. 執行摘要
    # --------------------------------------------------------

    print(f"本次找到 {len(discovered)} 個候選碼。")
    print(f"本次新增 {len(new_codes)} 個候選碼。")
    print(f"待通知數量：{len(pending_codes)}。")
    print(f"資料庫總數：{len(database)}。")


if __name__ == "__main__":
    main()
