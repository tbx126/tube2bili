# Third-party dependencies

The 2026-11-01 repair adds no runtime package dependency to the NAS image. The
deployment derives from the user's existing `tube2bili:0.2.0-rc.34` image and keeps
its installed distributions, package license files and operating-system notices.
Images are deployed privately to the same user's NAS, not published to a registry.

Local testing uses a project-specific `.venv`, managed with uv 0.13.6. uv was
obtained from its official PyPI distribution, verified against PyPI SHA-256, and
its MIT/Apache-2.0 license files remain beside the executable. Local test packages
and required dependencies were reviewed from PyPI metadata before installation;
installation used uv and PyPI, with no Anaconda or related repositories.

The resolved dependency review is in `docs/python-dependency-review.json`.
This is a review inventory, not a complete legal clearance or a deployment lock.
Package-provided copyright notices and license texts remain in each installed
`*.dist-info` directory, including `licenses/` and `LICENSE*` files where supplied.
System package notices remain in `/usr/share/doc` in the container. Do not remove
these files when copying or distributing artifacts.

Most reviewed components use MIT, BSD, Apache-2.0, PSF or Unlicense terms.
Notable copyleft components in the existing application dependency graph include:

| Component | License |
| --- | --- |
| bilibili-api-python | GPL-3.0-or-later |
| bgutil-ytdlp-pot-provider | GPLv3 |
| mutagen | GPL-2.0-or-later |
| pycountry | LGPL-2.1-only |
| certifi | MPL-2.0 |

Private execution does not itself require publishing source, but future external
distribution of code, bundled dependencies or container images requires a fresh
review of applicable source, attribution, notice and license obligations.
Compiled wheels can contain additional third-party code; preserve their notices.

biliup's upstream MIT license was checked separately because its PyPI metadata
does not identify a license: https://github.com/ForgQi/bilibiliupload/blob/master/LICENSE

API subscriptions and network subscriptions have separate service terms. A valid
API Key and a model appearing in a subscription do not establish permission for
every use. See `docs/RECOVERY.md` for the Token Plan scope restriction reviewed
during this repair.
