import os
import re
import json
import math
import tempfile
import html

from datetime import datetime, timedelta, timezone, time
from urllib.request import Request, urlopen
from urllib.error import HTTPError, URLError
from xml.etree import ElementTree as ET
from email.utils import parsedate_to_datetime
from zoneinfo import ZoneInfo


USERNAME = "mega_urtr"

RSS_URLS = [
    f"https://fxtwitter.com/{USERNAME}/feed.xml?count=100",
    f"https://fxtwitter.com/{USERNAME}/feed.atom.xml?count=100",
]

DATA_FILE = "data.json"
ICS_FILE = "calendar.ics"

JST = ZoneInfo("Asia/Tokyo")

PREDICTION_DAYS = 60
MIN_ACTIVE_DAYS = 7
EVENT_DURATION_MINUTES = 30

IRIAM_PATTERN = re.compile(
    r"https://web\.iriam\.app/s/live/[A-Za-z0-9_-]+",
    re.IGNORECASE
)

STATUS_PATTERN = re.compile(
    r"status[/:](\d+)",
    re.IGNORECASE
)


def atomic_write(path, content):
    directory = os.path.dirname(os.path.abspath(path)) or "."

    fd, temp_path = tempfile.mkstemp(
        prefix=".tmp_",
        dir=directory,
        text=True
    )

    try:
        with os.fdopen(
            fd,
            "w",
            encoding="utf-8",
            newline=""
        ) as f:
            f.write(content)

        os.replace(temp_path, path)

    except Exception:
        try:
            os.remove(temp_path)
        except OSError:
            pass
        raise


def parse_datetime(value):
    if not value:
        return None

    value = value.strip()

    try:
        dt = datetime.fromisoformat(
            value.replace("Z", "+00:00")
        )

        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)

        return dt.astimezone(JST)

    except ValueError:
        pass

    try:
        dt = parsedate_to_datetime(value)

        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)

        return dt.astimezone(JST)

    except (TypeError, ValueError):
        return None


def normalize_text(value):
    if not value:
        return ""

    value = html.unescape(value)

    value = re.sub(
        r"<br\s*/?>",
        "\n",
        value,
        flags=re.IGNORECASE
    )

    value = re.sub(
        r"<[^>]+>",
        "",
        value
    )

    return value.strip()


def fetch_url(url):
    request = Request(
        url,
        headers={
            "User-Agent": (
                "Mozilla/5.0 "
                "(compatible; Calendar-Mega/1.0; "
                "+https://github.com/Unofficial-site/Calendar-Mega)"
            ),
            "Accept": (
                "application/rss+xml, "
                "application/atom+xml, "
                "application/xml, "
                "text/xml, "
                "*/*"
            ),
        }
    )

    with urlopen(request, timeout=20) as response:
        return response.read()


def get_element_text(element, names):
    for name in names:
        child = element.find(name)

        if child is not None and child.text:
            return child.text.strip()

        for child in element.iter():
            tag = child.tag

            if isinstance(tag, str):
                local_name = tag.split("}")[-1]

                if local_name == name and child.text:
                    return child.text.strip()

    return ""


def extract_post_id(item):
    candidates = []

    for child in item.iter():
        tag = child.tag

        if not isinstance(tag, str):
            continue

        local_name = tag.split("}")[-1].lower()

        if local_name in ("link", "id", "guid"):
            if child.text:
                candidates.append(child.text.strip())

            href = child.attrib.get("href")

            if href:
                candidates.append(href)

    for value in candidates:
        match = STATUS_PATTERN.search(value)

        if match:
            return match.group(1)

    return None


def parse_feed(xml_bytes):
    root = ET.fromstring(xml_bytes)

    posts = []
    items = []

    for element in root.iter():
        tag = element.tag

        if not isinstance(tag, str):
            continue

        local_name = tag.split("}")[-1].lower()

        if local_name in ("item", "entry"):
            items.append(element)

    for item in items:
        post_id = extract_post_id(item)

        if not post_id:
            continue

        title = get_element_text(
            item,
            ["title"]
        )

        description = get_element_text(
            item,
            ["description", "summary", "content"]
        )

        link = ""

        for child in item.iter():
            tag = child.tag

            if not isinstance(tag, str):
                continue

            local_name = tag.split("}")[-1].lower()

            if local_name == "link":
                href = child.attrib.get("href")

                if href:
                    link = href
                    break

                if child.text:
                    link = child.text.strip()
                    break

        published = get_element_text(
            item,
            [
                "pubDate",
                "published",
                "updated",
                "date"
            ]
        )

        dt = parse_datetime(published)

        if dt is None:
            continue

        text = normalize_text(
            title + "\n" + description
        )

        iriam_match = IRIAM_PATTERN.search(text)

        if not iriam_match:
            continue

        iriam_url = iriam_match.group(0)

        if not link:
            link = (
                f"https://x.com/"
                f"{USERNAME}/status/{post_id}"
            )

        posts.append({
            "post_id": str(post_id),
            "datetime": dt.isoformat(),
            "text": text,
            "url": link,
            "iriam_url": iriam_url,
        })

    return posts


