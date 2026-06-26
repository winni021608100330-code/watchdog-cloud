from __future__ import annotations

import difflib
import hashlib
import json
import os
import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import urljoin, urlparse

import requests
import yaml
from bs4 import BeautifulSoup
from PIL import Image, ImageChops, ImageDraw
from playwright.sync_api import sync_playwright


ROOT_DIR = Path(__file__).resolve().parent
DATA_DIR = ROOT_DIR / "data"
HTML_DIR = DATA_DIR / "html"
SCREENSHOT_DIR = DATA_DIR / "screenshots"
STATE_PATH = DATA_DIR / "state.json"
MONITORS_PATH = ROOT_DIR / "monitors.yaml"

REQUEST_TIMEOUT = 30
PAGE_TIMEOUT_MS = 60_000
MAX_DIFF_LINES = 30
MAX_LINE_LENGTH = 180
DEFAULT_IMAGE_THRESHOLD = 0.5
DEFAULT_VIEWPORT = {"width": 1440, "height": 1200}

USER_AGENT = (
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/126.0 Safari/537.36 WatchDogCloud/3.0"
)

REMOVE_ELEMENT_PATTERN = re.compile(
    r"csrf|token|session|timestamp|analytics|advert|(^|[-_])ads?([-_]|$)|"
    r"view[-_]?count|hit[-_]?count|counter|tracking|captcha",
    re.IGNORECASE,
)
VIEW_LINE_PATTERN = re.compile(
    r"(^|\s)(조회|조회수|열람|열람수|views?|hits?|hit)\s*[:：]?\s*[\d,]+($|\s)",
    re.IGNORECASE,
)
TIMESTAMP_LINE_PATTERN = re.compile(
    r"^\s*(?:\d{4}[-./]\d{1,2}[-./]\d{1,2})"
    r"(?:\s+\d{1,2}:\d{2}(?::\d{2})?)?\s*$"
)
DYNAMIC_VALUE_PATTERN = re.compile(r"^[A-Fa-f0-9]{24,}$|^[A-Za-z0-9_=-]{40,}$")


@dataclass
class Monitor:
    name: str
    url: str
    keywords: list[str] = field(default_factory=list)
    mode: str = "text"
    image_threshold: float = DEFAULT_IMAGE_THRESHOLD
    wait_until: str = "networkidle"

    @property
    def key(self) -> str:
        digest = hashlib.sha256(f"{self.name}|{self.url}".encode("utf-8")).hexdigest()[:16]
        return f"{safe_filename(self.name)}_{digest}"

    @property
    def board_mode(self) -> bool:
        host = urlparse(self.url).netloc.lower()
        return self.mode.lower() == "board" or "ksasf.ksa.hs.kr" in host


@dataclass
class PageSnapshot:
    html: str
    visible_text: str
    screenshot_path: Path


@dataclass
class CheckResult:
    monitor: Monitor
    checked_at: str
    html_changed: bool
    text_changed: bool
    image_changed: bool
    keyword_changed: bool
    image_change_rate: float
    added: list[str]
    deleted: list[str]
    keywords_found: list[str]
    screenshot_path: Path
    compare_path: Path | None

    @property
    def changed(self) -> bool:
        meaningful_text_change = bool(self.added or self.deleted)
        return meaningful_text_change or self.keyword_changed


def now_text() -> str:
    return datetime.now(timezone.utc).astimezone().strftime("%Y-%m-%d %H:%M:%S %Z")


def timestamp_for_file() -> str:
    return datetime.now(timezone.utc).astimezone().strftime("%Y%m%d_%H%M%S")


def safe_filename(value: str) -> str:
    cleaned = re.sub(r"[^0-9A-Za-z가-힣_-]+", "_", value.strip())
    return cleaned.strip("_") or "monitor"


def sha256_text(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8", errors="ignore")).hexdigest()


def sha256_lines(lines: list[str]) -> str:
    return sha256_text("\n".join(lines))


def read_json(path: Path, default: Any) -> Any:
    if not path.exists():
        return default
    try:
        return json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, json.JSONDecodeError):
        return default


