from pydantic import BaseModel
from typing import List, Optional, Dict, Any


# Terminal information sent in heartbeat
class TerminalHeartbeat(BaseModel):
    id: int  # local terminal ID
    name: str
    ip: str
    port: int
    username: str
    status: str
    model: str
    firmware: str
    serial: str


# Employee information sent in heartbeat
class EmployeeSync(BaseModel):
    employee_id: str
    first_name: str
    last_name: str
    department: str
    phone: str
    is_active: int  # 1 or 0
    face_image: Optional[str] = None


# Attendance log information sent in heartbeat
class AttendanceLogSync(BaseModel):
    id: int  # local log ID
    employee_id: str
    terminal_id: int  # local terminal ID
    event_time: str
    event_type: str
    raw_data: str


# Heartbeat Request Payload
class HeartbeatRequest(BaseModel):
    app_version: str
    terminals: List[TerminalHeartbeat]
    today_stats: Dict[str, Any]
    unsynced_logs: List[AttendanceLogSync]
    unsynced_employees: List[EmployeeSync]


# Command Response Schema (sent from server to client in heartbeat)
class PendingCommand(BaseModel):
    command_id: str
    command_type: str
    payload: Dict[str, Any]


# Heartbeat Response
class HeartbeatResponse(BaseModel):
    status: str
    license_status: str  # active, blocked, expired
    pending_commands: List[PendingCommand]


# Command Execution Result (sent from client to server)
class CommandResultRequest(BaseModel):
    command_id: str
    status: str  # success, failed
    response_data: str
