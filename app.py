from flask import Flask, request, jsonify, send_file
from flask_cors import CORS
from pytubefix import YouTube, Playlist, Channel, Search
from vpn_gate import vpn_manager
from pytubefix.cli import on_progress
from pytubefix.exceptions import (
    BotDetection, PoTokenRequired, VideoUnavailable,
    AgeRestrictedError, VideoPrivate, VideoRegionBlocked,
    MembersOnly, LiveStreamError
)
import io
import re
import os
import shutil
import subprocess
import tempfile
import uuid

app = Flask(__name__)
CORS(app)

# oauth config
# token cached to file - login only once interactively
# set env var YT_TOKEN_FILE for custom path, default: ./tokens/oauth.json
_TOKEN_FILE = os.environ.get("YT_TOKEN_FILE", "./tokens/oauth.json")

# path to ffmpeg binary - used to merge video-only + audio-only streams
# needed for resolutions above 360p/720p that aren't progressive
_FFMPEG_BIN = shutil.which("ffmpeg") or os.environ.get("FFMPEG_BIN", "ffmpeg")
_FFMPEG_AVAILABLE = shutil.which(_FFMPEG_BIN) is not None

RESOLUTION_ORDER = ["144p", "240p", "360p", "480p", "720p", "1080p",
                     "1440p", "2160p", "2880p", "4320p"]

# create youtube instance with oauth (google account)
# token cached to file so no need to log in on every request
# first login on vps: run once interactively:
#   mkdir -p tokens
#   python3 -c "
#   from pytubefix import YouTube
#   yt = YouTube('https://youtu.be/dQw4w9WgXcQ',
#                use_oauth=True,
#                allow_oauth_cache=True,
#                token_file='./tokens/oauth.json')
#   _ = yt.streams
#   print('login successful, token saved.')
#   "
# follow instructions: open url in browser, enter device code
# after that token is saved and api works without interaction
def make_yt(url: str, **kwargs) -> YouTube:
    base = dict(on_progress_callback=on_progress)
    base.update(kwargs)

    os.makedirs(os.path.dirname(_TOKEN_FILE) or ".", exist_ok=True)

    bypass_vpn = _arg_bool("bypass_region", False)
    
    def create_yt():
        return YouTube(
            url,
            use_oauth=True,
            allow_oauth_cache=True,
            token_file=_TOKEN_FILE,
            **base,
        )

    if bypass_vpn:
        if not vpn_manager.is_connected:
            vpn_manager.connect()
            vpn_manager.start_auto_switch()
        
        try:
            yt = create_yt()
            _ = yt.vid_info
            return yt
        except VideoRegionBlocked:
            # Try switching VPN once if it was already connected but still blocked
            vpn_manager.connect()
            yt = create_yt()
            _ = yt.vid_info
            return yt
    else:
        # If not bypassing, ensure VPN is disconnected to save resources
        if vpn_manager.is_connected:
            vpn_manager.stop_auto_switch()
            vpn_manager.disconnect()
            
        yt = create_yt()
        _ = yt.vid_info
        return yt


# helpers

def valid_video_url(url: str) -> bool:
    return bool(re.match(
        r"(https?://)?(www\.|music\.)?(youtube\.com/watch\?v=|youtu\.be/|youtube\.com/shorts/)[\w\-]+", url
    ))


def valid_playlist_url(url: str) -> bool:
    return "youtube.com" in url and "list=" in url


def safe_filename(title: str) -> str:
    return re.sub(r"[^\w\s\-]", "", title).strip().replace(" ", "_")


# return a human-readable audio format label: m4a, opus, mp3, ogg, or none
def _audio_format(s) -> str | None:
    mime      = getattr(s, "mime_type", "") or ""
    codecs    = getattr(s, "codecs", []) or []
    has_video = getattr(s, "includes_video_track", False)
    if has_video:
        return None
    codec_str = " ".join(codecs).lower()
    if "opus"   in codec_str: return "opus"
    if "mp4a"   in codec_str or mime == "audio/mp4": return "m4a"
    if "mp3"    in codec_str or "audio/mpeg" in mime: return "mp3"
    if "vorbis" in codec_str: return "ogg"
    return getattr(s, "subtype", None)


# return the correct file extension for audio-only streams
def _audio_extension(s) -> str:
    ext_map = {"m4a": "m4a", "opus": "webm", "mp3": "mp3", "ogg": "ogg"}
    return ext_map.get(_audio_format(s) or "", getattr(s, "subtype", "mp4"))