def write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def default_state() -> dict[str, Any]:
    return {"monitors": {}}


def load_state() -> dict[str, Any]:
    state = read_json(STATE_PATH, default_state())
    if not isinstance(state, dict):
        state = default_state()
    state.setdefault("monitors", {})
    return state


def load_monitors(path: Path) -> list[Monitor]:
    if not path.exists():
        raise FileNotFoundError(f"설정 파일이 없습니다: {path}")

    raw = yaml.safe_load(path.read_text(encoding="utf-8-sig")) or []
    if not isinstance(raw, list):
        raise ValueError("monitors.yaml 최상위 값은 목록이어야 합니다.")

    monitors: list[Monitor] = []
    for item in raw:
        if not isinstance(item, dict):
            continue
        name = str(item.get("name", "")).strip()
        url = str(item.get("url", "")).strip()
        if not name or not url:
            raise ValueError(f"name/url이 비어 있습니다: {item}")

        keywords = item.get("keywords") or []
        if isinstance(keywords, str):
            keywords = [keywords]

        monitors.append(
            Monitor(
                name=name,
                url=url,
                mode=str(item.get("mode", "text")).strip() or "text",
                keywords=[str(keyword).strip() for keyword in keywords if str(keyword).strip()],
                image_threshold=float(item.get("image_threshold", DEFAULT_IMAGE_THRESHOLD)),
                wait_until=str(item.get("wait_until", "networkidle")).strip() or "networkidle",
            )
        )
    return monitors


def ensure_dirs() -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    HTML_DIR.mkdir(parents=True, exist_ok=True)
    SCREENSHOT_DIR.mkdir(parents=True, exist_ok=True)


def html_snapshot_path(monitor: Monitor) -> Path:
    return HTML_DIR / f"{monitor.key}.html"


def save_html_snapshot(monitor: Monitor, html: str) -> None:
    html_snapshot_path(monitor).write_text(html, encoding="utf-8")


def capture_page(monitor: Monitor) -> PageSnapshot:
    filename = f"{timestamp_for_file()}_{monitor.key}.png"
    screenshot_path = SCREENSHOT_DIR / filename

    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        page = browser.new_page(
            viewport=DEFAULT_VIEWPORT,
            user_agent=USER_AGENT,
        )
        page.goto(monitor.url, wait_until=monitor.wait_until, timeout=PAGE_TIMEOUT_MS)
        html = page.content()
        visible_text = page.locator("body").inner_text(timeout=10_000)
        page.screenshot(path=str(screenshot_path), full_page=True)
        browser.close()

    return PageSnapshot(html=html, visible_text=visible_text, screenshot_path=screenshot_path)


def remove_dynamic_elements(soup: BeautifulSoup) -> None:
    for element in soup.select("script, style, noscript, template, iframe, canvas, svg"):
        element.decompose()

    for element in soup.select('input[type="hidden"], [hidden]'):
        element.decompose()

    for element in list(soup.find_all(True)):
        if not getattr(element, "attrs", None):
            continue
        attributes = " ".join(
            str(value) if not isinstance(value, list) else " ".join(map(str, value))
            for key, value in element.attrs.items()
            if key.lower()
            in {
                "id",
                "class",
                "name",
                "data-testid",
                "data-name",
                "aria-label",
                "autocomplete",
            }
        )
        if attributes and REMOVE_ELEMENT_PATTERN.search(attributes):
            element.decompose()


def normalize_line(value: str) -> str:
    return re.sub(r"\s+", " ", value).strip()


def is_dynamic_line(line: str) -> bool:
    return bool(
        not line
        or VIEW_LINE_PATTERN.search(line)
        or TIMESTAMP_LINE_PATTERN.fullmatch(line)
        or DYNAMIC_VALUE_PATTERN.fullmatch(line)
        or re.fullmatch(r"(?:csrf|token|session|timestamp)\s*[:=].*", line, re.IGNORECASE)
    )


