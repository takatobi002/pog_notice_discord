"""
netkeibaから馬の出走予定・レース結果を取得する。

データソース:
  1. ajax_horse_results.html     - 戦績テーブル（デビュー後しばらくすると反映される）
  2. api_db_horse_info_simple.html - 次走・近況情報（直近の出走はこちらが先に更新される）
  3. race.netkeiba.com/race/result.html - レース着順（結果確定後に取得）
"""

import re
import time
from dataclasses import dataclass
from datetime import date, datetime, timedelta

import requests
from bs4 import BeautifulSoup

AJAX_RESULTS_URL = "https://db.netkeiba.com/horse/ajax_horse_results.html"
HORSE_INFO_URL = "https://db.netkeiba.com/social/api_db_horse_info_simple.html"
RACE_RESULT_URL = "https://race.netkeiba.com/race/result.html"
HORSE_URL = "https://db.netkeiba.com/horse/{horse_id}/"
RACE_DB_URL = "https://db.netkeiba.com/race/{race_id}/"
RESULT_PAGE_URL = "https://race.netkeiba.com/race/result.html?race_id={race_id}"
SHUTUBA_URL = "https://race.netkeiba.com/race/shutuba.html?race_id={race_id}"

HEADERS_PC = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
}

# race_id の5〜6桁目が競馬場コード（YYYY + CC + ...）
VENUE_CODE: dict[str, str] = {
    "01": "札幌", "02": "函館", "03": "福島", "04": "新潟",
    "05": "東京", "06": "中山", "07": "中京", "08": "京都",
    "09": "阪神", "10": "小倉",
}


def parse_venue_race(race_id: str) -> tuple[str, int | None]:
    """race_id（12桁）から競馬場名とレース番号を返す。
    例: '202609030105' → ('阪神', 5)
    """
    if len(race_id) != 12:
        return "", None
    venue_code = race_id[4:6]
    race_num_str = race_id[10:12]
    venue = VENUE_CODE.get(venue_code, "")
    race_num = int(race_num_str) if race_num_str.isdigit() else None
    return venue, race_num

# 戦績テーブルの列インデックス
COL_DATE = 0
COL_VENUE = 1
COL_RACE_NUM = 3
COL_RACE_NAME = 4
COL_PLACE = 11
COL_TIME = 18

# 結果ページの列インデックス
RESULT_COL_PLACE = 0
RESULT_COL_NAME = 3
RESULT_COL_TIME = 7


@dataclass
class RaceEntry:
    race_id: str
    race_name: str
    race_date: date
    venue: str
    race_num: int | None
    grade: str         # "G1", "G2", "G3", "" など
    place: int | None  # 着順（結果あり時）
    race_url: str
    confirmed: bool    # 結果確定済みかどうか


def _get(url: str, params: dict | None = None) -> requests.Response:
    time.sleep(1)
    resp = requests.get(url, headers=HEADERS_PC, params=params, timeout=15)
    resp.raise_for_status()
    return resp


def _parse_date(text: str) -> date | None:
    text = re.sub(r"[（(][^)）]*[)）]", "", text).strip()
    for fmt in ("%Y/%m/%d", "%Y年%m月%d日", "%Y-%m-%d"):
        try:
            return datetime.strptime(text, fmt).date()
        except ValueError:
            pass
    return None


def _extract_grade(text: str) -> str:
    m = re.search(r"(G[123I]{1,3})", text, re.IGNORECASE)
    if not m:
        return ""
    g = m.group(1).upper()
    return {"GI": "G1", "GII": "G2", "GIII": "G3"}.get(g, g if g in ("G1", "G2", "G3") else "")


def _extract_race_id_from_url(url: str) -> str:
    m = re.search(r"race_id=(\w+)", url) or re.search(r"/race/(\w+)/?$", url)
    return m.group(1) if m else ""


# ── ① 戦績テーブル（ajax_horse_results.html） ────────────────────────────────

def _fetch_results_entries(horse_id: str) -> list[RaceEntry]:
    resp = _get(AJAX_RESULTS_URL, {"input": "UTF-8", "output": "json", "id": horse_id})
    data = resp.json()
    if data.get("status") != "OK":
        return []
    soup = BeautifulSoup(data["data"], "html.parser")
    table = soup.select_one("table")
    if not table:
        return []

    entries = []
    for row in table.select("tr")[1:]:
        cells = row.select("td")
        if len(cells) <= COL_RACE_NAME:
            continue
        race_date = _parse_date(cells[COL_DATE].get_text(strip=True))
        if not race_date:
            continue
        race_name = cells[COL_RACE_NAME].get_text(strip=True)
        grade = _extract_grade(race_name)
        link = cells[COL_RACE_NAME].select_one("a[href*='/race/']")
        race_id = _extract_race_id_from_url(link.get("href", "")) if link else ""
        venue, race_num = parse_venue_race(race_id)

        place = None
        if len(cells) > COL_PLACE:
            p = cells[COL_PLACE].get_text(strip=True)
            if p.isdigit():
                place = int(p)

        race_url = RESULT_PAGE_URL.format(race_id=race_id) if race_id else HORSE_URL.format(horse_id=horse_id)
        entries.append(RaceEntry(
            race_id=race_id, race_name=race_name, race_date=race_date,
            venue=venue, race_num=race_num, grade=grade,
            place=place, race_url=race_url, confirmed=True,
        ))
    return entries


