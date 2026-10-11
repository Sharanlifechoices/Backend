-- Gallery / retreat images (Cloudinary URLs, uploaded from the browser) and monthly newsletters.

create table if not exists public.gallery (
  id uuid primary key default gen_random_uuid(),
  url text not null,
  title text not null default '',
  type text not null default 'gallery' check (type in ('gallery', 'retreat')),
  created_at timestamptz not null default now()
);
create index if not exists gallery_type_idx on public.gallery (type, created_at desc);
alter table public.gallery enable row level security;

create table if not exists public.newsletters (
  id uuid primary key default gen_random_uuid(),
  subject text not null,
  body_html text not null,
  status text not null default 'draft' check (status in ('draft', 'sending', 'sent', 'partial')),
  audience_count integer,
  sent_count integer not null default 0,
  failed_count integer not null default 0,
  created_at timestamptz not null default now(),
  started_at timestamptz,
  finished_at timestamptz
);
alter table public.newsletters enable row level security;