def stream_dict(s) -> dict:
    # use getattr to prevent crashes on missing attributes (e.g. audio streams lack 'fps')
    codecs    = getattr(s, "codecs", []) or []
    has_video = getattr(s, "includes_video_track", False)
    has_audio = getattr(s, "includes_audio_track", False)
    # for progressive streams codecs = [vid, aud]; for single-track just [codec]
    vid_codec = codecs[0] if codecs and has_video else None
    aud_codec = (codecs[1] if len(codecs) > 1 else codecs[0]) if has_audio and not has_video else \
                (codecs[1] if len(codecs) > 1 else None)
    return {
        "itag":               getattr(s, "itag", None),
        "mime_type":          getattr(s, "mime_type", None),
        "audio_format":       _audio_format(s),   # "m4a" | "opus" | "mp3" | None
        "type":               getattr(s, "type", None),
        "subtype":            getattr(s, "subtype", None),
        "resolution":         getattr(s, "resolution", None),
        "width":              getattr(s, "width", None),
        "height":             getattr(s, "height", None),
        "fps":                getattr(s, "fps", None),
        "abr":                getattr(s, "abr", None),
        "codecs":             codecs,
        "video_codec":        vid_codec,
        "audio_codec":        aud_codec,
        "is_progressive":     getattr(s, "is_progressive", False),
        "is_adaptive":        getattr(s, "is_adaptive", False),
        "is_otf":             getattr(s, "is_otf", False),
        "includes_video":     has_video,
        "includes_audio":     has_audio,
        "filesize_bytes":     getattr(s, "filesize", None),
        "filesize_kb":        getattr(s, "filesize_kb", None),
        "filesize_mb":        getattr(s, "filesize_mb", None),
        "filesize_gb":        getattr(s, "filesize_gb", None),
        "default_filename":   getattr(s, "default_filename", None),
        # true if this exact resolution needs ffmpeg merge (no progressive match)
        "requires_merge":     has_video and not has_audio,
    }


def yt_meta(yt: YouTube) -> dict:
    chapters = []
    try:
        chapters = [
            {
                "title":         c.title,
                "start_seconds": c.start_seconds,
                "start_label":   getattr(c, "start_label", None),
                "thumbnails":    [
                    {"width": t.width, "height": t.height, "url": t.url}
                    for t in getattr(c, "thumbnails", [])
                ],
            }
            for c in yt.chapters
        ] if yt.chapters else []
    except Exception:
        pass

    key_moments = []
    try:
        key_moments = [
            {"title": k.title, "start_seconds": k.start_seconds}
            for k in yt.key_moments
        ] if yt.key_moments else []
    except Exception:
        pass

    replayed_heatmap = []
    try:
        replayed_heatmap = yt.replayed_heatmap or []
    except Exception:
        pass

    return {
        "video_id":          yt.video_id,
        "title":              yt.title,
        "author":             yt.author,
        "channel_id":         yt.channel_id,
        "channel_url":        yt.channel_url,
        "duration_seconds":   yt.length,
        "views":              yt.views,
        "likes":              yt.likes,
        "rating":             yt.rating,
        "description":        yt.description,
        "keywords":           yt.keywords,
        "thumbnail_url":      yt.thumbnail_url,
        "publish_date":       str(yt.publish_date),
        "watch_url":          yt.watch_url,
        "embed_url":          yt.embed_url,
        "age_restricted":     yt.age_restricted,
        "chapters":           chapters,
        "key_moments":        key_moments,
        "replayed_heatmap":   replayed_heatmap,
    }


def _send_stream(stream, yt) -> object:
    buf = io.BytesIO()
    stream.stream_to_buffer(buf)
    buf.seek(0)
    has_video = getattr(stream, "includes_video_track", False)
    if not has_video:
        ext = _audio_extension(stream)
        mime_map = {"m4a": "audio/mp4", "webm": "audio/webm", "mp3": "audio/mpeg", "ogg": "audio/ogg"}
        mime = mime_map.get(ext, stream.mime_type)
    else:
        ext  = stream.subtype
        mime = stream.mime_type
    fname = f"{safe_filename(yt.title)}.{ext}"
    return send_file(buf, as_attachment=True, download_name=fname, mimetype=mime)


