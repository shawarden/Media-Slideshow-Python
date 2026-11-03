# Media-Slideshow-Python
Media Slideshow application for Linux, writen in Python.

```
slideshow.py - image + video slideshow for Linux (Wayland and X11).

USAGE
    slideshow.py [FOLDER_OR_FILE] [options]

    With no path a folder chooser opens. A file path opens that file's folder
    and starts on that file. Sub-folders are always read recursively.

    -i, --interval SEC        seconds per image while the slideshow is playing (5)
    --play                    start playing instead of paused
    --shuffle                 force shuffle      (default: shuffle only if the folder
    --no-shuffle              force natural sort  has media in sub-folders)
    --no-rotate               start with rotate-to-fit off
    --windowed                do not start fullscreen
    --include-hidden          include dot-files and dot-folders
    --icon-size SIZE          set toolbar icon button size in pixels (default: 64)
    --icon-padding PAD        set icon button padding in pixels (0 and negative values
                              are allowed; default: automatic, 1/5 of the icon size)
    --controls [visible|hidden] initial state of on-screen controls (default: visible)
    --controls-opacity OPACITY controls background opacity from 0.0 to 1.0 (default: 0.0)
    -a1, --action-1 CMD       Command for key 1 (supports {path}, which is shell-quoted
                              automatically - do not add quotes around it)
    ...
    -a0, --action-10 CMD      Command for key 0 (supports {path}, as above)

KEYS / MOUSE
    Left / Right        previous / next
    Up / Down / wheel   zoom in / out
    Home / End          first / last
    Space               next
    P                   play / pause slideshow
    V                   video play / pause
    M                   mute / unmute
    PgUp / PgDn         volume up / down
    S                   toggle shuffle / sorted
    A                   auto-rotate to fit on/off
    1~0                 run custom shell commands (configured via CLI or COMMAND_MAPPINGS)
    Delete              move current file to trash
    O / Ctrl+O          open folder
    F / F11             fullscreen
    Esc / Q             quit
    H                   toggle keep controls visible (pin)
    I                   toggle file info overlay
    Drag                cycles through items (drag left = next); when zoomed in,
                        drag pans instead. Top bar: click = jump, drag = scrub.
                        Video seek bar: bottom edge of the video (rotates with it).

DEPENDENCIES
    PySide6 (Qt 6.5+, with the Qt Multimedia FFmpeg backend - the pip wheel has it)
    Optional: Pillow (+ pillow-heif / pillow-jxl-plugin) as a fallback decoder for
    HEIC / AVIF / JPEG XL when Qt has no plugin for them.
    Arch:   pacman -S pyside6 qt6-multimedia-ffmpeg qt6-wayland qt6-svg \
                       qt6-imageformats python-pillow
```
