# Unstract Marker X2Text Adapter

Text extraction through a self-hosted [Marker](https://github.com/datalab-to/marker)
server (`marker_server`). The adapter uploads the document to
`POST <url>/marker/upload` and uses the `output` field of the JSON reply.

Settings: `url` (server root, e.g. `http://192.168.188.28:8001`), `output_format`
(`markdown`/`html`/`json`), `force_ocr`, `paginate_output`, `page_range`,
`timeout`.

The request is made from the process running the adapter (workers/backend), so
the URL must be reachable from those containers.
