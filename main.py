import html
import json
import math
import os
import re
import sys
import urllib.request
import xml.etree.ElementTree as ET

from datetime import datetime, timedelta, timezone, date, time
from email.utils import parsedate_to_datetime
from zoneinfo import ZoneInfo


# ============================================================
# 設定
# ============================================================

USERNAME = "mega_urtr"

IRIAM_PREFIX = "https://web.iriam.app/s/live/"

RSS_URLS = [
    f"https://fxtwitter.com/{USERNAME}/feed.xml?count=100",
    f"https://fxtwitter.com/{USERNAME}/feed.atom.xml?count=100",
]

OUTPUT_ICS = "calendar.ics"
DATA_FILE = "data.json"

JST = ZoneInfo("Asia/Tokyo")
UTC = timezone.utc

# 何日先まで予測するか
PREDICTION_DAYS = 60

# 平均時刻に使用する直近の投稿数
HISTORY_LIMIT = 60

# 1イベントの長さ
EVENT_DURATION_MINUTES = 30

# RSS取得タイムアウト
HTTP_TIMEOUT = 20


# ============================================================
# 曜日予測設定
# ============================================================

# 曜日番号
# 月=0 火=1 水=2 木=3 金=4 土=5 日=6

WEEKDAY_NAMES = [
    "月",
    "火",
    "水",
    "木",
    "金",
    "土",
    "日",
]

# 最低でもこの件数以上ある曜日を候補にする
MIN_WEEKDAY_COUNT = 2

# 最大出現数に対してこの割合以上なら「主要曜日」
CORE_WEEKDAY_RATIO = 0.50

# 最大出現数に対してこの割合以上なら「準主要曜日」
# ただし、予測頻度を下げる
SECONDARY_WEEKDAY_RATIO = 0.35


# ============================================================
# 基本処理
# ============================================================

def now_utc():
    return datetime.now(UTC)


def iso(dt):
    return dt.astimezone(UTC).isoformat()


def load_data():
    if not os.path.exists(DATA_FILE):
        return {
            "posts": [],
            "last_success": None,
            "source": None
        }

    try:
        with open(DATA_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)

        if not isinstance(data, dict):
            raise ValueError("data.jsonの形式が不正です")

        if not isinstance(data.get("posts"), list):
            data["posts"] = []

        return data

    except Exception as e:
        print(f"[WARN] data.json読み込み失敗: {e}")

        return {
            "posts": [],
            "last_success": None,
            "source": None
        }


def save_data(data):
    temp_file = DATA_FILE + ".tmp"

    with open(temp_file, "w", encoding="utf-8") as f:
        json.dump(
            data,
            f,
            ensure_ascii=False,
            indent=2
        )

    os.replace(temp_file, DATA_FILE)


# ============================================================
# RSS取得
# ============================================================

def fetch_url(url):
    print(f"[INFO] RSS取得: {url}")

    request = urllib.request.Request(
        url,
        headers={
            "User-Agent": (
                "Mozilla/5.0 "
                "(compatible; MegaUrtrCalendar/1.0)"
            ),
            "Accept": (
                "application/rss+xml, "
                "application/atom+xml, "
                "application/xml, "
                "text/xml"
            ),
            "Cache-Control": "no-cache"
        }
    )

    with urllib.request.urlopen(
        request,
        timeout=HTTP_TIMEOUT
    ) as response:

        status = getattr(response, "status", 200)

        if status != 200:
            raise RuntimeError(
                f"HTTPステータス: {status}"
            )

        body = response.read()

        if not body:
            raise RuntimeError("RSSが空です")

        return body


def fetch_feed():
    errors = []

    for url in RSS_URLS:
        try:
            body = fetch_url(url)
            root = ET.fromstring(body)

            print(f"[INFO] RSS取得成功: {url}")

            return root, url

        except Exception as e:
            print(f"[WARN] RSS取得失敗: {url}")
            print(f"       {e}")

            errors.append(
                f"{url}: {e}"
            )

    raise RuntimeError(
        "すべてのRSS取得先に失敗しました。\n"
        + "\n".join(errors)
    )


# ============================================================
# XML処理
# ============================================================

def local_name(tag):
    if "}" in tag:
        return tag.split("}", 1)[1]

    return tag


def child_text(element, names):
    names = set(names)

    for child in list(element):
        if local_name(child.tag) in names:
            if child.text:
                return child.text.strip()

    return ""


