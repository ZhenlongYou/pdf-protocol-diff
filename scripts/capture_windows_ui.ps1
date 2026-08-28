param(
    [Parameter(Mandatory = $true)][string]$Executable,
    [Parameter(Mandatory = $true)][string]$OutputPath,
    [string]$ProbePath = ""
)

$ErrorActionPreference = "Stop"
Add-Type -AssemblyName System.Drawing
Add-Type @"
using System;
using System.Runtime.InteropServices;
public static class NativeWindowCapture {
    [StructLayout(LayoutKind.Sequential)]
    public struct RECT { public int Left; public int Top; public int Right; public int Bottom; }
    [DllImport("user32.dll")]
    public static extern bool GetWindowRect(IntPtr hWnd, out RECT rect);
}
"@

$OutputPath = [System.IO.Path]::GetFullPath($OutputPath)
if (-not $ProbePath) { $ProbePath = [System.IO.Path]::ChangeExtension($OutputPath, ".json") }
$ProbePath = [System.IO.Path]::GetFullPath($ProbePath)
New-Item -ItemType Directory -Force -Path (Split-Path -Parent $OutputPath) | Out-Null
New-Item -ItemType Directory -Force -Path (Split-Path -Parent $ProbePath) | Out-Null
if (Test-Path $ProbePath) { Remove-Item -LiteralPath $ProbePath -Force }
$env:PROTOCOL_DIFF_RENDERER_PROBE_PATH = $ProbePath

$process = Start-Process -FilePath $Executable -PassThru
$forcedStop = $false
try {
    $deadline = (Get-Date).AddSeconds(30)
    do {
        Start-Sleep -Milliseconds 500
        $process.Refresh()
    } while (
        ($process.MainWindowHandle -eq 0 -or -not (Test-Path $ProbePath)) -and
        -not $process.HasExited -and
        (Get-Date) -lt $deadline
    )

    if ($process.HasExited) { throw "GUI exited before its window became visible." }
    if ($process.MainWindowHandle -eq 0) { throw "Timed out waiting for the WebView2 window." }
    if (-not (Test-Path $ProbePath)) { throw "Timed out waiting for the WebView2 renderer probe." }
    $probe = Get-Content -LiteralPath $ProbePath -Raw | ConvertFrom-Json
    if (-not $probe.webview2) { throw "Renderer probe did not confirm Edge WebView2." }
    if (-not $probe.overflowFree) { throw "Renderer probe found horizontal overflow." }
    if (-not $probe.columns -or $probe.columns -notmatch "\s") { throw "Renderer probe did not confirm the two-column layout." }
    if (-not $probe.backdrop -or $probe.backdrop -eq "none") { throw "Renderer probe did not confirm backdrop blur." }

    $rect = New-Object NativeWindowCapture+RECT
    if (-not [NativeWindowCapture]::GetWindowRect($process.MainWindowHandle, [ref]$rect)) {
        throw "Could not read the application window bounds."
    }
    $width = $rect.Right - $rect.Left
    $height = $rect.Bottom - $rect.Top
    if ($width -lt 760 -or $height -lt 520) { throw "Unexpected window size: ${width}x${height}." }

    $bitmap = New-Object System.Drawing.Bitmap($width, $height)
    $graphics = [System.Drawing.Graphics]::FromImage($bitmap)
    try {
        $graphics.CopyFromScreen($rect.Left, $rect.Top, 0, 0, $bitmap.Size)
        $bitmap.Save($OutputPath, [System.Drawing.Imaging.ImageFormat]::Png)
    } finally {
        $graphics.Dispose()
        $bitmap.Dispose()
    }
    if ((Get-Item $OutputPath).Length -lt 10000) { throw "Captured screenshot is unexpectedly small." }
} finally {
    if (-not $process.HasExited) {
        [void]$process.CloseMainWindow()
        if (-not $process.WaitForExit(10000)) {
            Stop-Process -Id $process.Id -Force
            $forcedStop = $true
        }
    }
}
if ($forcedStop) { throw "GUI did not close cleanly after evidence capture." }
if ($process.ExitCode -ne 0) { throw "GUI exited with code $($process.ExitCode)." }
