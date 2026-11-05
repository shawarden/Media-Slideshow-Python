# Media-Slideshow-Python
Media Slideshow application for Linux, writen in Python.

```
slideshow.py - image + video slideshow for Linux (Wayland and X11).

USAGE
    slideshow.py [FOLDER_OR_FILE] [options]

    With no path a folder chooser opens. A file path opens that file's folder
    and starts on that file. Sub-folders are always read recursively.

    Short  Long                      Description
    -i     --interval SEC            seconds per image while the slideshow is playing (5)
    -w     --windowed                do not start fullscreen
    -s     --icon-size SIZE          toolbar icon button size in pixels (default: 64)
    -p     --icon-padding PAD        icon button padding in pixels (0 and negative values
                                     are allowed; default: automatic, 1/5 of the icon size)
    -v     --controls STATE          initial state of on-screen controls: visible or
                                     hidden (default: visible)
    -t     --controls-opacity ALPHA  controls background opacity, 0.0 to 1.0 (default: 0.0)
    -d     --controls-dock DOCK      where the control panel sits: bottom, top, left or
                                     right (default: bottom; --dock is an alias)
    -o     --controls-order ORDER    comma- or space-separated order of control items,
                                     repeats allowed (e.g. prev,next,folder,prev,next);
                                     names: prev next play shuffle rotate info pin
                                     trash folder quit sep (--control-order is an alias)
    -a1    --action-1 CMD            command for key 1 (supports {path}, which is
                                     shell-quoted automatically - do not add quotes)
    ...    ...
    -a0    --action-10 CMD           command for key 0 (supports {path}, as above)
           --play                    start playing instead of paused
           --shuffle                 force shuffle      (default: shuffle only if the
           --no-shuffle              force natural sort  folder has media in sub-folders)
           --no-rotate               start with rotate-to-fit off
           --include-hidden          include dot-files and dot-folders

KEYS / MOUSE
    Space           Next
    Left / Right    Previous / next
    Wheel           Previous / next (scroll up / down)
    Up / Down       Zoom in / out
    Ctrl + wheel    Zoom in / out
    Home / End      First / last
    P               Play / pause slideshow
    V               Video play / pause
    M               Mute / unmute
    PgUp / PgDn     Volume up / down
    S               Toggle shuffle / sorted
    A               Auto-rotate to fit on/off
    1~0             Run custom shell commands (configured via CLI or COMMAND_MAPPINGS)
    Delete          Move current file to trash
    O / Ctrl+O      Open folder: new slideshow from a chosen folder (the dialog opens one
                    level up, with the current picture's folder pre-selected)
    F / F11         Fullscreen
    Esc / Q         Quit
    H               Toggle keep controls visible (pin)
    I               Toggle file info overlay
    Drag            Cycles through items (drag left = next)
    Top Seekbar     Jump/scrub though slideshow
    Bottom Seekbar  Jump/scrib through current video
    Control Panel   Long-press (~0.5 s) and drag to dock it bottom / top / left / right.
                    Icons and info text wrap onto extra lines when the window is small.

DEPENDENCIES
    PySide6 (Qt 6.5+, with the Qt Multimedia FFmpeg backend - the pip wheel has it)
    Optional: Pillow (+ pillow-heif / pillow-jxl-plugin) as a fallback decoder for
    HEIC / AVIF / JPEG XL when Qt has no plugin for them.
    Arch:   pacman -S pyside6 qt6-multimedia-ffmpeg qt6-wayland qt6-svg \
                       qt6-imageformats python-pillow
```
