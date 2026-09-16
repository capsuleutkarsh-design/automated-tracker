# ==============================================================================
# Automated Photogrammetry Tracker — Native WPF Modern Dark GUI
# 100% Portable & Relative Path Based — No Dependencies Required
# ==============================================================================

Add-Type -AssemblyName PresentationFramework
Add-Type -AssemblyName PresentationCore
Add-Type -AssemblyName WindowsBase
Add-Type -AssemblyName System.Drawing
Add-Type -AssemblyName System.Windows.Forms

# Resolve Root Directories dynamically relative to this script location
$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$BaseDir   = Split-Path -Parent $ScriptDir
$ColmapDir = Join-Path $BaseDir "01 COLMAP"
$VideosDir = Join-Path $BaseDir "02 VIDEOS"
$FfmpegDir = Join-Path $BaseDir "03 FFMPEG"
$ScenesDir = Join-Path $BaseDir "04 SCENES"

# Resolve Executables
$ColmapExe = if (Test-Path "$ColmapDir\bin\colmap.exe") { "$ColmapDir\bin\colmap.exe" } else { "$ColmapDir\colmap.exe" }
$FfmpegExe = if (Test-Path "$FfmpegDir\bin\ffmpeg.exe") { "$FfmpegDir\bin\ffmpeg.exe" } else { "$FfmpegDir\ffmpeg.exe" }
$ColmapBat = Join-Path $ColmapDir "COLMAP.bat"

# Set environment PATH
$env:PATH = "$ColmapDir\bin;$FfmpegDir\bin;$ColmapDir;$FfmpegDir;" + $env:PATH
$env:QT_PLUGIN_PATH = "$ColmapDir\plugins;" + $env:QT_PLUGIN_PATH

