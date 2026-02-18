import os
import json
import time
import requests
from typing import Dict, List
from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build

STATE_FILE = "state.json"
SPOTIFY_TOKEN_URL = "https://accounts.spotify.com/api/token"
YOUTUBE_SCOPES = ["https://www.googleapis.com/auth/youtube"]

# -------------------------------------------------
# LOG
# -------------------------------------------------
def log(msg):
    print(f"[SYNC] {msg}", flush=True)


# -------------------------------------------------
# STATE
# -------------------------------------------------
def load_state():
    if not os.path.exists(STATE_FILE):
        return {"map": {}}
    with open(STATE_FILE, "r", encoding="utf-8") as f:
        return json.load(f)


def save_state(state):
    with open(STATE_FILE, "w", encoding="utf-8") as f:
        json.dump(state, f, indent=2, ensure_ascii=False)


# -------------------------------------------------
# SPOTIFY
# -------------------------------------------------
def spotify_access_token():
    data = {
        "grant_type": "refresh_token",
        "refresh_token": os.environ["SPOTIFY_REFRESH_TOKEN"],
        "client_id": os.environ["SPOTIFY_CLIENT_ID"],
        "client_secret": os.environ["SPOTIFY_CLIENT_SECRET"],
    }

    r = requests.post(SPOTIFY_TOKEN_URL, data=data)
    r.raise_for_status()
    return r.json()["access_token"]


def spotify_tracks(playlist_id):
    token = spotify_access_token()
    headers = {"Authorization": f"Bearer {token}"}

    tracks = []
    offset = 0

    while True:
        url = f"https://api.spotify.com/v1/playlists/{playlist_id}/tracks"
        params = {"limit": 100, "offset": offset}

        r = requests.get(url, headers=headers, params=params)
        r.raise_for_status()
        data = r.json()

        for item in data["items"]:
            track = item["track"]
            if not track or track["is_local"]:
                continue

            tracks.append({
                "id": track["id"],
                "query": f"{track['artists'][0]['name']} - {track['name']} official audio"
            })

        if data["next"] is None:
            break

        offset += 100
        time.sleep(0.1)

    return tracks


# -------------------------------------------------
# YOUTUBE
# -------------------------------------------------
def youtube_client():
    client_json = json.loads(os.environ["YOUTUBE_CLIENT_JSON"])
    refresh_token = os.environ["YOUTUBE_REFRESH_TOKEN"]

    installed = client_json.get("installed") or client_json.get("web")

    creds = Credentials(
        token=None,
        refresh_token=refresh_token,
        token_uri=installed["token_uri"],
        client_id=installed["client_id"],
        client_secret=installed["client_secret"],
        scopes=YOUTUBE_SCOPES,
    )

    return build("youtube", "v3", credentials=creds)


def youtube_playlist_items(yt, playlist_id):
    items = []
    page_token = None

    while True:
        req = yt.playlistItems().list(
            part="snippet,contentDetails",
            playlistId=playlist_id,
            maxResults=50,
            pageToken=page_token
        )
        res = req.execute()

        for item in res["items"]:
            items.append({
                "playlist_item_id": item["id"],
                "video_id": item["contentDetails"]["videoId"],
                "position": item["snippet"]["position"]
            })

        page_token = res.get("nextPageToken")
        if not page_token:
            break

        time.sleep(0.1)

    return items


def youtube_search(yt, query):
    req = yt.search().list(
        part="snippet",
        q=query,
        type="video",
        maxResults=1
    )
    res = req.execute()

    if not res["items"]:
        raise Exception("YouTube search failed")

    return res["items"][0]["id"]["videoId"]


def youtube_insert(yt, playlist_id, video_id, position):
    body = {
        "snippet": {
            "playlistId": playlist_id,
            "position": position,
            "resourceId": {
                "kind": "youtube#video",
                "videoId": video_id
            }
        }
    }

    res = yt.playlistItems().insert(
        part="snippet",
        body=body
    ).execute()

    return res["id"]


def youtube_move(yt, playlist_id, playlist_item_id, video_id, position):
    body = {
        "id": playlist_item_id,
        "snippet": {
            "playlistId": playlist_id,
            "position": position,
            "resourceId": {
                "kind": "youtube#video",
                "videoId": video_id
            }
        }
    }

    yt.playlistItems().update(
        part="snippet",
        body=body
    ).execute()


# -------------------------------------------------
# MAIN
# -------------------------------------------------
def main():
    spotify_playlist_id = os.environ["SPOTIFY_PLAYLIST_ID"]
    youtube_playlist_id = os.environ["YOUTUBE_PLAYLIST_ID"]

    state = load_state()

    log("Spotify çekiliyor...")
    sp_tracks = spotify_tracks(spotify_playlist_id)
    log(f"Spotify şarkı sayısı: {len(sp_tracks)}")

    yt = youtube_client()

    log("YouTube çekiliyor...")
    yt_items = youtube_playlist_items(yt, youtube_playlist_id)

    yt_lookup = {item["playlist_item_id"]: item for item in yt_items}

    changed = False

    # Yeni şarkılar
    for index, track in enumerate(sp_tracks):
        sid = track["id"]

        if sid not in state["map"]:
            log(f"Yeni şarkı bulundu: {track['query']}")
            video_id = youtube_search(yt, track["query"])
            pid = youtube_insert(yt, youtube_playlist_id, video_id, index)

            state["map"][sid] = {
                "video_id": video_id,
                "playlist_item_id": pid
            }

            changed = True
            time.sleep(0.2)

    # Reorder
    yt_items = youtube_playlist_items(yt, youtube_playlist_id)
    yt_positions = {item["playlist_item_id"]: item["position"] for item in yt_items}

    for index, track in enumerate(sp_tracks):
        sid = track["id"]
        mapping = state["map"].get(sid)

        if not mapping:
            continue

        pid = mapping["playlist_item_id"]
        video_id = mapping["video_id"]

        current_pos = yt_positions.get(pid)

        if current_pos is None:
            continue

        if current_pos != index:
            log(f"Sıra değiştiriliyor: {sid} {current_pos} → {index}")
            youtube_move(yt, youtube_playlist_id, pid, video_id, index)
            changed = True
            time.sleep(0.1)

    if changed:
        save_state(state)
        log("State güncellendi.")
    else:
        log("Değişiklik yok.")

    log("Bitti.")


if __name__ == "__main__":
    main()