def visible_text_lines(html: str, fallback_visible_text: str = "") -> list[str]:
    soup = BeautifulSoup(html, "html.parser")
    remove_dynamic_elements(soup)
    text = soup.get_text("\n")
    if not text.strip() and fallback_visible_text:
        text = fallback_visible_text

    lines = [normalize_line(line) for line in text.splitlines()]
    return [line for line in lines if not is_dynamic_line(line)]


def board_lines(html: str, base_url: str, fallback_visible_text: str = "") -> list[str]:
    soup = BeautifulSoup(html, "html.parser")
    remove_dynamic_elements(soup)
    rows: list[tuple[int, str]] = []

    for row in soup.select("table tbody tr, table tr"):
        link = row.select_one("a[href]")
        if not link:
            continue

        title = normalize_line(link.get_text(" ", strip=True))
        href = str(link.get("href", "")).strip()
        if not title or not href:
            continue

        values = [normalize_line(value) for value in row.stripped_strings]
        number_text = next((value for value in values if re.fullmatch(r"\d+", value)), "")
        sort_number = int(number_text) if number_text else 0
        if not number_text and any("공지" in value for value in values):
            number_text = "공지"
            sort_number = 10**12
        if not number_text:
            continue

        absolute_url = urljoin(base_url, href)
        rows.append((sort_number, f"{number_text} | {title} | {absolute_url}"))

    if not rows:
        return visible_text_lines(html, fallback_visible_text)

    rows.sort(key=lambda item: item[0], reverse=True)
    return [line for _, line in rows]


def normalized_lines(monitor: Monitor, html: str, fallback_visible_text: str = "") -> list[str]:
    if monitor.board_mode:
        return board_lines(html, monitor.url, fallback_visible_text)
    return visible_text_lines(html, fallback_visible_text)


def unique_lines(lines: list[str]) -> list[str]:
    result: list[str] = []
    seen: set[str] = set()
    for line in lines:
        if line and line not in seen:
            seen.add(line)
            result.append(line)
    return result


def text_diff(previous: list[str], current: list[str]) -> tuple[list[str], list[str]]:
    added: list[str] = []
    deleted: list[str] = []
    matcher = difflib.SequenceMatcher(a=previous, b=current, autojunk=False)

    for tag, old_start, old_end, new_start, new_end in matcher.get_opcodes():
        if tag in {"replace", "delete"}:
            deleted.extend(previous[old_start:old_end])
        if tag in {"replace", "insert"}:
            added.extend(current[new_start:new_end])

    return unique_lines(added), unique_lines(deleted)


def image_change_rate(before_path: str | None, after_path: Path) -> float:
    if not before_path or not Path(before_path).exists() or not after_path.exists():
        return 0.0

    with Image.open(before_path).convert("RGB") as before, Image.open(after_path).convert("RGB") as after:
        if before.size != after.size:
            after = after.resize(before.size)
        diff = ImageChops.difference(before, after)
        histogram = diff.histogram()
        changed = sum(count for index, count in enumerate(histogram) if index % 256 != 0)
        total = before.size[0] * before.size[1] * 3
        return (changed / total) * 100 if total else 0.0


def create_compare_image(before_path: str | None, after_path: Path, output_path: Path) -> Path | None:
    if not before_path or not Path(before_path).exists() or not after_path.exists():
        return None

    with Image.open(before_path).convert("RGB") as before, Image.open(after_path).convert("RGB") as after:
        if before.size != after.size:
            after = after.resize(before.size)

        diff = ImageChops.difference(before, after)
        bbox = diff.getbbox()

        before_marked = before.copy()
        after_marked = after.copy()
        if bbox:
            for image in (before_marked, after_marked):
                draw = ImageDraw.Draw(image)
                draw.rectangle(bbox, outline="red", width=5)

        width = before_marked.width + after_marked.width
        height = max(before_marked.height, after_marked.height)
        compare = Image.new("RGB", (width, height), "white")
        compare.paste(before_marked, (0, 0))
        compare.paste(after_marked, (before_marked.width, 0))

        output_path.parent.mkdir(parents=True, exist_ok=True)
        compare.save(output_path)
        return output_path


