from pydantic import BaseModel


class FileResponse(BaseModel):
    model_config = {"from_attributes": True}

    id: str
    user_id: str
    filename: str
    file_type: str
    file_extension: str
    chunk_count: int
    splitting_method: str
    status: str
    error_message: str
    created_at: str


class FileUploadResponse(BaseModel):
    file_id: str
    filename: str
    status: str
