-- Schemas present in the database, with object counts.
-- Read-only. Safe to run against any environment.
SELECT
    s.name                                AS schema_name,
    COUNT(t.object_id)                    AS table_count
FROM sys.schemas AS s
LEFT JOIN sys.tables AS t
    ON t.schema_id = s.schema_id
WHERE s.name NOT IN ('sys', 'INFORMATION_SCHEMA', 'guest', 'db_owner',
                     'db_accessadmin', 'db_securityadmin', 'db_ddladmin',
                     'db_backupoperator', 'db_datareader', 'db_datawriter',
                     'db_denydatareader', 'db_denydatawriter')
GROUP BY s.name
ORDER BY table_count DESC, schema_name;
