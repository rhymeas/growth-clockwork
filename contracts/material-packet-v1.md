# Material packet v1

`material_packet_version = 1.0` binds the files selected for one channel draft to
their immutable Studio records and exact decoded bytes. The packet is written in
the same Root Writer transaction as the QA report, pending review manifest and
release lineage.

Each descriptor contains the local material ID, record path and SHA-256, filename,
MIME type, byte count, content SHA-256 and honest processing state. It contains no
base64 payload or credential. Project Desk resolves a preview only after rechecking
the packet hash, Studio receipt, record hash, decoded byte hash and project profile.

Current Project Desk intake sends exact binary request bodies to
`POST /api/material-assets`, capped at 64 MiB per file and 512 MiB per project.
New originals live as immutable content-addressed blobs under the selected project's
append-only state root. Small Studio JSON records contain only the verified blob
reference, digest, metadata and bounded derived text. The reference is hidden from
the browser read model. The legacy base64 JSON endpoint remains available only for
compatible clients and keeps its 2 MiB cap. A crash can leave one unreferenced inert
blob; exact retry safely reuses it by hash.

Text files remain research input. Optional local processors can add bounded OCR,
media metadata, or transcripts before review: Tesseract, FFprobe/FFmpeg and
whisper.cpp. Their output is untrusted derived material; original bytes and hashes
remain unchanged. Missing tools stay visibly `indexed`, never fake-processed. JPEG,
PNG, WebP and MP4 files may become Postiz attachments after the exact draft revision
is approved. Other media still fail closed with `needs local conversion` because v1
does not treat OCR, transcription, or signature checking as platform compatibility.

The packet is evidence attached to the existing review item, not a second approval
queue. Approval still applies to the visible content package: exact draft bytes plus
the exact media packet presented in the same review. The resulting approved release
package can therefore be consumed by a publisher without reconstructing or guessing
which Studio files belonged to the draft.
