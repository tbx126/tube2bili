"""Portable ASS styling, independent of Bilibili's plain CC tracks."""
import math


def timestamp(value):
    ticks = round(value.total_seconds() * 100)
    hours, ticks = divmod(ticks, 360000)
    minutes, ticks = divmod(ticks, 6000)
    seconds, hundredths = divmod(ticks, 100)
    return f'{hours}:{minutes:02}:{seconds:02}.{hundredths:02}'


def text(value):
    # Source text must never become ASS override tags or layout commands.
    return ' '.join(value.split()).replace('\\', '＼').replace('{', '｛').replace('}', '｝')


def write_ass(path, chinese, english):
    if len(chinese) != len(english) or any(
        (a.start, a.end) != (b.start, b.end) for a, b in zip(chinese, english)
    ):
        raise ValueError('ASS bilingual tracks must have matching timelines')
    header = '''[Script Info]
Title: Tube2Bili bilingual subtitles
ScriptType: v4.00+
PlayResX: 1920
PlayResY: 1080
WrapStyle: 0
ScaledBorderAndShadow: yes

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: Bilingual,Noto Sans CJK SC,44,&H004FD5FF,&H004FD5FF,&H00101010,&H80000000,-1,0,0,0,100,100,0,0,1,2.5,1,2,100,100,54,1

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
'''
    rows = []
    for zh, en in zip(chinese, english):
        if not (math.isfinite(zh.start.total_seconds()) and zh.end > zh.start):
            raise ValueError('Invalid ASS timing')
        if timestamp(zh.start) == timestamp(zh.end):
            raise ValueError('ASS cue is shorter than time precision')
        content = text(zh.content) + r'\N{\c&HFFFFFF&\fs34\b0}' + text(en.content)
        rows.append(f'Dialogue: 0,{timestamp(zh.start)},{timestamp(zh.end)},Bilingual,,0,0,0,,{content}')
    path.write_text(header + '\n'.join(rows) + '\n', encoding='utf-8-sig')
