import re
from pathlib import Path

from app.rag.schemas import DocumentMetadata, TextChunk


def clean_text(text: str) -> str:
    """Normalize line endings, whitespace and repeated blank lines without changing meaning."""
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    text = re.sub(r"[\t ]+", " ", text)
    text = re.sub(r"\n[ \t]+", "\n", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def read_document(path: Path) -> str:
    """Read supported text formats; PDF support is optional but available when pypdf is installed."""
    suffix = path.suffix.lower()
    if suffix in {".md", ".txt"}:
        return clean_text(path.read_text(encoding="utf-8"))
    if suffix == ".pdf":
        from pypdf import PdfReader

        return clean_text("\n".join(page.extract_text() or "" for page in PdfReader(path).pages))
    raise ValueError(f"Unsupported document type: {path.suffix}. Use .md, .txt, or .pdf")


def chunk_text(
    text: str, metadata: DocumentMetadata, chunk_size: int, overlap: int
) -> list[TextChunk]:
    """Create overlapping character chunks, preferring a sentence boundary near each cut."""
    if chunk_size < 100:
        raise ValueError("chunk_size must be at least 100")
    if not 0 <= overlap < chunk_size:
        raise ValueError("overlap must be non-negative and smaller than chunk_size")

    normalized = clean_text(text)
    if not normalized:
        return []

    chunks: list[TextChunk] = []
    start = 0
    while start < len(normalized):
        end = min(len(normalized), start + chunk_size)
        if end < len(normalized):
            boundary = max(normalized.rfind(". ", start, end), normalized.rfind("\n", start, end))
            if boundary > start + chunk_size // 2:
                end = boundary + 1
        content = normalized[start:end].strip()
        if content:
            chunks.append(TextChunk(text=content, metadata=metadata, chunk_index=len(chunks)))
        if end >= len(normalized):
            break
        start = end - overlap
    return chunks
