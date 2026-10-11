-- Addresses that must receive no marketing email (follow-up, birthday, newsletter).
-- Covers guest purchasers who have no profile; account holders also get profiles.is_unsubscribed.
create table if not exists public.email_suppressions (
  email text primary key,               -- stored lowercase
  created_at timestamptz not null default now()
);
alter table public.email_suppressions enable row level security;
