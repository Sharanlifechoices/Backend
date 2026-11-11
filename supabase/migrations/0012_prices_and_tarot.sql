-- Final prices (SGD, minor units) and the Tarot course. Run before 0013 (the Tarot file).
update public.catalog_items set price_minor = 2500
  where id in ('course-ttc200', 'course-retreat10', 'course-ttc300', 'course-ttc500');
update public.catalog_items set price_minor = 9600 where id = 'course-tantra';
update public.catalog_items set price_minor = 1575
  where id in ('book-ayurveda', 'book-forex', 'book-vedic-gods', 'book-vedic-sages');

-- Tarot course: inserted inactive with no file; 0013 sets pdf_url and active = true after the upload.
insert into public.catalog_items
  (id, type, title, subtitle, description, price_minor, currency, cover, pdf_url, tags, related_ids, route, requires_schedule, sort_order, active)
values
  ('course-tarot', 'course', 'Tarot Learning Package', 'Vedic Tarot with NumberSaySo — Syllabus, Workbook and a free Question Techniques booklet',
   'A complete course in the 78 cards of the Rider-Waite tarot, joined with the Vedic wisdom of the nine planets and Sharan Ji Baba''s NumberSaySo method: the Tarot Course Syllabus, the Student Workbook and the free Tarot Question Techniques booklet.',
   2775, 'SGD', '/covers/course-tarot.jpg', '', array['course','tarot'], array['course-tantra','course-ttc200'], '/courses/tarot', false, 45, false)
on conflict (id) do update set
  type = excluded.type, title = excluded.title, subtitle = excluded.subtitle, description = excluded.description,
  cover = excluded.cover, tags = excluded.tags, related_ids = excluded.related_ids, route = excluded.route,
  requires_schedule = excluded.requires_schedule, sort_order = excluded.sort_order;
-- price_minor of the Tarot row is set on first insert only; pdf_url / active are owned by 0013.
