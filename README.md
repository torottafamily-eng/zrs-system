# 業界ニュース自動選別・リンク掲載システム

許諾済みメディアのRSSから記事を取得し、Gemini APIで「業界全体のニュース」のみを自動選別した上で、
タイトルに元記事リンクを付与してSlack(および段階解放後はWebサイト)へ自動投稿する、運用コスト0円のシステムです。

## 全体構成

- **実行**: GitHub Actions(毎日 UTC 23:00 = JST 8:00 起動。実際の投稿間隔は48時間おきになるようスクリプト内で制御)
- **AI判定**: Google Gemini API(`GEMINI_MODEL`で指定。構造化出力で`is_industry_news`/`reason`を取得)
- **永続化**: Supabase(`posted_articles` / `judgement_logs` / `run_state`)
- **通知**: Slack Incoming Webhook
- **Web公開**: `site/data/articles.json`を更新してリポジトリにcommit・push → GitHub Pages等で公開

## セットアップ手順

### 1. Supabaseプロジェクトの作成とテーブル作成

1. [Supabase](https://supabase.com/)で無料プロジェクトを作成する。
2. 左メニューの「SQL Editor」を開き、`supabase/schema.sql` の内容を貼り付けて実行する。
   - `posted_articles`(処理済み記事)、`judgement_logs`(AI判定ログ)、`run_state`(実行状態、1行のみ)の3テーブルが作成されます。
3. 「Project Settings」→「API」から、後述の`SUPABASE_URL`と`SUPABASE_KEY`(service_role key)を控える。

### 2. Gemini APIキーの取得

[Google AI Studio](https://aistudio.google.com/)でAPIキーを発行する(無料枠)。

### 3. Slack Incoming Webhookの作成

投稿先チャンネルにIncoming Webhookを設定し、Webhook URLを控える。
エラー通知を別チャンネルにしたい場合はもう一つ作成する(任意)。

### 4. GitHubリポジトリへの登録

このディレクトリの内容をGitHubリポジトリにpushし、`Settings > Secrets and variables > Actions` に以下を登録する。

| Secret名 | 内容 |
| --- | --- |
| `GEMINI_API_KEY` | Gemini APIキー |
| `GEMINI_MODEL` | 使用するモデル名(例: `gemini-2.5-flash`) |
| `SLACK_WEBHOOK_URL` | Slack投稿用Webhook URL |
| `SLACK_ERROR_WEBHOOK_URL` | エラー通知用Webhook URL(未設定時は`SLACK_WEBHOOK_URL`と共用) |
| `RSS_URL` | 対象メディアのRSSフィードURL |
| `SUPABASE_URL` | SupabaseプロジェクトURL |
| `SUPABASE_KEY` | Supabaseのservice_role key |
| `ENABLE_WEBSITE_PUBLISH` | `true`/`false`(運用初期は`false`推奨) |

### 5. GitHub Pagesの有効化(Webサイト反映を使う場合)

`Settings > Pages` で公開元を「GitHub Actionsからのpush」または`site/`配下を参照する設定にする(既存の静的サイトホスティング構成に合わせて調整してください)。

## 運用フロー

1. **初期運用(目安2〜4週間)**: `ENABLE_WEBSITE_PUBLISH=false` のままSlack投稿のみで運用し、AIの誤判定率を確認する。
   - `judgement_logs`テーブルを見れば、OK/NG両方の判定理由を確認できる。
2. 誤判定率が許容範囲であることを確認したら、Secretsの`ENABLE_WEBSITE_PUBLISH`を`true`に変更する。
   - 以降、OK判定の記事は`site/data/articles.json`に追記され、自動でcommit・pushされる。
3. 手動でその場で実行を試したい場合は、GitHubリポジトリの「Actions」タブから対象ワークフローを選び「Run workflow」(workflow_dispatch)で即時実行できる(48時間判定はスキップされないため、直近実行から間隔が空いていない場合は何もせず終了する)。

## モデル廃止・変更時の対応

Gemini 2.5系のモデルは将来的に段階廃止される可能性があります。モデル名はコードに直書きせず`GEMINI_MODEL`環境変数(Secrets)で管理しているため、対応は以下のみです。

1. [Gemini APIのモデル一覧](https://ai.google.dev/gemini-api/docs/models)で後継モデル名を確認する。
2. GitHub Secretsの`GEMINI_MODEL`を新しいモデル名に変更する。
3. 「Run workflow」で手動実行し、正常に判定されることを確認する。

また、Gemini無料枠のレート制限(RPM/RPD)は変更される可能性があります。1回の実行で大量の新規記事を処理する場合、`main.py`の判定ループはリトライ(指数バックオフ)を行いますが、記事数が非常に多い場合は判定間に`time.sleep`を挟む等の調整を検討してください。

## GitHub Actionsの60日非アクティブ問題への注意

GitHubの仕様上、**リポジトリに60日間コミットが無いと、スケジュール実行(`schedule`トリガー)が自動的に無効化されます。**

- `ENABLE_WEBSITE_PUBLISH=true`で運用していれば、記事投稿のたびに`site/data/articles.json`がcommitされるため、通常は問題になりません。
- `ENABLE_WEBSITE_PUBLISH=false`のSlackのみ運用期間が60日以上続く場合は要注意です。以下いずれかで対処してください。
  - 定期的(60日以内)に、README更新など何でもよいので手動でコミットする。
  - スケジュールが自動停止した場合は、「Actions」タブ→対象ワークフロー→「Enable workflow」ボタンで再有効化する。

## ローカルでの動作確認

```
pip install -r requirements.txt
cp .env.example .env
# .env に各値を入力
python main.py
```

## ディレクトリ構成

```
main.py                       # メイン処理
requirements.txt              # 依存ライブラリ
.env.example                  # 環境変数テンプレート
supabase/schema.sql           # Supabaseテーブル定義(DDL)
.github/workflows/cron.yml    # GitHub Actionsワークフロー
site/index.html               # 公開用トップページ(articles.jsonを読み込んで一覧表示)
site/data/articles.json       # 自動生成される記事データ(ENABLE_WEBSITE_PUBLISH=true時に更新)
```
