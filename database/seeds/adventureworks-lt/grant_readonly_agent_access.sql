-- Read-only role and user for the future Text-to-SQL agent.
--
-- Run against the AdventureWorksLT database as the Microsoft Entra
-- administrator. Rerunnable: every statement checks for existing state first.
--
-- The agent identity receives db_datareader and nothing else. No write role, no
-- DDL, no EXECUTE. This is the durable control that makes the SQL validator in
-- src/enterprise_agents_on_foundry/setup/database.py a convenience rather than
-- the only line of defence.
--
-- Replace {{AGENT_IDENTITY_NAME}} with the managed identity display name before
-- running. The bootstrap script performs that substitution.

IF NOT EXISTS (SELECT 1 FROM sys.database_principals WHERE name = '{{AGENT_IDENTITY_NAME}}')
BEGIN
    CREATE USER [{{AGENT_IDENTITY_NAME}}] FROM EXTERNAL PROVIDER;
END;

IF NOT EXISTS (
    SELECT 1
    FROM sys.database_role_members AS drm
    JOIN sys.database_principals AS r ON r.principal_id = drm.role_principal_id
    JOIN sys.database_principals AS m ON m.principal_id = drm.member_principal_id
    WHERE r.name = 'db_datareader' AND m.name = '{{AGENT_IDENTITY_NAME}}'
)
BEGIN
    ALTER ROLE db_datareader ADD MEMBER [{{AGENT_IDENTITY_NAME}}];
END;

-- Explicitly deny the write roles in case a future change adds them by mistake.
DENY INSERT, UPDATE, DELETE, ALTER, EXECUTE TO [{{AGENT_IDENTITY_NAME}}];