def find_items(root):
    items = []

    for element in root.iter():
        name = local_name(element.tag)

        if name in ("item", "entry"):
            items.append(element)

    return items


# ============================================================
# IRIAM URL
# ============================================================

def extract_iriam_url(text):
    if not text:
        return None

    pattern = (
        re.escape(IRIAM_PREFIX)
        + r"[A-Za-z0-9_-]+"
    )

    match = re.search(
        pattern,
        text
    )

    if match:
        return match.group(0)

    return None


# ============================================================
# 日時
# ============================================================

def parse_datetime(value):
    if not value:
        return None

    value = value.strip()

    # RSS / RFC822
    try:
        dt = parsedate_to_datetime(value)

        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=UTC)

        return dt.astimezone(JST)

    except Exception:
        pass

    # ISO8601
    try:
        normalized = value.replace(
            "Z",
            "+00:00"
        )

        dt = datetime.fromisoformat(
            normalized
        )

        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=UTC)

        return dt.astimezone(JST)

    except Exception:
        pass

    return None


def extract_post_datetime(item):
    value = child_text(
        item,
        [
            "pubDate",
            "published",
            "date"
        ]
    )

    dt = parse_datetime(value)

    if dt:
        return dt

    value = child_text(
        item,
        ["updated"]
    )

    return parse_datetime(value)


# ============================================================
# X投稿ID / URL
# ============================================================

def extract_post_id(item):
    guid = child_text(
        item,
        ["guid"]
    )

    if guid:
        match = re.search(
            r"/status/(\d+)",
            guid
        )

        if match:
            return match.group(1)

        return guid

    atom_id = child_text(
        item,
        ["id"]
    )

    if atom_id:
        match = re.search(
            r"/status/(\d+)",
            atom_id
        )

        if match:
            return match.group(1)

        return atom_id

    link = child_text(
        item,
        ["link"]
    )

    if link:
        match = re.search(
            r"/status/(\d+)",
            link
        )

        if match:
            return match.group(1)

        return link

    return None


def extract_post_link(item):
    guid = child_text(
        item,
        ["guid"]
    )

    if guid and (
        "x.com" in guid
        or "twitter.com" in guid
    ):
        return guid

    link = child_text(
        item,
        ["link"]
    )

    return link


# ============================================================
# RSS → IRIAM対象投稿
# ============================================================

def parse_matching_posts(root):
    items = find_items(root)

    print(
        f"[INFO] RSS内アイテム数: {len(items)}"
    )

    posts = []

    for item in items:

        title = child_text(
            item,
            ["title"]
        )

        description = child_text(
            item,
            [
                "description",
                "content",
                "encoded"
            ]
        )

        link = extract_post_link(item)

        combined = " ".join([
            title,
            description,
            link or ""
        ])

        combined = html.unescape(
            combined
        )

        # IRIAM URLを含む投稿だけ
        iriam_url = extract_iriam_url(
            combined
        )

        if not iriam_url:
            continue

        post_id = extract_post_id(
            item
        )

        if not post_id:
            print(
                "[WARN] 投稿IDを取得できないためスキップ"
            )
            continue

        posted_at = extract_post_datetime(
            item
        )

        if not posted_at:
            print(
                "[WARN] 投稿日時を取得できないためスキップ: "
                f"{post_id}"
            )
            continue

        posts.append({
            "id": str(post_id),
            "datetime": posted_at.isoformat(),
            "url": link,
            "iriam_url": iriam_url
        })

    return posts


# ============================================================
# 投稿履歴統合
# ============================================================

def merge_posts(data, new_posts):
    existing = data.get(
        "posts",
        []
    )

    by_id = {}

    for post in existing:
        if post.get("id"):
            by_id[str(post["id"])] = post

    added = 0

    for post in new_posts:

        post_id = str(
            post["id"]
        )

        if post_id not in by_id:

            by_id[post_id] = post
            added += 1

        else:

            old = by_id[post_id]

            for key in [
                "datetime",
                "url",
                "iriam_url"
            ]:

                if post.get(key):
                    old[key] = post[key]

    merged = list(
        by_id.values()
    )

    merged.sort(
        key=lambda x: x.get(
            "datetime",
            ""
        )
    )

    data["posts"] = merged

    return added


# ============================================================
# 有効な投稿日時
# ============================================================

