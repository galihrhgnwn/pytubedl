#!/bin/bash
set -e

echo "=== pytubedl setup ==="

# check/install ffmpeg (needed to merge video+audio for resolutions >360p)
echo "[1/4] checking ffmpeg..."
if ! command -v ffmpeg &> /dev/null; then
    echo "  ffmpeg not found, installing..."
    if command -v apt-get &> /dev/null; then
        sudo apt-get update -qq && sudo apt-get install -y ffmpeg -qq
    elif command -v yum &> /dev/null; then
        sudo yum install -y ffmpeg
    else
        echo "  warning: could not auto-install ffmpeg. install it manually,"
        echo "  otherwise downloads above 360p/720p will fail to include audio."
    fi
else
    echo "  ffmpeg found: $(ffmpeg -version | head -n1)"
fi

# install dependencies
echo "[2/4] installing dependencies..."
pip install -r requirements.txt --break-system-packages -q

# create tokens folder
echo "[3/4] creating tokens directory..."
mkdir -p tokens

# oauth login
echo "[4/4] starting oauth login..."
echo ""
echo "a url and device code will appear."
echo "open the url in your browser, enter the code, and log in with your google account."
echo ""

python3 -c "
from pytubefix import YouTube
yt = YouTube(
    'https://youtu.be/dQw4w9WgXcQ',
    use_oauth=True,
    allow_oauth_cache=True,
    token_file='./tokens/oauth.json'
)
_ = yt.streams
print('login successful, token saved to ./tokens/oauth.json')
"

echo ""
echo "=== setup complete! ==="
echo "run the server with:"
echo "  python3 app.py"
echo ""
echo "or use gunicorn (production):"
echo "  gunicorn -w 4 -b 0.0.0.0:9715 app:app"
