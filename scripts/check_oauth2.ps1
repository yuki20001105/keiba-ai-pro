$uri = "https://api.supabase.com/v1/projects/grfwkutcsavqicaimssn/database/query"
$h = @{ Authorization = "Bearer sbp_d9ce696678b5af6ff1da9b3fcb44578044a9fbcd"; "Content-Type" = "application/json" }

# oauth_clients の全カラム確認
Write-Host "=== auth.oauth_clients (all columns) ==="
$body = '{"query":"SELECT * FROM auth.oauth_clients LIMIT 5;"}'
(Invoke-RestMethod -Method POST -Uri $uri -Headers $h -Body $body) | ConvertTo-Json -Depth 3

# custom_oauth_providers の確認
Write-Host "`n=== auth.custom_oauth_providers ==="
$body2 = '{"query":"SELECT * FROM auth.custom_oauth_providers LIMIT 5;"}'
(Invoke-RestMethod -Method POST -Uri $uri -Headers $h -Body $body2) | ConvertTo-Json -Depth 3

# GoTrue schema_migrations の最新5件 (バージョン詳細)
Write-Host "`n=== schema_migrations latest 5 ==="
$body3 = '{"query":"SELECT version FROM auth.schema_migrations ORDER BY version DESC LIMIT 5;"}'
(Invoke-RestMethod -Method POST -Uri $uri -Headers $h -Body $body3) | Select-Object -ExpandProperty version