def _send_file_path(path: str, title: str, ext: str, mime: str) -> object:
    fname = f"{safe_filename(title)}.{ext}"
    return send_file(path, as_attachment=True, download_name=fname, mimetype=mime)


# download video-only + audio-only separately, then mux with ffmpeg
# stream copy, no re-encode. returns path to merged mp4 (caller must clean up)
def _merge_video_audio(video_stream, audio_stream, yt) -> str:
    if not _FFMPEG_AVAILABLE:
        raise RuntimeError(
            "ffmpeg not found on server. install it (apt install ffmpeg) "
            "to enable merging high-resolution video with audio."
        )

    workdir = tempfile.mkdtemp(prefix="pytubedl_")
    vid_path = os.path.join(workdir, f"v_{uuid.uuid4().hex}.{video_stream.subtype}")
    aud_path = os.path.join(workdir, f"a_{uuid.uuid4().hex}.{_audio_extension(audio_stream)}")
    out_path = os.path.join(workdir, f"out_{uuid.uuid4().hex}.mp4")

    video_stream.download(output_path=workdir, filename=os.path.basename(vid_path), skip_existing=False)
    audio_stream.download(output_path=workdir, filename=os.path.basename(aud_path), skip_existing=False)

    # -c:v copy keeps video as-is (fast, no quality loss)
    # -c:a aac re-encodes audio only if needed for mp4 container compatibility
    # (opus/webm audio can't be copied directly into an mp4 container)
    audio_codec = "copy"
    if _audio_format(audio_stream) == "opus":
        audio_codec = "aac"

    cmd = [
        _FFMPEG_BIN, "-y",
        "-i", vid_path,
        "-i", aud_path,
        "-c:v", "copy",
        "-c:a", audio_codec,
        "-map", "0:v:0",
        "-map", "1:a:0",
        "-movflags", "+faststart",
        out_path,
    ]
    result = subprocess.run(cmd, capture_output=True, text=True, timeout=600)
    if result.returncode != 0 or not os.path.exists(out_path):
        shutil.rmtree(workdir, ignore_errors=True)
        raise RuntimeError(f"ffmpeg merge failed: {result.stderr[-800:]}")

    # clean up the raw source files, keep only the merged output
    try:
        os.remove(vid_path)
        os.remove(aud_path)
    except OSError:
        pass

    return out_path


# return the name of the first missing query parameter, or none
def _require(keys: list) -> str | None:
    for k in keys:
        if not request.args.get(k):
            return k
    return None


# convert a query parameter to bool (default none if not provided)
def _arg_bool(key, default=None):
    v = request.args.get(key)
    if v is None:
        return default
    return v.lower() == "true"


# health
@app.route("/")
def health():
    token_exists = os.path.exists(_TOKEN_FILE)
    return jsonify({
        "status":         "ok",
        "version":        "3.0.0",
        "auth":           "oauth",
        "token_cached":   token_exists,
        "ffmpeg_available": _FFMPEG_AVAILABLE,
        "message": "API ready." if token_exists else "WARNING: OAuth token not found. Run login script on VPS first.",
    })


