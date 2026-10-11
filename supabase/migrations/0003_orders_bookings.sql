-- Orders (Razorpay), their line items, session bookings and per-date availability.
-- All writes happen server-side with the service-role key; RLS is on with no policies.

create table if not exists public.orders (
  id uuid primary key default gen_random_uuid(),
  user_id uuid references auth.users(id) on delete set null,  -- null = guest/legacy order, linked by email later
  email text not null,
  customer_name text not null default '',
  phone text not null default '',
  status text not null default 'created' check (status in ('created', 'paid', 'failed')),
  source text not null default 'cart' check (source in ('cart', 'direct', 'legacy')),
  total_minor integer not null check (total_minor >= 0),
  currency text not null default 'SGD',
  razorpay_order_id text unique,
  razorpay_payment_id text,
  paid_at timestamptz,
  confirmation_emailed_at timestamptz,   -- atomic claim so the confirmation email goes out once
  followup_emailed_at timestamptz,
  created_at timestamptz not null default now()
);
create index if not exists orders_user_idx on public.orders (user_id, created_at desc);
create index if not exists orders_email_idx on public.orders (lower(email));
create index if not exists orders_paid_idx on public.orders (status, paid_at);
alter table public.orders enable row level security;

create table if not exists public.order_items (
  id uuid primary key default gen_random_uuid(),
  order_id uuid not null references public.orders(id) on delete cascade,
  item_id text not null,                 -- snapshot, no FK: catalog rows can be retired later
  type text not null,
  title text not null,
  subtitle text not null default '',
  price_minor integer not null,
  currency text not null default 'SGD',
  preferred_date date,
  preferred_time text
);
create index if not exists order_items_order_idx on public.order_items (order_id);
alter table public.order_items enable row level security;

create table if not exists public.bookings (
  id uuid primary key default gen_random_uuid(),
  order_item_id uuid unique references public.order_items(id) on delete cascade,  -- unique = idempotent creation
  user_id uuid references auth.users(id) on delete set null,
  email text not null,
  name text not null default '',
  phone text not null default '',
  service_id text not null,
  service_title text not null,
  booking_date date not null,
  slot text not null,
  status text not null default 'pending' check (status in ('pending', 'confirmed', 'cancelled')),
  created_at timestamptz not null default now()
);
create index if not exists bookings_date_idx on public.bookings (booking_date, slot);
alter table public.bookings enable row level security;

create table if not exists public.availability (
  day date primary key,
  blocked_slots text[] not null default '{}'
);
alter table public.availability enable row level security;
