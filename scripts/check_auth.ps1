$uri = "https://api.supabase.com/v1/projects/grfwkutcsavqicaimssn/database/query"
$h = @{ Authorization = "Bearer sbp_d9ce696678b5af6ff1da9b3fcb44578044a9fbcd"; "Content-Type" = "application/json" }

# auth テーブル一覧
$body = '{"query":"SELECT table_name FROM information_schema.tables WHERE table_schema=''auth'' ORDER BY table_name;"}'
Write-Host "=== AUTH TABLES ==="
$r = Invoke-RestMethod -Method POST -Uri $uri -Headers $h -Body $body
$r | Select-Object -ExpandProperty table_name

# auth.schema_migrations
$body2 = '{"query":"SELECT version FROM auth.schema_migrations ORDER BY version DESC LIMIT 15;"}'
Write-Host "`n=== SCHEMA MIGRATIONS (latest 15) ==="
$r2 = Invoke-RestMethod -Method POST -Uri $uri -Headers $h -Body $body2
$r2 | Select-Object -ExpandProperty version
