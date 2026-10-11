-- Email dedupe log + verified flag on profiles + birthday lookup, used by the daily jobs.

-- profiles.email_verified mirrors auth.users.email_confirmed_at so jobs can tell real accounts
-- from abandoned signups without calling the auth API.
alter table public.profiles add column if not exists email_verified boolean not null default false;

update public.profiles p set email_verified = true
from auth.users u where u.id = p.user_id and u.email_confirmed_at is not null;

create or replace function public.handle_new_user_profile()
returns trigger language plpgsql security definer set search_path = public as $$
begin
  insert into public.profiles (user_id, email, full_name, phone, dob, email_verified)
  values (
    new.id,
    lower(new.email),
    coalesce(new.raw_user_meta_data->>'full_name', ''),
    coalesce(new.raw_user_meta_data->>'phone', ''),
    nullif(new.raw_user_meta_data->>'dob', '')::date,
    new.email_confirmed_at is not null
  )
  on conflict (user_id) do nothing;
  return new;
end;
$$;

create or replace function public.sync_profile_verified()
returns trigger language plpgsql security definer set search_path = public as $$
begin
  update public.profiles set email_verified = (new.email_confirmed_at is not null) where user_id = new.id;
  return new;
end;
$$;

drop trigger if exists on_auth_user_verified on auth.users;
create trigger on_auth_user_verified
  after update of email_confirmed_at on auth.users
  for each row execute function public.sync_profile_verified();

-- One row per (kind, recipient, subject) email. The unique dedupe_key is what makes the daily
-- jobs safe to re-run: a second attempt cannot insert the same key.
create table if not exists public.email_log (
  id uuid primary key default gen_random_uuid(),
  dedupe_key text not null unique,        -- e.g. followup:<order_id>, birthday:<user_id>:<year>
  kind text not null,
  recipient text not null,
  status text not null default 'pending' check (status in ('pending', 'sent', 'failed')),
  provider_id text,
  error text,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now()
);
create index if not exists email_log_kind_idx on public.email_log (kind, created_at desc);
alter table public.email_log enable row level security;

-- Verified, subscribed users whose birthday is month/day.
create or replace function public.birthday_profiles(m int, d int)
returns setof public.profiles language sql stable as $$
  select * from public.profiles
  where dob is not null
    and extract(month from dob) = m and extract(day from dob) = d
    and email_verified and not is_unsubscribed;
$$;
