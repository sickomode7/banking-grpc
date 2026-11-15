# Videos

Four silent screen recordings, one per deliverable, plus the cue sheet to
voice them.

| file | covers | length |
|---|---|---|
| `01-proto.mp4` | `banking.proto` — services, messages, codegen | ~3:00 |
| `02-server.mp4` | `account_server.py` + `transaction_server.py` | ~5:15 |
| `03-client.mp4` | `client.py` + the test suite | ~2:05 |
| `04-demo.mp4` | live run: transfer, history, five rejected calls | ~2:15 |

Each has a matching `.srt`. `VOICEOVER.md` is the read-aloud script with the
start time of every cue, in the same order as the video.

Everything on screen is real: terminal frames come from commands executed
against the running services while the video was rendering, and code frames are
the actual files. Rebuild with:

```bash
python make_videos.py            # all four (~10 min)
python make_videos.py --only 4   # just the demo
python make_videos.py --preview  # a few PNG stills, no encoding
```

## Recording the voice-over

1. Play the video once, out loud, with `VOICEOVER.md` open. Do not record yet.
2. Record audio only (Audacity, or `pw-record` from the terminal). Watch the
   video while you speak; the holds are sized for that paragraph at a calm pace.
3. Mux without re-encoding the picture:

```bash
ffmpeg -i 01-proto.mp4 -i 01-proto-voice.m4a \
       -c:v copy -c:a aac -b:a 192k -shortest 01-proto-final.mp4
```

If your audio is shorter than the video, `-shortest` keeps the video length and
the tail stays silent; if it is longer, trim the audio first rather than letting
the last words get cut.

## Optional subtitles

```bash
ffmpeg -i 01-proto-final.mp4 -vf "subtitles=01-proto.srt" -c:a copy 01-proto-subtitled.mp4
```

## Before you submit

- Watch each final file end to end once. The most common failure is a muted
  microphone that looked fine in the meter.
- `ffprobe -v error -show_entries format=duration,size -of default=nw=1 01-proto-final.mp4`
  confirms both duration and that audio was actually muxed.
- 1080p, 24 fps, H.264, yuv420p — plays everywhere, uploads quickly.
