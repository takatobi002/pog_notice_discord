"""
週次通知メイン処理。

使い方:
  python notify.py schedule           # 今週の出走予定を投稿（金曜夜実行想定）
  python notify.py results            # 今週の土日レース結果を投稿（日曜夜実行想定）
  python notify.py schedule --dry-run # Discord投稿せずターミナルに表示
  python notify.py results  --dry-run
"""

import argparse
import json
import sys
from datetime import date, timedelta
from pathlib import Path

import discord_notify
import netkeiba

HORSES_FILE = Path(__file__).parent / "horses.json"
WEEK_CACHE_FILE = Path(__file__).parent / "this_week_races.json"

WEEKDAY_JP = ["月", "火", "水", "木", "金", "土", "日"]
PLACE_MEDALS = {1: "🥇", 2: "🥈", 3: "🥉"}


def load_horses() -> list[dict]:
    if not HORSES_FILE.exists():
        print(f"エラー: {HORSES_FILE} が見つかりません。先に import_horses.py を実行してください。")
        sys.exit(1)
    with open(HORSES_FILE, encoding="utf-8") as f:
        return json.load(f)


def load_week_cache() -> dict[str, list[dict]]:
    """schedule実行時に保存した今週の出走予定キャッシュを読み込む。
    存在しない場合は空辞書を返す（初回実行など）。"""
    if not WEEK_CACHE_FILE.exists():
        return {}
    with open(WEEK_CACHE_FILE, encoding="utf-8") as f:
        return json.load(f)


def save_week_cache(week_cache: dict[str, list[dict]]) -> None:
    with open(WEEK_CACHE_FILE, "w", encoding="utf-8") as f:
        json.dump(week_cache, f, ensure_ascii=False, indent=2)


def this_week_range() -> tuple[date, date]:
    today = date.today()
    monday = today - timedelta(days=today.weekday())
    return monday, monday + timedelta(days=6)


def this_weekend_range() -> tuple[date, date]:
    """今週の土日（日曜夜の結果取得用）"""
    today = date.today()
    saturday = today - timedelta(days=today.weekday()) + timedelta(days=5)
    return saturday, saturday + timedelta(days=1)


def format_date(d: date) -> str:
    return f"{d.month}/{d.day}({WEEKDAY_JP[d.weekday()]})"


def _owners_label(owners: list[str]) -> str:
    return "・".join(owners) + " の指名馬"


def _dedup_horses(horses: list[dict]) -> tuple[dict[str, list[str]], dict[str, str]]:
    """馬ID → [owner, ...] と 馬ID → 馬名 の辞書を返す"""
    id_to_owners: dict[str, list[str]] = {}
    id_to_name: dict[str, str] = {}
    for h in horses:
        hid = h["netkeiba_id"]
        id_to_owners.setdefault(hid, [])
        if h["owner"] not in id_to_owners[hid]:
            id_to_owners[hid].append(h["owner"])
        id_to_name[hid] = h["name"]
    return id_to_owners, id_to_name


def build_schedule_message(
    horses: list[dict], verbose: bool = False, week_cache: dict[str, list[dict]] | None = None
) -> str:
    """今週（月〜日）の出走予定メッセージを返す。

    week_cache を渡すと、今週の出走予定を horse_id ごとに書き込む。
    日曜の結果取得時に、netkeiba側の「次走情報」が既に次のレースへ
    切り替わってしまい今週末の結果を見失うケースへの備え（詳細は
    build_results_message を参照）。
    """
    this_start, this_end = this_week_range()
    id_to_owners, id_to_name = _dedup_horses(horses)
    unique_ids = list(id_to_owners.keys())

    lines = [f"📅 **今週の出走予定**（{format_date(this_start)}〜{format_date(this_end)}）\n"]
    any_entry = False

    for i, horse_id in enumerate(unique_ids, 1):
        name = id_to_name[horse_id]
        owners_str = _owners_label(id_to_owners[horse_id])
        dup = f" (共同指名: {len(id_to_owners[horse_id])}人)" if len(id_to_owners[horse_id]) > 1 else ""

        if verbose:
            print(f"[{i:2d}/{len(unique_ids)}] {name}{dup} ...", end=" ", flush=True)

        entries = netkeiba.get_race_entries(horse_id)
        # 今週全て（地方競馬平日含む）。確定済みも含める
        this_week = [e for e in entries if this_start <= e.race_date <= this_end]

        if verbose:
            print(f"戦績{len(entries)}件 {'今週' + str(len(this_week)) + '件' if this_week else '対象なし'}")

        if week_cache is not None and this_week:
            week_cache[horse_id] = [
                {
                    "race_id": e.race_id,
                    "race_date": e.race_date.isoformat(),
                    "race_name": e.race_name,
                    "venue": e.venue,
                    "race_num": e.race_num,
                    "grade": e.grade,
                }
                for e in this_week
                if e.race_id
            ]

        for e in this_week:
            any_entry = True
            grade_tag = f" [{e.grade}]" if e.grade else ""
            venue_race = f"{e.venue}{e.race_num}R " if e.venue and e.race_num else ""
            if e.confirmed:
                medal = PLACE_MEDALS.get(e.place, "")
                place_str = f" → {e.place}着 {medal}" if e.place else " → 結果確定"
            else:
                place_str = ""
            lines.append(
                f"🐴 **{name}**（{owners_str}）\n"
                f"　→ {format_date(e.race_date)} | {venue_race}{e.race_name}{grade_tag}{place_str}\n"
                f"　🔗 <{e.race_url}>"
            )

    if not any_entry:
        lines.append("今週の出走予定はありません。")

    return "\n".join(lines)


