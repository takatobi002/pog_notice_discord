"""
みんなのPOGのHTMLをパースして horses.json を生成する。

使い方:
  python import_horses.py <HTMLファイルパス> [--update-secret]

  --update-secret を付けると、生成したhorses.jsonの内容で
  GitHub Actions用のリポジトリシークレット HORSES_JSON を
  自動更新する（要 gh CLI ログイン済み）。

HTMLファイルの取得方法:
  netkeibaの「みんなのPOG」グループページをブラウザで開き、
  「名前を付けて保存」でHTMLファイルとして保存する。
"""

import json
import re
import shutil
import subprocess
import sys
from pathlib import Path

from bs4 import BeautifulSoup

OUTPUT_FILE = Path(__file__).parent / "horses.json"


def parse_owner(dt_text: str) -> str:
    """「〇〇さんの指名馬」→「〇〇」を返す"""
    return re.sub(r"さんの指名馬.*", "", dt_text).strip()


def parse_horse_id(url: str) -> str | None:
    """URLから馬IDを抽出する。例: ?pid=horse_profile&id=2024106624 → 2024106624"""
    m = re.search(r"[?&]id=(\d+)", url)
    return m.group(1) if m else None


def parse_horse_name(text: str) -> str:
    """「ジャンゴッド (牡2)」→「ジャンゴッド」"""
    return re.sub(r"\s*[\(（].*", "", text).strip()


def parse_horses(html_path: str) -> list[dict]:
    with open(html_path, encoding="euc-jp", errors="replace") as f:
        soup = BeautifulSoup(f, "html.parser")

    horses = []
    seen = set()  # (owner, horse_id) の重複除去

    # dl.Desc_Box01 の中に全参加者の dt/dd が交互に並ぶ構造:
    #   <dt> 〇〇さんの指名馬
    #   <dd> ポイント等（馬リンクなし）
    #   <dd> 馬リスト（馬リンクあり）
    #   <dd> 指名馬を追加する
    #   <dt> △△さんの指名馬
    #   ...
    block = soup.select_one("dl.Desc_Box01")
    if not block:
        return horses

    current_owner = None
    for child in block.children:
        if not hasattr(child, "name") or child.name is None:
            continue

        if child.name == "dt" and "の指名馬" in child.get_text():
            current_owner = parse_owner(child.get_text(strip=True))

        elif child.name == "dd" and current_owner:
            for horse_link in child.select("a[href*='horse_profile']"):
                horse_id = parse_horse_id(horse_link.get("href", ""))
                if not horse_id:
                    continue
                key = (current_owner, horse_id)
                if key in seen:
                    continue
                seen.add(key)
                name = parse_horse_name(horse_link.get_text(strip=True))
                if name:
                    horses.append({"owner": current_owner, "name": name, "netkeiba_id": horse_id})

    return horses


def update_secret_via_gh() -> bool:
    """gh CLIで、生成済みのhorses.jsonの中身をリポジトリシークレット
    HORSES_JSON に登録する。カレントディレクトリのgitリモートから
    対象リポジトリが自動判定される。"""
    if shutil.which("gh") is None:
        print(
            "gh コマンドが見つかりません。https://cli.github.com/ からインストールし、"
            "`gh auth login` でログインしてから再実行してください。"
        )
        return False

    with open(OUTPUT_FILE, "rb") as f:
        result = subprocess.run(
            ["gh", "secret", "set", "HORSES_JSON"],
            stdin=f,
            capture_output=True,
        )

    if result.returncode != 0:
        print("シークレットの更新に失敗しました:")
        print(result.stderr.decode("utf-8", errors="replace"))
        return False

    print("リポジトリシークレット HORSES_JSON を更新しました。")
    return True


def main():
    if len(sys.argv) < 2:
        print("使い方: python import_horses.py <HTMLファイルパス> [--update-secret]")
        print("例:     python import_horses.py minnano_pog.html --update-secret")
        sys.exit(1)

    html_path = sys.argv[1]
    update_secret = "--update-secret" in sys.argv[2:]

    if not Path(html_path).exists():
        print(f"エラー: ファイルが見つかりません: {html_path}")
        sys.exit(1)

    horses = parse_horses(html_path)

    if not horses:
        print("馬が見つかりませんでした。HTMLの構造を確認してください。")
        sys.exit(1)

    with open(OUTPUT_FILE, "w", encoding="utf-8") as f:
        json.dump(horses, f, ensure_ascii=False, indent=2)

    print(f"{len(horses)} 頭の馬を登録しました → {OUTPUT_FILE}")
    for h in horses:
        print(f"  {h['owner']:12s}  {h['name']}  (ID: {h['netkeiba_id']})")

    if update_secret:
        print()
        update_secret_via_gh()
    else:
        print(
            "\n※ horses.json はリポジトリにコミットされません。GitHub Actionsで使うには、"
            "`python import_horses.py <HTML> --update-secret` を実行するか"
            "(要 gh CLI)、Settings > Secrets and variables > Actions から"
            "手動でリポジトリシークレット HORSES_JSON に登録してください。"
        )


if __name__ == "__main__":
    main()
