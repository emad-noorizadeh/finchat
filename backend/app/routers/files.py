import os
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, UploadFile, Form
from sqlmodel import Session, select

from app.database import get_session, get_chroma_client
from app.models.file import File
from app.schemas.file import FileResponse, FileUploadResponse
from app.services.indexing_service import IndexingService

router = APIRouter(prefix="/api/files", tags=["files"])

UPLOAD_DIR = Path(__file__).resolve().parent.parent.parent / "uploads"

ALLOWED_EXTENSIONS = {".md"}


def _validate_markdown(content: bytes) -> tuple[bool, str]:
    """Check if content looks like valid markdown. Returns (is_valid, warning)."""
    try:
        text = content.decode("utf-8")
    except UnicodeDecodeError:
        return False, "File is not valid UTF-8 text"

    if not text.strip():
        return False, "File is empty"

    # Check for markdown indicators
    md_indicators = 0
    lines = text.split("\n")
    for line in lines:
        stripped = line.strip()
        if stripped.startswith("#"):
            md_indicators += 2  # headings are strong signal
        elif stripped.startswith("- ") or stripped.startswith("* ") or stripped.startswith("1. "):
            md_indicators += 1  # lists
        elif "**" in stripped or "__" in stripped:
            md_indicators += 1  # bold
        elif stripped.startswith("```"):
            md_indicators += 1  # code blocks
        elif stripped.startswith("> "):
            md_indicators += 1  # blockquotes
        elif "[" in stripped and "](" in stripped:
            md_indicators += 1  # links

    # If no markdown indicators at all and file is long, warn
    if md_indicators == 0 and len(lines) > 5:
        return True, "Warning: File has no markdown formatting. It will be indexed as plain text."

    return True, ""


@router.post("/upload", response_model=FileUploadResponse)
async def upload_file(
    file: UploadFile,
    user_id: str = Form(...),
    splitting_method: str = Form("recursive"),
    session: Session = Depends(get_session),
):
    # Validate extension
    ext = Path(file.filename).suffix.lower()
    if ext not in ALLOWED_EXTENSIONS:
        raise HTTPException(
            status_code=400,
            detail=f"Unsupported file type: {ext}. Only markdown (.md) files are accepted.",
        )

    # Read and validate content
    content = await file.read()

    # Validate markdown content
    is_valid, warning = _validate_markdown(content)
    if not is_valid:
        raise HTTPException(status_code=400, detail=warning)

    # Save file to disk
    user_dir = UPLOAD_DIR / user_id
    user_dir.mkdir(parents=True, exist_ok=True)
    file_path = user_dir / file.filename
    file_path.write_bytes(content)

    # Create File record — collection is per-user (system-wide for knowledge)
    file_record = File(
        user_id=user_id,
        filename=file.filename,
        path=str(file_path),
        file_type=file.content_type or "",
        file_extension=ext,
        collection_name="system_knowledge",
        splitting_method=splitting_method,
        status="processing",
    )

    session.add(file_record)
    session.commit()
    session.refresh(file_record)

    # Index the file (synchronous for now)
    try:
        chroma = get_chroma_client()
        indexer = IndexingService(chroma)
        chunk_count = await indexer.index_file(
            file_path=str(file_path),
            file_id=file_record.id,
            filename=file_record.filename,
            file_extension=ext,
            collection_name=file_record.collection_name,
            user_id=user_id,
            splitting_method=splitting_method,
        )
        file_record.chunk_count = chunk_count
        file_record.status = "ready"
    except Exception as e:
        file_record.status = "error"
        file_record.error_message = str(e)

    session.add(file_record)
    session.commit()
    session.refresh(file_record)

    return FileUploadResponse(
        file_id=file_record.id,
        filename=file_record.filename,
        status=file_record.status,
    )


@router.get("")
def list_files(user_id: str, session: Session = Depends(get_session)):
    stmt = (
        select(File)
        .where(File.user_id == user_id)
        .order_by(File.created_at.desc())
    )
    files = session.exec(stmt).all()
    return [
        {
            "id": f.id,
            "filename": f.filename,
            "file_type": f.file_type,
            "file_extension": f.file_extension,
            "chunk_count": f.chunk_count,
            "splitting_method": f.splitting_method,
            "status": f.status,
            "error_message": f.error_message,
            "created_at": f.created_at.isoformat(),
        }
        for f in files
    ]


@router.get("/{file_id}")
def get_file(file_id: str, session: Session = Depends(get_session)):
    f = session.get(File, file_id)
    if not f:
        raise HTTPException(status_code=404, detail="File not found")
    return {
        "id": f.id,
        "user_id": f.user_id,
        "filename": f.filename,
        "file_type": f.file_type,
        "file_extension": f.file_extension,
        "collection_name": f.collection_name,
        "chunk_count": f.chunk_count,
        "splitting_method": f.splitting_method,
        "status": f.status,
        "error_message": f.error_message,
        "created_at": f.created_at.isoformat(),
    }


@router.get("/{file_id}/content")
def get_file_content(file_id: str, session: Session = Depends(get_session)):
    """Get the raw file content."""
    f = session.get(File, file_id)
    if not f:
        raise HTTPException(status_code=404, detail="File not found")
    try:
        content = Path(f.path).read_text(encoding="utf-8")
        return {"filename": f.filename, "content": content}
    except FileNotFoundError:
        raise HTTPException(status_code=404, detail="File not found on disk")


@router.get("/{file_id}/chunks")
def get_file_chunks(file_id: str, session: Session = Depends(get_session)):
    """Get all chunks for a file from ChromaDB."""
    f = session.get(File, file_id)
    if not f:
        raise HTTPException(status_code=404, detail="File not found")

    try:
        chroma = get_chroma_client()
        collection = chroma.get_collection(f.collection_name)
        results = collection.get(
            where={"file_id": file_id},
            include=["documents", "metadatas"],
        )
        chunks = []
        if results and results.get("documents"):
            for i, doc in enumerate(results["documents"]):
                meta = results["metadatas"][i] if results.get("metadatas") else {}
                chunks.append({
                    "index": meta.get("chunk_index", i),
                    "content": doc,
                    "section": meta.get("section_heading", ""),
                    "char_count": meta.get("char_count", len(doc)),
                    "is_whole_doc": meta.get("is_whole_doc", False),
                })
        chunks.sort(key=lambda c: c["index"])
        return {"filename": f.filename, "chunk_count": len(chunks), "chunks": chunks}
    except Exception as e:
        return {"filename": f.filename, "chunk_count": 0, "chunks": [], "error": str(e)}


@router.delete("/{file_id}")
def delete_file(file_id: str, session: Session = Depends(get_session)):
    f = session.get(File, file_id)
    if not f:
        raise HTTPException(status_code=404, detail="File not found")

    # Delete vectors from ChromaDB using the file's actual collection name
    chroma = get_chroma_client()
    indexer = IndexingService(chroma)
    indexer.delete_file_vectors(collection_name=f.collection_name, file_id=f.id)

    # Delete file from disk
    try:
        os.remove(f.path)
    except FileNotFoundError:
        pass

    # Delete record
    session.delete(f)
    session.commit()

    return {"status": "deleted"}
