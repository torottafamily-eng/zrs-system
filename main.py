"""業界ニュース自動選別・リンク掲載システム

処理フロー:
  Step 0: 前回実行から指定時間(既定48時間)経っていなければスキップ
  Step 1: RSSから記事取得
  Step 2: 未処理(posted_articles未登録)の記事のみ抽出
  Step 3: Geminiで「業界全体ニュース」かどうかを構造化出力で判定し、全件ログ保存
  Step 4: OK判定の記事のみSlack投稿(有効時はWebサイトへも反映)
  Step 5: 各外部呼び出しはリトライし、最終失敗時はSlackへエラー通知

設計メモ:
  - posted_articles は「投稿済み」だけでなく「判定処理済み」の記事も登録する。
    NG記事を登録しないと、RSSに残り続ける限り毎回Geminiに再判定させることになり、
    無料枠のレート制限を無駄に消費するため。
  - Slackのテキストはmrkdwn形式(<url|text>)でリンク化する。HTMLの<a>タグは
    Slackでは文字列としてそのまま表示されクリックできないため、Webサイト用の
    HTMLリンクとは別に生成する。
"""

from __future__ import annotations

import json
import logging
import os
import subprocess
import time
from datetime import datetime, timedelta, timezone
from typing import Any

import feedparser
import requests
from dotenv import load_dotenv
from google import genai
from google.genai import types
from pydantic import BaseModel
from supabase import Client, create_client

load_dotenv()

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)
logger = logging.getLogger("news-bot")

SITE_DATA_PATH = os.path.join("site", "data", "articles.json")


class ConfigError(RuntimeError):
    pass


class Config:
    def __init__(self) -> None:
        self.gemini_api_key = self._require("GEMINI_API_KEY")
        self.gemini_model = self._require("GEMINI_MODEL")
        self.slack_webhook_url = self._require("SLACK_WEBHOOK_URL")
        self.slack_error_webhook_url = (
            os.environ.get("SLACK_ERROR_WEBHOOK_URL") or self.slack_webhook_url
        )
        self.rss_url = self._require("RSS_URL")
        self.supabase_url = self._require("SUPABASE_URL")
        self.supabase_key = self._require("SUPABASE_KEY")
        self.enable_website_publish = (
            os.environ.get("ENABLE_WEBSITE_PUBLISH", "false").strip().lower() == "true"
        )
        self.run_interval_hours = float(os.environ.get("RUN_INTERVAL_HOURS", "48"))

    @staticmethod
    def _require(name: str) -> str:
        value = os.environ.get(name)
        if not value:
            raise ConfigError(f"環境変数 {name} が設定されていません")
        return value


class JudgementResult(BaseModel):
    is_industry_news: bool
    reason: str


JUDGE_PROMPT_TEMPLATE = """あなたはB2B業界メディアの編集者です。
以下の記事が「業界全体に関わるニュース」かどうかを判定してください。

【掲載NG(弾く)】
- 個別企業の「新製品発表」「イベント開催」「個別決算」「人事情報」
- 特定の1社のみに帰属するトピック

【掲載OK(採用)】
- 業界全体の動向、市場規模の予測・調査データ
- 法改正、省庁のガイドライン改定、業界標準化の動き
- 複数企業や業界全体に影響を与えるニュース

記事タイトル: {title}
概要: {summary}
"""


def with_retry(func, *args, retries: int = 3, base_delay: float = 2.0, **kwargs):
    last_exc: Exception | None = None
    for attempt in range(1, retries + 1):
        try:
            return func(*args, **kwargs)
        except Exception as exc:  # noqa: BLE001 - リトライのため広く捕捉
            last_exc = exc
            logger.warning("試行 %s/%s 失敗: %s", attempt, retries, exc)
            if attempt < retries:
                time.sleep(base_delay * (2 ** (attempt - 1)))
    assert last_exc is not None
    raise last_exc