# video info (full metadata)
@app.route("/api/info")
def info():
    url = request.args.get("url")
    if not url:
        return jsonify({"error": "Missing 'url'"}), 400
    if not valid_video_url(url):
        return jsonify({"error": "Invalid YouTube URL"}), 400
    try:
        yt = make_yt(url)
        streams = yt.streams

        captions_list = [
            {"code": cap.code, "name": cap.name}
            for cap in yt.captions
        ]

        dubbed = []
        try:
            dubbed = [stream_dict(s) for s in streams.get_extra_audio_track()]
        except Exception:
            pass

        progressive_streams = streams.filter(progressive=True).order_by("resolution").desc()
        video_only_streams  = streams.filter(adaptive=True, only_video=True).order_by("resolution").desc()

        # build a list of every resolution actually downloadable, noting whether
        # it comes as progressive (audio baked in) or needs an ffmpeg merge
        available_resolutions = []
        seen_res = set()
        for s in list(progressive_streams) + list(video_only_streams):
            res = s.resolution
            if not res or res in seen_res:
                continue
            seen_res.add(res)
            available_resolutions.append({
                "resolution":     res,
                "is_progressive": bool(s.is_progressive),
                "requires_merge": not bool(s.is_progressive),
                "itag":           s.itag,
            })
        available_resolutions.sort(
            key=lambda r: RESOLUTION_ORDER.index(r["resolution"]) if r["resolution"] in RESOLUTION_ORDER else -1,
            reverse=True,
        )

        return jsonify({
            **yt_meta(yt),
            "captions_available":   captions_list,
            "available_resolutions": available_resolutions,
            "ffmpeg_available":     _FFMPEG_AVAILABLE,
            "streams": {
                "progressive": [stream_dict(s) for s in progressive_streams],
                "video_only":  [stream_dict(s) for s in video_only_streams],
                "audio_only":  [stream_dict(s) for s in
                    streams.filter(only_audio=True).order_by("abr").desc()],
                "dubbed": dubbed,
            },
        })
    except BotDetection as e:
        return jsonify({"error": "Bot detection triggered.", "detail": str(e)}), 503
    except PoTokenRequired as e:
        return jsonify({"error": "PO Token required.", "detail": str(e)}), 503
    except AgeRestrictedError as e:
        return jsonify({"error": "Video is age-restricted.", "detail": str(e)}), 403
    except VideoPrivate as e:
        return jsonify({"error": "Video is private.", "detail": str(e)}), 403
    except VideoRegionBlocked as e:
        return jsonify({"error": "Video is region-blocked.", "detail": str(e)}), 403
    except MembersOnly as e:
        return jsonify({"error": "Video is members-only.", "detail": str(e)}), 403
    except LiveStreamError as e:
        return jsonify({"error": "Live stream error.", "detail": str(e)}), 400
    except VideoUnavailable as e:
        return jsonify({"error": "Video unavailable.", "detail": str(e)}), 404
    except Exception as e:
        return jsonify({"error": str(e)}), 500


# chapters / key moments
@app.route("/api/chapters")
def chapters():
    url = request.args.get("url")
    if not url:
        return jsonify({"error": "Missing 'url'"}), 400
    if not valid_video_url(url):
        return jsonify({"error": "Invalid YouTube URL"}), 400
    try:
        yt = make_yt(url)
        result = [
            {
                "title":         c.title,
                "start_seconds": c.start_seconds,
                "start_label":   getattr(c, "start_label", None),
                "thumbnails":    [
                    {"width": t.width, "height": t.height, "url": t.url}
                    for t in getattr(c, "thumbnails", [])
                ],
            }
            for c in yt.chapters
        ]
        return jsonify({"count": len(result), "chapters": result})
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/api/key-moments")
def key_moments():
    url = request.args.get("url")
    if not url:
        return jsonify({"error": "Missing 'url'"}), 400
    if not valid_video_url(url):
        return jsonify({"error": "Invalid YouTube URL"}), 400
    try:
        yt = make_yt(url)
        moments = [
            {"title": k.title, "start_seconds": k.start_seconds}
            for k in yt.key_moments
        ]
        heatmap = []
        try:
            heatmap = yt.replayed_heatmap or []
        except Exception:
            pass
        return jsonify({
            "count":           len(moments),
            "key_moments":     moments,
            "replayed_heatmap": heatmap,
        })
    except Exception as e:
        return jsonify({"error": str(e)}), 500


# stream listing
@app.route("/api/streams")
def streams_list():
    url = request.args.get("url")
    if not url:
        return jsonify({"error": "Missing 'url'"}), 400
    if not valid_video_url(url):
        return jsonify({"error": "Invalid YouTube URL"}), 400

    try:
        yt = make_yt(url)
        s = yt.streams

        stream_type = request.args.get("type", "all")
        if stream_type == "progressive":
            s = s.filter(progressive=True)
        elif stream_type == "audio":
            s = s.filter(only_audio=True)
        elif stream_type == "video":
            s = s.filter(only_video=True)
        elif stream_type == "adaptive":
            s = s.filter(adaptive=True)

        filter_kwargs = {}
        for key in ["resolution", "fps", "mime_type", "subtype",
                    "video_codec", "audio_codec", "abr"]:
            val = request.args.get(key)
            if val:
                filter_kwargs[key] = int(val) if key == "fps" else val

        for bool_key in ["progressive", "adaptive", "only_audio", "only_video", "is_dash", "is_drc"]:
            val = _arg_bool(bool_key)
            if val is not None:
                filter_kwargs[bool_key] = val

        if filter_kwargs:
            s = s.filter(**filter_kwargs)

        order_by = request.args.get("order_by")
        if order_by:
            s = s.order_by(order_by)
            if _arg_bool("desc", True):
                s = s.desc()

        return jsonify({
            "count": len(s),
            "streams": [stream_dict(x) for x in s],
        })
    except Exception as e:
        return jsonify({"error": str(e)}), 500


