param(
    [int]$ScreenIndex = 1,
    [double]$WidthFraction = 0.48,
    [double]$HeightFraction = 0.62,
    [int]$MinWidth = 980,
    [int]$MinHeight = 680
)

try {
    Add-Type -AssemblyName System.Windows.Forms -ErrorAction Stop

    $csharp = @"
using System;
using System.Runtime.InteropServices;

public static class WinApi
{
    [DllImport("kernel32.dll")]
    public static extern IntPtr GetConsoleWindow();

    [DllImport("user32.dll")]
    public static extern bool MoveWindow(IntPtr hWnd, int X, int Y, int nWidth, int nHeight, bool bRepaint);
}
"@
    Add-Type -TypeDefinition $csharp -Language CSharp -ErrorAction Stop | Out-Null

    $screens = [System.Windows.Forms.Screen]::AllScreens
    if ($screens.Count -le $ScreenIndex) {
        return
    }

    $bounds = $screens[$ScreenIndex].WorkingArea
    $w = [Math]::Max($MinWidth, [int]($bounds.Width * $WidthFraction))
    $h = [Math]::Max($MinHeight, [int]($bounds.Height * $HeightFraction))
    $hwnd = [WinApi]::GetConsoleWindow()
    if ($hwnd -eq [IntPtr]::Zero) {
        return
    }

    [void][WinApi]::MoveWindow($hwnd, $bounds.X, $bounds.Y, $w, $h, $true)
} catch {
    # Keep launcher resilient; placement is best-effort only.
}
