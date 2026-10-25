<p align="center">
  <img src="firefox-extension/icons/icon128.png" width="96" alt="Kidlat logo">
</p>

<h1 align="center">Kidlat Download Manager</h1>

<p align="center">
  A free, fast download manager for Windows with a Firefox extension.<br>
  No subscription. No trial. No ads.
</p>

<p align="center">
  <img alt="Platform" src="https://img.shields.io/badge/platform-Windows-0078D6">
  <img alt="Python" src="https://img.shields.io/badge/python-3.9%2B-3776AB">
  <img alt="Browser" src="https://img.shields.io/badge/browser-Firefox-FF7139">
  <img alt="License" src="https://img.shields.io/badge/license-MIT-green">
</p>

> **Kidlat** (Filipino for *lightning*) is an IDM-style download manager written in plain Python. It splits files into parts and downloads them over several connections at once, survives bad internet, and adds a **⚡ Download this video** button to videos in Firefox.

---

## Table of contents

- [Features](#features)
- [Requirements](#requirements)
- [Installation](#installation)
- [Using Kidlat](#using-kidlat)
- [Video downloads (YouTube and other sites)](#video-downloads-youtube-and-other-sites)
- [Options](#options)
- [Troubleshooting](#troubleshooting)
- [How it works](#how-it-works)
- [Project structure](#project-structure)
- [Limitations](#limitations)
- [Credits](#credits)
- [License](#license)

---

## Features

**Downloading**
- **Multi-connection downloads**: splits each file into up to 32 parts and downloads them at the same time (8 by default).
- **Dynamic splitting**: when one connection finishes, it takes over half of the biggest remaining part, so no connection sits idle.
- **Pause and resume anytime**, even after closing the app or restarting the PC.
- **Built for unstable internet**: when the connection drops, it waits and retries automatically and keeps everything already downloaded.
- **Refresh expired links**: continue a stopped download with a new link instead of starting over.
- **Download queue**: runs a set number of downloads at a time (3 by default); the rest wait their turn.
- **Speed limiter** to save data or leave bandwidth for other things.
- **Auto-sorting** into Video, Music, Compressed, Documents, Programs, Images and Other folders.

**Firefox integration**
- **Catches downloads** automatically when you click a download link.
- **⚡ Download this video** button on top of videos, IDM-style.
- **Right-click menu**: *Download with Kidlat*, and *Download all links with Kidlat* for every file linked on a page.
- **Toolbar popup**: turn catching on or off, download the video on the current page, or paste a link.

**Video sites**
- YouTube, Facebook, TikTok, X/Twitter, Instagram, Vimeo, Dailymotion, Reddit and [1,000+ more sites](https://github.com/yt-dlp/yt-dlp/blob/master/supportedsites.md), plus streamed video on ordinary websites.
- **Choose the quality** before downloading, with the file size of each option.
- **Fast mode**: video files are downloaded over 8 connections, which helps a lot on sites like YouTube that slow down single connections.
- **Audio only** (M4A or MP3).

**Other**
- Starts with Windows (optional), minimized and ready for Firefox.
- Normal downloads use only the Python standard library; no `pip install` needed.

---

## Requirements

| What | Why | Size |
|---|---|---|
| **Windows 10 or 11** | Kidlat is a Windows app | |
| **[Python 3.9+](https://www.python.org/downloads/windows/)** with *tcl/tk* | Runs Kidlat (tcl/tk is included by default) | ~25 MB |
| **Firefox 115+** | For the extension | |
| *Optional:* Video Tools | YouTube and other video sites (Kidlat downloads these for you) | 17–145 MB |

---

## Installation

### 1. Install Python
Download Python from [python.org](https://www.python.org/downloads/windows/). During setup, tick **"Add python.exe to PATH"** and leave everything else as default.

To check it's ready, open **Command Prompt** and run:
```bat
python -c "import tkinter; print('OK')"
```
If it prints `OK`, continue. If it shows an error, open **Settings → Apps → Installed apps → Python 3.x → ⋯ → Modify**, click **Modify**, tick **"tcl/tk and IDLE"**, then **Next → Install**.

### 2. Download Kidlat
Either:
- Click the green **Code** button on this page → **Download ZIP**, and unzip it, **or**
- Clone it:
  ```bat
  git clone https://github.com/MohsenMacatoon/kidlat.git
  ```

Put the folder somewhere permanent, such as `C:\Kidlat` or `Documents\Kidlat`.

> **Don't** put it in `Downloads\Kidlat`. That's where Kidlat saves your downloads by default, so the app's files would get mixed in with them.

### 3. Turn off IDM's Firefox add-on (only if you have IDM)
In Firefox, open `about:addons`, find **IDM Integration Module**, and switch it **off**. Otherwise IDM grabs downloads before Kidlat can.

### 4. Start Kidlat
Double-click **`Start Kidlat.bat`**. The Kidlat window opens.

### 5. Set up options
In Kidlat, click **Options**:
- Choose where downloads are saved.
- Tick **"Start Kidlat when I log in to Windows"** so Firefox can always hand it downloads.
- Click **Save**.

### 6. Install the Firefox extension

**Quick (temporary, removed when Firefox closes):**
1. Open `about:debugging#/runtime/this-firefox` in Firefox.
2. Click **Load Temporary Add-on…**.
3. Select `firefox-extension/manifest.json` inside the Kidlat folder.

**Permanent (recommended, free):** regular Firefox only keeps extensions signed by Mozilla. You can sign your own copy privately:
1. Zip the **contents** of the `firefox-extension` folder, so `manifest.json` is at the top of the zip, not inside a subfolder.
   *(Or use `kidlat-firefox-extension.zip` if it's included.)*
2. Go to [addons.mozilla.org/developers](https://addons.mozilla.org/developers/) and sign in with a free Firefox account.
3. Click **Submit a New Add-on** → **"On your own"**. This keeps it private and unlisted.
4. Upload the zip and wait for the automatic check.
5. Download the signed **`.xpi`** file and drag it into Firefox → **Add**.

### 7. Check the connection
Click the **lightning icon** on the Firefox toolbar. If it isn't there, click the puzzle-piece icon and pin **Kidlat**. It should say **"App is running"** in green.

### 8. Optional: Video Tools
In Kidlat, click **Video Tools** and download what you need (see [Video downloads](#video-downloads-youtube-and-other-sites)).

### 9. Optional: build `Kidlat.exe`
Double-click **`Build Kidlat.exe.bat`**. It downloads [PyInstaller](https://pyinstaller.org) (~3 MB) once and creates `dist\Kidlat.exe`, which runs without Python. `Start Kidlat.bat` uses it automatically afterwards.
If you turned on *Start with Windows*, open **Options → Save** once so it points to the new `.exe`.

---

## Using Kidlat

| To do this | Do this |
|---|---|
| Download a file | Click the download link in Firefox. The Kidlat window appears; click **Start Download**. |
| Download a video | Hover over the video → **⚡ Download this video** → choose a quality. |
| Video button doesn't appear | Lightning icon → **Download video from this page**. |
| Download one link | Right-click the link → **Download with Kidlat**. |
| Download every file on a page | Right-click the page → **Download all links with Kidlat**. Select text first to limit it to that part. |
| Paste a link | **Add URL** in Kidlat (it reads your clipboard), or paste it in the extension popup. |
| Let Firefox handle a download | Lightning icon → untick **Catch Firefox downloads**. |
| Pause or continue | Select the download → **Pause** / **Resume**, or **Pause All** / **Resume All**. |
| Link expired mid-download | Copy a fresh link from the site → right-click the download → **Refresh Link**. |
| Open a finished file | Double-click it. Right-click → **Open Folder** shows it in Explorer. |
| See each connection working | Double-click an unfinished download. |

If Kidlat isn't running, Firefox downloads normally, so you never lose a download.

---

## Video downloads (YouTube and other sites)

Kidlat uses free, open-source tools for video sites. Click **Video Tools** in Kidlat to install them. They're downloaded by Kidlat itself, so they resume if your internet drops.

| Tool | Size | Needed for |
|---|---|---|
| [yt-dlp](https://github.com/yt-dlp/yt-dlp) | ~17 MB | Every video site |
| [Deno](https://deno.com) | ~41 MB | YouTube (yt-dlp needs it since late 2025) |
| [FFmpeg](https://ffmpeg.org) | ~87 MB | HD quality and MP3. Without it, YouTube is limited to 360p. |

**Quality picker:** every resolution is listed with its size. Options that need FFmpeg are greyed out until it's installed. Kidlat picks 720p by default to save data.

**Keep it working:** YouTube changes often. If YouTube downloads start failing, open **Video Tools → Update yt-dlp**.

---

## Options

| Option | Default | What it does |
|---|---|---|
| Save downloads to | `Downloads\Kidlat` | Main download folder |
| Sort into folders by type | On | Video, Music, Programs… subfolders |
| Connections per download | 8 | 1–32. More can help on throttled servers. |
| Downloads at the same time | 3 | The rest wait in the queue |
| Speed limit | 0 (none) | In KB/s |
| Ask before downloads caught from Firefox | On | Shows the file info window first |
| Popup when a download finishes | On | Small window with Open / Open Folder |
| Start with Windows | Off | Starts minimized at login |
| Browser bridge port | 9614 | Must match the extension's Options |

**Extension options** (lightning icon → Options): which file types to catch, sites to never catch from, minimum file size, and the port.

---

## Troubleshooting

| Problem | Fix |
|---|---|
| `Start Kidlat.bat` says tkinter is missing | See [step 1](#1-install-python): add **tcl/tk and IDLE** to Python. |
| IDM still grabs downloads | Turn off **IDM Integration Module** in `about:addons`, then restart Firefox. |
| Extension says **"App is not running"** | Start Kidlat. Check both use the same port (default 9614). |
| Extension disappeared after restarting Firefox | It was loaded as temporary. Load it again, or [sign it](#6-install-the-firefox-extension) once. |
| Video button doesn't show | Reload the page (F5). Videos smaller than 200×110 pixels (ads, previews) don't get a button. Use the popup's **Download video from this page**. |
| YouTube download fails | **Video Tools → Update yt-dlp**. Make sure **Deno** is installed. |
| Only 360p on YouTube | Install **FFmpeg** in Video Tools. |
| A site's downloads keep failing | Extension **Options → Never catch downloads from these sites**, then let Firefox download them. |
| "Port is busy" in the status bar | Kidlat is already running (check the taskbar), or change the port in both the app and the extension. |
| Download stuck on "Retrying" | Your connection dropped. Kidlat keeps retrying; you can also **Pause** and **Resume** later. |
| Server said 403 / link expired | Copy a new link from the website → right-click → **Refresh Link**. |

Your download list and settings are stored in `%APPDATA%\Kidlat`.

---

## How it works

```
┌──────────────────────┐   127.0.0.1:9614 (local only)   ┌─────────────────────────┐
│  Firefox extension   │ ──────────────────────────────▶ │  Kidlat app (Python)    │
│  • catches downloads │    link + cookies + referrer    │  • queue & window       │
│  • ⚡ video button    │                                 │  • multi-connection     │
│  • right-click menu  │                                 │    download engine      │
└──────────────────────┘                                 │  • yt-dlp / FFmpeg for  │
                                                         │    video sites          │
                                                         └─────────────────────────┘
```

- **Normal files:** Kidlat asks the server for the file size and whether it allows partial downloads (HTTP `Range`). If it does, the file is split into parts that are fetched in parallel and written into one `.kidlat` file, which is renamed when complete. Progress is saved every few seconds, so a crash or restart loses almost nothing.
- **Video sites:** yt-dlp finds the real video and audio file addresses. Kidlat downloads them with its own multi-connection engine, then FFmpeg joins video and audio. Sites that stream video in small pieces (HLS/DASH) are downloaded by yt-dlp, 8 pieces at a time.
- **Security:** the app only listens on `127.0.0.1` (your own PC). It accepts requests only from browser extensions carrying a special header and rejects requests from web pages.

---

## Project structure

```
kidlat/
├── app/
│   ├── kidlat.pyw          # Entry point (start this)
│   ├── engine.py           # Download engine: segments, retries, queue, speed limit
│   ├── gui.py              # Windows interface (tkinter)
│   ├── tools.py            # Video Tools: yt-dlp, Deno, FFmpeg
│   ├── bridge.py           # Local server the Firefox extension talks to
│   ├── autostart.py        # "Start with Windows" (registry Run key)
│   └── kidlat.ico          # App icon
├── firefox-extension/
│   ├── manifest.json
│   ├── background.js       # Catches downloads, right-click menu, video detection
│   ├── content.js          # ⚡ Download this video button
│   ├── common.js           # Shared settings and helpers
│   ├── popup.html/.js/.css # Toolbar popup
│   ├── options.html/.js    # Extension options
│   └── icons/
├── Start Kidlat.bat        # Starts the app (checks Python and tkinter)
├── Build Kidlat.exe.bat    # Optional: builds dist\Kidlat.exe with PyInstaller
├── LICENSE
└── README.md
```

---

## Limitations

- **DRM-protected video** (Netflix, Disney+, Spotify, many paid course sites) can't be downloaded, by Kidlat or any other download manager.
- **Live streams** can only be downloaded after they end.
- **Downloads that need a form submission** (some school portals, banking statements) may not work through Kidlat. Add those sites to the extension's skip list.
- Servers that don't allow partial downloads use 1 connection and restart from the beginning if paused. The Add window warns you ("Resume supported: No").
- Firefox only, for now.

Please respect each website's terms of service and copyright when downloading.

---

## Credits

- [yt-dlp](https://github.com/yt-dlp/yt-dlp) (Unlicense): video site support
- [Deno](https://github.com/denoland/deno) (MIT): JavaScript runtime used by yt-dlp for YouTube
- [FFmpeg](https://ffmpeg.org) (LGPL/GPL): joining video and audio, MP3 conversion. Builds from [yt-dlp/FFmpeg-Builds](https://github.com/yt-dlp/FFmpeg-Builds).
- [PyInstaller](https://pyinstaller.org): optional `.exe` build

These tools are not included in this repository. Kidlat downloads them from their official releases when you ask it to.

Kidlat is an independent project and is not affiliated with Internet Download Manager (IDM) or Tonec Inc.

---

## License

[MIT](LICENSE) © 2026 Mohsen Macatoon
