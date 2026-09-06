<#
.SYNOPSIS
    Print an edited shiur DOCX to a connected printer, preserving RTL layout (prints via Word).

.PARAMETER DocxPath
    Full path to the .docx file to print.

.PARAMETER PrinterName
    Optional. Exact printer name. If omitted, uses the system default printer,
    falling back to any online physical printer.

.NOTES
    Windows only, and requires Microsoft Word to be installed - printing goes
    through Word itself, which is what keeps the RTL layout correct.
    On macOS/Linux, convert to PDF first (see the skill's SKILL.md).

.EXAMPLE
    powershell -File print_docx.ps1 -DocxPath "C:\path\to\shiur.docx"
    powershell -File print_docx.ps1 -DocxPath "C:\path\to\shiur.docx" -PrinterName "My Printer"
#>
param(
    [Parameter(Mandatory = $true)]
    [string]$DocxPath,

    [string]$PrinterName = ''
)

if (-not (Test-Path -LiteralPath $DocxPath)) {
    Write-Output "ERROR: file not found: $DocxPath"
    exit 1
}

# Resolve printer. The default printer is the right first guess on any machine;
# only if there is none do we look for some other online, physical printer.
if ([string]::IsNullOrWhiteSpace($PrinterName)) {
    $def = Get-CimInstance -ClassName Win32_Printer |
        Where-Object { $_.Default -eq $true } | Select-Object -First 1
    if ($null -ne $def) {
        $PrinterName = $def.Name
    } else {
        $any = Get-Printer | Where-Object {
            $_.PrinterStatus -ne 'Offline' -and
            $_.Name -notlike '*Fax*' -and $_.Name -notlike '*OneNote*' -and
            $_.Name -notlike '*PDF*'  -and $_.Name -notlike '*XPS*'
        } | Select-Object -First 1
        if ($null -ne $any) { $PrinterName = $any.Name }
    }
}

if ([string]::IsNullOrWhiteSpace($PrinterName)) {
    Write-Output "ERROR: no printer found. Pass -PrinterName explicitly."
    exit 1
}

Write-Output "Printer: $PrinterName"

$word = New-Object -ComObject Word.Application
$word.Visible = $false
try {
    # Open read-only; ConfirmConversions=$false, ReadOnly=$true
    $doc = $word.Documents.Open($DocxPath, $false, $true)
    $word.ActivePrinter = $PrinterName
    $doc.PrintOut()
    Start-Sleep -Seconds 8
    $doc.Close($false)
    Write-Output "PRINTED: $DocxPath -> $PrinterName"
} catch {
    Write-Output "ERROR: $($_.Exception.Message)"
    exit 1
} finally {
    $word.Quit()
    [System.Runtime.Interopservices.Marshal]::ReleaseComObject($word) | Out-Null
}

# Report queue + status
try {
    Get-PrintJob -PrinterName $PrinterName | Select-Object Id, DocumentName, JobStatus, TotalPages | Format-Table -AutoSize
    "Printer status: " + (Get-Printer -Name $PrinterName).PrinterStatus
} catch {
    Write-Output "(could not read print queue: $($_.Exception.Message))"
}
