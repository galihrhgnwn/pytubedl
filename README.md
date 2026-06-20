![PytubeDL Banner](https://files.catbox.moe/thtf31.png)

# PytubeDL

PytubeDL is a self-hosted REST API that provides a robust interface for fetching YouTube video metadata, stream listings, chapters, key moments, captions, and direct file downloads. Built with Python, Flask, and the [pytubefix](https://pypi.org/project/pytubefix/) library, it serves as a reliable backend for applications requiring YouTube data extraction and media downloading capabilities.

## Project Overview

YouTube frequently updates its internal APIs, which often breaks traditional scraping libraries. PytubeDL leverages `pytubefix` (an actively maintained fork of the original `pytube` library) to ensure consistent functionality. It wraps these capabilities in a clean, documented REST API, making it easy to integrate YouTube data extraction into web applications, mobile apps, or automated workflows.

A key advantage of PytubeDL is its handling of high-resolution video downloads. YouTube only provides progressive streams (video and audio combined) up to 360p or 720p. For higher resolutions (1080p, 1440p, 4K), YouTube separates the video and audio tracks. PytubeDL automatically detects this and uses `ffmpeg` to seamlessly merge the highest quality video and audio streams on the server before delivering the final MP4 file to the client.

## Features

*   **Comprehensive Metadata Extraction**: Retrieve detailed video information, including title, author, duration, views, likes, descriptions, and thumbnails.
*   **Stream Discovery**: List all available progressive, video-only, and audio-only streams for a given video.
*   **High-Resolution Downloads**: Automatically merge adaptive video and audio streams using `ffmpeg` to support 1080p, 1440p, and 4K downloads with sound.
*   **Audio Extraction**: Download audio-only streams in various formats (M4A, MP3, Opus, OGG).
*   **Captions and Subtitles**: Fetch available captions in SRT, XML, or TXT formats, or as parsed JSON data.
*   **Chapters and Key Moments**: Extract video chapters and key moments for timeline navigation.
*   **Playlist and Channel Support**: Retrieve lists of videos from playlists and channels.
*   **Search Functionality**: Search for videos, shorts, playlists, and channels directly through the API.
*   **OAuth Authentication**: Utilizes Google OAuth to bypass YouTube bot detection and age restrictions, caching tokens for seamless operation.
*   **Interactive Documentation**: Includes a built-in, single-page API documentation frontend with a live playground.

## Requirements

*   Python 3.10 or higher
*   `ffmpeg` (Required for merging video and audio streams for resolutions above 720p)
*   A Google account (for initial OAuth authentication)

## Installation

### 1. Clone the Repository

```bash
git clone https://github.com/galihrhgnwn/pytubedl.git
cd pytubedl
```

### 2. Install System Dependencies

You must install `ffmpeg` on your server to enable high-resolution video downloads.

**Ubuntu/Debian:**
```bash
sudo apt-get update
sudo apt-get install ffmpeg
```

**CentOS/RHEL:**
```bash
sudo yum install ffmpeg
```

### 3. Run the Setup Script

The repository includes a `setup.sh` script that automates dependency installation and the initial OAuth login process.

```bash
bash setup.sh
```

During the setup process, the script will prompt you to authenticate with Google:
1. A URL and a device code will appear in your terminal.
2. Open the URL in your web browser.
3. Enter the device code.
4. Log in with your Google account.

Once authenticated, the OAuth token is cached in `./tokens/oauth.json`, and subsequent API requests will not require manual intervention.

### Manual Installation (Alternative)

If you prefer not to use the setup script, you can install the dependencies and authenticate manually:

```bash
pip install -r requirements.txt
mkdir -p tokens
python3 -c "
from pytubefix import YouTube
yt = YouTube('https://youtu.be/dQw4w9WgXcQ', use_oauth=True, allow_oauth_cache=True, token_file='./tokens/oauth.json')
_ = yt.streams
print('Login successful, token saved.')
"
```

## Quick Start

### Development Server

To run the API using the built-in Flask development server:

```bash
python3 app.py
```

The API will be available at `http://0.0.0.0:24698`. You can access the interactive documentation by navigating to `http://localhost:24698/` in your browser.

### Production Server

For production environments, it is recommended to use a WSGI server like Gunicorn:

```bash
gunicorn -w 4 -b 0.0.0.0:9715 app:app
```

## Configuration

PytubeDL uses environment variables for configuration.

| Variable | Description | Default Value |
| :--- | :--- | :--- |
| `YT_TOKEN_FILE` | Path to the cached OAuth token file. | `./tokens/oauth.json` |
| `FFMPEG_BIN` | Path to the `ffmpeg` executable. | `ffmpeg` (resolved via `shutil.which`) |

## API Reference

All endpoints use the `GET` method.

### Video Information

#### `GET /api/info`
Retrieves full metadata and available streams for a video.

**Parameters:**
*   `url` (required): The YouTube video URL.

**Example Request:**
```bash
curl "http://localhost:24698/api/info?url=https://youtu.be/dQw4w9WgXcQ"
```

#### `GET /api/chapters`
Retrieves chapters defined in the video.

**Parameters:**
*   `url` (required): The YouTube video URL.

#### `GET /api/key-moments`
Retrieves key moments and the replayed heatmap data.

**Parameters:**
*   `url` (required): The YouTube video URL.

### Downloading

#### `GET /api/download`
Downloads a specific stream by its `itag`.

**Parameters:**
*   `url` (required): The YouTube video URL.
*   `itag` (required): The integer ID of the stream.
*   `merge_audio` (optional): Boolean (`true`/`false`). If `true` and the stream is video-only, the server will merge it with the best audio stream using `ffmpeg`. Defaults to `true`.

#### `GET /api/download/highest`
Downloads the highest resolution available.

**Parameters:**
*   `url` (required): The YouTube video URL.
*   `progressive` (optional): Boolean. If `true`, forces the download of the highest progressive stream (max 720p). If `false`, downloads the highest adaptive stream and merges audio. Defaults to `false`.
*   `mime_type` (optional): Filter by MIME type (e.g., `video/mp4`).

#### `GET /api/download/resolution`
Downloads a video at a specific resolution.

**Parameters:**
*   `url` (required): The YouTube video URL.
*   `res` (required): The target resolution (e.g., `720p`, `1080p`, `2160p`).
*   `merge_audio` (optional): Boolean. Defaults to `true`.

**Example Request:**
```bash
curl -O -J "http://localhost:24698/api/download/resolution?url=https://youtu.be/dQw4w9WgXcQ&res=1080p"
```

#### `GET /api/download/audio`
Downloads the best available audio-only stream.

**Parameters:**
*   `url` (required): The YouTube video URL.
*   `format` (optional): The desired audio format (`m4a`, `mp3`, `opus`, `webm`). Defaults to `m4a`.

### Captions

#### `GET /api/captions`
Lists all available caption tracks for a video.

**Parameters:**
*   `url` (required): The YouTube video URL.

#### `GET /api/captions/get`
Downloads a specific caption track as a file.

**Parameters:**
*   `url` (required): The YouTube video URL.
*   `lang_code` (optional): The language code (e.g., `en`). Defaults to `en`.
*   `format` (optional): The output format (`srt`, `xml`, `txt`). Defaults to `srt`.

### Discovery

#### `GET /api/search`
Searches YouTube for videos, shorts, playlists, or channels.

**Parameters:**
*   `q` (required): The search query.
*   `limit` (optional): Maximum number of results to return. Defaults to 10.
*   `type` (optional): The type of content to search for (`videos`, `shorts`, `playlists`, `all`). Defaults to `videos`.

#### `GET /api/playlist`
Retrieves metadata and a list of videos from a playlist.

**Parameters:**
*   `url` (required): The YouTube playlist URL.
*   `limit` (optional): Maximum number of videos to return. Defaults to 50.

#### `GET /api/channel`
Retrieves videos, shorts, or live streams from a channel.

**Parameters:**
*   `url` (required): The YouTube channel URL.
*   `content` (optional): The type of content to retrieve (`videos`, `shorts`, `live`, `releases`). Defaults to `videos`.
*   `limit` (optional): Maximum number of items to return. Defaults to 20.

## Error Handling

The API returns standard HTTP status codes alongside a JSON payload containing error details.

| Status Code | Description | Example Scenario |
| :--- | :--- | :--- |
| `400 Bad Request` | Invalid input parameters. | Missing `url` parameter or invalid YouTube URL format. |
| `403 Forbidden` | Access denied by YouTube. | Video is age-restricted, private, or region-blocked. |
| `404 Not Found` | Resource not found. | Video unavailable or requested resolution/itag does not exist. |
| `500 Internal Server Error` | Unexpected server error. | Unhandled exception during processing. |
| `503 Service Unavailable` | Service limitation. | Bot detection triggered, PO Token required, or `ffmpeg` is missing when a merge is requested. |

**Example Error Response:**
```json
{
  "error": "This itag is video-only and requires ffmpeg to merge audio.",
  "detail": "ffmpeg is not installed on the server. Install it or pass merge_audio=false to download video-only (no sound)."
}
```

## Internal Mechanics: Audio Merging

When a client requests a resolution higher than 720p (e.g., via `/api/download/resolution?res=1080p`), the following process occurs internally:

1.  The API queries `pytubefix` for the adaptive video-only stream matching the requested resolution.
2.  It queries for the highest bitrate audio-only stream.
3.  Both streams are downloaded to a temporary directory on the server.
4.  The API invokes `ffmpeg` using a subprocess call. It uses the `-c:v copy` flag to copy the video stream without re-encoding, preserving original quality and minimizing CPU usage. The audio stream is either copied or re-encoded to AAC depending on container compatibility.
5.  The merged MP4 file is streamed to the client.
6.  The temporary files are deleted from the server.

## License

This project is open-source and available under the terms of the license specified in the repository.

## Contributing

Contributions are welcome. Please open an issue or submit a pull request on the [GitHub repository](https://github.com/galihrhgnwn/pytubedl) for any bugs, feature requests, or improvements. Ensure that your code adheres to the existing style and includes appropriate error handling.
