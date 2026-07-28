-- Tables with approximate row counts, taken from partition metadata rather than
-- COUNT(*) so the query stays cheap on a serverless database.
-- Read-only. Safe to run against any environment.
SELECT
    s.name                                AS schema_name,
    t.name                                AS table_name,
    SUM(p.rows)                           AS approximate_rows
FROM sys.tables AS t
JOIN sys.schemas AS s
    ON s.schema_id = t.schema_id
JOIN sys.partitions AS p
    ON p.object_id = t.object_id
   AND p.index_id IN (0, 1)
GROUP BY s.name, t.name
ORDER BY approximate_rows DESC, schema_name, table_name;
