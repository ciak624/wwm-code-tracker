
import os
import json
import urllib.request
import urllib.error

def send_discord(message):
    webhook_url = os.environ.get("DISCORD_WEBHOOK_URL")

    if not webhook_url:
        print("錯誤：沒有取得 Discord Webhook Secret")
        raise RuntimeError("Missing Discord webhook secret")

    data = json.dumps({
        "content": message
    }).encode("utf-8")

    request = urllib.request.Request(
        webhook_url,
        data=data,
        headers={
            "Content-Type": "application/json",
            "User-Agent": "DiscordBot (https://github.com/ciak624/wwm-code-tracker, 1.0)",
        },
        method="POST",
    )

    try:
        with urllib.request.urlopen(request, timeout=20) as response:
            print("Discord 通知成功，狀態碼：", response.status)

    except urllib.error.HTTPError as error:
        print("Discord 回應狀態碼：", error.code)
        print("Discord 回應內容：", error.read().decode("utf-8", errors="replace"))
        raise

    except urllib.error.URLError as error:
        print("連線失敗：", error.reason)
        raise

send_discord("測試成功：燕雲兌換碼巡查機器人已連線。")