def valid_post_datetimes(posts):
    result = []

    for post in posts:

        try:

            dt = datetime.fromisoformat(
                post["datetime"]
            )

            if dt.tzinfo is None:
                dt = dt.replace(
                    tzinfo=JST
                )

            dt = dt.astimezone(JST)

            result.append(dt)

        except Exception:
            continue

    result.sort()

    return result


# ============================================================
# 円環平均時刻
# ============================================================

def circular_average_minutes(datetimes):

    if not datetimes:
        return None

    minutes = []

    for dt in datetimes:

        value = (
            dt.hour * 60
            + dt.minute
            + dt.second / 60
        )

        minutes.append(value)

    x = 0
    y = 0

    for minute_value in minutes:

        angle = (
            2
            * math.pi
            * minute_value
            / 1440
        )

        x += math.cos(angle)
        y += math.sin(angle)

    if x == 0 and y == 0:

        return round(
            sum(minutes)
            / len(minutes)
        )

    angle = math.atan2(
        y,
        x
    )

    if angle < 0:
        angle += 2 * math.pi

    average = (
        angle
        / (2 * math.pi)
        * 1440
    )

    return round(
        average
    ) % 1440


# ============================================================
# 曜日別データ分析
# ============================================================

def analyze_weekdays(posts):

    dates = valid_post_datetimes(
        posts
    )

    dates = dates[
        -HISTORY_LIMIT:
    ]

    weekday_data = {}

    for weekday in range(7):
        weekday_data[weekday] = {
            "count": 0,
            "times": []
        }

    for dt in dates:

        weekday = dt.weekday()

        weekday_data[weekday]["count"] += 1
        weekday_data[weekday]["times"].append(dt)

    counts = [
        weekday_data[i]["count"]
        for i in range(7)
    ]

    max_count = max(counts) if counts else 0

    print("")
    print("[INFO] 曜日別配信実績")

    for weekday in range(7):

        count = weekday_data[weekday]["count"]

        print(
            f"       {WEEKDAY_NAMES[weekday]}曜日: "
            f"{count}件"
        )

    print("")

    if max_count == 0:
        return weekday_data

    # ----------------------------------------
    # 主要曜日・準主要曜日の判定
    # ----------------------------------------

    for weekday in range(7):

        count = weekday_data[weekday]["count"]

        if count < MIN_WEEKDAY_COUNT:

            weekday_data[weekday]["level"] = "none"

        elif count >= max_count * CORE_WEEKDAY_RATIO:

            weekday_data[weekday]["level"] = "core"

        elif count >= max_count * SECONDARY_WEEKDAY_RATIO:

            weekday_data[weekday]["level"] = "secondary"

        else:

            weekday_data[weekday]["level"] = "none"

        times = weekday_data[weekday]["times"]

        weekday_data[weekday]["average_minutes"] = (
            circular_average_minutes(times)
        )

    print("[INFO] 予測対象曜日")

    for weekday in range(7):

        info = weekday_data[weekday]

        if info["level"] == "core":

            print(
                f"       {WEEKDAY_NAMES[weekday]}曜日: "
                f"主要曜日"
            )

        elif info["level"] == "secondary":

            print(
                f"       {WEEKDAY_NAMES[weekday]}曜日: "
                f"準主要曜日"
            )

    print("")

    return weekday_data


# ============================================================
# 予測対象曜日かどうか
# ============================================================

def should_predict_weekday(
    weekday_data,
    weekday,
    target_day
):
    info = weekday_data[weekday]

    level = info.get(
        "level",
        "none"
    )

    if level == "core":
        return True

    if level != "secondary":
        return False

    # --------------------------------------------------------
    # 準主要曜日は毎週ではなく、約2週間に1回程度にする
    # --------------------------------------------------------

    # その日付を基準にした簡易ローテーション
    # 同じ曜日でも毎週予測されないようにする

    week_number = (
        target_day.toordinal()
        // 7
    )

    return week_number % 2 == 0


# ============================================================
# 実績イベント
# ============================================================

def make_real_event(post):

    dt = datetime.fromisoformat(
        post["datetime"]
    ).astimezone(JST)

    end = dt + timedelta(
        minutes=EVENT_DURATION_MINUTES
    )

    post_id = str(
        post["id"]
    )

    return {
        "uid": (
            f"mega-iriam-real-"
            f"{post_id}"
            f"@calendar-mega"
        ),

        "dtstart": dt,
        "dtend": end,

        "summary": "【配信開始】IRIAMライブ",

        "description": (
            "X投稿から検出したIRIAM配信\n"
            f"{post.get('iriam_url', '')}\n"
            f"X: {post.get('url', '')}"
        ),

        "url": (
            post.get("iriam_url")
            or post.get("url")
            or ""
        ),

        "type": "real"
    }


