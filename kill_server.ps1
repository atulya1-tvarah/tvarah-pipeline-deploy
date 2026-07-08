$procs = Get-WmiObject Win32_Process | Where-Object { $_.CommandLine -like '*uvicorn*' -or ($_.CommandLine -like '*python*' -and $_.CommandLine -like '*app:app*') }
foreach ($p in $procs) {
    Write-Host "Killing PID $($p.ProcessId): $($p.CommandLine)"
    Stop-Process -Id $p.ProcessId -Force -ErrorAction SilentlyContinue
}
Start-Sleep 2
$remaining = Get-NetTCPConnection -LocalPort 8000 -State Listen -ErrorAction SilentlyContinue
if ($remaining) {
    Write-Host "Still listening: $($remaining.OwningProcess -join ', ')"
} else {
    Write-Host "Port 8000 is now free"
}
