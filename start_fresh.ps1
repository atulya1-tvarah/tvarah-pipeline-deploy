# Kill ALL Python processes to clear everything
Write-Host "Killing all Python processes..."
Get-Process -Name "python","python3","python3.12" -ErrorAction SilentlyContinue | Stop-Process -Force
Get-Process -Name "uvicorn" -ErrorAction SilentlyContinue | Stop-Process -Force
Start-Sleep 3

# Verify port 8000 is free
$check = Get-NetTCPConnection -LocalPort 8000 -State Listen -ErrorAction SilentlyContinue
if ($check) {
    Write-Host "Still listening on 8000 - force killing $($check.OwningProcess -join ',')"
    $check.OwningProcess | ForEach-Object { Stop-Process -Id $_ -Force -ErrorAction SilentlyContinue }
    Start-Sleep 2
} else {
    Write-Host "Port 8000 is free"
}

# Start fresh
Write-Host "Starting fresh server..."
$proc = Start-Process -FilePath "E:\Dev\resume_intelligence\.venv\Scripts\python.exe" `
    -ArgumentList "-m", "uvicorn", "app:app", "--reload", "--host", "0.0.0.0", "--port", "8000" `
    -WorkingDirectory "E:\Dev\resume_intelligence" `
    -PassThru -WindowStyle Normal

Write-Host "Started PID: $($proc.Id)"
Start-Sleep 5
Write-Host "Done. Server should be running."
