
import os
import json
import urllib.request

def send_discord(message):
    webhook_url = os.environ.get("DISCORD_WEBHOOK_URL")

    if not webhook_url:
        print("找不到 Discord Webhook 設定")
        return

    data = json.dumps({
        "content": message
    }).encode("utf-8")

    request = urllib.request.Request(
        webhook_url,
        data=data,
        headers={"Content-Type": "application/json"},
        method="POST",
    )

    with urllib.request.urlopen(request, timeout=20) as response:
        print("Discord 通知狀態：", response.status)

send_discord("測試成功：燕雲兌換碼巡查機器人已連線。")
