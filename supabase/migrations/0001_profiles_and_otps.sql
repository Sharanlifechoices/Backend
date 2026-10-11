-- Run in the Supabase SQL editor. The backend uses the service-role key (bypasses RLS);
-- RLS is enabled with NO policies so the anon/authenticated keys can read nothing directly.

create extension if not exists pgcrypto;

create table if not exists public.profiles (
  user_id uuid primary key references auth.users(id) on delete cascade,
  email text not null unique,
  full_name text not null default '',
  phone text not null default '',
  dob date,
  is_unsubscribed boolean not null default false,
  unsubscribe_token uuid not null default gen_random_uuid() unique,
  created_at timestamptz not null default now()
);
create index if not exists profiles_created_at_idx on public.profiles (created_at);
alter table public.profiles enable row level security;

-- Create a profile row whenever an auth user is created (signup stores name/phone/dob
-- in user_metadata; the profile is the source of truth afterwards).
create or replace function public.handle_new_user_profile()
returns trigger language plpgsql security definer set search_path = public as $$
begin
  insert into public.profiles (user_id, email, full_name, phone, dob)
  values (
    new.id,
    lower(new.email),
    coalesce(new.raw_user_meta_data->>'full_name', ''),
    coalesce(new.raw_user_meta_data->>'phone', ''),
    nullif(new.raw_user_meta_data->>'dob', '')::date
  )
  on conflict (user_id) do nothing;
  return new;
end;
$$;

drop trigger if exists on_auth_user_created_profile on auth.users;
create trigger on_auth_user_created_profile
  after insert on auth.users
  for each row execute function public.handle_new_user_profile();

-- One-time codes for signup verification and password reset. Only an HMAC of the code is stored.
create table if not exists public.email_otps (
  id uuid primary key default gen_random_uuid(),
  email text not null,
  purpose text not null check (purpose in ('signup', 'reset')),
  code_hash text not null,
  attempts int not null default 0,
  expires_at timestamptz not null,
  consumed_at timestamptz,
  created_at timestamptz not null default now()
);
create index if not exists email_otps_lookup_idx on public.email_otps (email, purpose, created_at desc);
alter table public.email_otps enable row level security;
