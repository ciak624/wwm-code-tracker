
import json
import os
import re
from datetime import datetime, timezone
from pathlib import Path

import requests
from bs4 import BeautifulSoup

ROOT = Path(__file__).resolve().parent
SOURCES_FILE = ROOT / "sources.json"
DATA_DIR = ROOT / "data"
DATABASE_FILE = DATA_DIR / "codes.json"

WEBHOOK_URL = os.getenv("DISCORD_WEBHOOK_URL", "").strip()

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (compatible; WWMCodeTracker/1.0; "
        "+https://github.com/ciak624/wwm-code-tracker)"
    )
}

CODE_PATTERN = re.compile(r"(?<![A-Za-z0-9])[A-Za-z0-9]{6,20}(?![A-Za-z0-9])")

STOPWORDS = {
    "REDEEM", "REWARDS", "REWARD", "EXPIRED", "ACTIVE",
    "WORKING", "LATEST", "OFFICIAL", "WHEREWINDSMEET",
    "COPYCODE", "DISCORD", "PASSWORD", "REGISTER",
    "REDEMPTION", "CHARACTER", "AVAILABLE", "RELEASE",
    "REQUIREMENTS", "CONTINUE", "SUBSCRIBE", "FOLLOWING",
    "PLAYERS", "FEATURES", "UPDATE", "VERSION",
}


def now_utc():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def load_json(path, default):
    if not path.exists():
        return default

    with path.open("r", encoding="utf-8") as file:
        return json.load(file)


def save_database(database):
    DATA_DIR.mkdir(parents=True, exist_ok=True)

    with DATABASE_FILE.open("w", encoding="utf-8") as file:
        json.dump(database, file, ensure_ascii=False, indent=2)


def send_discord(message):
    if not WEBHOOK_URL:
        print("錯誤：未設定 DISCORD_WEBHOOK_URL")
        return False

    try:
        response = requests.post(
            WEBHOOK_URL,
            json={"content": message},
            headers={
                "Content-Type": "application/json",
                "User-Agent": (
                    "DiscordBot "
                    "(https://github.com/ciak624/wwm-code-tracker, 1.0)"
                ),
            },
            timeout=20,
        )
        response.raise_for_status()
        return True
    except requests.RequestException as error:
        print(f"Discord 通知失敗：{error}")
        return False


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

    for index, line in enumerate(lines):
        line_upper = line.upper().strip()

        for match in CODE_PATTERN.finditer(line):
            code = match.group(0).upper()

            if code in STOPWORDS:
                continue

            has_letters = any(char.isalpha() for char in code)
            has_digits = any(char.isdigit() for char in code)

            # 優先接受同時包含英文字母與數字的候選碼。
            if has_letters and has_digits:
                found.add(code)
                continue

            # 純英文字母候選碼採較嚴格條件，減少誤判。
            if (
                has_letters
                and len(code) >= 8
                and line_upper == code
            ):
                found.add(code)

    return found


def fetch_source(source):
    url = source["url"]
    name = source.get("name", url)

    try:
        response = requests.get(
            url,
            headers=HEADERS,
            timeout=25,
        )
        response.raise_for_status()

        content_type = response.headers.get("Content-Type", "")
        if "html" not in content_type.lower():
            print(f"略過非 HTML 網頁：{name}")
            return set()

        codes = extract_codes(response.text)
        print(f"{name}：找到 {len(codes)} 個候選碼")
        return codes

    except (requests.RequestException, ValueError) as error:
        print(f"來源讀取失敗：{name}：{error}")
        return set()


def send_in_chunks(lines, heading):
    # Discord 單則訊息上限為 2000 字元，保留安全空間。
    message = heading

    for line in lines:
        addition = "\n" + line

        if len(message) + len(addition) > 1800:
            send_discord(message)
            message = heading + "\n（續）"

        message += addition

    if message != heading and message != heading + "\n（續）":
        send_discord(message)


def main():
    sources_data = load_json(SOURCES_FILE, {"sources": []})
    sources = sources_data.get("sources", [])

    if not sources:
        print("sources.json 沒有設定任何來源。")
        return

    database = load_json(DATABASE_FILE, {})
    first_run = not DATABASE_FILE.exists()

    discovered = {}
    successful_sources = 0

    for source in sources:
        codes = fetch_source(source)

        if codes:
            successful_sources += 1

        for code in codes:
            if code not in discovered:
                discovered[code] = []

            discovered[code].append({
                "name": source.get("name", source["url"]),
                "url": source["url"],
            })

    if successful_sources == 0:
        send_discord(
            "⚠️ 燕雲兌換碼巡查：本次沒有任何來源成功取得候選碼。"
            "可能是網站暫時無法存取，請查看 GitHub Actions 執行紀錄。"
        )
        return

    new_codes = []

    for code, code_sources in discovered.items():
        if code not in database:
            database[code] = {
                "first_seen": now_utc(),
                "last_seen": now_utc(),
                "sources": code_sources,
            }

            new_codes.append((code, code_sources))

        else:
            database[code]["last_seen"] = now_utc()

            existing_urls = {
                item.get("url")
                for item in database[code].get("sources", [])
            }

            for item in code_sources:
                if item["url"] not in existing_urls:
                    database[code].setdefault("sources", []).append(item)
                    existing_urls.add(item["url"])

    save_database(database)

    if first_run:
        print(f"首次建立資料庫，共記錄 {len(database)} 個候選碼。")

        # 初次建立資料庫時只通知一批，避免大量歷史資料洗版。
        initial_codes = sorted(discovered.items())[:12]

        if initial_codes:
            lines = []
            for code, code_sources in initial_codes:
                source_names = ", ".join(
                    item["name"] for item in code_sources[:2]
                )
                lines.append(f"• `{code}`｜來源：{source_names}")

            send_in_chunks(
                lines,
                "🔎 燕雲兌換碼巡查：首次掃描結果\n"
                "以下是網站列出的候選碼，未確認仍有效；來源可能含過期碼。",
            )

        send_discord(
            f"資料庫已建立，共記錄 {len(database)} 個候選碼。"
            "之後將只通知新發現的代碼。"
        )
        return

    if new_codes:
        lines = []

        for code, code_sources in sorted(new_codes):
            source_names = ", ".join(
                item["name"] for item in code_sources[:3]
            )
            source_urls = "\n".join(
                f"  {item['url']}" for item in code_sources[:2]
            )

            lines.append(
                f"🆕 `{code}`\n"
                f"來源：{source_names}\n"
                f"{source_urls}\n"
                "狀態：尚未確認有效，請進遊戲測試。"
            )

        send_in_chunks(
            lines,
            f"燕雲十六聲｜發現 {len(new_codes)} 個新候選碼",
        )
    else:
        print("沒有發現新候選碼。")

    print(f"本次完成，資料庫共有 {len(database)} 個候選碼。")


if __name__ == "__main__":
    main()
