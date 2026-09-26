-- One-time Supabase hardening, run as the `postgres` role.
--
--   Supabase SQL editor (it connects as `postgres`), or:
--   psql -v ON_ERROR_STOP=1 -f scripts/harden_supabase.sql \
--        "postgresql://postgres.<ref>:<password>@aws-0-<region>.pooler.supabase.com:5432/postgres"
--
-- ON_ERROR_STOP matters: without it psql exits 0 even when the verification below raises and
-- the whole block rolls back, so anything wrapping this would read a failure as a success.
--
-- NOT an Alembic migration, deliberately. A Supabase project ships an `ensure_rls` event
-- trigger backed by `public.rls_auto_enable()`, a SECURITY DEFINER function owned by
-- `postgres`. Postgres grants EXECUTE on it to PUBLIC and Supabase grants it to anon,
-- authenticated and service_role, which publishes it at /rest/v1/rpc/rls_auto_enable.
-- This application connects - and migrates - as the least-privileged `eve_app`, which owns
-- its own tables and nothing else, so it cannot revoke grants on a function it does not own.
-- Putting this in the migration chain would only guarantee `alembic upgrade head` fails.
--
-- Calling the function achieves nothing even today: a function returning `event_trigger`
-- cannot be invoked from SQL, and `pg_event_trigger_ddl_commands()` raises outside trigger
-- context. This closes a needless SECURITY DEFINER entry point in the exposed schema, and
-- clears the only WARN in the project's security advisories.
--
-- Idempotent: safe to run more than once. Leaves the `ensure_rls` trigger itself in place,
-- so newly created tables still get RLS enabled automatically.

DO $$
DECLARE
    fn        regprocedure;
    fn_owner  name;
    grantee   text;
    survivors text;
BEGIN
    SELECT p.oid::regprocedure, pg_get_userbyid(p.proowner)
      INTO fn, fn_owner
      FROM pg_proc p
      JOIN pg_namespace n ON n.oid = p.pronamespace
     WHERE n.nspname = 'public' AND p.proname = 'rls_auto_enable' AND p.pronargs = 0;

    IF fn IS NULL THEN
        RAISE NOTICE 'public.rls_auto_enable() does not exist here; nothing to do';
        RETURN;
    END IF;

    IF NOT pg_has_role(current_user, fn_owner, 'USAGE') THEN
        RAISE EXCEPTION
            'cannot revoke grants on %: owned by %, connected as % - run this as %',
            fn, fn_owner, current_user, fn_owner;
    END IF;

    EXECUTE format('REVOKE EXECUTE ON FUNCTION %s FROM PUBLIC', fn);

    FOREACH grantee IN ARRAY ARRAY['anon', 'authenticated', 'service_role'] LOOP
        IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = grantee) THEN
            EXECUTE format('REVOKE EXECUTE ON FUNCTION %s FROM %I', fn, grantee);
        END IF;
    END LOOP;

    -- Re-read the ACL rather than trust the statements above.
    SELECT CASE
               -- A null ACL means default privileges, which for a function include EXECUTE
               -- to PUBLIC, and aclexplode() is STRICT so it would report nothing at all.
               -- The REVOKE above always materialises the ACL, so this cannot be reached
               -- today; the check should not silently depend on that ordering.
               WHEN p.proacl IS NULL THEN 'PUBLIC (default privileges - ACL was reset)'
               ELSE (
                   SELECT string_agg(
                              CASE WHEN a.grantee = 0
                                   THEN 'PUBLIC' ELSE pg_get_userbyid(a.grantee) END, ', ')
                     FROM aclexplode(p.proacl) a
                    WHERE a.privilege_type = 'EXECUTE'
                      AND (a.grantee = 0 OR pg_get_userbyid(a.grantee) <> fn_owner)
               )
           END
      INTO survivors
      FROM pg_proc p
     WHERE p.oid = fn;

    IF survivors IS NOT NULL THEN
        RAISE EXCEPTION 'EXECUTE on % is still granted to: %', fn, survivors;
    END IF;

    RAISE NOTICE 'EXECUTE on % is now restricted to %', fn, fn_owner;
END
$$;
