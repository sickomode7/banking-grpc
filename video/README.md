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
2. Record audio only, while the video plays. `pw-record` writes straight to WAV:

```bash
# the mic, not the output monitor. This machine has no default source set,
# so name the target explicitly (or run `wpctl set-default 60` once).
pw-record --target alsa_input.pci-0000_74_00.6.analog-stereo \
          --rate 48000 --channels 1 --format s16 video/01-proto-voice.wav
# speak, Ctrl+C when the video ends
```

3. Check the level before trusting the take:

```bash
ffmpeg -hide_banner -i 01-proto-voice.wav -af volumedetect -f null - 2>&1 \
  | grep -E "mean_volume|max_volume"
```

Aim for `max_volume` around −9 to −6 dB. Nearer 0 means it will clip; below
−30 means nobody will hear it.

4. Trim the lead-in silence you get from starting the recorder first:

```bash
ffmpeg -i 01-proto-voice.wav \
       -af "silenceremove=start_periods=1:start_threshold=-45dB:start_silence=0.2" \
       01-proto-trim.wav
```

5. Mux without re-encoding the picture:

```bash
ffmpeg -i 01-proto.mp4 -i 01-proto-voice.wav \
       -map 0:v -map 1:a -c:v copy -c:a aac -b:a 192k \
       -af apad -shortest 01-proto-final.mp4
```

`-af apad` pads the narration with silence, `-shortest` ends the file at the end
of the picture. Using `-shortest` without `apad` cuts the video down to the
length of your audio, which is the usual way a good take gets ruined.

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
