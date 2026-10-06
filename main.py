import feedparser
import json
import os
from datetime import datetime, timedelta
from dateutil import parser
from ics import Calendar, Event
import pytz

# --- 設定項目 ---
# TARGET_USER を監視したいXのユーザーIDに変更してください（例: iriam_official など）
RSS_URL = "https://rsshub.app/twitter/user/@mega_urtr" 

# 検知したいIRIAMのライブURL（live/ まで）
KEYWORD = "https://web.iriam.app/s/live/" 
JST = pytz.timezone('Asia/Tokyo')
# --------------

state_file = 'data.json'

# 過去データの読み込み
if os.path.exists(state_file):
    with open(state_file, 'r', encoding='utf-8') as f:
        data = json.load(f)
else:
    data = {"posts": []}

# RSSフィードの取得
feed = feedparser.parse(RSS_URL)
for entry in feed.entries:
    # タイトルか本文の中に、指定したIRIAMのURLが含まれているかチェック
    if KEYWORD in entry.title or KEYWORD in entry.description:
        # Xの正確な投稿時間をJSTに変換して保存
        dt = parser.parse(entry.published).astimezone(JST)
        dt_str = dt.isoformat()
        if dt_str not in data["posts"]:
            data["posts"].append(dt_str)

# 日時順にソートして保存
data["posts"].sort()
with open(state_file, 'w', encoding='utf-8') as f:
    json.dump(data, f, ensure_ascii=False, indent=2)

cal = Calendar()
now = datetime.now(JST)

if data["posts"]:
    # 過去の投稿から平均時刻（時・分）を計算
    total_minutes = 0
    for p in data["posts"]:
        dt = parser.parse(p)
        total_minutes += dt.hour * 60 + dt.minute
    avg_minutes = total_minutes // len(data["posts"])
    avg_hour = avg_minutes // 60
    avg_minute = avg_minutes % 60
    today_avg_time = now.replace(hour=avg_hour, minute=avg_minute, second=0, microsecond=0)

    actual_dates = set()
    
    # 1. 実際の投稿をカレンダーに登録（実績）
    for p in data["posts"]:
        dt = parser.parse(p)
        actual_dates.add(dt.date())
        e = Event()
        e.name = "【配信開始】IRIAMライブ" # カレンダーに表示されるタイトル
        e.begin = dt
        e.end = dt + timedelta(minutes=30) # 予定の長さを30分として表示
        cal.events.add(e)

    # 2. 予測予定を2ヶ月後（60日分）まで作成
    for i in range(60):
        pred_date = today_avg_time + timedelta(days=i)
        
        # 過去の予測で、実際の投稿がなかった場合は予定を作らない（過ぎた予定の消去）
        if pred_date.date() < now.date() and pred_date.date() not in actual_dates:
            continue
        # 今日の予測で、すでに時間が過ぎており、かつ実際の投稿がない場合は作らない
        if pred_date.date() == now.date() and now > pred_date and pred_date.date() not in actual_dates:
            continue
        # その日にすでに実際の投稿がある場合は、予測を作らない
        if pred_date.date() in actual_dates:
            continue

        e = Event()
        e.name = f"【予測】IRIAM配信 ({avg_hour:02d}:{avg_minute:02d}頃?)"
        e.begin = pred_date
        e.end = pred_date + timedelta(minutes=30)
        cal.events.add(e)

# カレンダーファイルの出力
with open('calendar.ics', 'w', encoding='utf-8') as f:
    f.writelines(cal.serialize_iter())