def fetch_posts():
    errors = []
    all_posts = {}

    for rss_url in RSS_URLS:
        try:
            xml_bytes = fetch_url(rss_url)
            posts = parse_feed(xml_bytes)

            if posts:
                for post in posts:
                    all_posts[post["post_id"]] = post

                print(
                    f"RSS取得成功: {rss_url} "
                    f"({len(posts)}件)"
                )
            else:
                print(
                    f"RSS取得成功だが対象投稿なし: "
                    f"{rss_url}"
                )

        except HTTPError as e:
            errors.append(
                f"{rss_url}: HTTP {e.code}"
            )

        except URLError as e:
            errors.append(
                f"{rss_url}: {e.reason}"
            )

        except Exception as e:
            errors.append(
                f"{rss_url}: {e}"
            )

    if not all_posts:
        error_text = "\n".join(errors)

        raise RuntimeError(
            "すべてのRSSから有効な投稿を取得できませんでした。\n"
            + error_text
        )

    posts = list(all_posts.values())

    posts.sort(
        key=lambda x: x["datetime"]
    )

    return posts


def load_data():
    if not os.path.exists(DATA_FILE):
        return {"posts": []}

    try:
        with open(
            DATA_FILE,
            "r",
            encoding="utf-8"
        ) as f:
            data = json.load(f)

        if not isinstance(data, dict):
            return {"posts": []}

        posts = data.get("posts", [])

        if not isinstance(posts, list):
            posts = []

        return {"posts": posts}

    except Exception:
        print(
            "data.jsonを読み込めなかったため、"
            "空の履歴として扱います。"
        )

        return {"posts": []}


def merge_posts(old_posts, new_posts):
    merged = {}

    for post in old_posts:
        post_id = post.get("post_id")

        if post_id:
            merged[str(post_id)] = post

    for post in new_posts:
        post_id = post.get("post_id")

        if post_id:
            merged[str(post_id)] = post

    result = list(merged.values())

    result.sort(
        key=lambda x: x.get("datetime", "")
    )

    return result


def build_daily_history(posts):
    daily = {}

    for post in posts:
        dt = parse_datetime(
            post.get("datetime", "")
        )

        if dt is None:
            continue

        date = dt.date()

        if date not in daily:
            daily[date] = []

        daily[date].append(dt)

    for date in daily:
        daily[date].sort()

    return daily


def calculate_weekday_probabilities(daily_history):
    if not daily_history:
        return {}

    first_date = min(daily_history.keys())
    last_date = max(daily_history.keys())

    weekday_total = {
        weekday: 0
        for weekday in range(7)
    }

    weekday_active = {
        weekday: 0
        for weekday in range(7)
    }

    current = first_date

    while current <= last_date:
        weekday = current.weekday()
        weekday_total[weekday] += 1

        if current in daily_history:
            weekday_active[weekday] += 1

        current += timedelta(days=1)

    probabilities = {}

    for weekday in range(7):
        total = weekday_total[weekday]
        active = weekday_active[weekday]

        if total <= 0:
            probabilities[weekday] = 0.0
        else:
            probabilities[weekday] = active / total

    return probabilities


def circular_average_minutes(values):
    if not values:
        return None

    angles = []

    for minutes in values:
        angle = (
            minutes / 1440
        ) * 2 * math.pi

        angles.append(angle)

    sin_sum = sum(
        math.sin(angle)
        for angle in angles
    )

    cos_sum = sum(
        math.cos(angle)
        for angle in angles
    )

    average_angle = math.atan2(
        sin_sum,
        cos_sum
    )

    if average_angle < 0:
        average_angle += 2 * math.pi

    minutes = (
        average_angle
        / (2 * math.pi)
        * 1440
    )

    return int(round(minutes)) % 1440


def calculate_weekday_start_times(daily_history):
    values = {
        weekday: []
        for weekday in range(7)
    }

    for date, datetimes in daily_history.items():
        if not datetimes:
            continue

        first_dt = datetimes[0]

        minutes = (
            first_dt.hour * 60
            + first_dt.minute
            + round(first_dt.second / 60)
        )

        values[date.weekday()].append(minutes)

    result = {}

    for weekday in range(7):
        result[weekday] = circular_average_minutes(
            values[weekday]
        )

    return result


