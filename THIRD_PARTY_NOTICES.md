# Third-party notices

Competitor Census integrates with the following separately maintained tools. Their source code is not copied or rebranded in this repository.

## OpenCLI

- Project: [jackwener/OpenCLI](https://github.com/jackwener/OpenCLI)
- Role here: optional browser bridge used by the TikTok and Facebook connectors to read an authorized Chrome session
- License: Apache License 2.0

OpenCLI remains the work of its upstream authors. Users install it separately. Competitor Census supplies its own command surface, TikTok and Facebook collection logic, checkpoints, evidence schema, validation, and reporting workflow.

## yt-dlp

- Project: [yt-dlp/yt-dlp](https://github.com/yt-dlp/yt-dlp)
- Role here: optional public YouTube metadata and selected public-comment collector invoked by the YouTube connectors
- License: Unlicense

Users install yt-dlp separately. No third-party credentials, cookies, or binaries are committed to this repository.
