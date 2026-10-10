"""Verify configured Telegram identity without sending messages or consuming updates."""
import fcntl
import json
from pathlib import Path
import shlex
import urllib.error
import urllib.request
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from app.coordinator import ROOT, audit


def main():
    audit("Telegram identity verification attempted")
    values = {}
    for line in (ROOT / ".env").read_text().splitlines():
        line = line.strip().removeprefix("export ")
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, raw = line.split("=", 1)
        if key.strip() in ("TELEGRAM_BOT_TOKEN", "TELEGRAM_CHAT_ID", "TELEGRAM_USER_ID"):
            parts = shlex.split(raw, comments=True)
            values[key.strip()] = parts[0] if len(parts) == 1 else ""
    if not all(values.get(key) for key in ("TELEGRAM_BOT_TOKEN", "TELEGRAM_CHAT_ID", "TELEGRAM_USER_ID")):
        print("FAIL: token, chat ID and user ID must all be configured in .env.")
        audit("Telegram identity verification blocked; missing configuration")
        return 1

    def request(method, payload):
        audit("Telegram identity " + method + " attempted")
        url = "https://api.telegram.org/bot" + values["TELEGRAM_BOT_TOKEN"] + "/" + method
        req = urllib.request.Request(url, json.dumps(payload).encode(), {"Content-Type": "application/json"})
        try:
            with urllib.request.urlopen(req, timeout=15) as response:
                result = json.load(response)
        except urllib.error.HTTPError as error:
            print(f"FAIL: Telegram rejected {method} (HTTP {error.code}); private response omitted.")
            audit("Telegram identity " + method + " rejected")
            return None
        except (urllib.error.URLError, TimeoutError, OSError):
            print("NETWORK_BLOCKED: could not reach Telegram.")
            audit("Telegram identity " + method + " network failure")
            return None
        if not result.get("ok"):
            print("FAIL: Telegram rejected " + method + ".")
            audit("Telegram identity " + method + " rejected")
            return None
        audit("Telegram identity " + method + " completed")
        return result["result"]

    bot = request("getMe", {})
    if bot is None:
        return 1
    print("PASS: bot token accepted by Telegram.")
    chat = request("getChat", {"chat_id": values["TELEGRAM_CHAT_ID"]})
    if chat is None:
        return 1
    if chat.get("type") != "private" or str(chat.get("id")) != values["TELEGRAM_CHAT_ID"]:
        print("FAIL: configured chat ID must identify your numeric private chat.")
        audit("Telegram identity verification failed; private chat mismatch")
        return 1
    print("PASS: configured chat ID resolves to a private chat.")
    # A private bot conversation uses that Telegram user's ID as its chat ID.
    if str(chat["id"]) != values["TELEGRAM_USER_ID"] or str(bot["id"]) == values["TELEGRAM_USER_ID"]:
        print("FAIL: TELEGRAM_USER_ID does not match the user in this private chat.")
        audit("Telegram identity verification failed; user mismatch")
        return 1
    print("PASS: configured user ID matches the private chat ID.")
    audit("Telegram identity verification completed; no messages sent or updates consumed")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception:
        audit("Telegram identity verification failed; private details suppressed")
        print("FAIL: could not read configuration or validate Telegram response; details suppressed.")
        raise SystemExit(1)
