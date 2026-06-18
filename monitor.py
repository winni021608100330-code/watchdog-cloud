from __future__ import annotations

import hashlib
import html
import json
import os
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import requests
from bs4 import BeautifulSoup


ROOT_DIR = Path(__file__).resolve().parent
DATA_DIR = ROOT_DIR / "data"
HTML_DIR = DATA_DIR / "html"
HASHES_PATH = DATA_DIR / "hashes.json"
KEYWORD_ALERTS_PATH = DATA_DIR / "keyword_alerts.json"
MONITORS_PATH = ROOT_DIR / "monitors.yaml"

REQUEST_TIMEOUT = 30
USER_AGENT = "WatchDog Cloud/1.0 (+GitHub Actions webpage monitor)"


@dataclass
class Monitor:
    name: str
    url: str
    keywords: list[str]

    @property
    def key(self) -> str:
        digest = hashlib.sha256(f"{self.name}|{self.url}".encode("utf-8")).hexdigest()[:16]
        return f"{safe_filename(self.name)}_{digest}"


def now_text() -> str:
    return datetime.now(timezone.utc).astimezone().strftime("%Y-%m-%d %H:%M:%S %Z")


def safe_filename(value: str) -> str:
    cleaned = re.sub(r"[^0-9A-Za-z가-힣._-]+", "_", value.strip())
    return cleaned.strip("_") or "monitor"


def read_json(path: Path, default: Any) -> Any:
    if not path.exists():
        return default
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return default


def write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def clean_yaml_value(value: str) -> str:
    value = value.strip()
    if value.startswith("- "):
        value = value[2:].strip()
    if (value.startswith('"') and value.endswith('"')) or (value.startswith("'") and value.endswith("'")):
        value = value[1:-1]
    return value.strip()


def load_monitors(path: Path) -> list[Monitor]:
    """Parse the simple monitors.yaml format used by this project.

    This intentionally supports only the beginner-friendly structure in README.md,
    so the project does not need PyYAML.
    """
    if not path.exists():
        raise FileNotFoundError(f"Missing config file: {path}")

    monitors: list[dict[str, Any]] = []
    current: dict[str, Any] | None = None
    in_keywords = False

    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.rstrip()
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue

        if stripped.startswith("- name:"):
            if current:
                monitors.append(current)
            current = {"name": clean_yaml_value(stripped.split(":", 1)[1]), "url": "", "keywords": []}
            in_keywords = False
            continue

        if current is None:
            continue

        if stripped.startswith("url:"):
            current["url"] = clean_yaml_value(stripped.split(":", 1)[1])
            in_keywords = False
            continue

        if stripped.startswith("keywords:"):
            in_keywords = True
            continue

        if in_keywords and stripped.startswith("- "):
            current["keywords"].append(clean_yaml_value(stripped))

    if current:
        monitors.append(current)

    parsed: list[Monitor] = []
    for item in monitors:
        name = str(item.get("name", "")).strip()
        url = str(item.get("url", "")).strip()
        keywords = [str(keyword).strip() for keyword in item.get("keywords", []) if str(keyword).strip()]
        if not name or not url:
            raise ValueError(f"Invalid monitor entry. name and url are required: {item}")
        parsed.append(Monitor(name=name, url=url, keywords=keywords))
    return parsed


def fetch_html(url: str) -> str:
    response = requests.get(
        url,
        headers={"User-Agent": USER_AGENT},
        timeout=REQUEST_TIMEOUT,
    )
    response.raise_for_status()
    response.encoding = response.apparent_encoding or response.encoding
    return response.text


def html_hash(content: str) -> str:
    return hashlib.sha256(content.encode("utf-8", errors="ignore")).hexdigest()


def page_text(content: str) -> str:
    soup = BeautifulSoup(content, "html.parser")
    return soup.get_text("\n", strip=True)


def telegram_enabled() -> bool:
    return bool(os.environ.get("TELEGRAM_BOT_TOKEN") and os.environ.get("TELEGRAM_CHAT_ID"))


