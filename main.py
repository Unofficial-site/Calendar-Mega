import os
import re
import json
import math
import random
import tempfile
from datetime import datetime, timedelta, timezone, time
from urllib.request import Request, urlopen
from xml.etree import ElementTree as ET
from email.utils import parsedate_to_datetime
from zoneinfo import ZoneInfo


# =========================================================
# Settings
# =========================================================

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

# 未来何日まで予測するか
PREDICTION_DAYS = 60

# 学習する過去の履歴数
HISTORY_LIMIT = 60

# 実績・予測イベントの長さ
EVENT_DURATION_MINUTES = 30

# RSS取得タイムアウト
HTTP_TIMEOUT = 20

# RSS User-Agent
USER_AGENT = (
    "Mozilla/5.0 (compatible; Calendar-Mega/1.0; "
    "+https://github.com/Unofficial-site/Calendar-Mega)"
)

# ---------------------------------------------------------
# 学習設定
# ---------------------------------------------------------

# 予測確率がこの値未満なら、その日は予測しない。
#
# 例:
# 0.60 = 過去にその曜日で60%以上の頻度なら予測
#
# ただし「曜日そのものを予測対象から削除する」のではなく、
# 未来の日付ごとに過去の発生率を計算する。
MIN_PREDICTION_PROBABILITY = 0.20

# 学習データが少なすぎる場合の予測を抑制
MIN_HISTORY_FOR_PREDICTION = 7

# ランダム予測ではなく、再現可能な判定をするための係数
RANDOM_SEED = 20261007


# =========================================================
# HTTP
# =========================================================

def fetch_url(url):
    request = Request(
        url,
        headers={
            "User-Agent": USER_AGENT,
            "Accept": (
                "application/rss+xml, "
                "application/atom+xml, "
                "application/xml, "
                "text/xml"
            ),
        },
    )

    with urlopen(
        request,
        timeout=HTTP_TIMEOUT,
    ) as response:
        return response.read()


# =========================================================
# XML helpers
# =========================================================

def local_name(tag):
    return tag.split("}", 1)[-1]


def child_text(element, names):
    names = set(names)

    for child in element.iter():
        if local_name(child.tag) in names:
            if child.text:
                return child.text.strip()

    return None


# =========================================================
# Date parsing
# =========================================================

def parse_datetime(value):
    if not value:
        return None

    value = value.strip()

    # ISO 8601
    try:
        dt = datetime.fromisoformat(
            value.replace("Z", "+00:00")
        )

        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=UTC)

        return dt.astimezone(JST)

    except Exception:
        pass

    # RFC 2822
    try:
        dt = parsedate_to_datetime(value)

        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=UTC)

        return dt.astimezone(JST)

    except Exception:
        return None


# =========================================================
# X post ID
# =========================================================

def extract_post_id(value):
    if not value:
        return None

    patterns = [
        r"/status/(\d+)",
        r"status[/:](\d+)",
    ]

    for pattern in patterns:
        match = re.search(
            pattern,
            value,
        )

        if match:
            return match.group(1)

    if re.fullmatch(
        r"\d{10,30}",
        value,
    ):
        return value

    return None


# =========================================================
# IRIAM URL
# =========================================================

def extract_iriam_url(text):
    if not text:
        return None

    match = re.search(
        r"https://web\.iriam\.app/s/live/[A-Za-z0-9_-]+",
        text,
    )

    if match:
        return match.group(0)

    return None


# =========================================================
# RSS / Atom parser
# =========================================================

def parse_feed(xml_bytes):
    root = ET.fromstring(xml_bytes)

    results = []

    for element in root.iter():

        if local_name(element.tag) not in (
            "item",
            "entry",
        ):
            continue

        title = child_text(
            element,
            ["title"],
        )

        description = child_text(
            element,
            [
                "description",
                "summary",
                "content",
            ],
        )

        link = child_text(
            element,
            ["link"],
        )

        guid = child_text(
            element,
            [
                "guid",
                "id",
            ],
        )

        published = child_text(
            element,
            [
                "pubDate",
                "published",
                "updated",
                "date",
            ],
        )

        post_datetime = parse_datetime(
            published
        )

        if not post_datetime:
            continue

        combined_text = "\n".join(
            value
            for value in [
                title,
                description,
                link,
                guid,
            ]
            if value
        )

        iriam_url = extract_iriam_url(
            combined_text
        )

        if not iriam_url:
            continue

        post_id = (
            extract_post_id(guid)
            or extract_post_id(link)
            or extract_post_id(combined_text)
        )

        if not post_id:
            continue

        results.append(
            {
                "post_id": post_id,
                "datetime": post_datetime.isoformat(),
                "iriam_url": iriam_url,
            }
        )

    return results


