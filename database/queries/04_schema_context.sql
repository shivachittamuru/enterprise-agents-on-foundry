-- Column-level schema for every user table, with primary keys and foreign key
-- targets. This is the entire context the model is given about the database.
--
-- Read-only and single-statement, so it passes the same validator that agent
-- generated SQL passes. Nothing about this query is privileged.
SELECT
    s.name AS schema_name,
    t.name AS table_name,
    c.column_id AS column_position,
    c.name AS column_name,
    TYPE_NAME(c.user_type_id) AS data_type,
    c.is_nullable AS is_nullable,
    CASE WHEN pk.column_id IS NULL THEN 0 ELSE 1 END AS is_primary_key,
    CASE
        WHEN rt.name IS NULL THEN NULL
        ELSE rs.name + '.' + rt.name + '.' + rc.name
    END AS references_column
FROM sys.columns AS c
    JOIN sys.tables AS t ON t.object_id = c.object_id
    JOIN sys.schemas AS s ON s.schema_id = t.schema_id
    LEFT JOIN sys.indexes AS pki ON pki.object_id = t.object_id AND pki.is_primary_key = 1
    LEFT JOIN sys.index_columns AS pk
        ON pk.object_id = c.object_id
        AND pk.column_id = c.column_id
        AND pk.index_id = pki.index_id
    LEFT JOIN sys.foreign_key_columns AS fkc
        ON fkc.parent_object_id = c.object_id
        AND fkc.parent_column_id = c.column_id
    LEFT JOIN sys.columns AS rc
        ON rc.object_id = fkc.referenced_object_id
        AND rc.column_id = fkc.referenced_column_id
    LEFT JOIN sys.tables AS rt ON rt.object_id = fkc.referenced_object_id
    LEFT JOIN sys.schemas AS rs ON rs.schema_id = rt.schema_id
ORDER BY s.name, t.name, c.column_id
