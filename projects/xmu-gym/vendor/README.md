# Vendored wheels

`ccl_chromium_reader` and `ccl_simplesnappy` are not on PyPI. These pure-Python
wheels are built from the pinned commits below so `pip install -r requirements.txt`
works without Git:

| Package | Version | Commit | License |
|---|---|---|---|
| `ccl_chromium_reader` | 0.3.18 | `ef840de30221c4d65bc96d2f4d9057e9ef2f526d` | MIT |
| `ccl_simplesnappy` | 0.4 | `3d085230baa8c46cf2090ebba29bf6e8eab31087` | MIT |

Upstream: <https://github.com/cclgroupltd/ccl_chromium_reader> and
<https://github.com/cclgroupltd/ccl_simplesnappy>. The wheels include their
upstream license files in `.dist-info/licenses/`.
