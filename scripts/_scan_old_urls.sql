DO $$
DECLARE
  r record;
  n bigint;
  total bigint := 0;
BEGIN
  FOR r IN
    SELECT table_name, column_name, data_type
    FROM information_schema.columns
    WHERE table_schema = 'public'
      AND data_type IN ('text', 'character varying', 'jsonb', 'json')
    ORDER BY table_name, column_name
  LOOP
    BEGIN
      EXECUTE format(
        'SELECT count(*) FROM %I WHERE %I::text LIKE %L',
        r.table_name, r.column_name, '%muzihhh.com/files/%'
      ) INTO n;
      IF n > 0 THEN
        RAISE NOTICE 'HIT %  %.% (%)', n, r.table_name, r.column_name, r.data_type;
        total := total + n;
      END IF;
    EXCEPTION WHEN others THEN
      NULL;
    END;
  END LOOP;
  RAISE NOTICE 'TOTAL_ROWS %', total;
END $$;