def send_telegram_message(message: str) -> None:
    token = os.environ.get("TELEGRAM_BOT_TOKEN", "").strip()
    chat_id = os.environ.get("TELEGRAM_CHAT_ID", "").strip()
    if not token or not chat_id:
        print("Telegram secrets are not set. Skipping notification.")
        return

    url = f"https://api.telegram.org/bot{token}/sendMessage"
    response = requests.post(
        url,
        data={
            "chat_id": chat_id,
            "text": message,
            "disable_web_page_preview": False,
        },
        timeout=REQUEST_TIMEOUT,
    )
    response.raise_for_status()


def build_change_message(monitor: Monitor, reason: str, changed_keywords: list[str] | None = None) -> str:
    keyword_block = ""
    if changed_keywords:
        keyword_block = "\n감지 키워드:\n" + "\n".join(f"- {keyword}" for keyword in changed_keywords)

    return (
        "🚨 WatchDog Cloud 알림\n\n"
        f"이름: {monitor.name}\n"
        f"시간: {now_text()}\n"
        f"사유: {reason}\n"
        f"URL: {monitor.url}"
        f"{keyword_block}"
    )


def save_html_snapshot(monitor: Monitor, content: str) -> Path:
    HTML_DIR.mkdir(parents=True, exist_ok=True)
    path = HTML_DIR / f"{monitor.key}.html"
    path.write_text(content, encoding="utf-8")
    return path


def check_monitor(monitor: Monitor, hashes: dict[str, str], keyword_alerts: dict[str, list[str]]) -> bool:
    print(f"Checking {monitor.name}: {monitor.url}")

    content = fetch_html(monitor.url)
    save_html_snapshot(monitor, content)

    current_hash = html_hash(content)
    previous_hash = hashes.get(monitor.key)
    changed = previous_hash is not None and previous_hash != current_hash
    first_run = previous_hash is None

    hashes[monitor.key] = current_hash

    text_for_keyword_scan = f"{page_text(content)}\n{html.unescape(content)}"
    already_alerted = set(keyword_alerts.get(monitor.key, []))
    newly_found_keywords = [
        keyword
        for keyword in monitor.keywords
        if keyword not in already_alerted and keyword.lower() in text_for_keyword_scan.lower()
    ]

    notification_sent = False

    if newly_found_keywords:
        keyword_alerts[monitor.key] = sorted(already_alerted | set(newly_found_keywords))
        send_telegram_message(build_change_message(monitor, "키워드 최초 감지", newly_found_keywords))
        notification_sent = True
        print(f"Keyword alert: {monitor.name} -> {', '.join(newly_found_keywords)}")

    if changed:
        send_telegram_message(build_change_message(monitor, "HTML 변경 감지"))
        notification_sent = True
        print(f"HTML changed: {monitor.name}")
    elif first_run:
        print(f"First run baseline saved: {monitor.name}")
    else:
        print(f"No HTML change: {monitor.name}")

    return notification_sent


def main() -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    HTML_DIR.mkdir(parents=True, exist_ok=True)

    monitors = load_monitors(MONITORS_PATH)
    hashes = read_json(HASHES_PATH, {})
    keyword_alerts = read_json(KEYWORD_ALERTS_PATH, {})

    print(f"WatchDog Cloud started: {now_text()}")
    print(f"Loaded monitors: {len(monitors)}")

    sent_count = 0
    for monitor in monitors:
        try:
            if check_monitor(monitor, hashes, keyword_alerts):
                sent_count += 1
        except Exception as exc:
            print(f"ERROR: {monitor.name}: {exc}")
            send_telegram_message(
                "⚠️ WatchDog Cloud 오류\n\n"
                f"이름: {monitor.name}\n"
                f"시간: {now_text()}\n"
                f"오류: {exc}\n"
                f"URL: {monitor.url}"
            )

    write_json(HASHES_PATH, hashes)
    write_json(KEYWORD_ALERTS_PATH, keyword_alerts)
    print(f"WatchDog Cloud finished. Notifications sent: {sent_count}")


if __name__ == "__main__":
    main()