def limit_diff(added: list[str], deleted: list[str]) -> tuple[list[str], list[str], bool]:
    if len(added) + len(deleted) <= MAX_DIFF_LINES:
        return added, deleted, False

    if added and deleted:
        added_limit = MAX_DIFF_LINES // 2
        deleted_limit = MAX_DIFF_LINES - added_limit
    else:
        added_limit = MAX_DIFF_LINES
        deleted_limit = MAX_DIFF_LINES

    return added[:added_limit], deleted[:deleted_limit], True


def shorten(line: str) -> str:
    if len(line) <= MAX_LINE_LENGTH:
        return line
    return line[: MAX_LINE_LENGTH - 3] + "..."


def telegram_credentials() -> tuple[str, str] | None:
    token = os.environ.get("TELEGRAM_BOT_TOKEN", "").strip()
    chat_id = os.environ.get("TELEGRAM_CHAT_ID", "").strip()
    if not token or not chat_id:
        print("Telegram secrets are not set. Skipping notification.")
        return None
    return token, chat_id


def send_telegram_message(message: str) -> None:
    credentials = telegram_credentials()
    if not credentials:
        return
    token, chat_id = credentials

    response = requests.post(
        f"https://api.telegram.org/bot{token}/sendMessage",
        data={"chat_id": chat_id, "text": message[:4096], "disable_web_page_preview": True},
        timeout=REQUEST_TIMEOUT,
    )
    response.raise_for_status()
    print("Telegram message sent.")


def send_telegram_photo(photo_path: Path, caption: str = "") -> None:
    credentials = telegram_credentials()
    if not credentials:
        return
    token, chat_id = credentials

    with photo_path.open("rb") as file:
        response = requests.post(
            f"https://api.telegram.org/bot{token}/sendPhoto",
            data={"chat_id": chat_id, "caption": caption[:1024]},
            files={"photo": file},
            timeout=REQUEST_TIMEOUT,
        )
    response.raise_for_status()
    print(f"Telegram photo sent: {photo_path}")


def build_change_message(result: CheckResult) -> str:
    added, deleted, truncated = limit_diff(result.added, result.deleted)
    sections = [
        "🚨 페이지 변경 감지",
        "",
        f"이름: {result.monitor.name}",
        f"시간: {result.checked_at}",
        f"이미지 변경률: {result.image_change_rate:.2f}%",
        f"URL: {result.monitor.url}",
        "",
    ]

    if added:
        sections.extend(["추가된 텍스트:", *[f"* {shorten(line)}" for line in added], ""])
    if deleted:
        sections.extend(["삭제된 텍스트:", *[f"* {shorten(line)}" for line in deleted], ""])
    if result.keywords_found:
        sections.extend(["감지 키워드:", *[f"* {keyword}" for keyword in result.keywords_found], ""])
    if truncated:
        sections.extend(["* 변경 내용은 최대 30줄까지만 표시합니다.", ""])

    return "\n".join(sections).strip()


def build_keyword_message(monitor: Monitor, keywords: list[str]) -> str:
    keyword_lines = "\n".join(f"* {keyword}" for keyword in keywords)
    return (
        "🚨 키워드 감지\n\n"
        f"이름: {monitor.name}\n"
        f"시간: {now_text()}\n\n"
        f"감지 키워드:\n{keyword_lines}\n\n"
        f"URL:\n{monitor.url}"
    )


def find_new_keywords(lines: list[str], keywords: list[str], already_alerted: set[str]) -> list[str]:
    text = "\n".join(lines).casefold()
    found: list[str] = []
    for keyword in keywords:
        if keyword in already_alerted:
            continue
        if keyword.casefold() in text:
            found.append(keyword)
    return found


