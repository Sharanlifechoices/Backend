-- Catalog (books, services, retreat enquiry) and per-user cart lines.
-- pdf_url is PRIVATE: it is emailed after purchase and never returned by the public catalog API.

create table if not exists public.catalog_items (
  id text primary key,
  type text not null check (type in ('book', 'service', 'retreat')),
  title text not null,
  subtitle text not null default '',
  description text not null default '',
  price_minor integer check (price_minor is null or price_minor >= 0),  -- null = enquiry only
  currency text not null default 'SGD',
  cover text not null default '',
  pdf_url text not null default '',
  tags text[] not null default '{}',
  related_ids text[] not null default '{}',   -- curated "people also liked" links
  route text not null default '',              -- frontend path for the detail page
  requires_schedule boolean not null default false,  -- services need preferred date + time
  sort_order integer not null default 0,
  active boolean not null default true
);
alter table public.catalog_items enable row level security;

create table if not exists public.cart_items (
  id uuid primary key default gen_random_uuid(),
  user_id uuid not null references auth.users(id) on delete cascade,
  item_id text not null references public.catalog_items(id),
  preferred_date date,
  preferred_time text,
  created_at timestamptz not null default now()
);
-- A book can be in the cart once; a service once per (date, time) combination.
create unique index if not exists cart_items_unique_line
  on public.cart_items (user_id, item_id, coalesce(preferred_date, '1900-01-01'::date), coalesce(preferred_time, ''));
alter table public.cart_items enable row level security;

insert into public.catalog_items
  (id, type, title, subtitle, description, price_minor, currency, cover, pdf_url, tags, related_ids, route, requires_schedule, sort_order)