# docs
@app.route('/docs')
def docs():
    return send_file('index.html')


# downloads
@app.route("/api/download")
def download_by_itag():
    """
    download by exact itag. if the itag points to a video-only (adaptive)
    stream, automatically merges it with the best available audio using
    ffmpeg — so even 1080p/4k itags come out with sound.
    """
    url = request.args.get("url")
    itag = request.args.get("itag")
    if not url or not itag:
        return jsonify({"error": "Both 'url' and 'itag' required"}), 400
    if not valid_video_url(url):
        return jsonify({"error": "Invalid YouTube URL"}), 400
    try:
        itag = int(itag)
    except ValueError:
        return jsonify({"error": "'itag' must be an integer"}), 400

    merge_audio = _arg_bool("merge_audio", True)  # default: auto-merge if needed

    try:
        yt = make_yt(url)
        stream = yt.streams.get_by_itag(itag)
        if not stream:
            return jsonify({"error": f"No stream for itag {itag}"}), 404

        needs_merge = stream.includes_video_track and not stream.includes_audio_track
        if needs_merge and merge_audio:
            if not _FFMPEG_AVAILABLE:
                return jsonify({
                    "error": "This itag is video-only and requires ffmpeg to merge audio.",
                    "detail": "ffmpeg is not installed on the server. Install it or "
                              "pass merge_audio=false to download video-only (no sound).",
                }), 503
            audio_stream = yt.streams.filter(only_audio=True).order_by("abr").desc().first()
            if not audio_stream:
                return jsonify({"error": "No audio stream available to merge"}), 404
            merged_path = _merge_video_audio(stream, audio_stream, yt)
            try:
                return _send_file_path(merged_path, yt.title, "mp4", "video/mp4")
            finally:
                shutil.rmtree(os.path.dirname(merged_path), ignore_errors=True)

        return _send_stream(stream, yt)
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/api/download/highest")
def download_highest():
    """
    download the highest resolution available.
    by default uses progressive=false so true highest quality (e.g. 4k) is
    reachable — video-only + best audio are merged automatically via ffmpeg.
    pass progressive=true to force the old behavior (max ~360-720p, no merge).
    """
    url = request.args.get("url")
    if not url:
        return jsonify({"error": "Missing 'url'"}), 400
    if not valid_video_url(url):
        return jsonify({"error": "Invalid YouTube URL"}), 400
    progressive = _arg_bool("progressive", False)
    mime_type = request.args.get("mime_type")
    try:
        yt = make_yt(url)

        if progressive:
            stream = yt.streams.get_highest_resolution(progressive=True, mime_type=mime_type)
            if not stream:
                return jsonify({"error": "No progressive stream found"}), 404
            return _send_stream(stream, yt)

        # non-progressive: get best video-only stream, merge with best audio
        video_only = yt.streams.filter(adaptive=True, only_video=True)
        if mime_type:
            video_only = video_only.filter(mime_type=mime_type)
        video_stream = video_only.order_by("resolution").desc().first()

        if not video_stream:
            # fallback to progressive if no adaptive video found
            stream = yt.streams.get_highest_resolution(progressive=True)
            if not stream:
                return jsonify({"error": "No stream found"}), 404
            return _send_stream(stream, yt)

        if not _FFMPEG_AVAILABLE:
            # fall back to best progressive instead of failing outright
            stream = yt.streams.get_highest_resolution(progressive=True)
            if stream:
                return _send_stream(stream, yt)
            return jsonify({
                "error": "ffmpeg not available and no progressive fallback exists.",
            }), 503

        audio_stream = yt.streams.filter(only_audio=True).order_by("abr").desc().first()
        if not audio_stream:
            return jsonify({"error": "No audio stream available to merge"}), 404

        merged_path = _merge_video_audio(video_stream, audio_stream, yt)
        try:
            return _send_file_path(merged_path, yt.title, "mp4", "video/mp4")
        finally:
            shutil.rmtree(os.path.dirname(merged_path), ignore_errors=True)
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/api/download/lowest")
def download_lowest():
    url = request.args.get("url")
    if not url:
        return jsonify({"error": "Missing 'url'"}), 400
    if not valid_video_url(url):
        return jsonify({"error": "Invalid YouTube URL"}), 400
    progressive = _arg_bool("progressive", True)
    try:
        yt = make_yt(url)
        stream = yt.streams.get_lowest_resolution(progressive=progressive)
        if not stream:
            return jsonify({"error": "No stream found"}), 404
        return _send_stream(stream, yt)
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/api/download/resolution")
def download_by_resolution():
    """
    download at a specific resolution (e.g. 720p, 1080p, 4k).

    pytubefix's native get_by_resolution() only matches progressive streams,
    which youtube caps at 360p (sometimes 720p for older videos). for any
    resolution above that, this endpoint automatically:
      1. finds the matching video-only (adaptive) stream at that resolution
      2. finds the best available audio-only stream
      3. merges them together with ffmpeg (stream copy — fast, no re-encode)

    this means requesting 720p/1080p/1440p/4k will always come back with audio,
    not a silent video file. pass merge_audio=false to skip this and get the
    raw video-only file instead (useful if you want to mux it yourself).
    """
    url = request.args.get("url")
    res = request.args.get("res")
    if not url or not res:
        return jsonify({"error": "Both 'url' and 'res' required"}), 400
    if not valid_video_url(url):
        return jsonify({"error": "Invalid YouTube URL"}), 400

    merge_audio = _arg_bool("merge_audio", True)

    try:
        yt = make_yt(url)

        # 1. try progressive first — already has audio baked in, fastest path
        stream = yt.streams.get_by_resolution(res)
        if stream:
            return _send_stream(stream, yt)

        # 2. fall back to adaptive video-only stream at that exact resolution
        video_stream = (
            yt.streams
            .filter(adaptive=True, only_video=True, resolution=res)
            .order_by("fps")
            .desc()
            .first()
        )
        if not video_stream:
            available = sorted({
                s.resolution for s in yt.streams.filter(adaptive=True, only_video=True)
                if s.resolution
            }, key=lambda r: RESOLUTION_ORDER.index(r) if r in RESOLUTION_ORDER else -1)
            return jsonify({
                "error": f"No stream found at resolution '{res}'",
                "available_resolutions": available,
            }), 404

        if not merge_audio:
            # caller explicitly wants the raw video-only file, no audio
            return _send_stream(video_stream, yt)

        if not _FFMPEG_AVAILABLE:
            return jsonify({
                "error": f"'{res}' requires merging video with audio (ffmpeg), "
                         f"but ffmpeg is not installed on the server.",
                "detail": "Install ffmpeg (apt install ffmpeg) or pass merge_audio=false "
                          "to download the video-only file without sound.",
            }), 503

        audio_stream = yt.streams.filter(only_audio=True).order_by("abr").desc().first()
        if not audio_stream:
            return jsonify({"error": "No audio stream available to merge"}), 404

        merged_path = _merge_video_audio(video_stream, audio_stream, yt)
        try:
            return _send_file_path(merged_path, yt.title, "mp4", "video/mp4")
        finally:
            shutil.rmtree(os.path.dirname(merged_path), ignore_errors=True)
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/api/download/audio")
def download_audio():
    url = request.args.get("url")
    if not url:
        return jsonify({"error": "Missing 'url'"}), 400
    if not valid_video_url(url):
        return jsonify({"error": "Invalid YouTube URL"}), 400
    # accept human-friendly labels: m4a → mp4, opus/webm → webm
    fmt = request.args.get("format", "m4a").lower().strip()
    subtype_map = {"m4a": "mp4", "mp4": "mp4", "opus": "webm", "webm": "webm"}
    yt_subtype = subtype_map.get(fmt, "mp4")
    try:
        yt = make_yt(url)
        stream = yt.streams.get_audio_only(subtype=yt_subtype)
        if not stream:
            # fallback: pick any audio-only stream
            stream = yt.streams.filter(only_audio=True).order_by("abr").last()
        if not stream:
            return jsonify({"error": f"No audio stream found for format '{fmt}'"}), 404
        return _send_stream(stream, yt)
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/api/download/dubbed")
def download_dubbed_audio():
    """download a dubbed (alternate-language) audio track by name."""
    url = request.args.get("url")
    name = request.args.get("name")
    if not url or not name:
        return jsonify({"error": "Both 'url' and 'name' required"}), 400
    if not valid_video_url(url):
        return jsonify({"error": "Invalid YouTube URL"}), 400
    try:
        yt = make_yt(url)
        track = yt.streams.get_extra_audio_track_by_name(name)
        if not track:
            return jsonify({"error": f"No dubbed audio track named '{name}'"}), 404
        stream = track.first() if hasattr(track, "first") else track
        if not stream:
            return jsonify({"error": f"No dubbed audio track named '{name}'"}), 404
        return _send_stream(stream, yt)
    except Exception as e:
        return jsonify({"error": str(e)}), 500