# =========================================================
# Fetch posts
# =========================================================

def fetch_posts():

    errors = []

    for url in RSS_URLS:

        try:

            print(
                f"[INFO] RSS取得: {url}"
            )

            xml = fetch_url(url)

            posts = parse_feed(xml)

            if posts:

                print(
                    f"[INFO] IRIAM投稿検出: "
                    f"{len(posts)}件"
                )

                return posts

            errors.append(
                f"{url}: 投稿なし"
            )

        except Exception as e:

            print(
                f"[WARN] RSS取得失敗: {e}"
            )

            errors.append(
                f"{url}: {e}"
            )

    raise RuntimeError(
        "RSSを取得できませんでした: "
        + " / ".join(errors)
    )


# =========================================================
# History
# =========================================================

def load_history():

    if not os.path.exists(DATA_FILE):
        return []

    try:

        with open(
            DATA_FILE,
            "r",
            encoding="utf-8",
        ) as f:

            data = json.load(f)

        if isinstance(data, dict):
            history = data.get(
                "posts",
                [],
            )

        elif isinstance(data, list):
            history = data

        else:
            history = []

        return history

    except Exception as e:

        print(
            f"[WARN] data.json読み込み失敗: {e}"
        )

        return []


def save_history(history):

    data = {
        "posts": history,
        "updated_at": datetime.now(
            UTC
        ).isoformat(),
    }

    temp_fd, temp_path = tempfile.mkstemp(
        prefix="data-",
        suffix=".json",
    )

    try:

        with os.fdopen(
            temp_fd,
            "w",
            encoding="utf-8",
        ) as f:

            json.dump(
                data,
                f,
                ensure_ascii=False,
                indent=2,
            )

        os.replace(
            temp_path,
            DATA_FILE,
        )

    finally:

        if os.path.exists(temp_path):
            os.remove(temp_path)


def merge_history(
    old_history,
    new_posts,
):

    merged = {}

    for post in old_history:

        post_id = post.get(
            "post_id"
        )

        if post_id:
            merged[post_id] = post

    for post in new_posts:

        merged[
            post["post_id"]
        ] = post

    result = list(
        merged.values()
    )

    result.sort(
        key=lambda x: x.get(
            "datetime",
            "",
        )
    )

    return result[
        -HISTORY_LIMIT:
    ]


# =========================================================
# Convert history to datetime records
# =========================================================

def valid_history(history):

    result = []

    for post in history:

        try:

            dt = datetime.fromisoformat(
                post["datetime"]
            )

            if dt.tzinfo is None:
                dt = dt.replace(
                    tzinfo=JST
                )

            dt = dt.astimezone(JST)

            result.append(
                (
                    dt,
                    post,
                )
            )

        except Exception:
            continue

    result.sort(
        key=lambda x: x[0]
    )

    return result


# =========================================================
# Learn weekday statistics
# =========================================================

