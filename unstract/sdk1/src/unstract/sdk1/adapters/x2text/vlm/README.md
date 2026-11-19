# Unstract VLM X2Text Adapter

Extracts text from images (JPG/PNG/WebP/TIFF/BMP) and PDFs by sending each page
to a vision-capable model behind an OpenAI-compatible `/chat/completions`
endpoint (vLLM, llama.cpp server, LM Studio, SGLang, ...).

Photos are EXIF-rotated and downscaled to `max_side` px. PDFs are rendered page
by page with `pypdfium2` (already a transitive dependency of the SDK) at
`pdf_dpi`. Page texts are joined with a blank line. A page-count cap
(`max_pages`) fails explicitly rather than silently truncating.
