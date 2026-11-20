-- The web service's own role (2026-11-20, the security review with the app in
-- closed testing).
--
-- Run by hand, once, against the Supabase project, after 0016:
--
--     .venv/bin/python scripts/apply_migration.py migrations/0017_api_role.sql
--     .venv/bin/python scripts/api_role.py          # its password, and its URL
--
-- Until now the service on Render reached its rows as `postgres`, which may
-- create roles, bypasses row level security, and owns every table: a service
-- broken into could have dropped the curated catalog, which the Free plan
-- keeps no backup of. `helixpeek_api` may do what `app/` does and no more:
--
-- - read the tables its routes read;
-- - queue an ask, stop it, and forget a discarded gene's requests
--   (`resolve_request`);
-- - count an ask or a resume in the ledger, and nothing else there: the paid
--   calls' rows are the resolver's, and the role can neither change nor
--   delete any row of it (`spend_ledger`);
-- - queue, stop and keep a protein's ESM-2 scoring, and no other bake
--   (`bake_job`, `protein_track`, kind `constraint` alone);
-- - ask the resolver to delete a discarded protein's objects
--   (`discard_request`);
-- - delete a protein built on demand, and never a curated one: the policy
--   holds `catalog_order is null`, whatever a statement says. Its aliases,
--   tracks, bakes and folds go by the foreign keys' cascade, which runs as
--   the tables' owner.
--
-- It does not bypass row level security: every table keeps it on, and the
-- policies below are the role's alone (`to helixpeek_api`), so nothing changes
-- for anyone else. A role made with no password cannot log in;
-- `scripts/api_role.py` sets one, as a SCRAM verifier made on the machine that
-- runs it, and writes the URL Render then takes as DATABASE_URL. The resolver
-- and the scripts keep `postgres`.
--
-- A table a later route reads needs its grant and its policy here too, in
-- that migration: `pipeline/resolver/test_worker_pg.py` runs the service's
-- SQL as this role, so one that is missing fails there.
--
-- Roles belong to the whole server, not to one database: on a server with
-- several (the opt-in SQL tests make one a module), the role is made once.

begin;

do $$
begin
    if not exists (select 1 from pg_roles where rolname = 'helixpeek_api') then
        create role helixpeek_api login;
    end if;
end
$$;

alter role helixpeek_api set statement_timeout = '10s';

grant usage on schema public to helixpeek_api;

-- What the routes read.
grant select on protein, protein_alias, protein_track, protein_index, protein_index_term,
    protein_index_release, bake_job, resolve_request, spend_ledger, lm_run
    to helixpeek_api;

-- What an ask, a stop, a keep and a discard write.
grant insert, update, delete on resolve_request to helixpeek_api;
grant insert on spend_ledger, discard_request to helixpeek_api;
grant insert, update on bake_job to helixpeek_api;
grant update on protein_track to helixpeek_api;
grant delete on protein to helixpeek_api;
-- A discard locks the protein it deletes (`select ... for update`), which
-- Postgres allows only a role that may update a column of it. The policy
-- `api_locks` lets no update through.
grant update (slug) on protein to helixpeek_api;
grant usage on sequence resolve_request_id_seq, spend_ledger_id_seq, bake_job_id_seq,
    discard_request_id_seq to helixpeek_api;

-- Reads: every row of what the routes read.
create policy api_reads on protein for select to helixpeek_api using (true);
create policy api_reads on protein_alias for select to helixpeek_api using (true);
create policy api_reads on protein_track for select to helixpeek_api using (true);
create policy api_reads on protein_index for select to helixpeek_api using (true);
create policy api_reads on protein_index_term for select to helixpeek_api using (true);
create policy api_reads on protein_index_release for select to helixpeek_api using (true);
create policy api_reads on bake_job for select to helixpeek_api using (true);
create policy api_reads on resolve_request for select to helixpeek_api using (true);
create policy api_reads on spend_ledger for select to helixpeek_api using (true);
create policy api_reads on lm_run for select to helixpeek_api using (true);

-- Asks: queued, stopped, forgotten.
create policy api_asks on resolve_request for insert to helixpeek_api with check (true);
create policy api_stops on resolve_request for update to helixpeek_api
    using (true) with check (true);
create policy api_forgets on resolve_request for delete to helixpeek_api using (true);

-- The ledger: an ask or a resume, and nothing a paid call writes.
create policy api_counts on spend_ledger for insert to helixpeek_api
    with check (kind in ('ask', 'resume') and usd is null and finished_at is null);

-- A protein's ESM-2 scoring: queued again, stopped, kept.
create policy api_queues_scoring on bake_job for insert to helixpeek_api
    with check (kind = 'constraint');
create policy api_stops_scoring on bake_job for update to helixpeek_api
    using (kind = 'constraint') with check (kind = 'constraint');
create policy api_scores on protein_track for update to helixpeek_api
    using (kind = 'constraint') with check (kind = 'constraint');

-- A discard: the resolver's note, and the protein built on demand.
create policy api_discards on discard_request for insert to helixpeek_api with check (true);
create policy api_discards on protein for delete to helixpeek_api
    using (catalog_order is null);
create policy api_locks on protein for update to helixpeek_api
    using (catalog_order is null) with check (false);

commit;
