$uri = "https://api.supabase.com/v1/projects/grfwkutcsavqicaimssn/database/query"
$h = @{ Authorization = "Bearer sbp_d9ce696678b5af6ff1da9b3fcb44578044a9fbcd"; "Content-Type" = "application/json" }

# auth.users のインデックスが valid かチェック
Write-Host "=== auth.users indexes (valid check) ==="
$body = '{"query":"SELECT c.relname AS index_name, i.indisvalid, i.indisprimary FROM pg_index i JOIN pg_class c ON i.indexrelid=c.oid JOIN pg_class t ON i.indrelid=t.oid JOIN pg_namespace n ON t.relnamespace=n.oid WHERE n.nspname=''auth'' AND t.relname=''users'';"}'
(Invoke-RestMethod -Method POST -Uri $uri -Headers $h -Body $body) | Format-Table

# auth.users の実際のレコード数と最初の1件
Write-Host "`n=== auth.users direct query test ==="
$body2 = '{"query":"SELECT id, email, aud, instance_id FROM auth.users WHERE email=''yuki20001105@icloud.com'' AND aud=''authenticated'' LIMIT 1;"}'
(Invoke-RestMethod -Method POST -Uri $uri -Headers $h -Body $body2) | Format-Table

# email_confirmed_at チェック
Write-Host "`n=== email confirmation status ==="
$body3 = '{"query":"SELECT email, email_confirmed_at, confirmed_at FROM auth.users ORDER BY created_at;"}'
(Invoke-RestMethod -Method POST -Uri $uri -Headers $h -Body $body3) | Format-Table