# ── ② 次走・近況情報（api_db_horse_info_simple.html） ────────────────────────

def _fetch_next_race_entry(horse_id: str) -> RaceEntry | None:
    """次走・近況情報欄から直近のレースエントリーを取得する。
    戦績テーブルに反映される前の新馬戦などに対応。"""
    resp = _get(HORSE_INFO_URL, {"input": "UTF-8", "output": "json", "id": horse_id})
    import json as _json
    html_str = _json.loads(resp.text)
    soup = BeautifulSoup(html_str, "html.parser")

    box = soup.select_one("#HorseNextInfo_01_box .next_race_data_box_01")
    if not box:
        return None

    # ステータス画像の alt テキスト（「結果確定」「次走登録」など）
    status_img = box.select_one("dt img")
    status = status_img.get("alt", "") if status_img else ""
    confirmed = "結果確定" in status

    dd = box.select_one("dd")
    if not dd:
        return None

    # テキストノードから日付を抽出
    dd_text = dd.get_text(separator=" ", strip=True)
    date_m = re.search(r"(\d{4}/\d{1,2}/\d{1,2})", dd_text)
    race_date = _parse_date(date_m.group(1)) if date_m else None
    if not race_date:
        return None

    race_link = dd.select_one("a[href*='race_id']")
    if not race_link:
        return None
    race_name = race_link.get_text(strip=True)
    race_id = _extract_race_id_from_url(race_link.get("href", ""))
    grade = _extract_grade(race_name)
    venue, race_num = parse_venue_race(race_id)
    race_url = RACE_DB_URL.format(race_id=race_id) if race_id else HORSE_URL.format(horse_id=horse_id)

    # 結果確定済みなら結果ページから着順を取得
    place = None
    if confirmed and race_id:
        place = _fetch_place_from_result_page(race_id, horse_id)

    # 確定済み → 結果URL、未確定 → 出馬表URL
    if race_id:
        race_url = RESULT_PAGE_URL.format(race_id=race_id) if confirmed else SHUTUBA_URL.format(race_id=race_id)
    else:
        race_url = HORSE_URL.format(horse_id=horse_id)

    return RaceEntry(
        race_id=race_id, race_name=race_name, race_date=race_date,
        venue=venue, race_num=race_num, grade=grade,
        place=place, race_url=race_url, confirmed=confirmed,
    )


# ── ③ 結果ページから着順取得（race.netkeiba.com） ────────────────────────────

def _fetch_place_from_result_page(race_id: str, horse_id: str) -> int | None:
    """レース結果ページから指定馬の着順を返す"""
    resp = _get(RACE_RESULT_URL, {"race_id": race_id, "rf": "race_submenu"})
    resp.encoding = resp.apparent_encoding
    soup = BeautifulSoup(resp.text, "html.parser")

    table = soup.select_one("table.RaceTable01")
    if not table:
        return None

    for row in table.select("tr")[1:]:
        cells = row.select("td")
        if len(cells) <= RESULT_COL_NAME:
            continue
        horse_link = cells[RESULT_COL_NAME].select_one(f"a[href*='{horse_id}']")
        if not horse_link:
            continue
        place_text = cells[RESULT_COL_PLACE].get_text(strip=True)
        return int(place_text) if place_text.isdigit() else None

    return None


# ── 公開API ──────────────────────────────────────────────────────────────────

def fetch_confirmed_place(race_id: str, horse_id: str) -> int | None:
    """指定レースの結果ページから該当馬の着順を取得する。

    「次走・近況情報」欄が既に次のレース登録へ切り替わってしまい、
    戦績テーブルへの反映も間に合っていない場合の直接確認用。
    レース結果が見つからない（未確定・出走取消等）場合は None を返す。
    """
    return _fetch_place_from_result_page(race_id, horse_id)


def get_race_entries(horse_id: str) -> list[RaceEntry]:
    """馬の全レースエントリーを返す。
    戦績テーブル + 次走・近況情報を統合し、重複は除去する。
    """
    entries = _fetch_results_entries(horse_id)
    existing_ids = {e.race_id for e in entries if e.race_id}

    next_entry = _fetch_next_race_entry(horse_id)
    if next_entry and next_entry.race_id not in existing_ids:
        entries.append(next_entry)

    return sorted(entries, key=lambda e: e.race_date, reverse=True)


def get_this_week_entries(horse_id: str) -> list[RaceEntry]:
    today = date.today()
    monday = today - timedelta(days=today.weekday())
    sunday = monday + timedelta(days=6)
    return [e for e in get_race_entries(horse_id) if monday <= e.race_date <= sunday]


def get_this_weekend_entries(horse_id: str) -> list[RaceEntry]:
    today = date.today()
    saturday = today - timedelta(days=today.weekday()) + timedelta(days=5)
    sunday = saturday + timedelta(days=1)
    return [e for e in get_race_entries(horse_id) if saturday <= e.race_date <= sunday]
