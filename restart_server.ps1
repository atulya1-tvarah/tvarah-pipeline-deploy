# Kill all remaining port 8000 listeners
$pids = (Get-NetTCPConnection -LocalPort 8000 -State Listen -ErrorAction SilentlyContinue).OwningProcess | Sort-Object -Unique
foreach ($p in $pids) {
    Write-Host "Killing PID $p"
    Stop-Process -Id $p -Force -ErrorAction SilentlyContinue
}
Start-Sleep 3

# Verify port is free
$remaining = Get-NetTCPConnection -LocalPort 8000 -State Listen -ErrorAction SilentlyContinue
if ($remaining) {
    Write-Host "Still running on port 8000 - trying again"
    $remaining.OwningProcess | ForEach-Object { Stop-Process -Id $_ -Force -ErrorAction SilentlyContinue }
    Start-Sleep 2
}

# Start new server
Write-Host "Starting uvicorn..."
Start-Process -FilePath "E:\Dev\resume_intelligence\.venv\Scripts\uvicorn.exe" `
    -ArgumentList "app:app", "--reload", "--host", "0.0.0.0", "--port", "8000" `
    -WorkingDirectory "E:\Dev\resume_intelligence" `
    -WindowStyle Normal

Start-Sleep 4
$check = (Get-NetTCPConnection -LocalPort 8000 -State Listen -ErrorAction SilentlyContinue).OwningProcess
Write-Host "New server PID(s): $($check -join ', ')"