def fetch_rss_articles(rss_url: str) -> list[dict[str, Any]]:
    feed = feedparser.parse(rss_url)
    if getattr(feed, "bozo", False) and not feed.entries:
        raise RuntimeError(f"RSS取得/パースに失敗しました: {feed.bozo_exception}")

    articles = []
    for entry in feed.entries:
        url = entry.get("link")
        title = entry.get("title")
        if not url or not title:
            continue
        guid = entry.get("id") or entry.get("guid") or url
        summary = entry.get("summary", "") or ""
        published_at = entry.get("published") or entry.get("updated") or ""
        articles.append(
            {
                "guid": guid,
                "url": url,
                "title": title,
                "summary": summary,
                "published_at": published_at,
            }
        )
    return articles


def get_last_run_at(supabase: Client) -> datetime | None:
    res = supabase.table("run_state").select("last_run_at").eq("id", 1).limit(1).execute()
    rows = res.data or []
    if not rows or not rows[0].get("last_run_at"):
        return None
    raw = rows[0]["last_run_at"].replace("Z", "+00:00")
    return datetime.fromisoformat(raw)


def update_last_run_at(supabase: Client) -> None:
    supabase.table("run_state").update(
        {"last_run_at": datetime.now(timezone.utc).isoformat()}
    ).eq("id", 1).execute()


def get_processed_keys(supabase: Client) -> set[str]:
    res = supabase.table("posted_articles").select("url,guid").execute()
    keys: set[str] = set()
    for row in res.data or []:
        if row.get("guid"):
            keys.add(row["guid"])
        if row.get("url"):
            keys.add(row["url"])
    return keys


def mark_processed(supabase: Client, article: dict[str, Any]) -> None:
    supabase.table("posted_articles").insert(
        {
            "url": article["url"],
            "guid": article.get("guid"),
            "title": article["title"],
        }
    ).execute()


def save_judgement_log(supabase: Client, article: dict[str, Any], result: JudgementResult) -> None:
    supabase.table("judgement_logs").insert(
        {
            "url": article["url"],
            "title": article["title"],
            "is_industry_news": result.is_industry_news,
            "reason": result.reason,
        }
    ).execute()


def judge_industry_news(client: genai.Client, model: str, article: dict[str, Any]) -> JudgementResult:
    prompt = JUDGE_PROMPT_TEMPLATE.format(
        title=article["title"], summary=article.get("summary") or "(概要なし)"
    )
    response = client.models.generate_content(
        model=model,
        contents=prompt,
        config=types.GenerateContentConfig(
            response_mime_type="application/json",
            response_schema=JudgementResult,
        ),
    )
    return JudgementResult.model_validate_json(response.text)


def format_slack_message(article: dict[str, Any]) -> str:
    # Slackのmrkdwn形式。HTMLの<a>タグはSlackでは文字列のまま表示されクリックできない。
    return f"<{article['url']}|{article['title']}>"


def format_html_link(article: dict[str, Any]) -> str:
    return f'<a href="{article["url"]}">{article["title"]}</a>'


def post_to_slack(webhook_url: str, text: str) -> None:
    resp = requests.post(webhook_url, json={"text": text}, timeout=15)
    resp.raise_for_status()


