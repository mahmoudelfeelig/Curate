[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)][string]$SourcePath,
    [string]$OutputDirectory = "public/assets/brand"
)

$ErrorActionPreference = "Stop"
Add-Type -AssemblyName System.Drawing

$source = (Resolve-Path -LiteralPath $SourcePath).Path
$projectRoot = (Resolve-Path -LiteralPath (Join-Path $PSScriptRoot "..")).Path
$output = [System.IO.Path]::GetFullPath((Join-Path $projectRoot $OutputDirectory))
if (-not $output.StartsWith($projectRoot, [System.StringComparison]::OrdinalIgnoreCase)) {
    throw "OutputDirectory must stay inside the Curate checkout"
}
New-Item -ItemType Directory -Path $output -Force | Out-Null

$image = [System.Drawing.Bitmap]::new($source)
try {
    $minX = $image.Width
    $minY = $image.Height
    $maxX = -1
    $maxY = -1
    for ($y = 0; $y -lt $image.Height; $y += 2) {
        for ($x = 0; $x -lt $image.Width; $x += 2) {
            $pixel = $image.GetPixel($x, $y)
            if ($pixel.R -lt 245 -or $pixel.G -lt 245 -or $pixel.B -lt 245) {
                $minX = [Math]::Min($minX, $x)
                $minY = [Math]::Min($minY, $y)
                $maxX = [Math]::Max($maxX, $x)
                $maxY = [Math]::Max($maxY, $y)
            }
        }
    }
    if ($maxX -lt 0 -or $maxY -lt 0) { throw "The source logo has no visible artwork" }
    $padding = 28
    $minX = [Math]::Max(0, $minX - $padding)
    $minY = [Math]::Max(0, $minY - $padding)
    $maxX = [Math]::Min($image.Width - 1, $maxX + $padding)
    $maxY = [Math]::Min($image.Height - 1, $maxY + $padding)
    $crop = [System.Drawing.Rectangle]::new($minX, $minY, $maxX - $minX + 1, $maxY - $minY + 1)

    function Write-CurateLogo([int]$Size, [string]$Name) {
        $target = [System.Drawing.Bitmap]::new($Size, $Size, [System.Drawing.Imaging.PixelFormat]::Format32bppArgb)
        try {
            $graphics = [System.Drawing.Graphics]::FromImage($target)
            try {
                $graphics.Clear([System.Drawing.Color]::Transparent)
                $graphics.CompositingQuality = [System.Drawing.Drawing2D.CompositingQuality]::HighQuality
                $graphics.InterpolationMode = [System.Drawing.Drawing2D.InterpolationMode]::HighQualityBicubic
                $graphics.SmoothingMode = [System.Drawing.Drawing2D.SmoothingMode]::HighQuality
                $scale = [Math]::Min(($Size * 0.88) / $crop.Width, ($Size * 0.88) / $crop.Height)
                $width = [int][Math]::Round($crop.Width * $scale)
                $height = [int][Math]::Round($crop.Height * $scale)
                $destination = [System.Drawing.Rectangle]::new([int](($Size - $width) / 2), [int](($Size - $height) / 2), $width, $height)
                $attributes = [System.Drawing.Imaging.ImageAttributes]::new()
                try {
                    $attributes.SetColorKey([System.Drawing.Color]::FromArgb(245, 245, 245), [System.Drawing.Color]::White)
                    $graphics.DrawImage($image, $destination, $crop.X, $crop.Y, $crop.Width, $crop.Height, [System.Drawing.GraphicsUnit]::Pixel, $attributes)
                }
                finally { $attributes.Dispose() }
            }
            finally { $graphics.Dispose() }
            $target.Save((Join-Path $output $Name), [System.Drawing.Imaging.ImageFormat]::Png)
        }
        finally { $target.Dispose() }
    }

    Write-CurateLogo 512 "curate-elephant.png"
    Write-CurateLogo 192 "curate-icon-192.png"
    Write-CurateLogo 64 "curate-favicon.png"
}
finally { $image.Dispose() }

Write-Host "Prepared Curate brand assets from the owner-supplied logo."
