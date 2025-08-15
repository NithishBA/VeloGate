from typing import Any 
from app.schemas import Response
from fastapi import HTTPException

def create_response(
    status: bool, data: Any = None, message: str = "", code: int = 200
) -> Response:
    return Response(
        status=status,
        code=code,
        message=message,
        data=data,
        errors=None,  
    )



def create_error_response(message: str, code: int = 400) -> HTTPException:
    raise HTTPException(
        status_code=code,
        detail={
            "status": False,
            "code": code,
            "message": message,
            "data": None,
            "errors": None, 
        },
    )
