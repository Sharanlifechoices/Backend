-- Support for importing the old Firestore data (see app/tools/firestore_migration.py).
-- legacy_id = the Firestore document id, so the import can be re-run without creating duplicates.

alter table public.orders   add column if not exists legacy_id text unique;
alter table public.orders   add column if not exists customer_dob date;   -- old site stored the buyer's birthday on the order
alter table public.bookings add column if not exists legacy_id text unique;
alter table public.gallery  add column if not exists legacy_id text unique;

-- Birthdays of people who bought without an account (their dob lives on the order).
create or replace function public.birthday_guest_buyers(m int, d int)
returns table (email text, customer_name text) language sql stable as $$
  select distinct on (lower(o.email)) lower(o.email), o.customer_name
  from public.orders o
  where o.status = 'paid' and o.user_id is null and o.customer_dob is not null
    and extract(month from o.customer_dob) = m and extract(day from o.customer_dob) = d
  order by lower(o.email), o.paid_at desc;
$$;
