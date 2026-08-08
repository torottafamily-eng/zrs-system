"""業界ニュース自動選別・リンク掲載システム

処理フロー:
  Step 0: 前回実行から指定時間(既定48時間)経っていなければスキップ
  Step 1: RSSから記事取得
  Step 2: 未判定(judgement_logs未登録)の記事のみ抽出
  Step 3: Groq(Llama系モデル)で「業界全体ニュース」かどうかを構造化出力で判定し、全件ログ保存
  Step 4: OK判定の記事のみ articles テーブルに保存(Webサイトはこのテーブルを直接参照する)

エラー発生時はSlack通知等は行わず、例外を上位に伝播させてスクリプトを異常終了させる。
GitHub Actions側でジョブが失敗として記録され、リポジトリの通知設定に従って
オーナーに失敗通知が届く(README参照)。

設計メモ:
  - 重複防止は judgement_logs.url を「一度判定した記事」の記録として利用する。
    NG記事もここに記録されるため、RSSに残り続ける記事を毎回AIに
    再判定させることがない。
  - articles テーブルは重複防止のキーではなく、Webサイトが直接読み込む
    公開データそのもの(OK判定の記事のみ)。RLSで読み取り専用に公開している。
"""

from __future__ import annotations

import logging
import os
import time
from datetime import datetime, timedelta, timezone
from typing import Any

import feedparser
from dotenv import load_dotenv
from groq import Groq
from pydantic import BaseModel
from supabase import Client, create_client

load_dotenv()

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)
logger = logging.getLogger("news-bot")


class ConfigError(RuntimeError):
    pass


class Config:
    def __init__(self) -> None:
        self.groq_api_key = self._require("GROQ_API_KEY")
        self.groq_model = self._require("GROQ_MODEL")
        self.rss_url = self._require("RSS_URL")
        self.supabase_url = self._require("SUPABASE_URL")
        self.supabase_key = self._require("SUPABASE_KEY")
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
        summary = entry.get("summary", "") or ""
        published_at = entry.get("published") or entry.get("updated") or ""
        articles.append(
            {
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


def get_judged_urls(supabase: Client) -> set[str]:
    res = supabase.table("judgement_logs").select("url").execute()
    return {row["url"] for row in (res.data or []) if row.get("url")}


def save_judgement_log(supabase: Client, article: dict[str, Any], result: JudgementResult) -> None:
    supabase.table("judgement_logs").insert(
        {
            "url": article["url"],
            "title": article["title"],
            "is_industry_news": result.is_industry_news,
            "reason": result.reason,
        }
    ).execute()


def save_article(supabase: Client, article: dict[str, Any]) -> None:
    supabase.table("articles").insert(
        {
            "url": article["url"],
            "title": article["title"],
            "published_at": article.get("published_at") or None,
        }
    ).execute()


JUDGEMENT_JSON_SCHEMA = {
    "type": "json_schema",
    "json_schema": {
        "name": "judgement",
        "strict": True,
        "schema": {
            "type": "object",
            "properties": {
                "is_industry_news": {"type": "boolean"},
                "reason": {"type": "string"},
            },
            "required": ["is_industry_news", "reason"],
            "additionalProperties": False,
        },
    },
}


def judge_industry_news(client: Groq, model: str, article: dict[str, Any]) -> JudgementResult:
    prompt = JUDGE_PROMPT_TEMPLATE.format(
        title=article["title"], summary=article.get("summary") or "(概要なし)"
    )
    response = client.chat.completions.create(
        model=model,
        messages=[{"role": "user", "content": prompt}],
        response_format=JUDGEMENT_JSON_SCHEMA,
    )
    return JudgementResult.model_validate_json(response.choices[0].message.content)


def main() -> None:
    config = Config()
    supabase = create_client(config.supabase_url, config.supabase_key)

    # Step 0: 前回実行からの経過時間チェック
    last_run_at = get_last_run_at(supabase)
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

    # Step 1: RSS取得
    articles = with_retry(fetch_rss_articles, config.rss_url)

    # Step 2: 未判定の記事のみ抽出
    judged_urls = get_judged_urls(supabase)
    new_articles = [a for a in articles if a["url"] not in judged_urls]
    logger.info("新規記事: %d件(取得%d件中)", len(new_articles), len(articles))

    if not new_articles:
        update_last_run_at(supabase)
        return

    # Step 3 & 4: AI判定 -> ログ保存 -> OK記事のみarticlesに保存
    groq_client = Groq(api_key=config.groq_api_key)
    saved_count = 0

    for article in new_articles:
        try:
            result = with_retry(judge_industry_news, groq_client, config.groq_model, article)
        except Exception as exc:  # noqa: BLE001
            logger.error("AI判定に失敗したためこの記事は今回スキップします: %s / %s", article["title"], exc)
            continue  # judgement_logsに残らないため次回また判定対象になる

        save_judgement_log(supabase, article, result)

        if result.is_industry_news:
            save_article(supabase, article)
            saved_count += 1

    logger.info("業界ニュースとして掲載した記事: %d件", saved_count)
    update_last_run_at(supabase)


if __name__ == "__main__":
    main()
