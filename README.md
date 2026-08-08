# 業界ニュース自動選別・リンク掲載システム

許諾済みメディアのRSSから記事を取得し、Groq API(Llama系モデル)で「業界全体のニュース」のみを自動選別した上で、
Supabaseに保存し、Webサイト(`site/index.html`)がそのデータを直接読み込んで一覧表示する、
運用コスト0円のシステムです。

## 全体構成

- **実行**: GitHub Actions(毎日 UTC 23:00 = JST 8:00 起動。実際の投稿間隔は48時間おきになるようスクリプト内で制御)
- **AI判定**: Groq API(`GROQ_MODEL`で指定。構造化出力で`is_industry_news`/`reason`を取得。Gemini APIは2026年3月の課金体系変更でカード登録なしの無料利用が難しくなったため、カード登録不要の無料枠があるGroqに変更)
- **永続化**: Supabase
  - `articles`: 業界ニュースと判定された記事(Webサイトが直接読み込む公開テーブル)
  - `judgement_logs`: 全記事の判定ログ(OK/NG問わず。重複判定防止のキーも兼ねる)
  - `run_state`: 前回実行日時(1行のみ)
- **Web公開**: `site/index.html`が、ブラウザから直接Supabaseの`articles`テーブルをREST API経由で取得して表示する(サーバー処理・ビルド不要)

Slackなどの通知は使用していません。実行が失敗した場合はGitHub Actionsのジョブが失敗として記録され、
リポジトリの通知設定に従ってオーナーにメール等で通知されます(詳しくは後述)。

## セットアップ手順

### 1. Supabaseプロジェクトの作成とテーブル作成

1. [Supabase](https://supabase.com/)で無料プロジェクトを作成する。
2. 左メニューの「SQL Editor」を開き、`supabase/schema.sql` の内容を貼り付けて実行する。
   - `articles`(公開記事)、`judgement_logs`(AI判定ログ)、`run_state`(実行状態、1行のみ)の3テーブルが作成されます。
   - `articles`のみRow Level Securityで「読み取り専用の公開ポリシー」を付与しています。他2テーブルはservice_roleキーのみアクセス可能です。
3. 「Project Settings」→「API Keys」→「Legacy anon, service_role API keys」から、後述の`SUPABASE_URL`(Project URL)と`SUPABASE_KEY`(service_role key)を控える。
   - 同じ画面の`anon`キーは`site/index.html`内に埋め込み済みです(公開して問題ない設計です。詳しくは後述)。

### 2. Groq APIキーの取得

[GroqCloud](https://console.groq.com/keys)でアカウントを作成し(Googleアカウント等でログイン可能)、APIキーを発行する(無料枠。クレジットカード登録不要)。

### 3. GitHubリポジトリへの登録

このディレクトリの内容をGitHubリポジトリにpushし、`Settings > Secrets and variables > Actions` に以下を登録する。

| Secret名 | 内容 |
| --- | --- |
| `GROQ_API_KEY` | Groq APIキー |
| `GROQ_MODEL` | 使用するモデル名(例: `openai/gpt-oss-120b`。構造化出力(`json_schema`)対応モデルのみ使用可) |
| `RSS_URL` | 対象メディアのRSSフィードURL |
| `SUPABASE_URL` | SupabaseプロジェクトURL |
| `SUPABASE_KEY` | Supabaseのservice_role key |

### 4. GitHub Pagesの有効化

`Settings > Pages` で、公開元を`site/`ディレクトリ(または任意のブランチ)に設定する。`site/index.html`はビルド不要の静的ファイルなので、そのまま公開できる。

## なぜanonキーをHTMLに直書きしているか

`site/index.html`には`SUPABASE_URL`と`anon`キーを直接埋め込んでいます。`anon`キーは「公開して問題ない」設計のキーで、
実際のアクセス制御は`articles`テーブルに設定したRow Level Security(RLS)ポリシー(読み取りのみ許可)で行っています。
`judgement_logs`や`run_state`にはRLSポリシーを一切付与していないため、このanonキーではアクセスできません。

## 運用フロー

- 通常運用は`main.py`の定期実行のみで完結します(段階的な公開フラグはありません。AIがOKと判定した記事は都度`articles`に保存され、即座にWebサイトに反映されます)。
- 誤判定が気になる場合は、Supabaseの`judgement_logs`テーブルをSQL Editorやテーブルビューで確認してください(OK/NG両方の判定理由が記録されています)。
- 手動でその場で実行を試したい場合は、GitHubリポジトリの「Actions」タブから対象ワークフローを選び「Run workflow」(workflow_dispatch)で即時実行できる(48時間判定はスキップされないため、直近実行から間隔が空いていない場合は何もせず終了する)。

## 失敗時の通知について

このシステムはSlack等の通知を使わず、エラーが発生した場合はスクリプトを異常終了させ、GitHub Actionsのジョブを「失敗」として終わらせる方式を採っています。
GitHubのデフォルト設定では、失敗したワークフロー実行についてリポジトリオーナーにメール通知が送られます(`https://github.com/settings/notifications` の「Actions」設定で確認・変更できます)。

## モデル廃止・変更時の対応

Groqが提供するモデルは将来的に廃止・変更される可能性があります。モデル名はコードに直書きせず`GROQ_MODEL`環境変数(Secrets)で管理しているため、対応は以下のみです。

1. [Groqのモデル一覧](https://console.groq.com/docs/models)で後継モデル名を確認する(構造化出力`json_schema`に対応しているモデルを選ぶこと。対応状況は[Structured Outputsのドキュメント](https://console.groq.com/docs/structured-outputs)を参照)。
2. GitHub Secretsの`GROQ_MODEL`を新しいモデル名に変更する。
3. 「Run workflow」で手動実行し、正常に判定されることを確認する。

また、Groq無料枠のレート制限(RPM/RPD/トークン数)は変更される可能性があります。1回の実行で大量の新規記事を処理する場合、`main.py`の判定ループはリトライ(指数バックオフ)を行いますが、記事数が非常に多い場合は判定間に`time.sleep`を挟む等の調整を検討してください。

## GitHub Actionsの60日非アクティブ問題への注意

GitHubの仕様上、**リポジトリに60日間コミットが無いと、スケジュール実行(`schedule`トリガー)が自動的に無効化されます。**
このシステムはコード自体をコミットしないため(データはすべてSupabase側)、対処が必要です。

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
site/index.html               # 公開用ページ(Supabaseのarticlesテーブルを直接取得して一覧表示)
```