# captions
@app.route("/api/captions")
def list_captions():
    url = request.args.get("url")
    if not url:
        return jsonify({"error": "Missing 'url'"}), 400
    if not valid_video_url(url):
        return jsonify({"error": "Invalid YouTube URL"}), 400
    try:
        yt = make_yt(url)
        caps = [
            {"code": c.code, "name": c.name}
            for c in yt.captions
        ]
        return jsonify({"count": len(caps), "captions": caps})
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/api/captions/get")
def get_caption():
    url = request.args.get("url")
    lang_code = request.args.get("lang_code", "en")
    fmt = request.args.get("format", "srt")
    if not url:
        return jsonify({"error": "Missing 'url'"}), 400
    if not valid_video_url(url):
        return jsonify({"error": "Invalid YouTube URL"}), 400
    if fmt not in ("srt", "xml", "txt"):
        return jsonify({"error": "format must be srt, xml, or txt"}), 400
    try:
        yt = make_yt(url)
        cap = yt.captions.get_by_language_code(lang_code)
        if not cap:
            return jsonify({"error": f"No caption for lang_code '{lang_code}'"}), 404

        if fmt == "xml":
            content = cap.xml_captions
            mime = "application/xml"
        elif fmt == "txt":
            content = cap.generate_txt_captions()
            mime = "text/plain"
        else:
            content = cap.generate_srt_captions()
            mime = "text/plain"

        buf = io.BytesIO(content.encode("utf-8"))
        fname = f"{safe_filename(yt.title)}_{lang_code}.{fmt}"
        return send_file(buf, as_attachment=True, download_name=fname, mimetype=mime)
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/api/captions/json")
def get_caption_json():
    """return the parsed json caption track (timed segments) instead of a file."""
    url = request.args.get("url")
    lang_code = request.args.get("lang_code", "en")
    if not url:
        return jsonify({"error": "Missing 'url'"}), 400
    if not valid_video_url(url):
        return jsonify({"error": "Invalid YouTube URL"}), 400
    try:
        yt = make_yt(url)
        cap = yt.captions.get_by_language_code(lang_code)
        if not cap:
            return jsonify({"error": f"No caption for lang_code '{lang_code}'"}), 404
        return jsonify({"lang_code": lang_code, "captions": cap.json_captions})
    except Exception as e:
        return jsonify({"error": str(e)}), 500