[xml]$xaml = @"
<Window xmlns="http://schemas.microsoft.com/winfx/2006/xaml/presentation"
        xmlns:x="http://schemas.microsoft.com/winfx/2006/xaml"
        Title="Automated_Tracker_V001.1 — VFX Camera Tracking"
        Height="780" Width="1120" MinHeight="620" MinWidth="900"
        Background="#14151A" WindowStartupLocation="CenterScreen">
    
    <Window.Resources>
        <Style TargetType="TextBlock">
            <Setter Property="FontFamily" Value="Segoe UI, -apple-system, Arial"/>
            <Setter Property="Foreground" Value="#D1D5DB"/>
            <Setter Property="FontSize" Value="12"/>
        </Style>
        <Style TargetType="GroupBox">
            <Setter Property="Foreground" Value="#9CA3AF"/>
            <Setter Property="BorderBrush" Value="#282A36"/>
            <Setter Property="BorderThickness" Value="1"/>
            <Setter Property="FontWeight" Value="Bold"/>
            <Setter Property="Margin" Value="0,0,0,10"/>
            <Setter Property="Padding" Value="10"/>
            <Setter Property="Background" Value="#181A21"/>
        </Style>
        <Style TargetType="Button">
            <Setter Property="Background" Value="#21232D"/>
            <Setter Property="Foreground" Value="#F3F4F6"/>
            <Setter Property="BorderBrush" Value="#2F3240"/>
            <Setter Property="BorderThickness" Value="1"/>
            <Setter Property="Padding" Value="10,5"/>
            <Setter Property="FontWeight" Value="SemiBold"/>
            <Setter Property="Cursor" Value="Hand"/>
        </Style>
        <Style TargetType="ComboBox">
            <Setter Property="Background" Value="#16171D"/>
            <Setter Property="Foreground" Value="#FFFFFF"/>
            <Setter Property="BorderBrush" Value="#2C2F3C"/>
            <Setter Property="Padding" Value="6,4"/>
        </Style>
        <Style TargetType="TextBox">
            <Setter Property="Background" Value="#16171D"/>
            <Setter Property="Foreground" Value="#FFFFFF"/>
            <Setter Property="BorderBrush" Value="#2C2F3C"/>
            <Setter Property="Padding" Value="6,4"/>
        </Style>
    </Window.Resources>

    <Grid Margin="12">
        <Grid.RowDefinitions>
            <RowDefinition Height="Auto"/>
            <RowDefinition Height="*"/>
        </Grid.RowDefinitions>

        <!-- Header -->
        <Grid Grid.Row="0" Margin="0,0,0,10">
            <Grid.ColumnDefinitions>
                <ColumnDefinition Width="*"/>
                <ColumnDefinition Width="Auto"/>
            </Grid.ColumnDefinitions>
            
            <StackPanel Grid.Column="0">
                <StackPanel Orientation="Horizontal">
                    <TextBlock Text="AUTOMATED TRACKER" FontSize="16" FontWeight="Bold" Foreground="#FFFFFF"/>
                    <Border Background="#21232D" CornerRadius="3" Padding="5,1" Margin="8,0,0,0" VerticalAlignment="Center">
                        <TextBlock Text="v001.1" FontSize="10" FontWeight="Bold" Foreground="#9CA3AF"/>
                    </Border>
                </StackPanel>
                <TextBlock Text="COLMAP / GLOMAP 3D Camera Solver | Meta CoTracker3 2D Motion Tracker" FontSize="11" Foreground="#6B7280" Margin="0,2,0,0"/>
            </StackPanel>

            <StackPanel Grid.Column="1" Orientation="Horizontal">
                <Button Name="btnOpenVideos" Content="Input Media" Margin="0,0,6,0" Background="#1A1C24"/>
                <Button Name="btnOpenScenes" Content="Scenes Folder" Margin="0,0,6,0" Background="#1A1C24"/>
                <Button Name="btnOpenColmap" Content="COLMAP 3D View" Background="#1A1C24"/>
            </StackPanel>
        </Grid>

        <!-- Main Layout (2 Columns) -->
        <Grid Grid.Row="1">
            <Grid.ColumnDefinitions>
                <ColumnDefinition Width="380"/>
                <ColumnDefinition Width="14"/>
                <ColumnDefinition Width="*"/>
            </Grid.ColumnDefinitions>

            <!-- Left Panel: Presets & Controls -->
            <ScrollViewer Grid.Column="0" VerticalScrollBarVisibility="Auto">
                <StackPanel>
                    <!-- Preset Group -->
                    <GroupBox Header="1. Shot Type Preset">
                        <StackPanel>
                            <ComboBox Name="cmbPreset" Margin="0,4,0,8">
                                <ComboBoxItem Content="Handheld / Walking (Recommended)" IsSelected="True"/>
                                <ComboBoxItem Content="Drone / Aerial Orbit"/>
                                <ComboBoxItem Content="Slow / Subtle Motion (Small Movement)"/>
                                <ComboBoxItem Content="Fast Action / Quick Turns"/>
                                <ComboBoxItem Content="Action Cam / GoPro / Fisheye"/>
                                <ComboBoxItem Content="Standard / Default"/>
                                <ComboBoxItem Content="Custom (Manual Tuning)"/>
                            </ComboBox>
                            <Border Background="#20202C" CornerRadius="4" Padding="8" Margin="0,0,0,4">
                                <TextBlock Name="txtPresetDesc" TextWrapping="Wrap" FontSize="11" Foreground="#00D2FF"/>
                            </Border>
                        </StackPanel>
                    </GroupBox>

                    <!-- Parameters Group -->
                    <GroupBox Header="2. Camera &amp; Solver Parameters">
                        <StackPanel>
                            <!-- Triangulation Angle -->
                            <TextBlock Text="Min Triangulation Angle (°):" Margin="0,4,0,2"/>
                            <TextBox Name="txtTriAngle" Text="6.0"/>
                            <TextBlock Text="Lower values (3°-6°) allow small camera movements to register." FontSize="10" Foreground="#888898" Margin="0,2,0,8"/>

                            <!-- Matching Overlap -->
                            <TextBlock Text="Sequential Overlap (Frames):" Margin="0,4,0,2"/>
                            <TextBox Name="txtOverlap" Text="20"/>
                            <TextBlock Text="Number of surrounding frames to match against (15-30)." FontSize="10" Foreground="#888898" Margin="0,2,0,8"/>

                            <!-- Min Initial Inliers -->
                            <TextBlock Text="Min Initial Inliers (Points):" Margin="0,4,0,2"/>
                            <TextBox Name="txtInliers" Text="60"/>
                            <TextBlock Text="Lowering (40-60) helps difficult footage initialize tracking." FontSize="10" Foreground="#888898" Margin="0,2,0,8"/>

                            <!-- Camera Model -->
                            <TextBlock Text="Camera Distortion Model:" Margin="0,4,0,2"/>
                            <ComboBox Name="cmbCamModel" Margin="0,0,0,8">
                                <ComboBoxItem Content="SIMPLE_RADIAL" IsSelected="True"/>
                                <ComboBoxItem Content="RADIAL"/>
                                <ComboBoxItem Content="OPENCV"/>
                                <ComboBoxItem Content="OPENCV_FISHEYE"/>
                                <ComboBoxItem Content="PINHOLE"/>
                            </ComboBox>

                            <!-- Frame Step -->
                            <TextBlock Text="Frame Extraction Step:" Margin="0,4,0,2"/>
                            <TextBox Name="txtFrameStep" Text="1"/>
                            <TextBlock Text="1 = Every frame. 2 = Every 2nd frame (increases parallax)." FontSize="10" Foreground="#888898" Margin="0,2,0,8"/>

                            <!-- Checkboxes -->
                            <CheckBox Name="chkSingleCam" Content="Single Camera (Fixed Focal Length)" IsChecked="True" Foreground="#E0E0E6" Margin="0,6,0,6"/>
                            <CheckBox Name="chkGpu" Content="Use NVIDIA GPU Acceleration (CUDA)" IsChecked="True" Foreground="#E0E0E6" Margin="0,0,0,6"/>
                        </StackPanel>
                    </GroupBox>
                </StackPanel>
            </ScrollViewer>

            <!-- Right Panel: Video Table & Live Logs -->
            <Grid Grid.Column="2">
                <Grid.RowDefinitions>
                    <RowDefinition Height="200"/>
                    <RowDefinition Height="*"/>
                    <RowDefinition Height="Auto"/>
                </Grid.RowDefinitions>

                <!-- Video List -->
                <GroupBox Grid.Row="0" Header="3. Video Queue (02 VIDEOS)">
                    <Grid>
                        <Grid.RowDefinitions>
                            <RowDefinition Height="Auto"/>
                            <RowDefinition Height="*"/>
                        </Grid.RowDefinitions>

                        <StackPanel Grid.Row="0" Orientation="Horizontal" Margin="0,0,0,8">
                            <Button Name="btnAddVideos" Content="➕ Add Video(s)..." Margin="0,0,8,0"/>
                            <Button Name="btnRefresh" Content="🔄 Refresh Queue" Margin="0,0,8,0"/>
                        </StackPanel>

                        <ListView Name="lstVideos" Grid.Row="1" Background="#121218" BorderBrush="#323242" Foreground="#FFFFFF">
                            <ListView.View>
                                <GridView>
                                    <GridViewColumn Header="Video Filename" Width="260" DisplayMemberBinding="{Binding Filename}"/>
                                    <GridViewColumn Header="Size" Width="80" DisplayMemberBinding="{Binding Size}"/>
                                    <GridViewColumn Header="Status" Width="180" DisplayMemberBinding="{Binding Status}"/>
                                </GridView>
                            </ListView.View>
                        </ListView>
                    </Grid>
                </GroupBox>

                <!-- Live Log Stream -->
                <GroupBox Grid.Row="1" Header="4. Real-Time Tracking Log" Margin="0,8,0,8">
                    <TextBox Name="txtLogs" Background="#101014" Foreground="#C8C8D8" FontFamily="Consolas" FontSize="11" 
                             IsReadOnly="True" VerticalScrollBarVisibility="Auto" HorizontalScrollBarVisibility="Auto"
                             TextWrapping="Wrap" BorderBrush="#252532"/>
                </GroupBox>

                <!-- Controls & Progress -->
                <StackPanel Grid.Row="2">
                    <ProgressBar Name="progBar" Height="22" Minimum="0" Maximum="100" Value="0" Margin="0,0,0,8"
                                 Background="#161620" Foreground="#00D2FF" BorderBrush="#323242"/>
                    
                    <Grid>
                        <Grid.ColumnDefinitions>
                            <ColumnDefinition Width="3*"/>
                            <ColumnDefinition Width="10"/>
                            <ColumnDefinition Width="*"/>
                        </Grid.ColumnDefinitions>

                        <Button Name="btnStart" Grid.Column="0" Content="▶ START TRACKING" Height="42" 
                                Background="#007ACC" Foreground="#FFFFFF" FontSize="14" FontWeight="Bold" BorderBrush="#0098FF"/>
                        <Button Name="btnStop" Grid.Column="2" Content="⏹ Stop" Height="42" 
                                Background="#A82020" Foreground="#FFFFFF" FontWeight="Bold" BorderBrush="#D63030" IsEnabled="False"/>
                    </Grid>
                </StackPanel>
            </Grid>
        </Grid>
    </Grid>