def learn_weekday_statistics(history):

    records = valid_history(
        history
    )

    # -----------------------------------------------------
    # 曜日ごとの実績
    # -----------------------------------------------------

    weekday_counts = {
        weekday: 0
        for weekday in range(7)
    }

    weekday_times = {
        weekday: []
        for weekday in range(7)
    }

    # 実際に「その曜日だった日」の数
    observed_dates = {
        weekday: set()
        for weekday in range(7)
    }

    for dt, _ in records:

        weekday = dt.weekday()

        weekday_counts[
            weekday
        ] += 1

        observed_dates[
            weekday
        ].add(
            dt.date()
        )

        minutes = (
            dt.hour * 60
            + dt.minute
            + dt.second / 60
        )

        weekday_times[
            weekday
        ].append(minutes)

    # -----------------------------------------------------
    # 曜日別の時間平均
    # -----------------------------------------------------

    weekday_average_minutes = {}

    for weekday in range(7):

        values = weekday_times[
            weekday
        ]

        if not values:
            continue

        weekday_average_minutes[
            weekday
        ] = circular_average_minutes(
            values
        )

    # -----------------------------------------------------
    # 学習結果表示
    # -----------------------------------------------------

    names = [
        "月曜日",
        "火曜日",
        "水曜日",
        "木曜日",
        "金曜日",
        "土曜日",
        "日曜日",
    ]

    print(
        "[INFO] ===== 過去データ学習結果 ====="
    )

    total_posts = len(records)

    print(
        f"[INFO] 学習対象投稿数: "
        f"{total_posts}件"
    )

    for weekday in range(7):

        count = weekday_counts[
            weekday
        ]

        if total_posts:
            share = (
                count
                / total_posts
                * 100
            )
        else:
            share = 0

        average = weekday_average_minutes.get(
            weekday
        )

        if average is not None:

            hour = int(
                average // 60
            )

            minute = int(
                average % 60
            )

            time_text = (
                f"{hour:02d}:{minute:02d}"
            )

        else:

            time_text = "--:--"

        print(
            f"[INFO] {names[weekday]}: "
            f"{count}件 / "
            f"{share:.1f}% / "
            f"平均 {time_text}"
        )

    print(
        "[INFO] ============================="
    )

    return {
        "weekday_counts": weekday_counts,
        "weekday_times": weekday_times,
        "weekday_average_minutes":
            weekday_average_minutes,
        "total_posts": total_posts,
    }


# =========================================================
# Circular average
# =========================================================

def circular_average_minutes(
    values
):

    if not values:
        return None

    angles = [
        (
            minutes
            / 1440.0
            * 2
            * math.pi
        )
        for minutes in values
    ]

    sin_sum = sum(
        math.sin(angle)
        for angle in angles
    )

    cos_sum = sum(
        math.cos(angle)
        for angle in angles
    )

    angle = math.atan2(
        sin_sum,
        cos_sum,
    )

    if angle < 0:
        angle += 2 * math.pi

    average = (
        angle
        / (2 * math.pi)
        * 1440.0
    )

    return int(
        round(average)
    ) % 1440


# =========================================================
# Historical probability
# =========================================================

def calculate_weekday_probability(
    weekday,
    history,
):

    records = valid_history(
        history
    )

    if len(records) < MIN_HISTORY_FOR_PREDICTION:
        return 0.0

    # -----------------------------------------------------
    # 履歴の「日付範囲」を求める
    # -----------------------------------------------------

    first_date = records[0][0].date()
    last_date = records[-1][0].date()

    total_days = (
        last_date - first_date
    ).days + 1

    if total_days <= 0:
        return 0.0

    # -----------------------------------------------------
    # その曜日が存在した日数
    # -----------------------------------------------------

    total_weekday_days = 0

    current = first_date

    while current <= last_date:

        if current.weekday() == weekday:
            total_weekday_days += 1

        current += timedelta(
            days=1
        )

    if total_weekday_days <= 0:
        return 0.0

    # -----------------------------------------------------
    # 実際に配信した曜日の日数
    # -----------------------------------------------------

    actual_dates = set()

    for dt, _ in records:

        if dt.weekday() == weekday:
            actual_dates.add(
                dt.date()
            )

    # -----------------------------------------------------
    # 発生率
    # -----------------------------------------------------

    probability = (
        len(actual_dates)
        / total_weekday_days
    )

    return probability


# =========================================================
# Prediction decision
# =========================================================

def should_predict_date(
    target_date,
    history,
    statistics,
):

    if (
        statistics["total_posts"]
        < MIN_HISTORY_FOR_PREDICTION
    ):
        return False

    weekday = target_date.weekday()

    probability = (
        calculate_weekday_probability(
            weekday,
            history,
        )
    )

    print(
        f"[INFO] "
        f"{target_date.isoformat()} "
        f"{['月','火','水','木','金','土','日'][weekday]} "
        f"過去発生率={probability * 100:.1f}%"
    )

    # ---------------------------------------------
    # 高頻度曜日
    # ---------------------------------------------

    if probability >= 0.75:
        return True

    # ---------------------------------------------
    # 中頻度曜日
    #
    # 過去の頻度をそのまま確率として使う。
    # ---------------------------------------------

    if probability >= MIN_PREDICTION_PROBABILITY:

        # 日付から再現可能な疑似乱数を作る。
        #
        # 毎回Actionsを実行しても同じ日付なら
        # 同じ判定になる。
        seed = (
            RANDOM_SEED
            + target_date.toordinal()
            * 100
            + weekday
        )

        rng = random.Random(
            seed
        )

        return (
            rng.random()
            < probability
        )

    return False


