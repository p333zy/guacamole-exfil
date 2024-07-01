$End = "---END-838974a7-0088-42da-b3e0-de74d6b8d23d"
$Start = "---START-838974a7-0088-42da-b3e0-de74d6b8d23d"
$StartEnd = "---START_E-838974a7-0088-42da-b3e0-de74d6b8d23d"
$ChunkStart = "---CHUNK_S-838974a7-0088-42da-b3e0-de74d6b8d23d"
$ChunkEnd = "---CHUNK_E-838974a7-0088-42da-b3e0-de74d6b8d23d"
$ErrorStart = "---ERROR_S-838974a7-0088-42da-b3e0-de74d6b8d23d"
$ErrorEnd = "---ERROR_E-838974a7-0088-42da-b3e0-de74d6b8d23d"

$ChunkSize = 65536

function SendError {
  param (
    $Data
  )

  $Formatted="$ErrorStart`n$Data`n$ErrorEnd"
  Set-Clipboard -Value $Formatted
}

function Exfil {
  param (
    $Data,
    $Count
  )

  $Formatted="$ChunkStart-$Count`n$Data`n$ChunkEnd-$Count"
  Set-Clipboard -Value $Formatted
}

function Handshake {
  # Get file & chunk number
  $File = (Read-Host "Enter path").Trim()
  $ChunkStart = [int] (Read-Host "Enter chunk (0 for beginning)").Trim()

  if (-Not([System.IO.File]::Exists($File))) {
    SendError "File not found"
    throw "File not found: $File"
  }

  # Get sha256 checksum of file
  $Hash = (Get-FileHash -Algorithm SHA256 $File).Hash

  # Return checksum to client
  $Formatted = "$Start`n$Hash`n$StartEnd"
  Set-Clipboard -Value $Formatted

  return @{
    File = $File;
    ChunkStart = $ChunkStart;
    Hash = $Hash;
  }
}

$Info = Handshake
$Data = Get-Content $Info.File -Raw -Encoding Byte

for (($Offset=$Info.ChunkStart * $ChunkSize), ($i=$Info.ChunkStart); 
      $Offset -Lt $Data.Length; 
      ($Offset += $ChunkSize), ($i++)) {
  
  $Line = Read-Host "Enter for next"
  if ($Line -Eq "StopExtract") {
    Set-Clipboard -Value $End
    return
  }

  $Limit = $Offset + [math]::Min($ChunkSize, $Data.Length - $Offset) - 1
  $Chunk = [Convert]::ToBase64String($Data[$Offset..$Limit])
  Exfil -Data $Chunk -Count $i
}

Read-Host "End of data"
Set-Clipboard -Value $End
