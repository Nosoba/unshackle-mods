import re

master_text = """
#EXTM3U
#EXT-X-MEDIA:TYPE=SUBTITLES,URI="https://streamc01cf.nxtv.jp/text_MEZ0001332708_webvtt_jaJP_sdh_fp.m3u8",GROUP-ID="text_webvtt"
"""

for match in re.finditer(r'#EXT-X-MEDIA:TYPE=SUBTITLES.*?URI="(.*?)"', master_text):
    uri = match.group(1)
    if "webvtt" in uri:
        print("FOUND:", uri)
