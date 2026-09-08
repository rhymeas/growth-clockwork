# Third-party software

Growth Clockwork's Python runtime uses the Python standard library. The dashboard's
JavaScript dependencies are pinned in `dashboard/package-lock.json` and installed by
npm; dependency code is not copied into this source distribution.

`SBOM.cdx.json` is the normalized CycloneDX inventory generated from that exact
lockfile. Regenerate it after every dependency change:

```bash
python3 -m pipeline.sbom --workspace . --output SBOM.cdx.json
```

The current dashboard lock identifiers include MIT, MIT-0, ISC, Apache-2.0,
BSD-2-Clause, BSD-3-Clause and MPL-2.0. Each dependency remains governed by its own
license. In particular, MPL-2.0 components retain their file-level terms.

Optional media processing calls separately installed open-source tools without a
command shell: Tesseract (Apache-2.0), whisper.cpp (MIT), and Homebrew's standard
FFmpeg formula (currently GPL-3.0-or-later). They and Whisper model files are not
copied into this source package or SBOM. Their own licenses apply when installed or
distributed.

Run this command against the exact export to verify the receipt and current license
inventory:

```bash
python3 -m pipeline.package_audit .
```

Review the lockfile and installed package license texts before distributing bundled
JavaScript dependencies or binary builds. This file is an inventory guide, not legal
advice and not a replacement for third-party license texts.