# ============================================================
# 予測イベント
# ============================================================

def make_prediction_event(
    target_day,
    average_minutes
):

    hour = average_minutes // 60
    minute = average_minutes % 60

    dt = datetime.combine(
        target_day,
        time(
            hour=hour,
            minute=minute
        ),
        tzinfo=JST
    )

    end = dt + timedelta(
        minutes=EVENT_DURATION_MINUTES
    )

    return {
        "uid": (
            f"mega-iriam-prediction-"
            f"{target_day.isoformat()}"
            f"@calendar-mega"
        ),

        "dtstart": dt,
        "dtend": end,

        "summary": "【予測】IRIAMライブ",

        "description": (
            "過去の同曜日のIRIAM配信投稿時刻から"
            "算出した予測です。"
        ),

        "url": "",

        "type": "prediction"
    }


# ============================================================
# カレンダーイベント生成
# ============================================================

def build_events(posts):

    events = []

    # ----------------------------------------
    # 実際の投稿
    # ----------------------------------------

    real_dates = set()

    for post in posts:

        try:

            event = make_real_event(
                post
            )

            events.append(
                event
            )

            real_dates.add(
                event["dtstart"].date()
            )

        except Exception as e:

            print(
                "[WARN] 実績イベント作成失敗: "
                f"{e}"
            )

    # ----------------------------------------
    # 曜日分析
    # ----------------------------------------

    weekday_data = analyze_weekdays(
        posts
    )

    # ----------------------------------------
    # 未来予測
    # ----------------------------------------

    now = datetime.now(
        JST
    )

    today = now.date()

    prediction_count = 0

    for offset in range(
        0,
        PREDICTION_DAYS + 1
    ):

        target_day = (
            today
            + timedelta(days=offset)
        )

        weekday = target_day.weekday()

        # 実際の投稿がある日は予測しない
        if target_day in real_dates:
            continue

        # この曜日を予測対象にするか
        if not should_predict_weekday(
            weekday_data,
            weekday,
            target_day
        ):
            continue

        average_minutes = weekday_data[
            weekday
        ].get(
            "average_minutes"
        )

        if average_minutes is None:
            continue

        prediction = make_prediction_event(
            target_day,
            average_minutes
        )

        # 今日の予測時刻を過ぎている場合は作らない
        if (
            target_day == today
            and prediction["dtstart"] <= now
        ):
            continue

        events.append(
            prediction
        )

        prediction_count += 1

    print(
        f"[INFO] 予測イベント: "
        f"{prediction_count}件"
    )

    return events


# ============================================================
# ICS
# ============================================================

def ics_escape(value):

    if value is None:
        return ""

    value = str(value)

    value = value.replace(
        "\\",
        "\\\\"
    )

    value = value.replace(
        ";",
        "\\;"
    )

    value = value.replace(
        ",",
        "\\,"
    )

    value = value.replace(
        "\r\n",
        "\\n"
    )

    value = value.replace(
        "\n",
        "\\n"
    )

    value = value.replace(
        "\r",
        "\\n"
    )

    return value


def ics_fold(line):

    result = []
    current = ""

    for char in line:

        candidate = (
            current + char
        )

        if len(
            candidate.encode(
                "utf-8"
            )
        ) > 75:

            result.append(
                current
            )

            current = (
                " " + char
            )

        else:

            current = candidate

    if current:
        result.append(
            current
        )

    return "\r\n".join(
        result
    )


def format_local(dt):

    return dt.astimezone(
        JST
    ).strftime(
        "%Y%m%dT%H%M%S"
    )


def format_utc(dt):

    return dt.astimezone(
        UTC
    ).strftime(
        "%Y%m%dT%H%M%SZ"
    )