def generate_predictions(
    daily_history,
    probabilities,
    start_times,
    today
):
    if len(daily_history) < MIN_ACTIVE_DAYS:
        print(
            "実績が少ないため、予測は作成しません。"
        )
        return []

    predictions = []

    accumulators = {
        weekday: 0.0
        for weekday in range(7)
    }

    for offset in range(
        1,
        PREDICTION_DAYS + 1
    ):
        date = today + timedelta(
            days=offset
        )

        weekday = date.weekday()

        probability = probabilities.get(
            weekday,
            0.0
        )

        start_minutes = start_times.get(
            weekday
        )

        if probability <= 0:
            continue

        if start_minutes is None:
            continue

        accumulators[weekday] += probability

        if date in daily_history:
            accumulators[weekday] = max(
                0.0,
                accumulators[weekday] - 1.0
            )
            continue

        if accumulators[weekday] >= 1.0:
            accumulators[weekday] -= 1.0

            hour = start_minutes // 60
            minute = start_minutes % 60

            dt = datetime.combine(
                date,
                time(
                    hour=hour,
                    minute=minute
                ),
                tzinfo=JST
            )

            predictions.append({
                "date": date.isoformat(),
                "datetime": dt.isoformat(),
                "weekday": weekday,
                "probability": probability,
            })

    return predictions


def escape_ics_text(value):
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


def format_ics_datetime(dt):
    if dt.tzinfo is None:
        dt = dt.replace(
            tzinfo=JST
        )

    utc_dt = dt.astimezone(
        timezone.utc
    )

    return utc_dt.strftime(
        "%Y%m%dT%H%M%SZ"
    )


def fold_ics_line(line):
    result = []
    current = ""

    for char in line:
        test = current + char

        if len(test.encode("utf-8")) > 75:
            result.append(current)
            current = " " + char
        else:
            current = test

    if current:
        result.append(current)

    return "\r\n".join(result)


def make_event(
    uid,
    start_dt,
    summary,
    description
):
    end_dt = (
        start_dt
        + timedelta(
            minutes=EVENT_DURATION_MINUTES
        )
    )

    dtstamp = format_ics_datetime(
        start_dt
    )

    lines = [
        "BEGIN:VEVENT",
        f"UID:{escape_ics_text(uid)}",
        f"DTSTAMP:{dtstamp}",
        f"DTSTART:{format_ics_datetime(start_dt)}",
        f"DTEND:{format_ics_datetime(end_dt)}",
        f"SUMMARY:{escape_ics_text(summary)}",
        f"DESCRIPTION:{escape_ics_text(description)}",
        "END:VEVENT",
    ]

    return "\r\n".join(
        fold_ics_line(line)
        for line in lines
    )


def build_calendar(
    actual_posts,
    predictions
):
    events = []

    actual_seen = set()

    for post in actual_posts:
        post_id = str(
            post.get("post_id", "")
        )

        if not post_id:
            continue

        if post_id in actual_seen:
            continue

        actual_seen.add(post_id)

        dt = parse_datetime(
            post.get("datetime", "")
        )

        if dt is None:
            continue

        url = post.get("url")

        if not url:
            url = (
                f"https://x.com/"
                f"{USERNAME}/status/{post_id}"
            )

        description = (
            "Xの実際の投稿から検出された"
            "IRIAMライブ開始情報です.\n"
            f"{url}\n"
            f"{post.get('iriam_url', '')}"
        )

        event = make_event(
            uid=(
                f"mega-iriam-real-"
                f"{post_id}@calendar-mega"
            ),
            start_dt=dt,
            summary="【配信開始】IRIAMライブ",
            description=description
        )

        events.append(
            (dt, event)
        )

    actual_dates = set()

    for post in actual_posts:
        dt = parse_datetime(
            post.get("datetime", "")
        )

        if dt:
            actual_dates.add(dt.date())

    prediction_seen = set()

    for prediction in predictions:
        date_string = prediction.get(
            "date"
        )

        if not date_string:
            continue

        if date_string in prediction_seen:
            continue

        prediction_seen.add(date_string)

        try:
            date = datetime.strptime(
                date_string,
                "%Y-%m-%d"
            ).date()

        except ValueError:
            continue

        if date in actual_dates:
            continue

        dt = parse_datetime(
            prediction.get(
                "datetime",
                ""
            )
        )

        if dt is None:
            continue

        probability = prediction.get(
            "probability",
            0
        )

        description = (
            "過去のIRIAM配信実績を学習して"
            "予測した配信開始日時です.\n"
            f"学習確率: {probability:.1%}"
        )

        event = make_event(
            uid=(
                f"mega-iriam-prediction-"
                f"{date_string}@calendar-mega"
            ),
            start_dt=dt,
            summary="【予測】IRIAMライブ",
            description=description
        )

        events.append(
            (dt, event)
        )

    events.sort(
        key=lambda item: item[0]
    )

    header = [
        "BEGIN:VCALENDAR",
        "VERSION:2.0",
        "PRODID:-//Calendar-Mega//IRIAM Calendar//JA",
        "CALSCALE:GREGORIAN",
        "METHOD:PUBLISH",
        "X-WR-CALNAME:メガ・ウルトラギガ IRIAM配信カレンダー",
        "X-WR-TIMEZONE:Asia/Tokyo",
    ]

    footer = [
        "END:VCALENDAR"
    ]

    all_lines = []

    for line in header:
        all_lines.append(
            fold_ics_line(line)
        )

    for _, event in events:
        all_lines.append(event)

    for line in footer:
        all_lines.append(
            fold_ics_line(line)
        )

    return "\r\n".join(
        all_lines
    ) + "\r\n"


