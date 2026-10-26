<p align="center">
    <img width="16" height="16" alt="no_encryption" src="https://github.com/user-attachments/assets/6ff88473-0dd2-4bbc-b1ea-c683d5d7a134" /> unshackle
    <br/>
    <sup><em>Movie, TV, and Music Archival Software</em></sup>
    <br/>
      <a href="https://discord.gg/mHYyPaCbFK">
        <img src="https://img.shields.io/discord/1395571732001325127?label=&logo=discord&logoColor=ffffff&color=7289DA&labelColor=7289DA" alt="Discord">
    </a>
</p>

<p align="center">
  <a href="#install">Install</a> &nbsp;·&nbsp;
  <a href="https://docs.unshackle.dev">Docs</a> &nbsp;·&nbsp;
  <a href="https://discord.gg/mHYyPaCbFK">Discord</a>
</p>

---

A fork of [unshackle](https://github.com/unshackle-dl/unshackle) (originally based on [Devine](https://github.com/devine-dl/devine/)). The upstream project provides modular movie, TV, and music archival, DASH/HLS/ISM parsing, Widevine and PlayReady support, and a REST API.

## What this fork adds

- **More precise episode selection:** fractional seasons and episodes, plus separately playable episode parts.
- **Optional Japanese title replacements:** add `source|replacement` entries to your user config's `config_romaji.txt` to customize title names.
- **External subtitles & Auto-Sync:** include a local subtitle file or supported URL with the CLI's `--dl-sub` option. External subtitles from TVer are automatically synchronized against the episode audio using [AutoSubSync](https://github.com/denizsafak/AutoSubSync) (`assy-cli` / `ffsubsync`) before muxing.
- **Output improvements:** shorter service source tags in filenames, improved track duration and muxed metadata, and export filenames.
- **API and download diagnostics:** stateless list/search requests fail clearly when a service requires interactive authentication; download errors retain their underlying HTTP cause.

See the [CLI reference](docs/guide/cli-reference.md) and [output and naming guide](docs/guide/output-and-naming.md) for more options.

<p align="center">
  <a href="https://asciinema.org/a/ldMiqYFFTgPAOxW7">
    <img src="https://asciinema.org/a/ldMiqYFFTgPAOxW7.svg" alt="unshackle demo" width="700">
  </a>
</p>

## Install

The recommended way is a git clone with the install script. The script installs `uv` and unshackle, then runs `unshackle setup` to download the external tools and write a first config.

```shell
git clone https://github.com/Nosoba/unshackle-mods.git
cd unshackle-mods
sh install.sh                 # Windows: .\install.bat
uv run unshackle env check
```

In a clone, run every command as `uv run unshackle ...`.

> [!NOTE]
> Advanced: `uv tool install git+https://github.com/Nosoba/unshackle-mods.git` installs unshackle into the tool folder of `uv`, not a folder that you choose. `unshackle setup` then puts the tools, config, and data in your user data and config directories. See [Installation](https://docs.unshackle.dev/getting-started/installation/) for details.

### Requirements

External tools. (recommended versions):

- [Python](https://www.python.org/) - 3.11 - 3.14
- [uv](https://docs.astral.sh/uv/) - ≥ 0.5
- [FFmpeg](https://ffmpeg.org/) - ≥ 6.0
- [MKVToolNix](https://mkvtoolnix.download/) - ≥ 80
- [shaka-packager](https://github.com/shaka-project/shaka-packager/releases) - 2.6.1, or ≥ 3.10.0

Optional:

- [Bento4](https://github.com/axiomatic-systems/Bento4) - ≥ 1.6.0-639
- [dovi_tool](https://github.com/quietvoid/dovi_tool) - ≥ 2.1
- [SubtitleEdit](https://github.com/SubtitleEdit/subtitleedit/releases) - ≥ 5.0 (`SeConv` CLI)

## License

[GPL-3.0](LICENSE). Do not use unshackle for content you do not have the rights to. Keep the core free and open. Keep service code private. Be kind.