# playlist
@app.route("/api/playlist")
def playlist():
    url = request.args.get("url")
    limit = min(int(request.args.get("limit", 50)), 200)
    if not url:
        return jsonify({"error": "Missing 'url'"}), 400
    if not valid_playlist_url(url):
        return jsonify({"error": "Invalid playlist URL"}), 400
    try:
        pl = Playlist(url)
        items = []
        for yt in list(pl.videos)[:limit]:
            try:
                items.append({
                    "video_id": yt.video_id,
                    "title":    yt.title,
                    "author":   yt.author,
                    "watch_url":yt.watch_url,
                })
            except Exception:
                continue
        return jsonify({
            "playlist_id":   pl.playlist_id,
            "title":         pl.title,
            "owner":         pl.owner,
            "owner_id":      pl.owner_id,
            "owner_url":     pl.owner_url,
            "length":        pl.length,
            "views":         pl.views,
            "last_updated":  str(pl.last_updated),
            "returned":      len(items),
            "videos":        items,
        })
    except Exception as e:
        return jsonify({"error": str(e)}), 500


# channel
@app.route("/api/channel")
def channel():
    url = request.args.get("url")
    content = request.args.get("content", "videos")
    limit = min(int(request.args.get("limit", 20)), 100)
    if not url:
        return jsonify({"error": "Missing 'url'"}), 400
    try:
        ch = Channel(url)
        if content == "shorts":
            src = ch.shorts
        elif content == "live":
            src = ch.live
        elif content == "releases":
            src = ch.releases
        else:
            src = ch.videos

        items = []
        for item in list(src)[:limit]:
            try:
                # 'releases' yields Playlist objects, others yield YouTube objects
                if hasattr(item, "video_id"):
                    items.append({
                        "video_id":  item.video_id,
                        "title":     item.title,
                        "watch_url": item.watch_url,
                    })
                else:
                    items.append({
                        "playlist_id":  item.playlist_id,
                        "title":        item.title,
                        "playlist_url": item.playlist_url,
                    })
            except Exception:
                continue

        return jsonify({
            "channel_id":    ch.channel_id,
            "channel_name":  ch.channel_name,
            "channel_url":   ch.channel_url,
            "vanity_url":    ch.vanity_url,
            "description":   ch.description,
            "thumbnail_url": ch.thumbnail_url,
            "views":         ch.views,
            "last_updated":  ch.last_updated,
            "approx_length": ch.length,
            "content_type":  content,
            "returned":      len(items),
            "items":         items,
        })
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/api/channel/playlists")
def channel_playlists():
    url = request.args.get("url")
    limit = min(int(request.args.get("limit", 20)), 50)
    if not url:
        return jsonify({"error": "Missing 'url'"}), 400
    try:
        ch = Channel(url)
        result = []
        for pl in list(ch.playlists)[:limit]:
            try:
                result.append({
                    "playlist_id":  pl.playlist_id,
                    "title":        pl.title,
                    "owner":        pl.owner,
                    "total_videos": pl.length,
                    "playlist_url": pl.playlist_url,
                })
            except Exception:
                continue
        return jsonify({
            "channel_name": ch.channel_name,
            "returned":     len(result),
            "playlists":    result,
        })
    except Exception as e:
        return jsonify({"error": str(e)}), 500


