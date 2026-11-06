-- Courses (Maharishi Yoga Peeth book sets) and three more books.
-- Everything is inserted INACTIVE with an empty pdf_url: 0009_product_files.sql (written by
-- `python -m app.tools.upload_material`) sets each pdf_url and activates the item only once its
-- file is really uploaded, so nothing sellable ever has an empty download link.
-- Prices match the existing books (SGD 27.75).

alter table public.catalog_items drop constraint if exists catalog_items_type_check;
alter table public.catalog_items
  add constraint catalog_items_type_check check (type in ('book', 'course', 'service', 'retreat'));

insert into public.catalog_items
  (id, type, title, subtitle, description, price_minor, currency, cover, pdf_url, tags, related_ids, route, requires_schedule, sort_order, active)
values
  ('course-ttc200', 'course', 'RYS TTC200', 'Hatha & Ashtanga — Foundation · Discipline · Transformation',
   'The complete study set for the 200-hour yoga teacher training at Maharishi Yoga Peeth, Rishikesh: the full Hatha & Ashtanga syllabus, the student workbook and the teacher answer book, delivered as one download.',
   2775, 'SGD', '/covers/course-ttc200.jpg', '', array['course','yoga','ttc200'], array['course-ttc300','course-retreat10'], '/courses/ttc200', false, 40, false),
  ('course-retreat10', 'course', '10 Day Retreat', 'Yoga · Healing · Transformation — Immerse · Rebalance · Renew',
   'The study set for the 10-day immersive retreat at Maharishi Yoga Peeth: the retreat syllabus, the student workbook and the teacher guide, delivered as one download.',
   2775, 'SGD', '/covers/course-retreat10.jpg', '', array['course','yoga','retreat10'], array['course-ttc200','course-tantra'], '/courses/retreat-10-day', false, 41, false),
  ('course-ttc300', 'course', 'RYS TTC300', 'Advanced Yoga Teacher Training — Deepen · Integrate · Teach',
   'The complete study set for the advanced 300-hour yoga teacher training at Maharishi Yoga Peeth: the TTC 300 syllabus, the student workbook and the teacher answer book, delivered as one download.',
   2775, 'SGD', '/covers/course-ttc300.jpg', '', array['course','yoga','ttc300'], array['course-ttc200','course-ttc500'], '/courses/ttc300', false, 42, false),
  ('course-ttc500', 'course', 'RYS TTC500', 'Advanced Yoga Teacher Training — Refine · Express · Lead',
   'The complete study set for the 500-hour yoga teacher training at Maharishi Yoga Peeth: the TTC 500 syllabus, the student workbook and the teacher answer book, delivered as one download.',
   2775, 'SGD', '/covers/course-ttc500.jpg', '', array['course','yoga','ttc500'], array['course-ttc300','course-tantra'], '/courses/ttc500', false, 43, false),
  ('course-tantra', 'course', 'Tantra', 'Yoga · Healing · Higher Union — Awaken · Unite · Transcend',
   'The study set for the Tantra course at Maharishi Yoga Peeth: the Tantra syllabus, the student workbook and the teacher guide, delivered as one download.',
   2775, 'SGD', '/covers/course-tantra.jpg', '', array['course','yoga','tantra'], array['course-ttc500','course-retreat10'], '/courses/tantra', false, 44, false),

  ('book-ayurveda', 'book', 'The Old Fart''s Guide to Ayurveda', 'Baba Ji presents — with the Ayurveda Plants Guide',
   'Everything the sages taught Baba Ji about Ayurveda, kept simple and kept honest: the three doshas, the subtle body, your number from 1 to 9, a daily routine, sattvic recipes and seasonal living. Includes the companion Ayurveda Plants Guide, a garden tour of 36 plants the sages loved.',
   2775, 'SGD', '/covers/book-ayurveda.jpg', '', array['book','ayurveda'], array['book-vedic-gods','book-forex'], '/books/ayurveda', false, 10, false),
  ('book-forex', 'book', 'The Old Fart''s Guide to Savings & Forex Trading', 'Plain wisdom from the Dollar-Mark desk — with the Trade Ruler booklet',
   'Read a chart the old-school way, plan every trade before you place it, and find out what your number says about how you handle money. Includes the companion Trade Ruler booklet. For education only; this is not financial advice.',
   2775, 'SGD', '/covers/book-forex.jpg', '', array['book','forex'], array['book-ayurveda','book-vedic-gods'], '/books/forex', false, 11, false),
  ('book-vedic-gods', 'book', 'The Old Fart''s Guide to Vedic Gods', 'Meet the gods · Know the stories · Chant the mantras — with the Mantras companion',
   'A vivid, down-to-earth guide to the gods and goddesses of the Vedic tradition, from Ganesha and Shiva to Lakshmi, Hanuman and Krishna, with Navaratri, the Navagraha and the great epics. Includes the companion Old Fart''s Guide to Mantras.',
   2775, 'SGD', '/covers/book-vedic-gods.jpg', '', array['book','vedic'], array['book-ayurveda','book-forex'], '/books/vedic-gods', false, 12, false)
on conflict (id) do update set
  type = excluded.type, title = excluded.title, subtitle = excluded.subtitle, description = excluded.description,
  price_minor = excluded.price_minor, cover = excluded.cover,
  tags = excluded.tags, related_ids = excluded.related_ids, route = excluded.route,
  requires_schedule = excluded.requires_schedule, sort_order = excluded.sort_order;
-- Re-running this file never touches pdf_url / active, which 0009 owns.
