-- 業界ニュース自動選別・リンク掲載システム: Supabaseテーブル定義
-- Supabaseの SQL Editor でそのまま実行してください。

-- 公開記事(Web サイトが直接読み込むテーブル)
-- 重複投稿防止のキーも兼ねる(urlをunique制約にすることで、同じ記事のOK再登録を防ぐ)。
create table if not exists articles (
  id bigint generated always as identity primary key,
  url text not null unique,
  title text not null,
  published_at timestamptz,
  created_at timestamptz not null default now()
);

alter table articles enable row level security;

-- Webサイトはanon(publishable)キーで直接SELECTするため、読み取りのみ公開する。
create policy "Public read access" on articles
  for select
  using (true);

-- AI判定ログ(全件保存: OK/NG問わず)
-- 誤判定が疑われる場合の検証・チューニングに使う。また、
-- 一度判定した記事(NGも含む)のURLをここから拾って重複判定を避ける。
create table if not exists judgement_logs (
  id bigint generated always as identity primary key,
  url text not null,
  title text not null,
  is_industry_news boolean not null,
  reason text,
  judged_at timestamptz not null default now()
);

create index if not exists judgement_logs_url_idx on judgement_logs (url);

alter table judgement_logs enable row level security;
-- ポリシーを追加しないため、anon/publishableキーからは一切アクセスできない
-- (service_roleキーのみアクセス可能)。

-- 実行状態管理(1行のみ運用)
-- 「前回投稿処理を実行した日時」を保持し、48時間未満のスキップ判定に使う。
create table if not exists run_state (
  id smallint primary key default 1,
  last_run_at timestamptz,
  constraint run_state_singleton check (id = 1)
);

alter table run_state enable row level security;
-- こちらもポリシーなし = service_roleキーのみアクセス可能。

insert into run_state (id, last_run_at)
values (1, null)
on conflict (id) do nothing;