# search
@app.route("/api/search")
def search():
    q = request.args.get("q")
    limit = min(int(request.args.get("limit", 10)), 20)
    search_type = request.args.get("type", "videos")  # videos | shorts | playlists | all
    if not q:
        return jsonify({"error": "Missing 'q'"}), 400
    try:
        s = Search(q)

        if search_type == "shorts":
            source = s.shorts or []
        elif search_type == "playlists":
            source = s.playlist or []
        elif search_type == "all":
            source = s.all or []
        else:
            source = s.videos or []

        results = []
        for item in source[:limit]:
            try:
                if hasattr(item, "video_id"):
                    results.append({
                        "result_type":     "video",
                        "video_id":        item.video_id,
                        "title":           item.title,
                        "author":          item.author,
                        "duration_seconds":item.length,
                        "views":           item.views,
                        "thumbnail_url":   item.thumbnail_url,
                        "watch_url":       item.watch_url,
                    })
                elif hasattr(item, "playlist_id"):
                    results.append({
                        "result_type":  "playlist",
                        "playlist_id":  item.playlist_id,
                        "title":        item.title,
                        "playlist_url": item.playlist_url,
                    })
                elif hasattr(item, "channel_id"):
                    results.append({
                        "result_type":  "channel",
                        "channel_id":   item.channel_id,
                        "channel_name": getattr(item, "channel_name", None),
                        "channel_url":  item.channel_url,
                    })
            except Exception:
                continue

        suggestions = []
        try:
            suggestions = s.completion_suggestions or []
        except Exception:
            pass

        return jsonify({
            "query":       q,
            "type":        search_type,
            "suggestions": suggestions,
            "count":       len(results),
            "results":     results,
        })
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/api/search/next")
def search_next():
    """get the next page of search results (continuation)."""
    q = request.args.get("q")
    if not q:
        return jsonify({"error": "Missing 'q'"}), 400
    try:
        s = Search(q)
        _ = s.videos  # trigger first page
        more = s.get_next_results()
        results = []
        for yt in (s.videos or []):
            try:
                results.append({
                    "video_id":  yt.video_id,
                    "title":     yt.title,
                    "author":    yt.author,
                    "watch_url": yt.watch_url,
                })
            except Exception:
                continue
        return jsonify({"query": q, "count": len(results), "results": results})
    except Exception as e:
        return jsonify({"error": str(e)}), 500


if __name__ == "__main__":
    app.run(debug=True, host='0.0.0.0', port=24698)