</Window>
"@

$reader = (New-Object System.Xml.XmlNodeReader $xaml)
$window = [Windows.Markup.XamlReader]::Load($reader)

# Element References
$cmbPreset      = $window.FindName("cmbPreset")
$txtPresetDesc  = $window.FindName("txtPresetDesc")
$txtTriAngle    = $window.FindName("txtTriAngle")
$txtOverlap     = $window.FindName("txtOverlap")
$txtInliers     = $window.FindName("txtInliers")
$cmbCamModel    = $window.FindName("cmbCamModel")
$txtFrameStep   = $window.FindName("txtFrameStep")
$chkSingleCam   = $window.FindName("chkSingleCam")
$chkGpu         = $window.FindName("chkGpu")
$lstVideos      = $window.FindName("lstVideos")
$txtLogs        = $window.FindName("txtLogs")
$progBar        = $window.FindName("progBar")
$btnStart       = $window.FindName("btnStart")
$btnStop        = $window.FindName("btnStop")
$btnAddVideos   = $window.FindName("btnAddVideos")
$btnRefresh     = $window.FindName("btnRefresh")
$btnOpenVideos  = $window.FindName("btnOpenVideos")
$btnOpenScenes  = $window.FindName("btnOpenScenes")
$btnOpenColmap  = $window.FindName("btnOpenColmap")