values
  ('book-1', 'book', 'The Numbers Say So', 'Idealistic Innovator', 'You were never meant to follow the room — you were born to lead it. Number 1 carries the frequency of the Chariot King: bold, original, endlessly first. This book decodes the natural authority you''ve always carried and shows you exactly how to wield it without apology. Your story starts at the top. Claim it.', 2775, 'SGD', 'https://res.cloudinary.com/dbb5nj0ht/image/upload/f_auto,q_auto,w_500,c_limit/v1781609548/site/books/book-1.jpg', 'https://res.cloudinary.com/dbb5nj0ht/raw/upload/v1789744427/site/books/pdf/book-1-sun.pdf', array['numerology','book','sun'], array['svc-numerology','book-8'], '/books/1', false, 1),
  ('book-2', 'book', 'The Numbers Say So', 'Pragmatic Negotiator', 'You feel what others miss — and that sensitivity has never been your weakness, it''s your gift. Number 2 carries the Queen''s frequency: intuitive, diplomatic, quietly magnetic. This book reveals how to turn your emotional intelligence into real influence, without losing the softness that makes it work. Learn to lead the people you already move.', 2775, 'SGD', 'https://res.cloudinary.com/dbb5nj0ht/image/upload/f_auto,q_auto,w_500,c_limit/v1781609583/site/books/book-2.jpg', 'https://res.cloudinary.com/dbb5nj0ht/raw/upload/v1789744456/site/books/pdf/book-2-moon.pdf', array['numerology','book','moon'], array['svc-numerology','book-6'], '/books/2', false, 2),
  ('book-3', 'book', 'The Numbers Say So', 'Realistic Taskmaster', 'Everyone comes to you for the answer — have you ever asked yourself why? Number 3 carries the Guru''s frequency: expansive, wise, magnetically persuasive. This book unlocks the exact blueprint behind your gift for counsel, connection, and growth. Your wisdom was never an accident. Read the pattern behind it, and use it fully.', 2775, 'SGD', 'https://res.cloudinary.com/dbb5nj0ht/image/upload/f_auto,q_auto,w_500,c_limit/v1781609605/site/books/book-3.jpg', 'https://res.cloudinary.com/dbb5nj0ht/raw/upload/v1789744464/site/books/pdf/book-3-jupiter.pdf', array['numerology','book','jupiter'], array['svc-numerology','book-7'], '/books/3', false, 3),
  ('book-4', 'book', 'The Numbers Say So', 'Idealistic Strategist', 'You see the system nobody else can see — the grid beneath the chaos everyone else calls noise. Number 4 carries the Architect''s frequency: visionary, meticulous, quietly revolutionary. This book maps the exact blueprint behind your out-of-the-box mind, so you stop building alone in the dark. See the whole design at last.', 2775, 'SGD', 'https://res.cloudinary.com/dbb5nj0ht/image/upload/f_auto,q_auto,w_500,c_limit/v1781609634/site/books/book-4.jpg', 'https://res.cloudinary.com/dbb5nj0ht/raw/upload/v1789744471/site/books/pdf/book-4-rahu.pdf', array['numerology','book','rahu'], array['svc-numerology','book-5'], '/books/4', false, 4),
  ('book-5', 'book', 'The Numbers Say So', 'Pragmatic Architect', 'You were built to move — mind racing, hands busy, three steps ahead before anyone else has started. Number 5 carries the Architect''s frequency: resourceful, adaptable, endlessly quick-witted. This book shows you how to turn that restless energy into real momentum instead of scattered effort. Your next chapter moves fast. Start it now.', 2775, 'SGD', 'https://res.cloudinary.com/dbb5nj0ht/image/upload/f_auto,q_auto,w_500,c_limit/v1781609667/site/books/book-5.jpg', 'https://res.cloudinary.com/dbb5nj0ht/raw/upload/v1789744479/site/books/pdf/book-5-mercury.pdf', array['numerology','book','mercury'], array['svc-numerology','book-4'], '/books/5', false, 5),
  ('book-6', 'book', 'The Numbers Say So', 'Realistic Protector', 'You are the one everyone leans on — but who steadies you when it''s your turn to fall? Number 6 carries the Protector''s frequency: nurturing, disciplined, quietly powerful. This book reveals how to guard your own light as fiercely as you''ve always guarded everyone else''s. You''ve spent a lifetime giving. This one is yours.', 2775, 'SGD', 'https://res.cloudinary.com/dbb5nj0ht/image/upload/f_auto,q_auto,w_500,c_limit/v1781609701/site/books/book-6.jpg', 'https://res.cloudinary.com/dbb5nj0ht/raw/upload/v1789744494/site/books/pdf/book-6-venus.pdf', array['numerology','book','venus'], array['svc-numerology','book-2'], '/books/6', false, 6),
  ('book-7', 'book', 'The Numbers Say So', 'Idealistic Spiritualist', 'Some truths can''t be taught — they have to be sensed, long before they can be explained. Number 7 carries the Seeker''s frequency: intuitive, private, deeply wise. This book decodes the instinct you''ve trusted your whole life and finally puts language to what you''ve always simply known. Your inner world just found its map.', 2775, 'SGD', 'https://res.cloudinary.com/dbb5nj0ht/image/upload/f_auto,q_auto,w_500,c_limit/v1781609711/site/books/book-7.jpg', 'https://res.cloudinary.com/dbb5nj0ht/raw/upload/v1789744502/site/books/pdf/book-7-ketu.pdf', array['numerology','book','ketu'], array['svc-numerology','book-3'], '/books/7', false, 7),
  ('book-8', 'book', 'The Numbers Say So', 'Pragmatic Commander', 'Nothing you''ve built came easy — and somewhere along the way, that became exactly the point. Number 8 carries the Commander''s frequency: disciplined, relentless, built for the long game. This book shows you how to finally rest on the foundation you''ve spent a lifetime laying stone by stone. Your empire deserves its blueprint.', 2775, 'SGD', 'https://res.cloudinary.com/dbb5nj0ht/image/upload/f_auto,q_auto,w_500,c_limit/v1781609721/site/books/book-8.jpg', 'https://res.cloudinary.com/dbb5nj0ht/raw/upload/v1789744509/site/books/pdf/book-8-saturn.pdf', array['numerology','book','saturn'], array['svc-numerology','book-1'], '/books/8', false, 8),
  ('book-9', 'book', 'The Numbers Say So', 'Realistic Creator', 'You were the one who stayed when everyone else stepped back from the front line. Number 9 carries the Creator''s frequency: fearless, strategic, built to finish what others only start. This book reveals the vision driving every decision you make before you even realize you''ve made it. Your story closes the circle. Own it.', 2775, 'SGD', 'https://res.cloudinary.com/dbb5nj0ht/image/upload/f_auto,q_auto,w_500,c_limit/v1781609730/site/books/book-9.jpg', 'https://res.cloudinary.com/dbb5nj0ht/raw/upload/v1789744516/site/books/pdf/book-9-mars.pdf', array['numerology','book','mars'], array['svc-numerology','book-1'], '/books/9', false, 9),
  ('svc-numerology', 'service', 'Numerology Session', 'One-to-one with Sharan Babaji',
   'A personal numerology reading that decodes your numbers and maps your next chapter.',
   9600, 'SGD', '', '', array['numerology','service'], array['book-1','svc-chakra-reading'], '/numerology', true, 20),
  ('svc-meditation-breathwork', 'service', 'Meditation & Yogic Breathwork Session', 'One-to-one with Sharan Babaji',
   'Guided meditation and yogic breathwork to restore calm, focus and energy.',
   9600, 'SGD', '', '', array['meditation','service'], array['svc-chakra-reading','svc-numerology'], '/meditation-breathwork', true, 21),
  ('svc-chakra-reading', 'service', 'Chakra Reading Session', 'One-to-one with Sharan Babaji',
   'A chakra reading to identify blockages and bring your energy centres into balance.',
   9600, 'SGD', '', '', array['chakra','service'], array['svc-meditation-breathwork','svc-numerology'], '/chakra-reading', true, 22),
  ('retreat-enquiry', 'retreat', 'Retreats with Sharan Babaji', 'Enquire for upcoming retreats',
   'Immersive retreats for deep inner work. Enquire to be told about the next dates.',
   null, 'SGD', '', '', array['retreat'], array[]::text[], '/retreats', false, 30)
on conflict (id) do update set
  title = excluded.title, subtitle = excluded.subtitle, description = excluded.description,
  price_minor = excluded.price_minor, cover = excluded.cover, pdf_url = excluded.pdf_url,
  tags = excluded.tags, related_ids = excluded.related_ids, route = excluded.route,
  requires_schedule = excluded.requires_schedule, sort_order = excluded.sort_order;
