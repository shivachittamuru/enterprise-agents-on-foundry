-- Representative AdventureWorksLT join, aggregate, group, and order.
-- Establishes that the sample data is present and queryable, and provides the
-- simple-query latency baseline for the v0.1 measurements.
-- Read-only.
SELECT TOP (10)
    pc.Name                               AS category_name,
    COUNT(DISTINCT p.ProductID)           AS product_count,
    CAST(AVG(p.ListPrice) AS DECIMAL(10, 2)) AS average_list_price
FROM SalesLT.Product AS p
JOIN SalesLT.ProductCategory AS pc
    ON pc.ProductCategoryID = p.ProductCategoryID
WHERE p.ListPrice > 0
GROUP BY pc.Name
ORDER BY product_count DESC;
