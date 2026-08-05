-- 業界ニュース自動選別・リンク掲載システム: Supabaseテーブル定義
-- Supabaseの SQL Editor でそのまま実行してください。

-- 投稿(処理)済み記事テーブル
-- 重複投稿防止のためのキー。OK/NGを問わず、一度Geminiで判定した記事は
-- 次回以降のRSS取得結果から除外するため、NG判定の記事もここに登録する。
create table if not exists posted_articles (
  id bigint generated always as identity primary key,
  url text not null,
  guid text,
  title text not null,
  posted_at timestamptz not null default now(),
  unique (url)
);

create index if not exists posted_articles_guid_idx on posted_articles (guid);

-- AI判定ログ(全件保存: OK/NG問わず)
-- 誤判定が疑われる場合の検証・チューニングに使用する。
create table if not exists judgement_logs (
  id bigint generated always as identity primary key,
  url text not null,
  title text not null,
  is_industry_news boolean not null,
  reason text,
  judged_at timestamptz not null default now()
);

create index if not exists judgement_logs_url_idx on judgement_logs (url);

-- 実行状態管理(1行のみ運用)
-- 「前回投稿処理を実行した日時」を保持し、48時間未満のスキップ判定に使う。
create table if not exists run_state (
  id smallint primary key default 1,
  last_run_at timestamptz,
  constraint run_state_singleton check (id = 1)
);

insert into run_state (id, last_run_at)
values (1, null)
on conflict (id) do nothing;
