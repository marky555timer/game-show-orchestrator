# build_exe.ps1
# Packages the orchestrator into a standalone --onedir .exe build with
# PyInstaller. Run from the project root:
#   .\build_exe.ps1
#
# Output lands in dist\GameShowOrchestrator\. Before handing that folder
# off to a show rig, drop a plain-text anthropic_key.txt (just the raw
# "sk-ant-..." key, nothing else) next to GameShowOrchestrator.exe --
# config.py halts with a popup on launch if it's missing (see
# config.py::_load_anthropic_api_key()). rekordbox.xml is NOT bundled --
# the app already checks the Documents/Desktop/OneDrive locations
# drivers/rekordbox_driver.py::load_database() looks for on the target
# machine, since that's the DJ's real library, not a build-time asset.

$ErrorActionPreference = "Stop"

if (-not (Get-Command pyinstaller -ErrorAction SilentlyContinue)) {
    Write-Host "PyInstaller not found -- installing..."
    pip install pyinstaller
}

pyinstaller --noconfirm --onedir --windowed `
    --name "GameShowOrchestrator" `
    --add-data "audio;audio" `
    --add-data "web/static;web/static" `
    --add-data "fallback_questions.json;." `
    --add-data "light_prefs;light_prefs" `
    --add-data "price_game;price_game" `
    --add-data "announcements;announcements" `
    --add-data "music_metadata;music_metadata" `
    --hidden-import "uvicorn.logging" `
    --hidden-import "uvicorn.loops" `
    --hidden-import "uvicorn.loops.auto" `
    --hidden-import "uvicorn.protocols" `
    --hidden-import "uvicorn.protocols.http" `
    --hidden-import "uvicorn.protocols.http.auto" `
    --hidden-import "uvicorn.protocols.websockets" `
    --hidden-import "uvicorn.protocols.websockets.auto" `
    --hidden-import "uvicorn.lifespan" `
    --hidden-import "uvicorn.lifespan.on" `
    main.py

Write-Host ""
Write-Host "Build complete: dist\GameShowOrchestrator\GameShowOrchestrator.exe"
Write-Host "Remember to create dist\GameShowOrchestrator\anthropic_key.txt before launch."