def publish_to_website(article: dict[str, Any]) -> None:
    os.makedirs(os.path.dirname(SITE_DATA_PATH), exist_ok=True)
    if os.path.exists(SITE_DATA_PATH):
        with open(SITE_DATA_PATH, "r", encoding="utf-8") as f:
            data = json.load(f)
    else:
        data = []

    data.insert(
        0,
        {
            "title": article["title"],
            "url": article["url"],
            "title_html": format_html_link(article),
            "published_at": article.get("published_at"),
            "posted_at": datetime.now(timezone.utc).isoformat(),
        },
    )

    with open(SITE_DATA_PATH, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
        f.write("\n")

    _git_commit_and_push(SITE_DATA_PATH, f"chore: add article - {article['title']}")


def _git_commit_and_push(path: str, message: str) -> None:
    subprocess.run(["git", "config", "user.name", "news-bot"], check=True)
    subprocess.run(
        ["git", "config", "user.email", "news-bot@users.noreply.github.com"], check=True
    )
    subprocess.run(["git", "add", path], check=True)

    diff = subprocess.run(["git", "diff", "--cached", "--quiet"])
    if diff.returncode == 0:
        logger.info("git差分なし。コミットをスキップします。")
        return

    subprocess.run(["git", "commit", "-m", message], check=True)
    subprocess.run(["git", "push"], check=True)


def notify_error(config: Config, stage: str, exc: Exception) -> None:
    logger.error("%s でエラーが発生しました: %s", stage, exc)
    try:
        post_to_slack(
            config.slack_error_webhook_url,
            f":rotating_light: [業界ニュースBot] {stage} でエラーが発生しました: {exc}",
        )
    except Exception as notify_exc:  # noqa: BLE001
        logger.error("エラー通知の送信にも失敗しました: %s", notify_exc)


def safe_update_last_run(config: Config, supabase: Client) -> None:
    try:
        update_last_run_at(supabase)
    except Exception as exc:  # noqa: BLE001
        notify_error(config, "実行日時の更新", exc)


def main() -> None:
    try:
        config = Config()
    except ConfigError as exc:
        logger.error("設定エラー: %s", exc)
        raise SystemExit(1) from exc

    supabase = create_client(config.supabase_url, config.supabase_key)

    # Step 0: 前回実行からの経過時間チェック
    try:
        last_run_at = get_last_run_at(supabase)
    except Exception as exc:  # noqa: BLE001
        notify_error(config, "実行状態の取得", exc)
        return

    if last_run_at is not None:
        elapsed = datetime.now(timezone.utc) - last_run_at
        threshold = timedelta(hours=config.run_interval_hours)
        if elapsed < threshold:
            logger.info(
                "前回実行から%.1f時間しか経過していないためスキップします(閾値%s時間)。",
                elapsed.total_seconds() / 3600,
                config.run_interval_hours,
            )
            return

    # Step 1: RSS取得(失敗したら中断)
    try:
        articles = with_retry(fetch_rss_articles, config.rss_url)
    except Exception as exc:  # noqa: BLE001
        notify_error(config, "RSS取得", exc)
        return

    # Step 2: 重複(処理済み)チェック
    try:
        processed_keys = get_processed_keys(supabase)
    except Exception as exc:  # noqa: BLE001
        notify_error(config, "投稿済み記事一覧の取得", exc)
        return

    new_articles = [
        a for a in articles if a["guid"] not in processed_keys and a["url"] not in processed_keys
    ]
    logger.info("新規記事: %d件(取得%d件中)", len(new_articles), len(articles))

    if not new_articles:
        safe_update_last_run(config, supabase)
        return

    # Step 3: AI判定
    genai_client = genai.Client(api_key=config.gemini_api_key)
    ok_articles = []
    judged_articles = []

    for article in new_articles:
        try:
            result = with_retry(judge_industry_news, genai_client, config.gemini_model, article)
        except Exception as exc:  # noqa: BLE001
            logger.error("AI判定に失敗したためこの記事は今回スキップします: %s / %s", article["title"], exc)
            continue

        judged_articles.append(article)

        try:
            save_judgement_log(supabase, article, result)
        except Exception as exc:  # noqa: BLE001
            logger.error("判定ログの保存に失敗しました: %s", exc)

        if result.is_industry_news:
            ok_articles.append(article)

    # Step 4: 配信(OK記事のみ)。判定済み(NG含む)は処理済みとして登録し、次回以降の再判定を防ぐ。
    for article in ok_articles:
        message = format_slack_message(article)
        try:
            with_retry(post_to_slack, config.slack_webhook_url, message)
        except Exception as exc:  # noqa: BLE001
            notify_error(config, f"Slack投稿({article['title']})", exc)
            continue  # 投稿できなかった記事はposted登録せず次回リトライ対象にする

        if config.enable_website_publish:
            try:
                with_retry(publish_to_website, article)
            except Exception as exc:  # noqa: BLE001
                notify_error(config, f"Webサイト反映({article['title']})", exc)
                continue

        try:
            mark_processed(supabase, article)
        except Exception as exc:  # noqa: BLE001
            logger.error("処理済み登録に失敗しました: %s / %s", article["title"], exc)

    ok_urls = {a["url"] for a in ok_articles}
    for article in judged_articles:
        if article["url"] in ok_urls:
            continue
        try:
            mark_processed(supabase, article)
        except Exception as exc:  # noqa: BLE001
            logger.error("処理済み登録(NG記事)に失敗しました: %s / %s", article["title"], exc)

    safe_update_last_run(config, supabase)


if __name__ == "__main__":
    main()
