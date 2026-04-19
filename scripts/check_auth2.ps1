$uri = "https://api.supabase.com/v1/projects/grfwkutcsavqicaimssn/database/query"
$h = @{ Authorization = "Bearer sbp_d9ce696678b5af6ff1da9b3fcb44578044a9fbcd"; "Content-Type" = "application/json" }

# auth.identities 確認
$body = '{"query":"SELECT user_id, provider, identity_data->>''email'' as email FROM auth.identities ORDER BY created_at DESC LIMIT 10;"}'
Write-Host "=== auth.identities ==="
(Invoke-RestMethod -Method POST -Uri $uri -Headers $h -Body $body) | Format-Table

# auth.users の instance_id と aud 確認
$body2 = '{"query":"SELECT id, email, aud, instance_id, email_confirmed_at FROM auth.users ORDER BY created_at DESC LIMIT 5;"}'
Write-Host "`n=== auth.users ==="
(Invoke-RestMethod -Method POST -Uri $uri -Headers $h -Body $body2) | Format-Table

# GoTrue restart
Write-Host "`n=== Restarting project ==="
try {
  $r = Invoke-RestMethod -Method POST -Uri "https://api.supabase.com/v1/projects/grfwkutcsavqicaimssn/restart" -Headers $h
  $r | ConvertTo-Json
} catch {
  Write-Host "Restart error: $_"
}
