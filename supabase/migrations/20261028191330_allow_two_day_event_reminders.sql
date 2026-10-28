-- Apply separately in Supabase when ready. This never sends reminders and
-- does not backfill sent records. Preserve all previously allowed types.
begin;
set local lock_timeout = '3s';

do $$
declare
    existing_rule text;
begin
    select pg_get_expr(conbin, conrelid)
      into existing_rule
      from pg_constraint
     where conrelid = 'public.event_reminders'::regclass
       and conname = 'event_reminders_reminder_type_check'
       and contype = 'c';

    if existing_rule is null then
        raise exception 'Expected reminder_type check constraint was not found; inspect the table before applying this fix.';
    end if;

    alter table public.event_reminders
        drop constraint event_reminders_reminder_type_check;

    execute format(
        'alter table public.event_reminders add constraint event_reminders_reminder_type_check check ((%s) or reminder_type = %L)',
        existing_rule, '2d'
    );
end $$;

commit;
