# zrs-system

## プロジェクト概要

業界ニュース自動選別・リンク掲載システム。許諾済みメディアのRSSから記事を取得し、Gemini APIで「業界全体のニュース」のみを自動選別した上で、Slack（および段階解放後はWebサイト）へ自動投稿する。運用コストは完全0円（GitHub Actions + Gemini Free Tier + Supabase Free Tier + Slack Webhook）。

### 技術構成
- 言語: Python
- 定期実行: GitHub Actions（毎日実行、48時間未満ならスクリプト内でスキップ）
- AI判定: Google Gemini API（`google-genai`、`response_schema`によるネイティブ構造化出力。モデル名は`GEMINI_MODEL`環境変数で指定し固定コーディングしない）
- 永続化: Supabase（Free Tier）。GitHub Actionsランナーは使い捨てのため、ローカルjsonでは状態を保持できない
- 通知: Slack Incoming Webhook
- 出力先: 静的サイト（生成物をリポジトリにcommit・pushし、GitHub Pages等でビルド公開する想定）

### 運用上の注意点
- 初期運用（2〜4週間目安）はSlack投稿のみとし、`ENABLE_WEBSITE_PUBLISH`フラグでWebサイト反映を有効化する
- リポジトリが60日間コミットされないとGitHub Actionsのスケジュール実行が自動停止する（READMEに対処法を記載）
- 詳細仕様は本ディレクトリ内の構築指示書（ユーザーとの会話ログ）を参照

## Claudeとの協働ルール

過去のプロジェクトでの経験から得た教訓。このプロジェクトでも常に以下の方針で進める。

### 1. 裏方の作業はClaudeが直接やる

- git操作(commit, push)、ファイル編集、ビルド、依存関係のインストールなど、Claude Code自身が自分のPC上で実行できる作業は、ユーザーにPowerShellで打たせずClaudeが直接実行する。
- ユーザーに操作してもらうのは、**ブラウザで実際に目で見る必要がある作業**(アプリ画面の確認、GitHub/Vercel等へのログイン)のみに限定する。
- 何か「ターミナルで実行してください」と頼みそうになったら、まず「これは自分(Claude)で直接できないか?」を自問する。

### 2. 動作確認は「ユーザーのブラウザで見える状態」まで確認する

- Claude自身の実行環境で `npm run dev` 等が起動したことと、ユーザーが実際にブラウザで画面を見られる状態は別物。後者まで確認してから完了報告する。
- ユーザーに操作を依頼する場合は、抽象的な指示(「アドレスバーをクリックして」等)ではなく、Windowsキー→検索→クリックのように一つずつ具体的なステップに分解し、都度結果を確認しながら進める。
- ユーザーはPowerShell操作に不慣れなため、やむを得ずコマンド入力を依頼する場合は以下に注意する:
  - コマンドは1行ずつ、Enterを押させてから次を入力させる(複数行の一括貼り付けは避ける)。
  - 「何も表示されない」ときは、成功して何も出ていないのか、入力自体が失敗しているのかを、プロンプトが入力待ち状態(`PS C:\...\>`)に戻っているかで判断する。

### 3. セキュリティは「画面遷移だけで守らない」

- 認証・認可のチェックは必ずサーバー側の処理そのもの(API/Server Action)でも行う。画面側のガード(遷移制御)だけに頼らない。
- 本番公開前後を問わず、他人のID/データにアクセスできないか(テナント分離、なりすまし)を実際の攻撃シナリオで検証する。

## 関連メモ

- Obsidian Vault: `C:\Users\torot\Documents\Obsidian Vault\Claude Codeと作業するときの教訓.md`
- Obsidian Vault: `C:\Users\torot\Documents\Obsidian Vault\AI Flash Cards - ローカル起動メモ.md`
