$uri = "https://api.supabase.com/v1/projects/grfwkutcsavqicaimssn/database/query"
$h = @{ Authorization = "Bearer sbp_d9ce696678b5af6ff1da9b3fcb44578044a9fbcd"; "Content-Type" = "application/json" }

# oauth_clients の内容を確認
Write-Host "=== auth.oauth_clients ==="
$body = '{"query":"SELECT id, name, type, metadata FROM auth.oauth_clients ORDER BY created_at DESC LIMIT 10;"}'
(Invoke-RestMethod -Method POST -Uri $uri -Headers $h -Body $body) | Format-Table

# key_id テーブルがあれば確認
Write-Host "`n=== auth tables with 'key' ==="
$body2 = '{"query":"SELECT table_name FROM information_schema.tables WHERE table_schema=''auth'' AND table_name LIKE ''%key%'';"}'
(Invoke-RestMethod -Method POST -Uri $uri -Headers $h -Body $body2) | Select-Object -ExpandProperty table_name

# GoTrue の api_keys 的なテーブルを確認
Write-Host "`n=== all auth table columns summary ==="
$body3 = '{"query":"SELECT table_name, string_agg(column_name, '', '' ORDER BY ordinal_position) as cols FROM information_schema.columns WHERE table_schema=''auth'' GROUP BY table_name ORDER BY table_name;"}'
(Invoke-RestMethod -Method POST -Uri $uri -Headers $h -Body $body3) | Format-Table -AutoSize