# =========================================================
# ICS escaping
# =========================================================

def escape_ics_text(value):

    if value is None:
        return ""

    return (
        str(value)
        .replace(
            "\\",
            "\\\\",
        )
        .replace(
            ";",
            "\\;",
        )
        .replace(
            ",",
            "\\,",
        )
        .replace(
            "\r\n",
            "\\n",
        )
        .replace(
            "\n",
            "\\n",
        )
        .replace(
            "\r",
            "\\n",
        )
    )


# =========================================================
# ICS datetime
# =========================================================

def format_ics_datetime(dt):

    return dt.strftime(
        "%Y%m%dT%H%M%S"
    )


# =========================================================
# Real event
# =========================================================

def make_real_event(post):

    dt = datetime.fromisoformat(
        post["datetime"]
    )

    if dt.tzinfo is None:
        dt = dt.replace(
            tzinfo=JST
        )

    dt = dt.astimezone(
        JST
    )

    end = dt + timedelta(
        minutes=EVENT_DURATION_MINUTES
    )

    post_id = post[
        "post_id"
    ]

    iriam_url = post[
        "iriam_url"
    ]

    return "\n".join(
        [
            "BEGIN:VEVENT",
            (
                "UID:mega-iriam-real-"
                f"{post_id}@calendar-mega"
            ),
            (
                "DTSTAMP:"
                + datetime.now(
                    UTC
                ).strftime(
                    "%Y%m%dT%H%M%SZ"
                )
            ),
            (
                "DTSTART;TZID=Asia/Tokyo:"
                + format_ics_datetime(dt)
            ),
            (
                "DTEND;TZID=Asia/Tokyo:"
                + format_ics_datetime(end)
            ),
            "SUMMARY:【配信開始】IRIAMライブ",
            (
                "DESCRIPTION:"
                "X投稿から検出したIRIAM配信"
            ),
            f"URL:{iriam_url}",
            (
                f"X:https://x.com/{USERNAME}"
                f"/status/{post_id}"
            ),
            "END:VEVENT",
        ]
    )


# =========================================================
# Prediction event
# =========================================================

def make_prediction_event(
    target_date,
    predicted_minutes,
):

    hour = int(
        predicted_minutes // 60
    )

    minute = int(
        predicted_minutes % 60
    )

    dt = datetime.combine(
        target_date,
        time(
            hour=hour,
            minute=minute,
        ),
    ).replace(
        tzinfo=JST
    )

    end = dt + timedelta(
        minutes=EVENT_DURATION_MINUTES
    )

    return "\n".join(
        [
            "BEGIN:VEVENT",
            (
                "UID:mega-iriam-prediction-"
                f"{target_date.isoformat()}"
                "@calendar-mega"
            ),
            (
                "DTSTAMP:"
                + datetime.now(
                    UTC
                ).strftime(
                    "%Y%m%dT%H%M%SZ"
                )
            ),
            (
                "DTSTART;TZID=Asia/Tokyo:"
                + format_ics_datetime(dt)
            ),
            (
                "DTEND;TZID=Asia/Tokyo:"
                + format_ics_datetime(end)
            ),
            "SUMMARY:【予測】IRIAMライブ",
            (
                "DESCRIPTION:"
                "過去の同じ曜日のIRIAM配信投稿時刻"
                "から学習して算出した予測です。"
            ),
            "END:VEVENT",
        ]
    )


# =========================================================
# Build calendar
# =========================================================