# Global cancel flag and running process
$global:IsRunning = $false
$global:CancelRequested = $false

# Preset Definitions
$presetsData = @{
    "Handheld / Walking (Recommended)" = @{
        Desc = "Optimized for moving camera shots (walking, crane, handheld). Lowers the required angle between views to prevent tracking failure."
        TriAngle = "6.0"
        Overlap  = "20"
        Inliers  = "60"
        CamModel = "SIMPLE_RADIAL"
        Step     = "1"
    }
    "Drone / Aerial Orbit" = @{
        Desc = "Optimized for outdoor and high-altitude shots with wide parallax and high keypoint count."
        TriAngle = "12.0"
        Overlap  = "20"
        Inliers  = "100"
        CamModel = "OPENCV"
        Step     = "1"
    }
    "Slow / Subtle Motion (Small Movement)" = @{
        Desc = "Very forgiving on small camera movements. Subsamples frames to increase baseline and lowers initialization angle."
        TriAngle = "3.0"
        Overlap  = "15"
        Inliers  = "40"
        CamModel = "SIMPLE_RADIAL"
        Step     = "2"
    }
    "Fast Action / Quick Turns" = @{
        Desc = "Increases matching overlap window (30 frames) to maintain tracking during rapid camera motion."
        TriAngle = "8.0"
        Overlap  = "30"
        Inliers  = "50"
        CamModel = "SIMPLE_RADIAL"
        Step     = "1"
    }
    "Action Cam / GoPro / Fisheye" = @{
        Desc = "Uses Fisheye distortion model for wide-angle and action camera lenses."
        TriAngle = "6.0"
        Overlap  = "20"
        Inliers  = "60"
        CamModel = "OPENCV_FISHEYE"
        Step     = "1"
    }
    "Standard / Default" = @{
        Desc = "Default COLMAP settings."
        TriAngle = "16.0"
        Overlap  = "15"
        Inliers  = "100"
        CamModel = "SIMPLE_RADIAL"
        Step     = "1"
    }
    "Custom (Manual Tuning)" = @{
        Desc = "Unlock all parameters for full manual control."
        TriAngle = "6.0"
        Overlap  = "20"
        Inliers  = "60"
        CamModel = "SIMPLE_RADIAL"
        Step     = "1"
    }
}

