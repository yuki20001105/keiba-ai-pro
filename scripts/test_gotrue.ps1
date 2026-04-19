$anonKey = "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJpc3MiOiJzdXBhYmFzZSIsInJlZiI6ImdyZndrdXRjc2F2cWljYWltc3NuIiwicm9sZSI6ImFub24iLCJpYXQiOjE3NjgwNDA5NTksImV4cCI6MjA4MzYxNjk1OX0.mRElu2h9Sngrry72BBpW01bMBzQfK8-bNt4GOUqda5o"
$baseUrl = "https://grfwkutcsavqicaimssn.supabase.co"

# 1. GoTrue の設定エンドポイントをテスト
Write-Host "=== GoTrue /auth/v1/settings ==="
try {
  $r = Invoke-RestMethod -Uri "$baseUrl/auth/v1/settings" -Headers @{ apikey = $anonKey }
  $r | ConvertTo-Json -Depth 3
} catch {
  Write-Host "Error: $($_.Exception.Message)"
  Write-Host $_.ErrorDetails.Message
}

# 2. 実際のログインテスト（パスワードは不明なのでダミーで試す）
Write-Host "`n=== GoTrue raw error (expect Invalid credentials) ==="
$body = '{"email":"yuki20001105@icloud.com","password":"test12345"}'
try {
  $r2 = Invoke-RestMethod -Method POST `
    -Uri "$baseUrl/auth/v1/token?grant_type=password" `
    -Headers @{ apikey = $anonKey; "Content-Type" = "application/json" } `
    -Body $body
  $r2 | ConvertTo-Json
} catch {
  $statusCode = $_.Exception.Response.StatusCode.value__
  $errBody = $_.ErrorDetails.Message
  Write-Host "Status: $statusCode"
  Write-Host "Body: $errBody"
}