def build_calendar(
    history
):

    now = datetime.now(
        JST
    )

    events = []

    # =====================================================
    # 実際の投稿
    # =====================================================

    actual_dates = set()

    for post in history:

        try:

            dt = datetime.fromisoformat(
                post["datetime"]
            )

            if dt.tzinfo is None:
                dt = dt.replace(
                    tzinfo=JST
                )

            dt = dt.astimezone(
                JST
            )

            actual_dates.add(
                dt.date()
            )

            events.append(
                (
                    dt,
                    make_real_event(
                        post
                    ),
                )
            )

        except Exception:
            continue

    # =====================================================
    # 過去データから学習
    # =====================================================

    statistics = learn_weekday_statistics(
        history
    )

    # =====================================================
    # 未来予測
    # =====================================================

    print(
        "[INFO] ===== 未来予測 ====="
    )

    for offset in range(
        1,
        PREDICTION_DAYS + 1,
    ):

        target_date = (
            now.date()
            + timedelta(
                days=offset
            )
        )

        # 実際の投稿が存在する日は予測しない
        if target_date in actual_dates:
            continue

        weekday = target_date.weekday()

        # その曜日の平均開始時刻が
        # 学習できていなければ予測不能
        if (
            weekday
            not in statistics[
                "weekday_average_minutes"
            ]
        ):
            continue

        # 過去の頻度から予測
        if not should_predict_date(
            target_date,
            history,
            statistics,
        ):
            continue

        predicted_minutes = (
            statistics[
                "weekday_average_minutes"
            ][weekday]
        )

        predicted_dt = datetime.combine(
            target_date,
            time(
                hour=int(
                    predicted_minutes // 60
                ),
                minute=int(
                    predicted_minutes % 60
                ),
            ),
        ).replace(
            tzinfo=JST
        )

        events.append(
            (
                predicted_dt,
                make_prediction_event(
                    target_date,
                    predicted_minutes,
                ),
            )
        )

        print(
            f"[PREDICT] "
            f"{target_date} "
            f"{['月','火','水','木','金','土','日'][weekday]} "
            f"{predicted_dt.strftime('%H:%M')}"
        )

    print(
        "[INFO] ==================="
    )

    # =====================================================
    # 日時順
    # =====================================================

    events.sort(
        key=lambda x: x[0]
    )

    return events


# =========================================================
# Write calendar
# =========================================================

def write_calendar(
    events
):

    lines = [
        "BEGIN:VCALENDAR",
        "VERSION:2.0",
        "PRODID:-//Koqi//Calendar-Mega//JP",
        "CALSCALE:GREGORIAN",
        "METHOD:PUBLISH",
        "X-WR-CALNAME:メガ・ウルトラギガ IRIAM配信",
        "X-WR-TIMEZONE:Asia/Tokyo",
    ]

    for _, event in events:
        lines.append(
            event
        )

    lines.append(
        "END:VCALENDAR"
    )

    content = (
        "\r\n".join(lines)
        + "\r\n"
    )

    # RSS取得が正常なのに0件になった場合も
    # 既存カレンダーを破壊しない
    if (
        not events
        and os.path.exists(
            OUTPUT_ICS
        )
    ):

        print(
            "[WARN] イベントが0件なので、"
            "既存calendar.icsを保持します。"
        )

        return

    temp_fd, temp_path = tempfile.mkstemp(
        prefix="calendar-",
        suffix=".ics",
    )

    try:

        with os.fdopen(
            temp_fd,
            "w",
            encoding="utf-8",
            newline="",
        ) as f:

            f.write(
                content
            )

        os.replace(
            temp_path,
            OUTPUT_ICS
        )

    finally:

        if os.path.exists(
            temp_path
        ):
            os.remove(
                temp_path
            )


# =========================================================
# Main
# =========================================================

def main():

    print(
        "========================================"
    )

    print(
        " Mega IRIAM Calendar"
    )

    print(
        " Past-learning prediction system"
    )

    print(
        "========================================"
    )

    # -----------------------------------------------------
    # RSS
    # -----------------------------------------------------

    posts = fetch_posts()

    # -----------------------------------------------------
    # 履歴
    # -----------------------------------------------------

    old_history = load_history()

    print(
        f"[INFO] 保存済み履歴: "
        f"{len(old_history)}件"
    )

    history = merge_history(
        old_history,
        posts,
    )

    print(
        f"[INFO] 統合後履歴: "
        f"{len(history)}件"
    )

    # -----------------------------------------------------
    # 保存
    # -----------------------------------------------------

    save_history(
        history
    )

    # -----------------------------------------------------
    # カレンダー生成
    # -----------------------------------------------------

    events = build_calendar(
        history
    )

    real_count = sum(
        1
        for _, event in events
        if "UID:mega-iriam-real-" in event
    )

    prediction_count = sum(
        1
        for _, event in events
        if "UID:mega-iriam-prediction-" in event
    )

    print(
        f"[INFO] 実績イベント: "
        f"{real_count}件"
    )

    print(
        f"[INFO] 予測イベント: "
        f"{prediction_count}件"
    )

    # -----------------------------------------------------
    # ICS
    # -----------------------------------------------------

    write_calendar(
        events
    )

    print(
        "[INFO] calendar.ics更新完了"
    )


if __name__ == "__main__":
    main()
