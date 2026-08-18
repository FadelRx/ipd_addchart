# ตั้งให้ IPDaddChart เปิดเองทุกครั้งที่ล็อกอินเข้าเครื่อง — ผู้ใช้ไม่ต้องกด run.bat ทุกวัน
#
# ทำอะไร:
#   1. สร้าง Scheduled Task ชื่อ IPDaddChart ที่รันตอนล็อกอิน ด้วยสิทธิ์สูงสุด
#      (สิทธิ์สูงสุด = ไม่มีหน้าต่าง UAC เด้งทุกวัน ซึ่งเป็นเหตุผลหลักที่ต้องใช้ Scheduled Task
#       แทนการวางช็อตคัตใน Startup ธรรมดา)
#   2. สร้างช็อตคัตบนเดสก์ท็อป "IPDaddChart" ที่เปิดหน้าเว็บให้เลย
#
# ไม่ทำอะไร (ตั้งใจ):
#   - ไม่สั่งให้คีย์ยาอัตโนมัติ แค่เปิดโปรแกรมไว้เฉย ๆ การกด "เริ่มคีย์" ยังต้องให้คนกดเองเสมอ
#   - ไม่แตะไฟร์วอลล์ เพราะโปรแกรมผูกกับ 127.0.0.1 เครื่องตัวเองเท่านั้น
#
# ถอนออก:  Unregister-ScheduledTask -TaskName IPDaddChart -Confirm:$false
param(
    [string]$TaskName = "IPDaddChart"
)
$ErrorActionPreference = "Stop"

function Fail([string]$m) {
    Write-Host ""
    Write-Host "[ไม่สำเร็จ] $m" -ForegroundColor Red
    Read-Host "กด Enter เพื่อปิด"
    exit 1
}

$id = [Security.Principal.WindowsIdentity]::GetCurrent()
if (-not (New-Object Security.Principal.WindowsPrincipal($id)).IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) {
    Fail "ต้องรันด้วยสิทธิ์แอดมิน — ปิดหน้าต่างนี้แล้วดับเบิลคลิก setup_autostart.bat แทน"
}

$root = Split-Path -Parent $MyInvocation.MyCommand.Path
$pyw  = Join-Path $root "venv\Scripts\pythonw.exe"
$py   = Join-Path $root "venv\Scripts\python.exe"
if (-not (Test-Path $pyw)) { if (Test-Path $py) { $pyw = $py } else { Fail "ไม่พบ venv — ดับเบิลคลิก setup.bat ก่อนหนึ่งครั้ง" } }

# อ่านพอร์ตจากไฟล์ตั้งค่า เผื่อเครื่องนี้เปลี่ยนพอร์ตไว้
$port = 8770
foreach ($f in @("config\settings.json", "config\default_settings.json")) {
    $p = Join-Path $root $f
    if (Test-Path $p) {
        try {
            $j = Get-Content $p -Raw -Encoding UTF8 | ConvertFrom-Json
            if ($j.server -and $j.server.port) { $port = [int]$j.server.port; break }
        } catch { }
    }
}

$action  = New-ScheduledTaskAction -Execute $pyw `
    -Argument "-m uvicorn app.main:app --host 127.0.0.1 --port $port" -WorkingDirectory $root
$trigger = New-ScheduledTaskTrigger -AtLogOn -User $id.Name
$princ   = New-ScheduledTaskPrincipal -UserId $id.Name -LogonType InteractiveToken -RunLevel Highest
$set     = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries `
    -StartWhenAvailable -ExecutionTimeLimit ([TimeSpan]::Zero) -RestartCount 3 -RestartInterval (New-TimeSpan -Minutes 1)

if (Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue) {
    Unregister-ScheduledTask -TaskName $TaskName -Confirm:$false
    Write-Host "ลบงานเดิมที่ชื่อ $TaskName ออกก่อน" -ForegroundColor Yellow
}
Register-ScheduledTask -TaskName $TaskName -Action $action -Trigger $trigger -Principal $princ `
    -Settings $set -Description "เปิด IPDaddChart ไว้ให้พร้อมใช้งานทุกครั้งที่ล็อกอิน" | Out-Null
Write-Host "ตั้งให้เปิดเองตอนล็อกอินแล้ว (Scheduled Task: $TaskName)" -ForegroundColor Green

# ช็อตคัตบนเดสก์ท็อปไว้เปิดหน้าเว็บ
try {
    $desk = [Environment]::GetFolderPath("Desktop")
    $lnk = Join-Path $desk "IPDaddChart.url"
    "[InternetShortcut]`r`nURL=http://127.0.0.1:$port`r`n" | Set-Content -Path $lnk -Encoding ASCII
    Write-Host "สร้างช็อตคัตบนเดสก์ท็อปแล้ว: IPDaddChart" -ForegroundColor Green
} catch {
    Write-Host "[เตือน] สร้างช็อตคัตไม่สำเร็จ: $($_.Exception.Message)" -ForegroundColor Yellow
}

Write-Host ""
Write-Host "เริ่มงานเลยตอนนี้เพื่อทดสอบ..." -ForegroundColor Cyan
Start-ScheduledTask -TaskName $TaskName
Start-Sleep -Seconds 5
try {
    $r = Invoke-WebRequest -Uri "http://127.0.0.1:$port/api/status" -UseBasicParsing -TimeoutSec 10
    Write-Host "เปิดสำเร็จ — เข้าใช้งานได้ที่ http://127.0.0.1:$port" -ForegroundColor Green
} catch {
    Write-Host "[เตือน] ยังต่อไม่ได้: $($_.Exception.Message)" -ForegroundColor Yellow
    Write-Host "        ลองล็อกเอาต์แล้วล็อกอินใหม่ หรือเปิด Task Scheduler ดูงานชื่อ $TaskName"
}
Write-Host ""
Write-Host "ต่อไปนี้: เปิดเครื่อง -> ล็อกอิน -> โปรแกรมพร้อมใช้เอง กดช็อตคัตบนเดสก์ท็อปได้เลย" -ForegroundColor Cyan
Write-Host "ถอนออกเมื่อไหร่ก็ได้ด้วยคำสั่ง: Unregister-ScheduledTask -TaskName $TaskName -Confirm:`$false"
Write-Host ""
Read-Host "กด Enter เพื่อปิด"
