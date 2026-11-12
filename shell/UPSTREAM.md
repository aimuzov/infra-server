# Bundled shell assets

The Linux profile was moved from [aimuzov/dotfiles](https://github.com/aimuzov/dotfiles/tree/83db955).
The server prompt is maintained here independently of the macOS prompt.

| Component | Revision | License |
| --- | --- | --- |
| [fzf.fish](https://github.com/PatrickF1/fzf.fish) | `6a6136998879dcc1f29a405dfdd6b92c5f229c39` | [MIT](licenses/fzf.fish.txt) |
| [Catppuccin fish](https://github.com/catppuccin/fish) | `5fc5ae9c2ec22eb376cb03ce76f0d262a38960f3` | [MIT](licenses/catppuccin-fish.txt) |
| [Catppuccin fzf](https://github.com/catppuccin/fzf) | `7508f8141286fb95249100a2b5325960320dcf32` | [MIT](licenses/catppuccin-fzf.txt) |

The installer pins fzf 0.74.4 and Oh My Posh 31.4.1 and verifies their release SHA-256 sums.
Fish, zoxide, fd and bat come from Debian packages. Ghostty terminfo was exported from
Ghostty 1.3.1. To update a plugin or theme, replace its files, retain its license and record
the upstream revision here.
