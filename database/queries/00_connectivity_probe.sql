-- Smallest possible query that proves a working Entra-authenticated session.
-- Used to measure connection and first-query latency.
SELECT 1 AS connectivity_probe;