# Update Preset UI
$updatePreset = {
    $sel = $cmbPreset.Text
    if ($presetsData.ContainsKey($sel)) {
        $p = $presetsData[$sel]
        $txtPresetDesc.Text = $p.Desc
        $txtTriAngle.Text   = $p.TriAngle
        $txtOverlap.Text    = $p.Overlap
        $txtInliers.Text    = $p.Inliers
        $txtFrameStep.Text  = $p.Step
        
        for ($i = 0; $i -lt $cmbCamModel.Items.Count; $i++) {
            if ($cmbCamModel.Items[$i].Content -eq $p.CamModel) {
                $cmbCamModel.SelectedIndex = $i
                break
            }
        }
    }
}
$cmbPreset.add_SelectionChanged({ & $updatePreset })
& $updatePreset

# Refresh Videos List
$refreshVideosList = {
    if (-not (Test-Path $VideosDir)) { New-Item -ItemType Directory -Force -Path $VideosDir | Out-Null }
    $lstVideos.Items.Clear()
    $videoExts = @(".mp4", ".mov", ".avi", ".mkv", ".m4v")
    $files = Get-ChildItem -Path $VideosDir -File -ErrorAction SilentlyContinue | Where-Object { $videoExts -contains $_.Extension.ToLower() }
    
    foreach ($f in $files) {
        $sizeMb = [math]::Round($f.Length / 1MB, 1)
        $baseName = $f.BaseName
        $sparseCam = Join-Path $ScenesDir "$baseName\sparse\cameras.txt"
        $status = if (Test-Path $sparseCam) { "Completed ✔" } else { "Ready" }

        $item = [PSCustomObject]@{
            Filename = $f.Name
            Size     = "$sizeMb MB"
            Status   = $status
            FullPath = $f.FullName
        }
        $lstVideos.Items.Add($item) | Out-Null
    }
}
$btnRefresh.add_Click({ & $refreshVideosList })
& $refreshVideosList

# Log Helper Function
$log = {
    param([string]$text)
    $txtLogs.AppendText("$text`r`n")
    $txtLogs.ScrollToEnd()
    [System.Windows.Forms.Application]::DoEvents()
}

# Add Video Files
$btnAddVideos.add_Click({
    $dlg = New-Object System.Windows.Forms.OpenFileDialog
    $dlg.Multiselect = $true
    $dlg.Filter = "Video Files (*.mp4;*.mov;*.avi;*.mkv)|*.mp4;*.mov;*.avi;*.mkv|All Files (*.*)|*.*"
    if ($dlg.ShowDialog() -eq [System.Windows.Forms.DialogResult]::OK) {
        if (-not (Test-Path $VideosDir)) { New-Item -ItemType Directory -Force -Path $VideosDir | Out-Null }
        foreach ($file in $dlg.FileNames) {
            $dest = Join-Path $VideosDir (Split-Path -Leaf $file)
            if (-not (Test-Path $dest)) {
                Copy-Item -Path $file -Destination $dest
            }
        }
        & $refreshVideosList
    }
})