def build_results_message(horses: list[dict], verbose: bool = False) -> str:
    """今週の土日レース結果メッセージを返す（日曜夜実行想定）"""
    sat, sun = this_weekend_range()
    id_to_owners, id_to_name = _dedup_horses(horses)
    unique_ids = list(id_to_owners.keys())
    week_cache = load_week_cache()

    lines = [f"🏆 **今週末のレース結果**（{format_date(sat)}・{format_date(sun)}）\n"]
    any_result = False

    for i, horse_id in enumerate(unique_ids, 1):
        name = id_to_name[horse_id]
        owners_str = _owners_label(id_to_owners[horse_id])
        dup = f" (共同指名: {len(id_to_owners[horse_id])}人)" if len(id_to_owners[horse_id]) > 1 else ""

        if verbose:
            print(f"[{i:2d}/{len(unique_ids)}] {name}{dup} ...", end=" ", flush=True)

        entries = netkeiba.get_race_entries(horse_id)
        # 今週末の確定済み結果のみ表示
        weekend = [e for e in entries if sat <= e.race_date <= sun and e.confirmed]

        # netkeiba側の「次走情報」が既に来週以降の登録へ切り替わっていると、
        # 戦績テーブルの反映が間に合わない場合に今週末の結果が両ソースから
        # 抜け落ちてしまう。金曜の予定投稿時に保存したキャッシュのレースIDを
        # 直接結果ページで確認し、見つかった分を補完する。
        found_race_ids = {e.race_id for e in weekend if e.race_id}
        for cached in week_cache.get(horse_id, []):
            if cached["race_id"] in found_race_ids:
                continue
            race_date = date.fromisoformat(cached["race_date"])
            if not (sat <= race_date <= sun):
                continue
            place = netkeiba.fetch_confirmed_place(cached["race_id"], horse_id)
            if place is None:
                continue
            weekend.append(netkeiba.RaceEntry(
                race_id=cached["race_id"],
                race_name=cached["race_name"],
                race_date=race_date,
                venue=cached["venue"],
                race_num=cached["race_num"],
                grade=cached["grade"],
                place=place,
                race_url=netkeiba.RESULT_PAGE_URL.format(race_id=cached["race_id"]),
                confirmed=True,
            ))
            found_race_ids.add(cached["race_id"])
            if verbose:
                print(f"  ↳ キャッシュ経由で結果を補完: {cached['race_name']} {place}着")

        if verbose:
            print(f"戦績{len(entries)}件 {'今週末結果' + str(len(weekend)) + '件' if weekend else '対象なし'}")

        for e in weekend:
            any_result = True
            grade_tag = f" [{e.grade}]" if e.grade else ""
            medal = PLACE_MEDALS.get(e.place, "")
            place_str = f"{e.place}着 {medal}" if e.place else "着順未取得"
            venue_race = f"{e.venue}{e.race_num}R " if e.venue and e.race_num else ""
            lines.append(
                f"🐴 **{name}**（{owners_str}）\n"
                f"　→ {format_date(e.race_date)} | {venue_race}{e.race_name}{grade_tag} {place_str}\n"
                f"　🔗 <{e.race_url}>"
            )

    if not any_result:
        lines.append("今週末の出走はありませんでした。")

    return "\n".join(lines)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("mode", choices=["schedule", "results"], help="schedule: 出走予定 / results: レース結果")
    parser.add_argument("--dry-run", action="store_true", help="Discordに投稿せずターミナルに表示")
    args = parser.parse_args()

    horses = load_horses()
    print(f"{len(horses)} 頭の指名馬を読み込みました。netkeibaから情報取得中...")

    if args.mode == "schedule":
        week_cache: dict[str, list[dict]] = {}
        msg = build_schedule_message(horses, verbose=args.dry_run, week_cache=week_cache)
        save_week_cache(week_cache)
        label = "出走予定"
    else:
        msg = build_results_message(horses, verbose=args.dry_run)
        label = "レース結果"

    if args.dry_run:
        print(f"\n--- {label} ---")
        print(msg)
    else:
        discord_notify.post(msg)
        print(f"{label}を投稿しました。")


if __name__ == "__main__":
    main()