def check_monitor(monitor: Monitor, state: dict[str, Any]) -> CheckResult:
    print(f"Checking: {monitor.name}")
    monitor_state = state["monitors"].setdefault(monitor.key, {})

    previous_html = ""
    previous_snapshot = html_snapshot_path(monitor)
    if previous_snapshot.exists():
        previous_html = previous_snapshot.read_text(encoding="utf-8", errors="replace")

    snapshot = capture_page(monitor)
    current_lines = normalized_lines(monitor, snapshot.html, snapshot.visible_text)
    current_html_hash = sha256_text(snapshot.html)
    current_text_hash = sha256_lines(current_lines)

    added: list[str] = []
    deleted: list[str] = []
    text_changed = False
    if previous_html:
        previous_lines = normalized_lines(monitor, previous_html)
        added, deleted = text_diff(previous_lines, current_lines)
        text_changed = bool(added or deleted)

    html_changed = bool(monitor_state.get("html_hash") and monitor_state["html_hash"] != current_html_hash)
    if html_changed and not text_changed:
        print("HTML changed, but visible content did not change. Ignoring.")

    previous_screenshot = monitor_state.get("last_screenshot")
    image_rate = image_change_rate(previous_screenshot, snapshot.screenshot_path)
    image_changed = image_rate >= monitor.image_threshold

    compare_path = None
    if previous_screenshot and Path(previous_screenshot).exists():
        compare_name = f"{timestamp_for_file()}_{monitor.key}_compare.png"
        compare_path = create_compare_image(previous_screenshot, snapshot.screenshot_path, SCREENSHOT_DIR / compare_name)

    already_alerted = set(monitor_state.get("alerted_keywords", []))
    keywords_found = find_new_keywords(current_lines, monitor.keywords, already_alerted)
    keyword_changed = bool(keywords_found)

    result = CheckResult(
        monitor=monitor,
        checked_at=now_text(),
        html_changed=html_changed,
        text_changed=text_changed,
        image_changed=image_changed,
        keyword_changed=keyword_changed,
        image_change_rate=image_rate,
        added=added,
        deleted=deleted,
        keywords_found=keywords_found,
        screenshot_path=snapshot.screenshot_path,
        compare_path=compare_path,
    )

    monitor_state.update(
        {
            "name": monitor.name,
            "url": monitor.url,
            "html_hash": current_html_hash,
            "text_hash": current_text_hash,
            "last_checked_at": result.checked_at,
            "last_screenshot": str(snapshot.screenshot_path),
            "last_compare": str(compare_path) if compare_path else "",
            "alerted_keywords": sorted(already_alerted | set(keywords_found)),
        }
    )
    save_html_snapshot(monitor, snapshot.html)

    return result


def notify_result(result: CheckResult) -> bool:
    if not result.changed:
        print("No meaningful change.")
        return False

    if result.keyword_changed and not (result.added or result.deleted or result.image_changed):
        send_telegram_message(build_keyword_message(result.monitor, result.keywords_found))
    else:
        send_telegram_message(build_change_message(result))

    if result.compare_path and result.compare_path.exists():
        send_telegram_photo(
            result.compare_path,
            caption=f"{result.monitor.name} 변경 비교 이미지 ({result.image_change_rate:.2f}%)",
        )
    elif result.screenshot_path.exists():
        send_telegram_photo(result.screenshot_path, caption=f"{result.monitor.name} 최신 스크린샷")

    return True


def main() -> None:
    ensure_dirs()
    monitors = load_monitors(MONITORS_PATH)
    state = load_state()

    print(f"WatchDog Cloud started: {now_text()}")
    print(f"Loaded monitors: {len(monitors)}")

    sent_count = 0
    for monitor in monitors:
        try:
            result = check_monitor(monitor, state)
            print(f"HTML changed: {result.html_changed}")
            print(f"Visible text changed: {result.text_changed}")
            print(f"Image change rate: {result.image_change_rate:.2f}%")
            print(f"Keywords found: {', '.join(result.keywords_found) if result.keywords_found else 'none'}")
            if notify_result(result):
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

    write_json(STATE_PATH, state)
    print(f"WatchDog Cloud finished. Notifications sent: {sent_count}")


if __name__ == "__main__":
    main()