# Folder Navigation Buttons
$btnOpenVideos.add_Click({
    if (-not (Test-Path $VideosDir)) { New-Item -ItemType Directory -Force -Path $VideosDir | Out-Null }
    [System.Diagnostics.Process]::Start("explorer.exe", $VideosDir)
})
$btnOpenScenes.add_Click({
    if (-not (Test-Path $ScenesDir)) { New-Item -ItemType Directory -Force -Path $ScenesDir | Out-Null }
    [System.Diagnostics.Process]::Start("explorer.exe", $ScenesDir)
})
$btnOpenColmap.add_Click({
    $cmd = if (Test-Path $ColmapBat) { $ColmapBat } else { $ColmapExe }
    [System.Diagnostics.Process]::Start($cmd, "gui")
})

# Stop Tracking
$btnStop.add_Click({
    $global:CancelRequested = $true
    & $log "⏹ Cancel requested by user..."
    $btnStop.IsEnabled = $false
})

# Run Tracking Pipeline
$btnStart.add_Click({
    if ($global:IsRunning) { return }
    $videos = $lstVideos.Items
    if ($videos.Count -eq 0) {
        [System.Windows.MessageBox]::Show("Please place video file(s) in '02 VIDEOS' or click 'Add Video(s)...'", "No Videos Found", [System.Windows.MessageBoxButton]::OK, [System.Windows.MessageBoxImage]::Warning)
        return
    }

    $global:IsRunning = $true
    $global:CancelRequested = $false
    $btnStart.IsEnabled = $false
    $btnStop.IsEnabled = $true
    $txtLogs.Clear()

    if (-not (Test-Path $ScenesDir)) { New-Item -ItemType Directory -Force -Path $ScenesDir | Out-Null }

    $triAngle   = $txtTriAngle.Text.Trim()
    $overlap    = $txtOverlap.Text.Trim()
    $inliers    = $txtInliers.Text.Trim()
    $camModel   = $cmbCamModel.Text.Trim()
    $frameStep  = [math]::Max(1, [int]$txtFrameStep.Text.Trim())
    $singleCam  = if ($chkSingleCam.IsChecked) { "1" } else { "0" }
    $useGpu     = if ($chkGpu.IsChecked) { "1" } else { "0" }

    $total = $videos.Count
    $idx = 0

    foreach ($vItem in $videos) {
        if ($global:CancelRequested) { break }
        $idx++
        $videoPath = $vItem.FullPath
        $videoName = $vItem.Filename
        $baseName  = [System.IO.Path]::GetFileNameWithoutExtension($videoName)
        
        $sceneDir  = Join-Path $ScenesDir $baseName
        $imgDir    = Join-Path $sceneDir "images"
        $sparseDir = Join-Path $sceneDir "sparse"
        $dbPath    = Join-Path $sceneDir "database.db"

        & $log "=================================================="
        & $log "[$idx/$total] Processing: $videoName"
        & $log "=================================================="

        # Check if already reconstructed
        if (Test-Path (Join-Path $sparseDir "cameras.txt")) {
            & $log "↻ Skipping '$baseName' — already successfully reconstructed."
            $vItem.Status = "Completed ✔"
            $lstVideos.Items.Refresh()
            continue
        }

        # Clean slate for incomplete scene
        if (Test-Path $sceneDir) {
            & $log "Cleaning previous incomplete attempt for '$baseName'..."
            Remove-Item -Path $sceneDir -Recurse -Force -ErrorAction SilentlyContinue
        }

        New-Item -ItemType Directory -Force -Path $imgDir | Out-Null
        New-Item -ItemType Directory -Force -Path $sparseDir | Out-Null

        # 1. FFmpeg Frame Extraction
        $progBar.Value = 15
        & $log "▶ [1/4] Extracting frames with FFmpeg..."
        $vItem.Status = "Extracting Frames..."
        $lstVideos.Items.Refresh()

        $ffmpegArgs = @("-loglevel", "error", "-stats", "-i", "`"$videoPath`"")
        if ($frameStep -gt 1) {
            $ffmpegArgs += @("-vf", "select=not(mod(n\,$frameStep))", "-vsync", "vfr")
        }
        $ffmpegArgs += @("-qscale:v", "2", "`"$imgDir\frame_%06d.jpg`"")

        $pInfo = New-Object System.Diagnostics.ProcessStartInfo
        $pInfo.FileName = $FfmpegExe
        $pInfo.Arguments = ($ffmpegArgs -join " ")
        $pInfo.UseShellExecute = $false
        $pInfo.CreateNoWindow = $true
        $pInfo.RedirectStandardError = $true

        $proc = [System.Diagnostics.Process]::Start($pInfo)
        $proc.WaitForExit()

        $frames = Get-ChildItem -Path $imgDir -Filter "*.jpg"
        if ($frames.Count -eq 0) {
            & $log "✖ Error: No frames extracted from $videoName"
            $vItem.Status = "FFmpeg Failed ✖"
            $lstVideos.Items.Refresh()
            continue
        }
        & $log "✔ Extracted $($frames.Count) frames."

        # 2. Feature Extraction
        if ($global:CancelRequested) { break }
        $progBar.Value = 40
        & $log "▶ [2/4] Extracting SIFT features (GPU=$useGpu, Camera=$camModel)..."
        $vItem.Status = "Feature Extraction..."
        $lstVideos.Items.Refresh()

        $featArgs = "feature_extractor --database_path `"$dbPath`" --image_path `"$imgDir`" --ImageReader.camera_model $camModel --ImageReader.single_camera $singleCam --SiftExtraction.use_gpu $useGpu --SiftExtraction.max_image_size 4096"
        $pInfo.FileName = $ColmapExe
        $pInfo.Arguments = $featArgs
        $pInfo.RedirectStandardOutput = $true
        $proc = [System.Diagnostics.Process]::Start($pInfo)
        
        while (-not $proc.HasExited) {
            $line = $proc.StandardOutput.ReadLine()
            if ($line -and ($line -match "Features:|Elapsed time:|Registering")) { & $log "   $line" }
            [System.Windows.Forms.Application]::DoEvents()
        }
        if ($proc.ExitCode -ne 0) {
            & $log "✖ Feature extractor failed for $videoName"
            $vItem.Status = "Feature Extraction Failed ✖"
            $lstVideos.Items.Refresh()
            continue
        }

        # 3. Sequential Matching
        if ($global:CancelRequested) { break }
        $progBar.Value = 65
        & $log "▶ [3/4] Sequential Matching (Overlap=$overlap)..."
        $vItem.Status = "Matching Features..."
        $lstVideos.Items.Refresh()

        $vocabPath = Join-Path $ColmapDir "vocab_tree_faiss_flickr100K_words256K.bin"
        if (Test-Path $vocabPath) {
            $matchArgs = "sequential_matcher --database_path `"$dbPath`" --SequentialMatching.overlap $overlap --SequentialMatching.vocab_tree_path `"$vocabPath`" --SequentialMatching.loop_detection 1"
        } else {
            $matchArgs = "sequential_matcher --database_path `"$dbPath`" --SequentialMatching.overlap $overlap --SequentialMatching.loop_detection 0"
        }

        $pInfo.Arguments = $matchArgs
        $proc = [System.Diagnostics.Process]::Start($pInfo)
        while (-not $proc.HasExited) {
            $line = $proc.StandardOutput.ReadLine()
            if ($line -and ($line -match "Elapsed time:|Matching image")) { & $log "   $line" }
            [System.Windows.Forms.Application]::DoEvents()
        }
        if ($proc.ExitCode -ne 0) {
            & $log "✖ Sequential matcher failed for $videoName"
            $vItem.Status = "Matching Failed ✖"
            $lstVideos.Items.Refresh()
            continue
        }

        # 4. Mapper Reconstruction
        if ($global:CancelRequested) { break }
        $progBar.Value = 85
        & $log "▶ [4/4] Reconstructing 3D Camera Track (min_tri_angle=$triAngle°)..."
        $vItem.Status = "Reconstructing Track..."
        $lstVideos.Items.Refresh()

        $mapperArgs = "mapper --database_path `"$dbPath`" --image_path `"$imgDir`" --output_path `"$sparseDir`" --Mapper.init_min_tri_angle $triAngle --Mapper.init_min_num_inliers $inliers --Mapper.abs_pose_min_num_inliers $([math]::Max(15, [int]$inliers / 2)) --Mapper.ba_use_gpu $useGpu"
        $pInfo.Arguments = $mapperArgs
        $proc = [System.Diagnostics.Process]::Start($pInfo)
        while (-not $proc.HasExited) {
            $line = $proc.StandardOutput.ReadLine()
            if ($line -and ($line -match "Registering image|Elapsed time:|Triangulated")) { & $log "   $line" }
            [System.Windows.Forms.Application]::DoEvents()
        }

        # 5. Convert to TXT & Multi-Format Exports (Blender, USD, PLY, Nuke)
        $model0 = Join-Path $sparseDir "0"
        if (Test-Path $model0) {
            & $log "▶ Exporting best model to TXT format..."
            $convArgs = "model_converter --input_path `"$model0`" --output_path `"$sparseDir`" --output_type TXT"
            $pInfo.Arguments = $convArgs
            $proc = [System.Diagnostics.Process]::Start($pInfo)
            $proc.WaitForExit()

            # Surface Mesh Reconstruction
            $meshOut = Join-Path $sceneDir "environment_mesh.ply"
            $meshArgs = "delaunay_mesher --input_type sparse --input_path `"$model0`" --output_path `"$meshOut`""
            $pInfo.Arguments = $meshArgs
            $meshProc = [System.Diagnostics.Process]::Start($pInfo)
            $meshProc.WaitForExit()

            # Call export_tools.py
            $localPy = Join-Path $BaseDir "00 PYTHON\python.exe"
            $exportScript = Join-Path $ScriptDir "export_tools.py"
            if ((Test-Path $localPy) -and (Test-Path $exportScript)) {
                & $log "▶ Generating Blender 1-Click Script, USD (.usda), Point Cloud (.ply), and Nuke (.chan)..."
                $pInfo.FileName = $localPy
                $pInfo.Arguments = "`"$exportScript`" `"$sceneDir`""
                $expProc = [System.Diagnostics.Process]::Start($pInfo)
                $expProc.WaitForExit()
                & $log "   ✔ Generated 1-Click Blender Script: import_to_blender.py"
                & $log "   ✔ Generated Universal Scene Description: camera_track.usda"
                & $log "   ✔ Generated 3D Point Cloud: points3D.ply"
                & $log "   ✔ Generated Nuke Camera Track: camera_track.chan"
            }

            & $log "✔ Successfully generated camera track and exports for '$baseName'!"
            $vItem.Status = "Completed ✔"
        } else {
            & $log "✖ Mapper completed but could not reconstruct camera poses."
            & $log "  Tip: Select the 'Handheld' or 'Slow Motion' preset to lower the required triangulation angle."
            $vItem.Status = "No Track Found ✖"
        }
        $lstVideos.Items.Refresh()
    }

    $progBar.Value = 100
    & $log "`n=================================================="
    & $log "All processing completed!"
    & $log "Results are ready inside '04 SCENES'."
    & $log "=================================================="

    $global:IsRunning = $false
    $btnStart.IsEnabled = $true
    $btnStop.IsEnabled = $false
})

# Display Window
$window.ShowDialog() | Out-Null