def main():
    print("=" * 60)
    print("Calendar-Mega 更新開始")
    print("=" * 60)

    print("RSSを取得しています...")

    try:
        new_posts = fetch_posts()

    except Exception as e:
        print("RSS取得に失敗しました。")
        print(str(e))
        print(
            "既存のcalendar.icsとdata.jsonは"
            "変更しません。"
        )
        raise

    print(
        f"今回取得した対象投稿: "
        f"{len(new_posts)}件"
    )

    data = load_data()

    old_posts = data.get(
        "posts",
        []
    )

    print(
        f"保存済み履歴: "
        f"{len(old_posts)}件"
    )

    all_posts = merge_posts(
        old_posts,
        new_posts
    )

    print(
        f"統合後の全履歴: "
        f"{len(all_posts)}件"
    )

    daily_history = build_daily_history(
        all_posts
    )

    print(
        f"活動実績日数: "
        f"{len(daily_history)}日"
    )

    probabilities = (
        calculate_weekday_probabilities(
            daily_history
        )
    )

    weekday_names = [
        "月",
        "火",
        "水",
        "木",
        "金",
        "土",
        "日",
    ]

    print("")
    print("曜日別配信確率:")

    for weekday in range(7):
        probability = probabilities.get(
            weekday,
            0
        )

        print(
            f"  {weekday_names[weekday]}曜日: "
            f"{probability:.1%}"
        )

    start_times = (
        calculate_weekday_start_times(
            daily_history
        )
    )

    print("")
    print("曜日別学習開始時刻:")

    for weekday in range(7):
        minutes = start_times.get(
            weekday
        )

        if minutes is None:
            print(
                f"  {weekday_names[weekday]}曜日: "
                f"データなし"
            )

        else:
            hour = minutes // 60
            minute = minutes % 60

            print(
                f"  {weekday_names[weekday]}曜日: "
                f"{hour:02d}:{minute:02d}"
            )

    now = datetime.now(JST)
    today = now.date()

    print("")
    print(
        f"現在: "
        f"{now.strftime('%Y-%m-%d %H:%M:%S %Z')}"
    )

    predictions = generate_predictions(
        daily_history=daily_history,
        probabilities=probabilities,
        start_times=start_times,
        today=today
    )

    print("")
    print(
        f"生成した予測イベント: "
        f"{len(predictions)}件"
    )

    for prediction in predictions:
        print(
            "  "
            f"{prediction['datetime']} "
            f"(確率 "
            f"{prediction['probability']:.1%})"
        )

    calendar_content = build_calendar(
        actual_posts=all_posts,
        predictions=predictions
    )

    event_count = calendar_content.count(
        "BEGIN:VEVENT"
    )

    if event_count == 0:
        raise RuntimeError(
            "生成されたcalendar.icsに"
            "イベントが1件もありません。"
            "既存ファイルを上書きしません。"
        )

    print("")
    print(
        f"calendar.icsイベント数: "
        f"{event_count}"
    )

    data_content = json.dumps(
        {
            "posts": all_posts
        },
        ensure_ascii=False,
        indent=2
    ) + "\n"

    atomic_write(
        DATA_FILE,
        data_content
    )

    atomic_write(
        ICS_FILE,
        calendar_content
    )

    print("")
    print("=" * 60)
    print("Calendar-Mega 更新完了")
    print("=" * 60)


if __name__ == "__main__":
    main()