def generate_ics(events):

    events.sort(
        key=lambda event:
        event["dtstart"]
    )

    lines = [
        "BEGIN:VCALENDAR",
        "VERSION:2.0",
        "PRODID:-//Koqi//Calendar-Mega//JP",
        "CALSCALE:GREGORIAN",
        "METHOD:PUBLISH",
        "X-WR-CALNAME:メガ・ウルトラギガ IRIAM配信",
        "X-WR-TIMEZONE:Asia/Tokyo"
    ]

    for event in events:

        lines.append(
            "BEGIN:VEVENT"
        )

        lines.append(
            f"UID:{ics_escape(event['uid'])}"
        )

        lines.append(
            f"DTSTAMP:{format_utc(now_utc())}"
        )

        lines.append(
            "DTSTART;TZID=Asia/Tokyo:"
            f"{format_local(event['dtstart'])}"
        )

        lines.append(
            "DTEND;TZID=Asia/Tokyo:"
            f"{format_local(event['dtend'])}"
        )

        lines.append(
            f"SUMMARY:{ics_escape(event['summary'])}"
        )

        lines.append(
            "DESCRIPTION:"
            f"{ics_escape(event['description'])}"
        )

        if event["url"]:

            lines.append(
                f"URL:{ics_escape(event['url'])}"
            )

        lines.append(
            "END:VEVENT"
        )

    lines.append(
        "END:VCALENDAR"
    )

    return "\r\n".join(
        ics_fold(line)
        for line in lines
    ) + "\r\n"


def save_ics(ics):

    temp_file = (
        OUTPUT_ICS + ".tmp"
    )

    with open(
        temp_file,
        "w",
        encoding="utf-8",
        newline=""
    ) as f:

        f.write(ics)

    os.replace(
        temp_file,
        OUTPUT_ICS
    )


# ============================================================
# メイン
# ============================================================

def main():

    print(
        "========================================"
    )

    print(
        "Mega-Urtr IRIAM Calendar Generator"
    )

    print(
        "========================================"
    )

    data = load_data()

    old_posts_count = len(
        data.get(
            "posts",
            []
        )
    )

    # ----------------------------------------
    # RSS取得
    # ----------------------------------------

    try:

        root, source_url = fetch_feed()

    except Exception as e:

        print(
            "[ERROR] RSS取得に失敗しました"
        )

        print(
            str(e)
        )

        print(
            "[SAFE] 既存のcalendar.icsは"
            "変更しません。"
        )

        sys.exit(1)

    # ----------------------------------------
    # 対象投稿解析
    # ----------------------------------------

    new_posts = parse_matching_posts(
        root
    )

    print(
        "[INFO] IRIAM対象投稿: "
        f"{len(new_posts)}件"
    )

    # ----------------------------------------
    # RSS取得成功だが0件
    # ----------------------------------------

    if (
        len(new_posts) == 0
        and old_posts_count > 0
    ):

        print(
            "[WARN] RSSは取得できましたが、"
            "IRIAM対象投稿が0件です。"
        )

        print(
            "[SAFE] 既存の投稿履歴を保持します。"
        )

    # ----------------------------------------
    # 履歴統合
    # ----------------------------------------

    added = merge_posts(
        data,
        new_posts
    )

    print(
        "[INFO] 新規追加: "
        f"{added}件"
    )

    data["last_success"] = (
        iso(now_utc())
    )

    data["source"] = source_url

    # ----------------------------------------
    # カレンダー生成
    # ----------------------------------------

    events = build_events(
        data["posts"]
    )

    real_count = len([
        event
        for event in events
        if event["type"] == "real"
    ])

    prediction_count = len([
        event
        for event in events
        if event["type"] == "prediction"
    ])

    print(
        "[INFO] 実績イベント: "
        f"{real_count}件"
    )

    print(
        "[INFO] 予測イベント: "
        f"{prediction_count}件"
    )

    # ----------------------------------------
    # ICS生成
    # ----------------------------------------

    ics = generate_ics(
        events
    )

    # ----------------------------------------
    # 安全確認
    # ----------------------------------------

    if "BEGIN:VEVENT" not in ics:

        print(
            "[ERROR] VEVENTが1件も生成されませんでした。"
        )

        if os.path.exists(
            OUTPUT_ICS
        ):

            print(
                "[SAFE] 既存calendar.icsを保持します。"
            )

            save_data(
                data
            )

            sys.exit(0)

        print(
            "[INFO] 初回実行で対象投稿がないため、"
            "calendar.icsは作成しません。"
        )

        save_data(
            data
        )

        sys.exit(0)

    # ----------------------------------------
    # 保存
    # ----------------------------------------

    save_data(
        data
    )

    save_ics(
        ics
    )

    print(
        "========================================"
    )

    print(
        "完了"
    )

    print(
        "========================================"
    )

    print(
        "保存済み対象投稿: "
        f"{len(data['posts'])}件"
    )

    print(
        "カレンダーイベント: "
        f"{len(events)}件"
    )


if __name__ == "__main__":
    main()
